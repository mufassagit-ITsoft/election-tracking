from django.core.management.base import BaseCommand, CommandError

__author__ = "Mustafa Prodhan"

class Command(BaseCommand):
    help = "Open the desktop election map (reads SQLite; Pull button fetches CivicAPI)."
 
    def add_arguments(self, p):
        p.add_argument("--year", type=int, default=2026)
 
    def handle(self, *a, **o):
        try:
            from tracker.gui import app
        except ImportError as exc:
            raise CommandError("Tkinter is missing. On Ubuntu run: sudo apt install python3-tk") from exc
        app.run(o["year"])