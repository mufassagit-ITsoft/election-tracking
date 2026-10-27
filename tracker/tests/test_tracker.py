from django.test import TestCase, override_settings
 
from tracker import civicapi
from tracker.models import Election, Race, ResultSnapshot
from tracker.services import apply_races_payload, load_seed
 
 
class SeedTests(TestCase):
    def test_seed_counts(self):
        el, n = load_seed()
        self.assertEqual(Race.objects.filter(election=el, office="senate").count(), 35)
        self.assertEqual(Race.objects.filter(election=el, office="governor").count(), 36)
        self.assertEqual(Race.objects.filter(election=el, office="house").count(), 435)
 
    def test_idempotent(self):
        load_seed(); c = Race.objects.count(); load_seed()
        self.assertEqual(c, Race.objects.count())
 
 
@override_settings(CIVICAPI_IGNORE_PREELECTION=False)
class IngestTests(TestCase):
    def setUp(self):
        self.el, _ = load_seed()
 
    def test_apply_payload_and_call(self):
        payload = {"races": [{"id": "x1", "office": "Senate", "state": "OH", "percent_reporting": "42.5",
                              "candidates": [{"name": "Jon Husted", "party": "R", "votes": "1,000", "winner": False},
                                             {"name": "Sherrod Brown", "party": "D", "votes": 1200, "winner": True}]}]}
        stored, un = apply_races_payload(self.el, payload)
        self.assertEqual((stored, un), (1, []))
        s = ResultSnapshot.objects.get()
        self.assertTrue(s.called); self.assertEqual(s.winner_party, "D")
        self.assertEqual(Race.objects.get(office="senate", state="OH", is_special=True).civic_id, "x1")
 
    def test_unmatched_reported(self):
        stored, un = apply_races_payload(self.el, [{"id": "zz", "office": "mayor", "state": "OH"}])
        self.assertEqual(stored, 0); self.assertEqual(un, ["zz"])
 
    def test_extract_and_normalize_house(self):
        n = civicapi.normalize_race({"office": "House", "state": "az", "district": "06"})
        self.assertEqual((n["office"], n["state"], n["district"]), ("house", "AZ", "6"))
 
 
class ApiTests(TestCase):
    def setUp(self):
        load_seed()
 
    def test_endpoints(self):
        for url in ("/api/health/", "/api/elections/", "/api/2026/races/?office=senate",
                    "/api/2026/map/senate/", "/api/2026/map/house/", "/api/2026/early-vote/",
                    "/api/2026/results/live/"):
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
        self.assertEqual(len(self.client.get("/api/2026/map/house/").json()["features"]), 435)
        self.assertEqual(self.client.get("/api/2099/races/").status_code, 404)
 
 
class NormalizeStatewideTests(TestCase):
    def test_statewide_district_is_blank(self):
        n = civicapi.normalize_race({"type": "Governor", "province": "AZ", "district": "AZ"})
        self.assertEqual((n["office"], n["state"], n["district"]), ("governor", "AZ", ""))
 
 
class PreElectionGuardTests(TestCase):
    PAYLOAD = {"races": [{"id": "x1", "type": "US Senate", "province": "OH", "percent_reporting": 100,
                          "candidates": [{"name": "A", "party": "Republican", "votes": 5, "winner": True},
                                         {"name": "B", "party": "Democratic", "votes": 3}]}]}
 
    def setUp(self):
        self.el, _ = load_seed()
 
    def _apply(self, now):
        from datetime import datetime, timezone as tz
        from unittest import mock
        stats = {}
        with mock.patch("tracker.services.timezone.now", return_value=datetime(*now, tzinfo=tz.utc)):
            return apply_races_payload(self.el, self.PAYLOAD, stats), stats
 
    def test_results_before_election_day_are_ignored(self):
        (stored, un), stats = self._apply((2026, 10, 27, 17))
        self.assertEqual((stored, un, stats["skipped"]), (0, [], 1))
        self.assertEqual(ResultSnapshot.objects.count(), 0)
 
    def test_results_after_polls_open_are_stored(self):
        (stored, un), stats = self._apply((2026, 11, 4, 3))
        self.assertEqual((stored, stats["skipped"]), (1, 0))
        self.assertTrue(ResultSnapshot.objects.get().called)