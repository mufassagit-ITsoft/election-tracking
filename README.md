(Mustafa Prodhan, 2026)
# Election Tracker

Tracks US elections (built for the **Nov 3, 2026 midterms**: US Senate, governors, US House) from the
[CivicAPI](https://civicapi.org) v2 feed. Every response is stored in SQLite; a background poller keeps early vote
and election-night results fresh; a desktop (Tkinter) map and a JSON API both read the same database.

Data: CivicAPI (https://civicapi.org/api/v2/<endpoint>), no key required, attribution required.

```
CivicAPI --> civicapi.py --> services.py --> SQLite (data/db.sqlite3) --> mapdata.py --> Tkinter map
                                ^                       |
                          poller.py (schedule)          +--> Django JSON API (/api/...)
```

## Contents

1. [Quick start](#quick-start)
2. [Configuration](#configuration)
3. [Data model](#data-model)
4. [Getting data](#getting-data)
5. [Background poller](#background-poller)
6. [Desktop map](#desktop-map)
7. [JSON API](#json-api)
8. [Docker](#docker)
9. [Election night](#election-night)
10. [Project layout](#project-layout)
11. [Tests](#tests)
12. [Troubleshooting](#troubleshooting)

See `RUNBOOK.md` for the step-by-step command checklist.

## Quick start

**Golden rule:** every host command that touches the database starts with `DB_DIR=$PWD/data`, so the host, the
containers and the map all share one file, `data/db.sqlite3`.

### With Docker (recommended)

```bash
cp .env.example .env            # then set SECRET_KEY
mkdir -p data
docker compose up -d --build    # web (:8000) + poller, both migrate and load the 2026 seed
docker compose ps
DB_DIR=$PWD/data python manage.py map_gui     # desktop map on the host (needs the venv, see below)
```

### Local only (Poetry)

```bash
python3.12 -m venv .venv && source .venv/bin/activate
poetry install --no-root
sudo apt install python3-tk                    # Tkinter, for the map
export DB_DIR=$PWD/data && mkdir -p data
python manage.py migrate
python manage.py load_seed                     # races, ratings, baselines, map shapes
python manage.py sync_civicapi --all           # results, early vote, county detail, forecasts
python manage.py map_gui
```

## Configuration

Secrets use `python-decouple` (read from `.env` or the environment). CivicAPI settings use `os.environ` with defaults.

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | required | Django secret key (name is `SECRET_KEY`, not `DJANGO_SECRET_KEY`) |
| `DEBUG` | `False` | Django debug flag |
| `ALLOWED_HOSTS` | | comma-separated hosts |
| `DB_DIR` | project folder | folder that holds `db.sqlite3` and `poller.lock` |
| `CIVICAPI_BASE_URL` | `https://civicapi.org/api/v2` | API root |
| `CIVICAPI_TIMEOUT` | `20` | read timeout in seconds (use `45` on slow links) |
| `CIVICAPI_MAX_PAGES` | `3` | page cap for `race/search` |
| `CIVICAPI_PAGE_DELAY` | `0.5` | seconds between pages |
| `CIVICAPI_IGNORE_PREELECTION` | `1` | ignore results dated before election day (see below) |
| `CIVICAPI_EP_*` | built in | override an endpoint path (`status`, `search`, `race`, `early_vote`, `forecast`) |

## Data model

| Model | Holds |
|---|---|
| Election | year and date (2026-11-03) |
| Race | office, state, district; `label` is `STATE-DISTRICT`; `civic_id`, `civic_updated` |
| Candidate | race, name, party, incumbent flag, `civic_id`, colour |
| Rating | rater (cook, sabato, inside, civicapi) and rating per race |
| ResultSnapshot, CandidateResult | **append-only**: every poll is kept, the map shows the latest |
| EarlyVoteSnapshot | statewide early vote totals, `% of 2024 final` |
| EarlyVoteRegion | early vote by state/county, category, party |
| Forecast | CivicAPI forecast per state (rating, D/R/I %, names, incumbent party) |
| SyncLog | one row per sync with counts (`stored= unmatched= ignored_pre_election= test_data=`) |
| MapShape | state outlines stored as JSON |

SQLite runs in WAL mode with a busy timeout (set in `tracker/apps.py`), so the poller can write while the map reads.

## Getting data

```bash
python manage.py load_seed                       # 2026 races, ratings, early-vote baseline + map shapes (idempotent)
python manage.py load_geo                        # re-store map shapes only
python manage.py sync_civicapi                   # one results pull (general-election races only)
python manage.py sync_civicapi --early           # results + statewide early-vote snapshots
python manage.py sync_civicapi --detail          # full early-vote JSON: every state and county, all categories
python manage.py sync_civicapi --forecast        # CivicAPI forecasts (also saved as rater "civicapi")
python manage.py sync_civicapi --all             # everything above
python manage.py sync_civicapi --early --watch --interval 300
python manage.py civic_probe                     # API health check: {"status":"ok"}
python manage.py civic_probe races --param k=v   # raw call with query params
python manage.py civic_search_test               # diagnostic: compares race/search query shapes
```

Search queries (in `tracker/civicapi.py`): Senate `type="US Senate"`, governor `type="Governor"` with
`election_type="Statewide"`, House `type="House of Representatives"`, limit 1000, filtered client-side to 2026-11-03
(CivicAPI ignores `election_date` when `type` is set). Expected coverage: Senate 35, governor 35, House 435.
The Guam governor race stays unmatched by design.

Early vote `total` = mail returned + in person; `mail_requested` is kept separately.

### Test data guard

CivicAPI currently streams **rehearsal data** (`test_data=true`) with results that look final. Until election day
**10:00 UTC (5:00 am ET on Nov 3**, because daylight time ends Nov 1), any row carrying votes, a winner or a call is
ignored and counted as `ignored_pre_election`. The map shows a red TEST DATA banner while the feed reports test data.
Set `CIVICAPI_IGNORE_PREELECTION=0` only for a dress rehearsal, and clear the snapshots afterward:

```bash
DB_DIR=$PWD/data python manage.py shell -c "from tracker.models import ResultSnapshot; print(ResultSnapshot.objects.all().delete())"
```

## Background poller

One schedule keeps SQLite fresh and speeds up by itself on election night (election day 10:00 UTC through 3 days after).

| Phase | Results | Early vote | Race detail | Forecast |
|---|---|---|---|---|
| before election day | 30 min | 10 min | 3 h | 6 h |
| election night | 30 s | 5 min | 1 h | 1 h |
| after | 10 min | 6 h | 6 h | 6 h |

```bash
python manage.py run_poller                  # foreground loop (Ctrl+C stops it)
python manage.py run_poller --once           # every job once, then exit
python manage.py run_poller --results 15     # fixed interval in seconds (also --early/--detail/--forecast)
systemctl --user enable --now election-poller   # always on, Ubuntu: see deploy/election-poller.service
docker compose logs -f poller               # the containerised poller
```

Safety: an `fcntl` lock (`poller.lock`) allows one poller per database; the map's embedded poller stays idle if another
holds the lock. Failures back off by `min(600, 15 * 2**fails)` seconds and are logged in SyncLog; they never stop the
loop. HTTP retries 429/502/503/504 three times. After a restart the poller respects the last run times.

## Desktop map

Reads everything from SQLite and redraws every 10 seconds. One-time: `sudo apt install python3-tk`.

```bash
DB_DIR=$PWD/data python manage.py map_gui              # --year 2026
```

- **Office:** senate or governor (state outlines, Alaska and Hawaii as insets), or house (one square per district, no
  real boundaries).
- **View:** `rating` (pick a rater), `results` (called winner or current leader), `early` (early vote as % of 2024 final;
  states without party registration show an unaffiliated total).
- Click a state or district for ratings, results and early vote.
- "Pull from CivicAPI" fetches now; "Auto every N s" repeats it; the status line shows times in UTC.
- Shows "Database not ready" instead of crashing if the tables do not exist yet.

The map runs on the host, not in Docker (it needs a display).

## JSON API

Served by Django (JSON only, CORS middleware, WhiteNoise, gunicorn in Docker) at `/api/`:

| Route | Returns |
|---|---|
| `health/` | service status |
| `elections/` | elections in the database |
| `{year}/summary/` | race and rating counts |
| `{year}/races/?office=&state=` | races with candidates and ratings |
| `{year}/map/{senate\|governor\|house}/?rater=cook\|sabato\|inside` | state/district-keyed map data |
| `{year}/results/live/` | latest results |
| `{year}/early-vote/` | early vote by state, `total_early` |

```bash
curl -s localhost:8000/api/health/
curl -s localhost:8000/api/2026/summary/ | python -m json.tool
curl -s "localhost:8000/api/2026/races/?office=senate&state=OH" | python -m json.tool
```

## Docker

`Dockerfile`: python:3.12-slim, Poetry install, `collectstatic` (with build-only dummy env values), then
`migrate`, `load_seed` and gunicorn. `docker-compose.yml` runs two services, `web` (:8000) and `poller`
(`run_poller`, restart unless-stopped), both mounting `./data:/data`. `.dockerignore` keeps `.venv`, `.git`, `.env`,
`data/` and `*.sqlite3` out of the image.

```bash
docker compose up -d --build            # start / rebuild
docker compose ps
docker compose logs --tail=30 web poller
docker compose stop poller              # pause polling (do this first if the API is rate-limiting)
docker compose up -d --force-recreate   # pick up .env changes
docker compose stop && rm -f data/db.sqlite3 && docker compose up -d   # full reset
```

On Ubuntu you need the Compose plugin and docker group: `sudo apt install docker-compose-v2`,
`sudo usermod -aG docker $USER && newgrp docker`. If the host map cannot open the WAL files, run the containers as your
user (`user: "1000:1000"` and `HOME=/tmp` in compose).

## Election night

On Nov 3 the poller switches to the night phase at 10:00 UTC and polls results every 30 seconds; every poll is
stored as a new snapshot and the map picks it up within 10 seconds.

```bash
docker compose up -d --build                   # day before
DB_DIR=$PWD/data python manage.py sync_civicapi --year 2026 --detail --forecast
DB_DIR=$PWD/data python manage.py sync_civicapi --early
systemd-inhibit --what=sleep:idle sleep infinity &     # keep the laptop awake
DB_DIR=$PWD/data python manage.py map_gui
DB_DIR=$PWD/data python manage.py shell -c "from tracker.models import *; print(ResultSnapshot.objects.count(), SyncLog.objects.order_by('-id').first().detail)"
```

Expect a snapshot count of 0 before election day and a growing count after, with `test_data=False` in the log. Real
election-night data has not been seen yet (only CivicAPI's rehearsal feed), so watch the first hour.

## Project layout

```
config/settings.py            decouple config, CivicAPI block, DB_DIR
tracker/models.py             all tables
tracker/civicapi.py           endpoint map, retry session, search queries, normalisation
tracker/services.py           load_seed, sync_results / early_vote / forecasts, pre-election guard
tracker/poller.py             phase-aware loop, lock file, backoff
tracker/geo.py                state name map, House tile grid
tracker/gui/mapdata.py        colours, projection, SQLite queries for the map
tracker/gui/app.py            Tkinter window
tracker/views.py, urls.py     JSON API
tracker/seed/                 midterm_2026.json, us_states.geojson
tracker/management/commands/  load_seed, load_geo, sync_civicapi, run_poller, map_gui, civic_probe, civic_search_test
tracker/tests/                test_tracker.py, test_map.py, test_poller.py
deploy/election-poller.service   systemd user unit
Dockerfile, docker-compose.yml, .dockerignore, .env.example
RUNBOOK.md                    command checklist
```

## Tests

```bash
python manage.py test tracker      # run from the folder with manage.py; expect "Ran 26 tests ... OK"
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: decouple` | activate the venv: `source .venv/bin/activate` |
| `no such table: tracker_election` | host and containers use different databases: use `./data:/data` and `DB_DIR=$PWD/data` |
| 404 "no election for that year" | database empty: `python manage.py load_seed` |
| `SECRET_KEY not found` during build | the Dockerfile `collectstatic` step needs `SECRET_KEY=build-only DEBUG=False` |
| Races show CALLED before Nov 3 | rehearsal data; keep `CIVICAPI_IGNORE_PREELECTION=1` and clear snapshots |
| Senate 0 of 35 | search type must be `US Senate` |
| `ReadTimeout` or 503s | `docker compose stop poller`, set `CIVICAPI_TIMEOUT=45`, restart |
| Docker `permission denied` on socket | add yourself to the `docker` group, then `newgrp docker` |
| `Found 0 test(s)` | run from the `manage.py` folder; need `tracker/tests/__init__.py` and no `tracker/tests.py` |

The full problem, solution and code log is in the Lab Guide (Part 9).