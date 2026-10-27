import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from tracker.models import Election
from tracker.services import sync_early_detail, sync_early_vote, sync_forecasts, sync_results
 
 
__author__ = "Mustafa Prodhan"
class Command(BaseCommand):
    help = "Poll CivicAPI for race results. Use --watch for election night."
 
    def add_arguments(self, p):
        p.add_argument("--year", type=int, default=2026)
        p.add_argument("--watch", action="store_true")
        p.add_argument("--interval", type=int, default=settings.CIVICAPI_POLL_SECONDS)
        p.add_argument("--early", action="store_true", help="also pull early-vote snapshots")
        p.add_argument("--detail", action="store_true", help="store full early-vote JSON (states + counties)")
        p.add_argument("--forecast", action="store_true", help="store civicAPI forecasts")
        p.add_argument("--all", action="store_true", help="results + early + detail + forecast")
        p.add_argument("--param", action="append", default=[], help="k=v query param passed to the races endpoint")
 
    def handle(self, *a, **o):
        el = Election.objects.filter(year=o["year"]).first()
        if not el:
            raise CommandError("No election for that year. Run load_seed first.")
        params = dict(x.split("=", 1) for x in o["param"])
        while True:
            try:
                stats = {}
                stored, unmatched = sync_results(el, stats, **params)
                self.stdout.write(f"stored={stored} unmatched={len(unmatched)} "
                                  f"ignored_pre_election={stats.get('skipped', 0)}")
                if o["all"] or o["forecast"]:
                    self.stdout.write("forecast stored=%s unmatched=%s" % sync_forecasts(el))
                if o["all"] or o["detail"]:
                    self.stdout.write(f"early_detail rows={sync_early_detail(el)}")
                if o["early"] or o["all"]:
                    self.stdout.write(f"early_vote states={sync_early_vote(el)}")
            except Exception as exc:
                self.stderr.write(f"sync failed: {exc!r}")
                if not o["watch"]:
                    raise CommandError(str(exc))
            if not o["watch"]:
                break
            time.sleep(o["interval"])
 