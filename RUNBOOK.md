# Election Tracker runbook

Three jobs, always in this order: **run the data** (poller) -> **save the data** (SQLite) -> **run the map** (GUI).
All commands run from the project folder with the virtual environment active (`source .venv/bin/activate`).

> **Golden rule:** every host command that touches data starts with `DB_DIR=$PWD/data`.
> That is the one database the containers and the map share. Without it you write to a different file.

## 0. One-time setup
```bash
sudo apt install python3-tk                  # the map window needs Tk
sudo systemctl enable docker                 # Docker starts after a reboot
cp -n .env.example .env                      # then set SECRET_KEY, DEBUG, ALLOWED_HOSTS in .env
mkdir -p data
```
`docker-compose.yml` must mount `./data:/data` for both `web` and `poller`.

## 1. Run the data (poller)
```bash
docker compose up -d --build                 # starts web + poller
docker compose ps                            # both "Up"
docker compose logs --tail 20 poller         # recent poller activity
docker compose stop poller                   # pause it       |  docker compose start poller   # resume
```
Poller schedule (automatic): before election day results 30 min / early vote 10 min; **from election day
10:00 UTC** results 30 s / early vote 5 min; after 3 days it slows down again.
Force a pull right now (host, not Docker):
```bash
DB_DIR=$PWD/data python manage.py sync_civicapi --early      # results + early vote by state
DB_DIR=$PWD/data python manage.py sync_civicapi --detail     # early vote: every state and county (~200 requests)
DB_DIR=$PWD/data python manage.py sync_civicapi --forecast   # civicAPI forecasts
DB_DIR=$PWD/data python manage.py sync_civicapi --all        # everything
```
Read the output line: `stored=N unmatched=M ignored_pre_election=K test_data=...`
- `ignored_pre_election=~500` before election day is **normal** (results are held back until polls open).
- `test_data=True` means CivicAPI is serving rehearsal data, not real counts.

## 2. Save and check the data (SQLite: `data/db.sqlite3`)
Coverage (expect senate 35, governor 35-36, house 435 "with results" once real results flow):
```bash
DB_DIR=$PWD/data python manage.py shell -c "
from tracker.models import Race
for o in ('senate','governor','house'):
    q = Race.objects.filter(office=o)
    print(o, q.count(), 'with results', q.filter(snapshots__isnull=False).distinct().count())"
```
What ran and whether it worked:
```bash
DB_DIR=$PWD/data python manage.py shell -c "
from tracker.models import SyncLog
for s in SyncLog.objects.all()[:8]: print(s.started_at, s.kind, s.ok, s.detail[:80])"
```
Early vote, all states:
```bash
DB_DIR=$PWD/data python manage.py shell -c "
from tracker.models import EarlyVoteSnapshot as E
seen = set()
for e in E.objects.filter(source='civicapi').order_by('state','-as_of','-fetched_at'):
    if e.state in seen: continue
    seen.add(e.state); print(e.state, f'{e.total:,}', e.pct_of_prior, '% of 2024')"
```
API checks (web container on port 8000):
```bash
curl -s localhost:8000/api/health/ | python -m json.tool
curl -s localhost:8000/api/2026/summary/ | python -m json.tool | head -40
curl -s localhost:8000/api/2026/early-vote/ | python -m json.tool | head -40
```
**Backup** (safe while the poller runs; do not just `cp` the file):
```bash
python - <<'PY'
import sqlite3, datetime
s = sqlite3.connect('data/db.sqlite3')
d = sqlite3.connect(f"data/backup-{datetime.datetime.now():%Y%m%d-%H%M}.sqlite3")
s.backup(d); d.close(); s.close(); print("backup done")
PY
```

## 3. Run the map
```bash
DB_DIR=$PWD/data python manage.py map_gui
```
- **Office:** senate / governor / house. **View:** `rating` (pick a rater), `results`, `early`.
- Election night: **View = results**. The window re-reads SQLite every 10 s. Click a race for votes and % reporting.
- Bottom line: `Poller running in another process` + recent run times. `FAILED` or a TEST DATA banner needs attention.
- Close with the window X button (or Ctrl+C in the terminal).

## 4. Optional dress rehearsal (CivicAPI test data)
```bash
echo "CIVICAPI_IGNORE_PREELECTION=0" >> .env      # and the line in config/settings.py (see README)
docker compose up -d --build
CIVICAPI_IGNORE_PREELECTION=0 DB_DIR=$PWD/data python manage.py sync_civicapi
DB_DIR=$PWD/data python manage.py map_gui        # View = results; the TEST DATA banner shows
```
**Undo it before real counts** (see Nov 2 below).

## 5. Nov 2 (the night before): reset and get ready
```bash
sed -i '/^CIVICAPI_IGNORE_PREELECTION=/d' .env   # rehearsal off
docker compose stop poller
docker compose up -d --build
docker compose stop poller
DB_DIR=$PWD/data python manage.py shell -c "
from tracker.models import ResultSnapshot
print(ResultSnapshot.objects.all().delete())"   # clears stored result snapshots (not ratings/early vote)
docker compose start poller
DB_DIR=$PWD/data python manage.py sync_civicapi --all   # expect stored=0, ignored_pre_election=~500, test_data=None
```
Keep the laptop awake and plugged in (leave this running in a spare terminal all night):
```bash
systemd-inhibit --what=sleep:idle:handle-lid-switch --who=election-tracker --why="election night" sleep infinity
```
Then run the backup command from section 2.

## 6. Nov 3 (election day)
Results unlock at **10:00 UTC = 5:00 am ET** (clocks are on standard time by then; polls close in the East at 7 pm ET = 00:00 UTC).
1. Morning: `docker compose ps`; `docker compose logs --tail 20 poller`; no `FAILED`.
2. Open the map: `DB_DIR=$PWD/data python manage.py map_gui`, View = results.
3. After the first polls close, run the coverage check (section 2): "with results" should climb.
4. Every few hours: the backup command.
5. If something fails: see section 7. Never delete snapshots on election night; they are the replay record.

## 7. Troubleshooting
| Symptom | Do this |
|---|---|
| `permission denied` on docker | `newgrp docker`, then `source .venv/bin/activate` (or log out and back in) |
| Map empty / "Database not ready" | You forgot `DB_DIR=$PWD/data`, or the containers are still migrating (wait 15 s) |
| `results ... FAILED` in status line | `docker compose logs poller \| grep -i failed \| tail -3`. 503/timeouts retry by themselves and back off |
| `unmatched=1` | Guam governor, not in the seed: expected |
| TEST DATA banner | CivicAPI rehearsal data. Fine before Nov 3, wrong on election night: do section 5 |
| `stored=0` on election night | Check `docker compose logs poller`; run `civic_search_test` to see what CivicAPI returns |
| Another poller holds the lock | Only one runs at a time (`poller.lock`). Stop the other one |

Credit civicapi.org on anything public.