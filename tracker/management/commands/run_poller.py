import signal

from django.core.management.base import BaseCommand, CommandError
from tracker.poller import INTERVALS, Lock, Poller

__author__ = "Mustafa Prodhan"

class Command(BaseCommand):
    help = ("Run the background poller: early-vote numbers + election-night results into SQLite. "
            "Speeds up automatically on election night. Ctrl+C to stop.")
 
    def add_arguments(self, p):
        p.add_argument("--year", type=int, default=2026)
        p.add_argument("--once", action="store_true", help="run every job once and exit")
        for job in ("results", "early", "detail", "forecast"):
            p.add_argument(f"--{job}", type=int, metavar="SECONDS", help=f"fixed interval for {job} (overrides the schedule)")
 
    def handle(self, *a, **o):
        lock = Lock()
        if not lock.acquire():
            raise CommandError("another poller is already running on this database (poller.lock is held)")
        overrides = {j: o[j] for j in ("results", "early", "detail", "forecast") if o[j]}
        poller = Poller(o["year"], overrides, log=lambda m: self.stdout.write(m))
        if o["once"]:
            poller.run_once(force=("results", "early", "detail", "forecast"))
            return
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: poller.stop.set())
        self.stdout.write("poller started. schedule (s): " + "; ".join(
            f"{ph}={v}" for ph, v in INTERVALS.items()))
        try:
            poller.run_forever()
        finally:
            lock.release()
            self.stdout.write("poller stopped")