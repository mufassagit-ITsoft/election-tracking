# Map data for the GUI and read from SQLite. 
__author__ = "Mustafa Prodhan"

import math

from tracker.models import (CandidateResult, EarlyVoteSnapshot, Election, Forecast, MapShape,
                            Race, ResultSnapshot)

NO_DATA = "#d4d4d4"
TOSSUP = "#e0bb24"
OTHER = "#8b7ab8"
D_SHADES = {3: "#1f5fa8", 2: "#4a86c5", 1: "#8db4de", 0: "#bcd3ec"}   # solid/safe, likely, lean, tilt
R_SHADES = {3: "#b3262d", 2: "#d6585f", 1: "#e7959a", 0: "#f1c3c6"}
STRENGTH = (("solid", 3), ("safe", 3), ("likely", 2), ("lean", 1), ("tilt", 0))
RATERS = ("cook", "sabato", "inside", "civicapi")
OFFICES = ("senate", "governor", "house")
MODES = ("rating", "results", "early")
 
 
def rating_side_strength(text):
    # 'Lean R (flip)' -> ('R', 1); 'Toss Up' -> ('T', 0); None/'' -> (None, 0).
    t = (text or "").lower().replace("(flip)", "").strip()
    if not t:
        return None, 0
    if "toss" in t:
        return "T", 0
    strength = next((v for k, v in STRENGTH if k in t), 0)
    words = t.replace("-", " ").split()
    if "democratic" in words or "d" in words or "democrat" in words:
        return "D", strength
    if "republican" in words or "r" in words:
        return "R", strength
    return None, 0
 
 
def rating_color(text):
    side, strength = rating_side_strength(text)
    return {"D": D_SHADES.get(strength), "R": R_SHADES.get(strength), "T": TOSSUP}.get(side, NO_DATA)
 
 
def party_side(party):
    p = (party or "").strip().lower()
    if p in ("d", "dem") or p.startswith("democrat"):
        return "D"
    if p in ("r", "rep", "gop") or p.startswith("republican"):
        return "R"
    return "O" if p else None
 
 
def party_color(party, strength=3):
    side = party_side(party)
    return {"D": D_SHADES[strength], "R": R_SHADES[strength], "O": OTHER}.get(side, NO_DATA)
 
 
def early_color(pct):
    # Sequential green by early vote as % of 2024 final: 0% pale -> 100%+ dark.
    if pct is None:
        return NO_DATA
    t = max(0.0, min(pct / 100.0, 1.0))
    lo, hi = (226, 240, 217), (0, 109, 44)
    return "#%02x%02x%02x" % tuple(round(a + (b - a) * t) for a, b in zip(lo, hi))
 
 
def legend(mode):
    if mode == "early":
        return [("0%", early_color(0)), ("50%", early_color(50)), ("100%+ of 2024", early_color(100)),
                ("no data", NO_DATA)]
    if mode == "results":
        return [("D called", D_SHADES[3]), ("R called", R_SHADES[3]), ("D leading", D_SHADES[1]),
                ("R leading", R_SHADES[1]), ("other", OTHER), ("no votes", NO_DATA)]
    return [("Solid D", D_SHADES[3]), ("Likely D", D_SHADES[2]), ("Lean D", D_SHADES[1]),
            ("Tilt D", D_SHADES[0]), ("Tossup", TOSSUP), ("Tilt R", R_SHADES[0]), ("Lean R", R_SHADES[1]),
            ("Likely R", R_SHADES[2]), ("Solid R", R_SHADES[3]), ("unrated", NO_DATA)]
 
 
# projection
class _Albers:
    def __init__(self, lat1, lat2, lat0, lon0):
        r = math.radians
        self.n = (math.sin(r(lat1)) + math.sin(r(lat2))) / 2
        self.c = math.cos(r(lat1)) ** 2 + 2 * self.n * math.sin(r(lat1))
        self.rho0 = math.sqrt(self.c - 2 * self.n * math.sin(r(lat0))) / self.n
        self.lon0 = lon0
 
    def __call__(self, lon, lat):
        rho = math.sqrt(self.c - 2 * self.n * math.sin(math.radians(lat))) / self.n
        th = self.n * math.radians(lon - self.lon0)
        return rho * math.sin(th), -(self.rho0 - rho * math.cos(th))   # y grows downward
 
 
_PROJ = {"main": _Albers(29.5, 45.5, 37.5, -96), "AK": _Albers(55, 65, 50, -154), "HI": _Albers(8, 18, 3, -157)}
 
 
def _rings(geom):
    polys = geom["coordinates"] if geom["type"] == "MultiPolygon" else [geom["coordinates"]]
    return [ring for poly in polys for ring in poly[:1]]   # outer rings only
 
 
def project_states():
    # {postal: [[(x, y), ...], ...]} in abstract units, with AK and HI as insets under the west.
    raw = {s.key: _rings(s.geometry) for s in MapShape.objects.filter(layer="states")}
    out = {}
    for key, rings in raw.items():
        p = _PROJ.get(key, _PROJ["main"])
        out[key] = [[p(lon, lat) for lon, lat in ring] for ring in rings]
    if not out:
        return out
 
    def bbox(keys):
        pts = [pt for k in keys for ring in out[k] for pt in ring]
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        return min(xs), min(ys), max(xs), max(ys)
 
    def place(key, scale, left, bottom):
        x0, y0, x1, y1 = bbox([key])
        out[key] = [[(left + (x - x0) * scale, bottom - (y1 - y) * scale) for x, y in ring]
                    for ring in out[key]]
        return left + (x1 - x0) * scale
 
    main = [k for k in out if k not in ("AK", "HI")]
    mx0, my0, mx1, my1 = bbox(main)
    gap = (mx1 - mx0) * 0.015
    right = mx0
    if "AK" in out:
        right = place("AK", 0.38, mx0, my1 + (my1 - my0) * 0.02) + gap
    if "HI" in out:
        place("HI", 1.0, right, my1 + (my1 - my0) * 0.02)
    return out
 
 
def tile_squares():
    # {'AZ-3': (x, y, w)} in tile-grid units.
    return {s.key: (s.geometry["x"], s.geometry["y"], s.geometry["w"])
            for s in MapShape.objects.filter(layer="house_tiles")}
 
 
# data
def _latest_snapshots(el, office):
    seen, snaps = {}, []
    for s in ResultSnapshot.objects.filter(race__election=el, race__office=office).order_by("race_id", "-fetched_at"):
        if s.race_id not in seen:
            seen[s.race_id] = s
            snaps.append(s)
    res = {}
    for c in CandidateResult.objects.filter(snapshot__in=snaps).order_by("-votes"):
        res.setdefault(c.snapshot_id, []).append(c)
    return seen, res
 
 
def early_by_state(el):
    # {state: {'total', 'pct_of_prior', 'as_of', ...}} from the newest EarlyVoteSnapshot per state.
    out = {}
    for e in EarlyVoteSnapshot.objects.filter(election=el).order_by("state", "-as_of", "-fetched_at"):
        if e.state not in out and e.total is not None:
            out[e.state] = dict(total=e.total, pct_of_prior=e.pct_of_prior, as_of=e.as_of,
                                prior_final=e.prior_final, mail_requested=e.mail_requested,
                                mail_returned=e.mail_returned, in_person=e.in_person,
                                dem=e.dem, rep=e.rep, other=e.other)
    return out
 
 
def layer_data(year, office, mode="rating", rater="cook"):
    # {key: {'fill': '#rrggbb', 'tip': 'short text', 'race_id': int|None}} keyed by AZ or AZ-3.
    el = Election.objects.filter(year=year).first()
    if not el:
        return {}
    races = Race.objects.filter(election=el, office=office).prefetch_related("ratings")
    snaps, results = _latest_snapshots(el, office) if mode == "results" else ({}, {})
    early = early_by_state(el) if mode == "early" else {}
    out = {}
    for r in races:
        key = r.label
        rt = next((x.rating for x in r.ratings.all() if x.rater == rater), None)
        if mode == "rating":
            fill, tip = rating_color(rt), f"{key}: {rt or 'unrated'} ({rater})"
        elif mode == "results":
            s = snaps.get(r.id)
            cands = results.get(s.id, []) if s else []
            if s and s.called and s.winner_party:
                fill, tip = party_color(s.winner_party, 3), f"{key}: {s.winner_name} ({s.winner_party}) called"
            elif cands and (cands[0].votes or 0) > 0:
                fill = party_color(cands[0].party, 1)
                tip = f"{key}: {cands[0].name} leads, {s.percent_reporting or 0:g}% reporting"
            else:
                fill, tip = NO_DATA, f"{key}: no votes yet"
        else:
            e = early.get(r.state)
            pct = e["pct_of_prior"] if e else None
            fill = early_color(pct)
            tip = f"{r.state}: {e['total']:,} early ({pct}% of 2024)" if e and pct is not None else f"{r.state}: no early data"
        if key not in out:
            out[key] = dict(fill=fill, tip=tip, race_id=r.id)
    if mode == "early" and office != "house":   # early vote is statewide: colour every state with data
        for st, e in early.items():
            if st not in out and st != "US":
                pct = e["pct_of_prior"]
                out[st] = dict(fill=early_color(pct), race_id=None, state=st,
                               tip=f"{st}: {e['total']:,} early" + (f" ({pct}% of 2024)" if pct is not None else ""))
    return out
 
 
def state_early_detail(year, state):
    el = Election.objects.filter(year=year).first()
    e = early_by_state(el).get(state) if el else None
    if not e:
        return f"{state}: no early-vote data"
    return "\n".join([f"{year} EARLY VOTE  {state} (no {year} race of this office)",
                      f"  total {e['total']:,}" + (f"  = {e['pct_of_prior']}% of 2024 final" if e["pct_of_prior"] is not None else ""),
                      f"  requested {e['mail_requested'] or 0:,}  returned {e['mail_returned'] or 0:,}  in person {e['in_person'] or 0:,}",
                      f"  D {e['dem'] or 0:,}  R {e['rep'] or 0:,}  other {e['other'] or 0:,}   as of {e['as_of']}"])
 
 
def race_detail(race_id):
    # Plain-text detail panel for one race.
    r = Race.objects.select_related("election").filter(pk=race_id).first()
    if not r:
        return ""
    L = [f"{r.election.year} {r.get_office_display().upper()}  {r.label}" + ("  (special)" if r.is_special else "")]
    if r.incumbent:
        L.append(f"Incumbent: {r.incumbent} ({r.incumbent_party})  {r.seat_status}".rstrip())
    if r.status_text:
        L.append(r.status_text)
    if r.candidates_text:
        L += ["", r.candidates_text]
    L += ["", "RATINGS"] + [f"  {x.rater:9s} {x.rating}  ({x.as_of})" for x in r.ratings.order_by("rater")] 
    f = Forecast.objects.filter(election=r.election, office=r.office, key=r.label).first()
    if f and f.dem_pct is not None:
        L.append(f"  civicAPI win chance: D {f.dem_pct:g}%  R {f.rep_pct:g}%" +
                 (f"  I {f.ind_pct:g}%" if f.ind_pct else ""))
    s = r.snapshots.order_by("-fetched_at").first()
    L += ["", "LIVE RESULTS"]
    if s:
        L.append(f"  {s.percent_reporting or 0:g}% reporting, as of {s.fetched_at:%Y-%m-%d %H:%M}"
                 + (f"  CALLED: {s.winner_name} ({s.winner_party})" if s.called else ""))
        for c in s.results.order_by("-votes")[:8]:
            L.append(f"  {c.name} ({c.party}): {c.votes or 0:,}  {c.percent or 0:g}%" + ("  *" if c.is_winner else ""))
    else:
        L.append("  none yet")
    e = early_by_state(r.election).get(r.state)
    L += ["", f"EARLY VOTE ({r.state}, statewide)"]
    if e:
        L.append(f"  total {e['total']:,}" + (f"  = {e['pct_of_prior']}% of 2024 final" if e["pct_of_prior"] is not None else ""))
        L.append(f"  requested {e['mail_requested'] or 0:,}  returned {e['mail_returned'] or 0:,}  in person {e['in_person'] or 0:,}")
        L.append(f"  D {e['dem'] or 0:,}  R {e['rep'] or 0:,}  other {e['other'] or 0:,}   as of {e['as_of']}")
    else:
        L.append("  none")
    return "\n".join(L)
 
 
def summary_line(year, office, mode, rater):
    # Counts for the status bar.
    data = layer_data(year, office, mode, rater)
    called = sum(1 for v in data.values() if v["fill"] not in (NO_DATA,))
    return f"{len(data)} {office} races, {called} coloured"