import requests
from django.conf import settings
 
__author__ = "Mustafa Prodhan"
# canonical name -> candidate keys in the CivicAPI payload (first match wins)
FIELD_MAP = {
    "id": ["id", "race_id", "raceId", "uuid"],
    "office": ["office", "office_name", "race_type", "type"],
    "state": ["province", "state", "state_abbr", "state_code", "stateAbbr"],
    "district": ["district", "district_number", "district_id"],
    "candidates": ["candidates", "results", "contestants"],
    "cand_name": ["name", "candidate", "candidate_name", "full_name"],
    "cand_party": ["party", "party_abbr", "party_name"],
    "cand_votes": ["votes", "vote_count", "total_votes", "votes_received"],
    "cand_pct": ["percent", "pct", "vote_pct", "percentage", "vote_percent"],
    "cand_winner": ["winner", "is_winner", "won", "elected"],
    "reporting": ["percent_reporting", "pct_reporting", "reporting", "precincts_reporting_pct"],
    "total_votes": ["total_votes", "votes_total", "total"],
    "called": ["called", "is_called", "race_called", "projected"],
}
 
OFFICE_ALIASES = {
    "senate": "senate", "us senate": "senate", "u.s. senate": "senate", "senator": "senate",
    "house": "house", "house of representatives": "house", "us house": "house", "u.s. house": "house", "representative": "house",
    "governor": "governor", "gov": "governor",
    "attorney general": "attorney_general", "attorney_general": "attorney_general", "ag": "attorney_general",
}
 
 
def pick(d, canon, default=None):
    for k in FIELD_MAP[canon]:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return default
 
 
def get(endpoint_key, suffix="", **params):
    path = settings.CIVICAPI_ENDPOINTS[endpoint_key].strip("/")
    if suffix:
        path = f"{path}/{str(suffix).strip('/')}"
    url = f"{settings.CIVICAPI_BASE_URL.rstrip('/')}/{path}"
    r = requests.get(url, params=params or None, timeout=settings.CIVICAPI_TIMEOUT,
                     headers={"Accept": "application/json", "User-Agent": "election-tracker/1.0"})
    r.raise_for_status()
    return r.json()
 
 
def extract_races(payload):
    """Accept a bare list or a dict wrapping a list under a common key."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in ("races", "data", "results", "items"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []
 
 
def _num(x):
    try:
        return float(str(x).replace(",", "").replace("%", ""))
    except (TypeError, ValueError):
        return None
 
 
def _bool(x):
    return x is True or str(x).strip().lower() in ("1", "true", "yes", "y", "called")
 
 
def normalize_race(item):
    # dict with office/state/district/civic_id and cleaned candidate results.
    office_raw = str(pick(item, "office", "")).strip().lower()
    office = OFFICE_ALIASES.get(office_raw, "")
    state = str(pick(item, "state", "")).strip().upper()[:2]
    district = pick(item, "district", "")
    if isinstance(district, str) and "-" in district:
        district = district.rsplit("-", 1)[1]  # "AZ-06" -> "06"
    district = "" if district in ("", None, "AL", "0") and office != "house" else str(district).lstrip("0") or "0"
    cands = []
    for c in pick(item, "candidates", []) or []:
        if not isinstance(c, dict):
            continue
        cands.append(dict(
            name=str(pick(c, "cand_name", "")).strip(),
            party=str(pick(c, "cand_party", "")).strip(),
            votes=(int(_num(pick(c, "cand_votes"))) if _num(pick(c, "cand_votes")) is not None else None),
            percent=_num(pick(c, "cand_pct")),
            is_winner=_bool(pick(c, "cand_winner", False)),
        ))
    winner = next((c for c in cands if c["is_winner"]), None)
    tv = _num(pick(item, "total_votes"))
    if tv is None and any(c["votes"] for c in cands):
        item = {**item, "total_votes": sum(c["votes"] or 0 for c in cands)}
    return dict(
        civic_id=str(pick(item, "id", "")), office=office, state=state, district=district,
        percent_reporting=_num(pick(item, "reporting")),
        total_votes=(int(_num(pick(item, "total_votes"))) if _num(pick(item, "total_votes")) is not None else None),
        called=_bool(pick(item, "called", False)) or winner is not None,
        candidates=[c for c in cands if c["name"]], winner=winner, raw=item,
    )
 
 
def _is_general(item, date_iso):
    text = f'{item.get("election_type", "")} {item.get("election_name", "")}'.lower()
    return (str(item.get("election_date", "")).startswith(date_iso)
            and "primary" not in text and "runoff" not in text
            and str(item.get("province", "")).upper() != "US")
 
 
def fetch_general_races(date_iso):
    # Paginated race/search per office type; keeps only general-election races on date_iso.
    out = []
    for types in settings.CIVICAPI_OFFICE_TYPES.values():
        for t in types:
            offset = 0
            while True:
                page = get("search", country="US", type=t, election_date=date_iso,
                           limit=100, offset=offset)
                races = extract_races(page)
                out += [r for r in races if _is_general(r, date_iso)]
                if len(races) < 100:
                    break
                offset += 100
    return out
 
 
def fetch_early_vote(category, state="US"):
    return get("early_vote", suffix=f"{state}/{category}")
 