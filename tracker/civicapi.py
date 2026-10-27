__author__ = "Mustafa Prodhan"
import time
 
import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
 
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
    "cand_id": ["candidate_id", "candidateId"],
    "cand_color": ["color"],
    "updated": ["last_updated", "updated_at"],
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
 
 
_session = None
 
 
def _get_session():
    """One shared session: retries timeouts / 429 / 502 / 503 / 504 with growing pauses (honours Retry-After)."""
    global _session
    if _session is None:
        _session = requests.Session()
        retry = Retry(total=3, connect=3, read=3, backoff_factor=2, status_forcelist=(429, 502, 503, 504),
                      allowed_methods=("GET",), respect_retry_after_header=True)
        _session.mount("https://", HTTPAdapter(max_retries=retry))
        _session.headers.update({"Accept": "application/json", "User-Agent": "election-tracker/1.0"})
    return _session
 
 
def get(endpoint_key, suffix="", **params):
    path = settings.CIVICAPI_ENDPOINTS[endpoint_key].strip("/")
    if suffix:
        path = f"{path}/{str(suffix).strip('/')}"
    url = f"{settings.CIVICAPI_BASE_URL.rstrip('/')}/{path}"
    r = _get_session().get(url, params=params or None, timeout=(10, settings.CIVICAPI_TIMEOUT))
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
    """-> dict with office/state/district/civic_id and cleaned candidate results."""
    office_raw = str(pick(item, "office", "")).strip().lower()
    office = OFFICE_ALIASES.get(office_raw, "")
    state = str(pick(item, "state", "")).strip().upper()[:2]
    district = pick(item, "district", "")
    if office != "house":
        district = ""        # statewide races: CivicAPI may put the state code ("AZ") here
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
            civic_id=pick(c, "cand_id"), color=str(pick(c, "cand_color", "") or ""),
        ))
    winner = next((c for c in cands if c["is_winner"]), None)
    tv = _num(pick(item, "total_votes"))
    if tv is None and any(c["votes"] for c in cands):
        item = {**item, "total_votes": sum(c["votes"] or 0 for c in cands)}
    return dict(
        civic_id=str(pick(item, "id", "")), office=office, state=state, district=district,
        updated=pick(item, "updated"), percent_reporting=_num(pick(item, "reporting")),
        total_votes=(int(_num(pick(item, "total_votes"))) if _num(pick(item, "total_votes")) is not None else None),
        called=_bool(pick(item, "called", False)) or winner is not None,
        candidates=[c for c in cands if c["name"]], winner=winner, raw=item,
    )
 
 
def _is_general(item, date_iso):
    text = f'{item.get("election_type", "")} {item.get("election_name", "")}'.lower()
    return (str(item.get("election_date", "")).startswith(date_iso)
            and "primary" not in text and "runoff" not in text
            and str(item.get("province", "")).upper() != "US")
 
 
# Confirmed against the live API (2026-10-27): the 2026 Senate generals are type "US Senate" (type "Senate"
# is old races only); governor is election_type "Statewide"; the House and Senate use "General".
OFFICE_QUERIES = {
    "senate": [{"type": "US Senate", "election_type": "General"}],
    "governor": [{"type": "Governor", "election_type": "Statewide"}],
    "house": [{"type": "House of Representatives", "election_type": "General"}],
}
PAGE_LIMIT = 1000
 
 
def fetch_general_races(date_iso):
    """One race/search request per office (limit 1000), keeping only general-election races on date_iso.
    Results come newest first, so paging continues only while a full page is still at or after date_iso;
    capped at CIVICAPI_MAX_PAGES and paced by CIVICAPI_PAGE_DELAY to stay polite to a free API."""
    out = []
    pages = getattr(settings, "CIVICAPI_MAX_PAGES", 3)
    delay = getattr(settings, "CIVICAPI_PAGE_DELAY", 0.5)
    for queries in OFFICE_QUERIES.values():
        for q in queries:
            for page_no in range(pages):
                page = get("search", limit=PAGE_LIMIT, offset=page_no * PAGE_LIMIT, **q)
                races = extract_races(page)
                out += [r for r in races if _is_general(r, date_iso)]
                if len(races) < PAGE_LIMIT:
                    break
                oldest = min(str(r.get("election_date", ""))[:10] for r in races)
                if oldest < date_iso:      # paged past the election date: nothing more to find
                    break
                time.sleep(delay)
    return out
 
 
def fetch_early_vote(category, state="US"):
    return get("early_vote", suffix=f"{state}/{category}")
 