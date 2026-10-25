import time
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from tracker.models import Election
from tracker.services import sync_results
 
 
class Command(BaseCommand):
    # argparse for django
    help = "Poll CivicAPI for race results. Use --watch for election night."
 
    def add_arguments(self, p):
        p.add_argument("--year", type=int, default=2026)
        p.add_argument("--watch", action="store_true")
        p.add_argument("--interval", type=int, default=settings.CIVICAPI_POLL_SECONDS)
        p.add_argument("--param", action="append", default=[], help="k=v query param passed to the races endpoint")
 
    def handle(self, *a, **o):
        el = Election.objects.filter(year=o["year"]).first()
        if not el:
            raise CommandError("No election for that year. Run load_seed first.")
        params = dict(x.split("=", 1) for x in o["param"])
        while True:
            try:
                stored, unmatched = sync_results(el, **params)
                self.stdout.write(f"stored={stored} unmatched={len(unmatched)}")
            except Exception as exc:
                self.stderr.write(f"sync failed: {exc!r}")
                if not o["watch"]:
                    raise CommandError(str(exc))
            if not o["watch"]:
                break
            time.sleep(o["interval"])