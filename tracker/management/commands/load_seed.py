from django.core.management.base import BaseCommand
from tracker.services import load_seed
 
__author__ = "Mustafa Prodhan"
class Command(BaseCommand):
    help = "Load the compiled 2026 midterm races, ratings and early-vote baseline."
 
    def add_arguments(self, p):
        p.add_argument("--path")
 
    def handle(self, *a, **o):
        el, n = load_seed(o["path"]) if o.get("path") else load_seed()
        self.stdout.write(self.style.SUCCESS(f"{el}: {n}"))