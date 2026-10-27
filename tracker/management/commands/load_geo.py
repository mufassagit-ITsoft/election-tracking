from django.core.management.base import BaseCommand

from tracker.models import Election
from tracker.services import load_geo

__author__ = "Mustafa Prodhan"

class Command(BaseCommand):
    help = "Store map geometry (state outlines, House tiles) in SQLite. Run after load_seed."
 
    def add_arguments(self, p):
        p.add_argument("--year", type=int, default=2026)
 
    def handle(self, *a, **o):
        el = Election.objects.filter(year=o["year"]).first()
        self.stdout.write(self.style.SUCCESS(f"shapes stored: {load_geo(el)}"))