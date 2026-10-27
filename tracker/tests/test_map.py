from unittest import mock
 
from django.test import TestCase
 
from tracker import civicapi
from tracker.gui import mapdata as md
from tracker.models import EarlyVoteRegion, Forecast, MapShape, Rating
from tracker.services import (apply_races_payload, load_geo, load_seed, sync_early_detail, sync_forecasts)
 
__author__ = "Mustafa Prodhan"

class ColorTests(TestCase):
    def test_rating_parse(self):
        f = md.rating_side_strength
        self.assertEqual(f("Lean D (flip)"), ("D", 1))
        self.assertEqual(f("Safe R"), ("R", 3))
        self.assertEqual(f("Likely Republican"), ("R", 2))
        self.assertEqual(f("Solid Democratic"), ("D", 3))
        self.assertEqual(f("Toss Up")[0], "T")
        self.assertEqual(f(None), (None, 0))
 
    def test_party_and_early_colors(self):
        self.assertEqual(md.party_color("Democratic"), md.D_SHADES[3])
        self.assertEqual(md.party_color("R", 1), md.R_SHADES[1])
        self.assertEqual(md.early_color(None), md.NO_DATA)
        self.assertNotEqual(md.early_color(0), md.early_color(100))
 
 
class MapDataTests(TestCase):
    def setUp(self):
        self.el, _ = load_seed()
        self.assertEqual(load_geo(self.el), 51 + 435)
 
    def test_shapes_loaded_and_idempotent(self):
        load_geo(self.el)
        self.assertEqual(MapShape.objects.filter(layer="states").count(), 51)
        self.assertEqual(MapShape.objects.filter(layer="house_tiles").count(), 435)
 
    def test_project_states_has_insets(self):
        p = md.project_states()
        self.assertEqual(len(p), 51)
        xs = [x for k, rs in p.items() if k not in ("AK", "HI") for r in rs for x, _ in r]
        ys = [y for k, rs in p.items() if k not in ("AK", "HI") for r in rs for _, y in r]
        ak = [pt for r in p["AK"] for pt in r]
        self.assertGreaterEqual(min(x for x, _ in ak), min(xs) - 1e-9)   # inset inside the map width
        self.assertGreaterEqual(min(y for _, y in ak), min(ys))          # and below the top
 
    def test_tiles_do_not_overlap(self):
        t = md.tile_squares()
        cells = [(x, y) for x, y, _ in t.values()]
        self.assertEqual(len(cells), len(set(cells)))
 
    def test_layers(self):
        self.assertEqual(len(md.layer_data(2026, "house")), 435)
        sen = md.layer_data(2026, "senate", "rating", "cook")
        self.assertEqual(len(sen), 35)
        self.assertNotEqual(sen["OH"]["fill"], md.NO_DATA)
        early = md.layer_data(2026, "senate", "early")
        self.assertGreater(len(early), 35)      # statewide early vote colours states without a Senate race
        self.assertIn("OH", md.race_detail(sen["OH"]["race_id"]) )
 
    def test_results_mode_after_call(self):
        apply_races_payload(self.el, {"races": [{"id": "x1", "office": "Senate", "state": "OH",
            "candidates": [{"name": "A", "party": "Republican", "votes": 1, "winner": True},
                           {"name": "B", "party": "Democratic", "votes": 0}]}]})
        d = md.layer_data(2026, "senate", "results")
        self.assertEqual(d["OH"]["fill"], md.R_SHADES[3])
 
 
class IngestTests(TestCase):
    def setUp(self):
        self.el, _ = load_seed()
 
    def test_forecast_and_early_detail(self):
        fc = {"last_updated": "2026-10-20", "states": {"OH": {"rating": "Tossup", "democrat_pct": 50.5,
              "republican_pct": 49.5, "incumbent_party": "R", "democratic": "S Brown", "republican": "J Husted"}}}
        ev = {"snapshot_date": "2026-10-25", "regions": {"Adams": {"Unspecified": {"votes": 7}},
                                                         "Allen": {"Democratic": {"votes": 3}}}}
 
        def fake(key, suffix="", **p):
            return fc if key == "forecast" else ev
        with mock.patch.object(civicapi, "get", fake):
            self.assertEqual(sync_forecasts(self.el, offices=("senate",)), (1, 0))
            self.assertEqual(sync_early_detail(self.el, states=["OH"]), 8)
        self.assertEqual(Forecast.objects.get(key="OH").dem_pct, 50.5)
        self.assertTrue(Rating.objects.filter(rater="civicapi", race__state="OH").exists())
        self.assertEqual(EarlyVoteRegion.objects.filter(state="OH", category="returned").count(), 2)