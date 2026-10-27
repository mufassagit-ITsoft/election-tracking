'''
This is a backround poller that runs early-vote numbers and election-nigh results 
adding them in SQLite database.
'''

import threading
import time
from datetime import datetime, timedelta, timezone as dt_tz
from pathlib import Path
 
from django.conf import settings
from django.db import close_old_connections
from django.utils import timezone
 
from . import services
from .models import Election, SyncLog
 
try:
    import fcntl
except ImportError:  # not Linux/macOS: run without a lock
    fcntl = None
 
__author__ = "Mustafa Prodhan"
 
# seconds between runs of each job, by phase
INTERVALS = {
    "pre":   dict(results=1800, early=600, detail=10800, forecast=21600),
    "night": dict(results=30, early=300, detail=3600, forecast=3600),
    "post":  dict(results=600, early=21600, detail=21600, forecast=21600),
}
LOGKIND = {"results": "results", "early": "early_vote", "detail": "early_detail", "forecast": "forecast"}
JOBS = ("results", "early", "detail", "forecast")   # run in this order
 
 
def phase(now, election_date):
    # 'night' = election day 6am ET through 3 days later: counts, calls and late ballots.
    start = datetime(election_date.year, election_date.month, election_date.day, 10, tzinfo=dt_tz.utc)
    if now < start:
        return "pre"
    return "night" if now < start + timedelta(days=3) else "post"
 
 
def due_jobs(now, election_date, last, overrides=None):
    # [job names] whose interval has passed. last: {job: epoch seconds}; overrides: {job: seconds}.
    iv = {**INTERVALS[phase(now, election_date)], **(overrides or {})}
    t = now.timestamp()
    return [j for j in JOBS if t - last.get(j, 0) >= iv[j]]
 
 
class Lock:
    # Non-blocking file lock next to the database.
    def __init__(self, path=None):
        self.path = Path(path or Path(settings.DB_DIR) / "poller.lock")
        self.fh = None
 
    def acquire(self):
        if fcntl is None:
            return True
        self.fh = open(self.path, "w")
        try:
            fcntl.flock(self.fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            self.fh.close()
            self.fh = None
            return False
 
    def release(self):
        if self.fh:
            self.fh.close()
            self.fh = None
 
 
def status():
    # {job: {'at': datetime, 'ok': bool, 'detail': str}} from SyncLog (what the GUI shows).
    out = {}
    for job, kind in LOGKIND.items():
        s = SyncLog.objects.filter(kind=kind).first()
        if s:
            out[job] = dict(at=s.started_at, ok=s.ok, detail=s.detail)
    return out
 
 
class Poller:
    def __init__(self, year=2026, overrides=None, log=None):
        self.year, self.overrides, self.log = year, overrides or {}, log or (lambda m: None)
        self.stop = threading.Event()
        self.last = {}
        self._seeded = False
 
    def _seed_last(self):
        # after a restart, respect the cadence: use the last time each job actually ran
        for job, kind in LOGKIND.items():
            s = SyncLog.objects.filter(kind=kind).first()
            if s:
                self.last[job] = s.started_at.timestamp()
        self._seeded = True
 
    def _run_job(self, job, el):
        if job == "results":
            stored, unmatched = services.sync_results(el)
            return f"results stored={stored} unmatched={len(unmatched)}"
        if job == "early":
            return f"early states={services.sync_early_vote(el)}"
        if job == "detail":
            return f"detail rows={services.sync_early_detail(el)}"
        n, un = services.sync_forecasts(el)
        return f"forecast stored={n} unmatched={un}"
 
    def run_once(self, now=None, force=None):
        # Run every job that is due (or `force`d). Failures are logged and never stop the loop.
        close_old_connections()
        el = Election.objects.filter(year=self.year).first()
        if not el:
            self.log("no election loaded; run load_seed")
            return []
        if not self._seeded:
            self._seed_last()
        now = now or timezone.now()
        jobs = list(force) if force else due_jobs(now, el.date, self.last, self.overrides)
        ran = []
        for job in jobs:
            self.last[job] = now.timestamp() # also on failure, so errors don't cause a retry storm
            try:
                self.log(f"[{phase(now, el.date)}] {self._run_job(job, el)}")
            except Exception as exc:
                SyncLog.objects.create(kind=LOGKIND[job], ok=False, detail=repr(exc)[:500])
                self.log(f"{job} failed: {exc!r}")
            ran.append(job)
        return ran
 
    def run_forever(self, tick=5):
        try:
            while not self.stop.is_set():
                try:
                    self.run_once()
                except Exception as exc:  # DB hiccup etc.: keep the loop alive
                    self.log(f"poller error: {exc!r}")
                self.stop.wait(tick)
        finally:
            close_old_connections()
 
    def start_thread(self):
        t = threading.Thread(target=self.run_forever, name="poller", daemon=True)
        t.start()
        return t