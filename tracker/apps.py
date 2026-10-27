from django.apps import AppConfig
from django.db.backends.signals import connection_created

__author__ = "Mustafa Prodhan"

def _sqlite_tuning(sender, connection, **kwargs):
    # WAL lets the poller write while the GUI / API read; busy_timeout waits instead of "database is locked".
    if connection.vendor == "sqlite":
        cur = connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL;")
        cur.execute("PRAGMA busy_timeout=30000;")
        cur.execute("PRAGMA synchronous=NORMAL;")

class TrackerConfig(AppConfig):
    name = 'tracker'

    def ready(self):
        connection_created.connect(_sqlite_tuning)
