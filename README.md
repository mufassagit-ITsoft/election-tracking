(Mustafa Prodhan, 2026)
# Election Tracker (data service)
Django, JSON only (no pages), SQLite, Docker. Ingests CivicAPI (https://civicapi.org/api/v2/<endpoint>)
and serves state/district-keyed JSON for a map front end.

## Run
    cp .env.example .env
    docker compose up --build              # migrates, loads the 2026 seed, serves :8000
    docker compose --profile live up       # also starts the election-night poller

Local (Poetry): `poetry install --no-root && poetry run python manage.py migrate && poetry run python manage.py load_seed && poetry run python manage.py runserver`

## Endpoints (/api/)
health/ · elections/ · {year}/summary/ · {year}/races/?office=&state= · {year}/map/{senate|governor|house}/?rater=cook|sabato|inside
· {year}/results/live/ · {year}/early-vote/

## Getting data
Run from the project folder with the environment active (`poetry run` prefix works too).

**Load / refresh from CivicAPI**

    python manage.py load_seed                       # 2026 races, ratings, early-vote baseline (idempotent)
    python manage.py sync_civicapi                   # one results pull (general-election races only)
    python manage.py sync_civicapi --early           # results + state early-vote snapshots
    python manage.py sync_civicapi --early --watch --interval 300   # keep polling (election night)
    python manage.py civic_probe races               # raw CivicAPI check; use `--param k=v` for query params

**Read from this service** (start it with `python manage.py runserver`)

    curl -s localhost:8000/api/health/
    curl -s localhost:8000/api/2026/summary/ | python -m json.tool
    curl -s "localhost:8000/api/2026/races/?office=senate&state=OH" | python -m json.tool
    curl -s localhost:8000/api/2026/map/senate/?rater=cook | python -m json.tool
    curl -s localhost:8000/api/2026/results/live/ | python -m json.tool
    curl -s localhost:8000/api/2026/early-vote/ | python -m json.tool

**Early-vote tally**

    python manage.py sync_civicapi --early
    curl -s localhost:8000/api/2026/early-vote/ | python -c "import sys,json; print(json.load(sys.stdin)['total_early'])"
    python manage.py shell -c "from tracker.models import EarlyVoteSnapshot as E; [print(e.state, e.as_of, e.total, e.pct_of_prior) for e in E.objects.filter(source='civicapi').order_by('-total')[:10]]"

Early vote `total` = mail returned + in person; `mail_requested` is separate. `pct_of_prior` compares with 2024 final turnout where known.

**Straight from CivicAPI** (no Django; base `https://civicapi.org/api/v2`)

    curl -s $B/status
    curl -s "$B/race/search?country=US&type=Senate&limit=100"      # filters: country, province, type, election_type, election_date, limit, offset
    curl -s $B/race/87524                                           # one race by id
    curl -s $B/early-vote/US/returned                               # states; use OH instead of US for counties
    curl -s $B/forecast/senate/2026/civicapi

Early-vote categories: `requested`, `returned`, `inperson`, `grace`. Endpoint paths are set in `CIVICAPI_ENDPOINTS`
and office search types in `CIVICAPI_OFFICE_TYPES` (config/settings.py); response field names are in `FIELD_MAP`
(tracker/civicapi.py). The House search `type` value is unconfirmed; check `unmatched=` after a sync.
Attribution: credit civicAPI (civicapi.org) in any public project.

## Data
tracker/seed/midterm_2026.json: 35 Senate, 36 governor races, Cook ratings for all 435 House districts,
state early-vote baseline. Ratings are as-of the dates stored on each rating. Early vote is by state, not contest.