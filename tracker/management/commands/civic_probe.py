import json
from django.core.management.base import BaseCommand
from tracker import civicapi
 
 
class Command(BaseCommand):
    help = "GET a CivicAPI endpoint and print the raw shape, to confirm FIELD_MAP / endpoint paths."
 
    def add_arguments(self, p):
        p.add_argument("endpoint", nargs="?", default="status", help="key in CIVICAPI_ENDPOINTS")
        p.add_argument("--param", action="append", default=[], help="k=v query param")
 
    def handle(self, *a, **o):
        params = dict(x.split("=", 1) for x in o["param"])
        data = civicapi.get(o["endpoint"], **params)
        self.stdout.write(json.dumps(data, indent=1)[:4000])
        races = civicapi.extract_races(data)
        if races:
            self.stdout.write("\nnormalised first race:\n" + json.dumps(
                {k: v for k, v in civicapi.normalize_race(races[0]).items() if k != "raw"}, indent=1))