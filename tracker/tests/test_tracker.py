from django.test import TestCase
 
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
 