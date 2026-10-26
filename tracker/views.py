# This is where the mapping results are located
from django.db.models import Max
from django.http import Http404, JsonResponse
 
from .models import EarlyVoteSnapshot, Election, Race, ResultSnapshot, SyncLog

__author__ = "Mustafa Prodhan"
RATERS = ("cook", "sabato", "inside")
 
 
def _election(year):
    el = Election.objects.filter(year=year).first()
    if not el:
        raise Http404("no election for that year")
    return el
 
 
def _ratings(race):
    return {r.rater: {"rating": r.rating, "as_of": r.as_of} for r in race.ratings.all()}
 
 
def _latest_snapshot(race):
    s = race.snapshots.order_by("-fetched_at").first()
    if not s:
        return None
    return {
        "fetched_at": s.fetched_at.isoformat(), "percent_reporting": s.percent_reporting,
        "total_votes": s.total_votes, "called": s.called,
        "winner": {"name": s.winner_name, "party": s.winner_party} if s.called and s.winner_name else None,
        "candidates": [{"name": c.name, "party": c.party, "votes": c.votes, "percent": c.percent,
                        "is_winner": c.is_winner}
                       for c in s.results.order_by("-votes")],
    }
 
 
def _race_json(race, with_results=True):
    d = {"id": race.id, "office": race.office, "state": race.state, "district": race.district,
         "label": race.label, "is_special": race.is_special, "incumbent": race.incumbent,
         "incumbent_party": race.incumbent_party, "seat_status": race.seat_status,
         "status_text": race.status_text, "candidates_text": race.candidates_text,
         "note": race.note, "ratings": _ratings(race)}
    if with_results:
        d["results"] = _latest_snapshot(race)
    return d
 
 
def health(request):
    last = SyncLog.objects.filter(kind="results").first()
    return JsonResponse({"ok": True, "elections": Election.objects.count(),
                         "last_results_sync": ({"at": last.started_at.isoformat(), "ok": last.ok,
                                                "detail": last.detail} if last else None)})
 
 
def elections(request):
    return JsonResponse({"elections": [{"year": e.year, "kind": e.kind, "date": e.date.isoformat(),
                                        "name": e.name, "races": e.races.count()}
                                       for e in Election.objects.all()]})
 
 
def races(request, year):
    el = _election(year)
    qs = Race.objects.filter(election=el).prefetch_related("ratings")
    if request.GET.get("office"):
        qs = qs.filter(office=request.GET["office"])
    if request.GET.get("state"):
        qs = qs.filter(state=request.GET["state"].upper())
    qs = qs.order_by("office", "state", "district")[:int(request.GET.get("limit", 600))]
    return JsonResponse({"races": [_race_json(r) for r in qs]})
 
 
def map_data(request, year, office):
    # One entry per state (senate/governor) or per district (house), for colouring a map.
    el = _election(year)
    if office not in ("senate", "governor", "house"):
        raise Http404
    rater = request.GET.get("rater", "cook")
    out = {}
    for r in Race.objects.filter(election=el, office=office).prefetch_related("ratings"):
        key = r.label
        rt = next((x for x in r.ratings.all() if x.rater == rater), None)
        snap = _latest_snapshot(r)
        entry = {"rating": rt.rating if rt else None,
                 "called": bool(snap and snap["called"]),
                 "winner_party": snap["winner"]["party"] if snap and snap["winner"] else None,
                 "percent_reporting": snap["percent_reporting"] if snap else None,
                 "leader": snap["candidates"][0] if snap and snap["candidates"] else None}
        if office != "house":
            entry["incumbent"] = r.incumbent; entry["incumbent_party"] = r.incumbent_party
            entry["is_special"] = r.is_special
        out[key] = entry if key not in out else out[key]
    return JsonResponse({"office": office, "rater": rater, "features": out})
 
 
def live_results(request, year):
    el = _election(year)
    latest = (ResultSnapshot.objects.filter(race__election=el).values("race").annotate(m=Max("fetched_at")))
    rows = []
    for x in latest:
        race = Race.objects.get(pk=x["race"])
        rows.append(_race_json(race))
    called = sum(1 for r in rows if r["results"] and r["results"]["called"])
    return JsonResponse({"races_reporting": len(rows), "races_called": called, "races": rows})
 
 
def early_vote(request, year):
    el = _election(year)
    latest = {}
    for s in EarlyVoteSnapshot.objects.filter(election=el).order_by("state", "as_of", "fetched_at"):
        latest[s.state] = s  # newest per state wins
    rows = [{"state": s.state, "as_of": s.as_of.isoformat() if s.as_of else None, "total": s.total,
             "mail_requested": s.mail_requested, "mail_returned": s.mail_returned,
             "in_person": s.in_person, "party": {"dem": s.dem, "rep": s.rep, "other": s.other,
                                                 "no_affiliation": s.no_affiliation},
             "prior_final": s.prior_final, "pct_of_prior": s.pct_of_prior, "source": s.source,
             "flags": s.flags} for s in latest.values()]
    return JsonResponse({
        "note": "Counts are by state, not by contest. 'as_of' differs by state.",
        "states": rows, "total_early": sum(r["total"] or 0 for r in rows)})
 
 
def _side(rating):
    r = (rating or "").lower().replace("(flip)", "").strip()
    if "toss" in r:
        return "tossup"
    if r.endswith(" d") or "democ" in r:
        return "D"
    if r.endswith(" r") or "republic" in r:
        return "R"
    return "other"
 
 
def summary(request, year):
    # Rating tallies per office/rater, plus how many races are called so far.
    el = _election(year)
    rater = request.GET.get("rater", "cook")
    out = {}
    for office in ("senate", "governor", "house"):
        tally = {"D": 0, "R": 0, "tossup": 0, "other": 0, "unrated": 0}
        races = Race.objects.filter(election=el, office=office).prefetch_related("ratings")
        called = {"D": 0, "R": 0, "other": 0}
        for r in races:
            rt = next((x for x in r.ratings.all() if x.rater == rater), None)
            tally[_side(rt.rating) if rt else "unrated"] += 1
            snap = r.snapshots.filter(called=True).order_by("-fetched_at").first()
            if snap:
                p = snap.winner_party.upper()[:1]
                called[p if p in ("D", "R") else "other"] += 1
        out[office] = {"races": races.count(), "rated_" + rater: tally, "called": called}
    return JsonResponse({"year": year, "rater": rater, "offices": out})
 
