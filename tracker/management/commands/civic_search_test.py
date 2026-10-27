import collections
 
from django.core.management.base import BaseCommand
 
from tracker import civicapi
 
__author__ = "Mustafa Prodhan"
 
TESTS = [
    {"type": "US Senate", "election_type": "General", "limit": 1000},
    {"type": "Governor", "election_type": "Statewide", "limit": 1000},
    {"type": "House of Representatives", "election_type": "General", "limit": 1000},
]
 
 
class Command(BaseCommand):
    help = "Try CivicAPI race/search filter combinations and print what each returns (read-only, 9 small requests)."
 
    def handle(self, *a, **o):
        for p in TESTS:
            try:
                d = civicapi.get("search", **{"limit": 100, **p})
            except Exception as exc:
                self.stdout.write(f"{p} -> ERROR {exc!r}")
                continue
            rs = civicapi.extract_races(d)
            dates = collections.Counter(str(r.get("election_date", ""))[:10] for r in rs)
            types = collections.Counter(f'{r.get("type")}/{r.get("election_type")}' for r in rs)
            extra = {k: v for k, v in d.items() if k != "races"}
            first = str(rs[0].get("election_date", ""))[:10] if rs else "-"
            last = str(rs[-1].get("election_date", ""))[:10] if rs else "-"
            self.stdout.write(f"   first row date {first}, last row date {last}")
            y26 = sum(1 for r in rs if str(r.get("election_date", "")).startswith("2026-11-03"))
            self.stdout.write(f"{p}\n   rows={len(rs)} on_2026-11-03={y26} meta={extra}\n   dates={dict(dates.most_common(4))}"
                              f"\n   types={dict(types.most_common(4))}")