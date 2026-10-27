import tempfile
from datetime import date, datetime, timedelta, timezone as tz
from unittest import mock
 
from django.test import TestCase
 
from tracker import poller
from tracker.models import SyncLog
from tracker.services import load_seed
 
__author__ = "Mustafa Prodhan"
ED = date(2026, 11, 3)
 
 
class ScheduleTests(TestCase):
    def test_phases(self):
        self.assertEqual(poller.phase(datetime(2026, 11, 3, 9, tzinfo=tz.utc), ED), "pre")
        self.assertEqual(poller.phase(datetime(2026, 11, 4, 3, tzinfo=tz.utc), ED), "night")
        self.assertEqual(poller.phase(datetime(2026, 11, 7, tzinfo=tz.utc), ED), "post")
 
    def test_due_speeds_up_on_election_night(self):
        night = datetime(2026, 11, 4, 3, tzinfo=tz.utc)
        last = {j: night.timestamp() - 45 for j in poller.JOBS}
        self.assertEqual(poller.due_jobs(night, ED, last), ["results"])          # 45s > 30s, early 300s not yet
        pre = datetime(2026, 10, 26, tzinfo=tz.utc)
        last = {j: pre.timestamp() - 45 for j in poller.JOBS}
        self.assertEqual(poller.due_jobs(pre, ED, last), [])                     # pre-election: slow
        self.assertEqual(poller.due_jobs(pre, ED, {}), list(poller.JOBS))        # first run: everything
        self.assertEqual(poller.due_jobs(pre, ED, last, {"results": 30}), ["results"])  # override
 
 
class RunTests(TestCase):
    def test_run_once_logs_and_survives_failures(self):
        load_seed()
        p = poller.Poller(2026)
        with mock.patch.object(poller.services, "sync_results", side_effect=RuntimeError("down")), \
             mock.patch.object(poller.services, "sync_early_vote", return_value=48) as ev:
            ran = p.run_once(force=("results", "early"))
        self.assertEqual(ran, ["results", "early"])
        ev.assert_called_once()
        self.assertTrue(SyncLog.objects.filter(kind="results", ok=False).exists())
        self.assertIn("results", poller.status())
        self.assertFalse(poller.status()["results"]["ok"])
 
    def test_cadence_respected_after_restart(self):
        load_seed()
        SyncLog.objects.create(kind="results")        # just ran
        p = poller.Poller(2026)
        with mock.patch.object(poller.services, "sync_results", return_value=(0, [])) as r, \
             mock.patch.object(poller.services, "sync_early_vote", return_value=0), \
             mock.patch.object(poller.services, "sync_early_detail", return_value=0), \
             mock.patch.object(poller.services, "sync_forecasts", return_value=(0, 0)):
            ran = p.run_once(now=datetime(2026, 10, 26, tzinfo=tz.utc))
        self.assertNotIn("results", ran)
        r.assert_not_called()
 
 
class LockTests(TestCase):
    def test_second_lock_refused(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = poller.Lock(f"{d}/l"), poller.Lock(f"{d}/l")
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            a.release()
            self.assertTrue(b.acquire())
            b.release()
 
 
class SafetyTests(TestCase):
    def test_paging_is_capped_and_stops_past_the_date(self):
        from tracker import civicapi
        row = {"id": 1, "type": "US Senate", "province": "OH", "election_type": "General",
               "election_name": "x", "election_date": "2026-11-03T05:00:00Z"}
        old = {**row, "election_date": "2022-11-08T05:00:00Z"}
        calls = []
 
        def fake(key, suffix="", **p):
            calls.append(p["offset"])
            return {"races": [row] * 1000}            # every row on the date: would run away if uncapped
        with mock.patch.object(civicapi, "get", fake), mock.patch.object(civicapi.time, "sleep"), \
             mock.patch.object(civicapi, "OFFICE_QUERIES", {"senate": [{"type": "US Senate", "election_type": "General"}]}), \
             self.settings(CIVICAPI_MAX_PAGES=3):
            civicapi.fetch_general_races("2026-11-03")
        self.assertEqual(calls, [0, 1000, 2000])
 
        calls.clear()
 
        def fake2(key, suffix="", **p):
            calls.append(p["offset"])
            return {"races": [row] * 10 + [old] * 990}  # a full page that already reaches older elections
        with mock.patch.object(civicapi, "get", fake2), mock.patch.object(civicapi.time, "sleep"), \
             mock.patch.object(civicapi, "OFFICE_QUERIES", {"senate": [{"type": "US Senate", "election_type": "General"}]}), \
             self.settings(CIVICAPI_MAX_PAGES=3):
            out = civicapi.fetch_general_races("2026-11-03")
        self.assertEqual(calls, [0])                  # stopped: nothing more on the date beyond this page
        self.assertEqual(len(out), 10)
 
    def test_failure_backs_off(self):
        load_seed()
        p = poller.Poller(2026)
        with mock.patch.object(poller.services, "sync_results", side_effect=RuntimeError("503")):
            p.run_once(force=("results",)); first = p.last["results"]
            p.run_once(force=("results",)); second = p.last["results"]
        self.assertGreater(first, datetime.now(tz.utc).timestamp())
        self.assertGreater(second - first, 10)
 
 
class RetryTests(TestCase):
    def test_session_retries_transient_errors(self):
        from tracker import civicapi
        civicapi._session = None
        a = civicapi._get_session().get_adapter("https://civicapi.org").max_retries
        self.assertEqual(a.total, 3)
        self.assertIn(503, a.status_forcelist)
        self.assertIn(429, a.status_forcelist)
 