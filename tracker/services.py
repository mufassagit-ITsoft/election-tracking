import json
from datetime import date
from pathlib import Path
 
from django.db import transaction
 
from . import civicapi
from .models import (Candidate, CandidateResult, EarlyVoteSnapshot, Election, Race, Rating,
                     ResultSnapshot, SyncLog)
 
__author__ = "Mustafa Prodhan"
SEED_PATH = Path(__file__).parent / "seed" / "midterm_2026.json"
RATER_MAP = {"cook": 0, "sabato": 1, "inside": 2}
 
 
@transaction.atomic
def load_seed(path=SEED_PATH):
    """Load the compiled 2026 midterm dataset (races, ratings, early vote baseline)."""
    s = json.loads(Path(path).read_text())
    e = s["election"]
    el, _ = Election.objects.get_or_create(
        year=e["year"], kind=e["kind"], date=e["date"], defaults={"name": e["name"]})
    n = {"races": 0, "ratings": 0, "early": 0}
 
    for office, key, dates in (("senate", "senate", {"cook": "Oct 6, 2026", "sabato": "Oct 14, 2026", "inside": "Sept 17, 2026"}),
                               ("governor", "governor", {"cook": "Oct 13, 2026", "sabato": "Oct 14, 2026", "inside": "Sept 4, 2026"})):
        for r in s[key]:
            special = "special election" in r["status_text"].lower() or (office == "senate" and r["state"] in ("FL", "OH"))
            race, _ = Race.objects.update_or_create(
                election=el, office=office, state=r["state"], district="", is_special=special,
                defaults=dict(incumbent=r["incumbent"], incumbent_party=r["incumbent_party"],
                              seat_status=r["seat_status"], status_text=r["status_text"],
                              candidates_text=r["candidates_text"], note=r["note"]))
            n["races"] += 1
            for rater, val in r["ratings"].items():
                if val:
                    Rating.objects.update_or_create(race=race, rater=rater,
                                                    defaults=dict(rating=val, as_of=dates[rater]))
                    n["ratings"] += 1
 
    cook = s["house_cook"]
    for did, rating in cook["by_id"].items():
        st, num = did.split("-")
        race, _ = Race.objects.get_or_create(election=el, office="house", state=st,
                                             district=num, is_special=False)
        Rating.objects.update_or_create(race=race, rater="cook",
                                        defaults=dict(rating=rating, as_of=cook["as_of"]))
        n["races"] += 1; n["ratings"] += 1
 
    load_early_vote(el, s["early_vote"]); n["early"] = EarlyVoteSnapshot.objects.filter(election=el).count()
    return el, n
 
 
def load_early_vote(el, ev):
    cols = ev["columns"]
    for row in ev["rows"]:
        d = dict(zip(cols, row))
        if d["total"] is None and d["final_2024"] is None:
            continue  # nothing usable
        EarlyVoteSnapshot.objects.update_or_create(
            election=el, state=d["state"], as_of=date.fromisoformat(d["as_of"]) if d["as_of"] else None,
            defaults=dict(total=d["total"], mail_requested=d["mail_requested"],
                          mail_returned=d["mail_returned"], in_person=d["in_person"], dem=d["dem"],
                          rep=d["rep"], other=d["other"], no_affiliation=d["no_affiliation"],
                          prior_final=d["final_2024"], source=d["source_key"] or "", flags=d["flags"] or ""))
 
 
def _find_race(el, n):
    if n["civic_id"]:
        r = Race.objects.filter(election=el, civic_id=n["civic_id"]).first()
        if r:
            return r
    if not (n["office"] and n["state"]):
        return None
    qs = Race.objects.filter(election=el, office=n["office"], state=n["state"], district=n["district"])
    r = qs.first()
    if r and n["civic_id"] and not r.civic_id:
        r.civic_id = n["civic_id"]; r.save(update_fields=["civic_id"])
    return r
 
 
@transaction.atomic
def apply_races_payload(el, payload):
    """Store one ResultSnapshot per matched race. Returns (stored, unmatched)."""
    stored, unmatched = 0, []
    for item in civicapi.extract_races(payload):
        n = civicapi.normalize_race(item)
        race = _find_race(el, n)
        if not race:
            unmatched.append(n["civic_id"] or f'{n["office"]}:{n["state"]}-{n["district"]}')
            continue
        w = n["winner"] or {}
        snap = ResultSnapshot.objects.create(
            race=race, percent_reporting=n["percent_reporting"], total_votes=n["total_votes"],
            called=n["called"], winner_name=w.get("name", ""), winner_party=w.get("party", ""), raw=n["raw"])
        for c in n["candidates"]:
            CandidateResult.objects.create(snapshot=snap, **{k: c[k] for k in ("name", "party", "votes", "percent", "is_winner")})
            Candidate.objects.get_or_create(race=race, name=c["name"], defaults={"party": c["party"]})
        stored += 1
    return stored, unmatched
 
 
def sync_results(el, **params):
    try:
        stored, unmatched = apply_races_payload(el, civicapi.fetch_general_races(str(el.date)))
        SyncLog.objects.create(kind="results", detail=f"stored={stored} unmatched={len(unmatched)}")
        return stored, unmatched
    except Exception as exc:  # network/shape problems must not kill the poll loop
        SyncLog.objects.create(kind="results", ok=False, detail=repr(exc)[:500])
        raise
 
 
def sync_early_vote(el):
    """Pull national early-vote snapshots (by state) and store one EarlyVoteSnapshot per state."""
    cats, as_of = {}, None
    for cat in ("requested", "returned", "inperson"):
        d = civicapi.fetch_early_vote(cat)
        as_of = d.get("snapshot_date") or as_of
        cats[cat] = d.get("regions", {})
    as_of = date.fromisoformat(as_of) if as_of else date.today()
    n = 0
    for st in sorted(set().union(*[set(v) for v in cats.values()])):
        def tot(cat, key=None):
            r = cats[cat].get(st, {})
            return sum(v["votes"] for k, v in r.items() if key is None or k == key)
        mail, inp = tot("returned"), tot("inperson")
        prior = (EarlyVoteSnapshot.objects.filter(election=el, state=st, prior_final__isnull=False)
                 .order_by("-as_of").values_list("prior_final", flat=True).first())
        EarlyVoteSnapshot.objects.update_or_create(
            election=el, state=st, as_of=as_of,
            defaults=dict(total=mail + inp, mail_requested=tot("requested"), mail_returned=mail,
                          in_person=inp, dem=tot("returned", "Democratic") + tot("inperson", "Democratic"),
                          rep=tot("returned", "Republican") + tot("inperson", "Republican"),
                          other=tot("returned", "Other") + tot("inperson", "Other"),
                          no_affiliation=tot("returned", "Unspecified") + tot("inperson", "Unspecified"),
                          prior_final=prior, source="civicapi"))
        n += 1
    SyncLog.objects.create(kind="early_vote", detail=f"states={n} as_of={as_of}")
    return n
 