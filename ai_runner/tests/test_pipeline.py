"""Unit tests for fact-check, miss reasons, extraction, scoring and query generation.

Run: python -m unittest discover -s ai_runner/tests -t .
"""
from __future__ import annotations

import unittest

from ai_runner.config import CITIES
from ai_runner.extract import finalize, rule_extract
from ai_runner.factcheck import check_claims
from ai_runner.inventory import Inventory, overlay, production_only
from ai_runner.miss_reason import miss_reason
from ai_runner.queries import generate_queries
from ai_runner.score import city_score
from ai_runner.sitemap import HotelPages, Sitemap

BLR = CITIES["bengaluru"]


def hotel(hid, name, locality, active=True, amenities=(), categories=(), rating=4.0, lat=12.93, lng=77.62):
    return {
        "hotel_id": hid, "name": name, "city": "Bengaluru", "locality": locality, "lat": lat, "lng": lng,
        "price_from": 2000.0, "rating": rating, "amenities": list(amenities), "categories": list(categories),
        "active": active, "url": f"https://www.treebo.com/hotels-in-bangalore/x-{hid}/",
    }


INV = Inventory([
    hotel("444", "Treebo Edha Suites", "Koramangala", amenities=["free-breakfast", "gym"], categories=["budget-hotels"]),
    hotel("332", "Treebo Terminus", "Koramangala", amenities=["parking"], categories=["family-hotels"]),
    hotel("510", "Treebo Olive", "HSR Layout", active=False, amenities=["free-breakfast"]),
])
LIVE = {"checkin": "2026-11-06", "prices": {"444": {"pretax_price": 2000.0, "sell_price": 2240.0}}}


def claim(name, attribute, value):
    return {"hotel_name": name, "claim": f"{name} {attribute} {value}", "attribute": attribute, "value": value}


class FactCheckTest(unittest.TestCase):
    def check(self, *claims):
        return check_claims(list(claims), INV, "Bengaluru", LIVE)

    def test_amenity_correct_and_wrong(self):
        out = self.check(claim("Treebo Edha Suites", "amenity", "free breakfast"),
                         claim("Treebo Edha Suites", "amenity", "swimming pool"))
        self.assertEqual([c["status"] for c in out], ["correct", "wrong"])
        self.assertEqual(out[0]["hotel_id"], "444")

    def test_inactive_hotel_is_outdated_once(self):
        out = self.check(claim("Treebo Olive", "amenity", "free breakfast"), claim("Treebo Olive", "locality", "HSR"))
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["status"], "outdated")

    def test_one_shared_word_is_not_a_match(self):
        inv = Inventory(INV.hotels + [dict(hotel("1069", "Treebo Trip Lotus", "Baner"), city="Pune")])
        self.assertIsNone(inv.match_name("Treebo Trend Purple Lotus", "Bengaluru"))

    def test_real_hotel_outside_inventory_is_not_wrong(self):
        pages = HotelPages(["/hotels-in-bangalore/treebo-crystal-suites-koramangala-999/"])
        real = check_claims([claim("Treebo Crystal Suites", "amenity", "gym")], INV, "Bengaluru", LIVE, pages)
        fake = check_claims([claim("Treebo Purple Lotus", "amenity", "gym")], INV, "Bengaluru", LIVE, pages)
        self.assertEqual(real, [])
        self.assertEqual(fake[0]["status"], "wrong")
        self.assertIn("on treebo.com", fake[0]["truth"])

    def test_generic_brand_mention_is_not_a_hotel(self):
        self.assertEqual(self.check(claim("Treebo Trend / FabHotels", "amenity", "free breakfast")), [])
        self.assertEqual(self.check(claim("Treebo Trend properties", "price", "1500")), [])

    def test_unknown_hotel_is_wrong(self):
        out = self.check(claim("Treebo Imaginary Palace", "amenity", "gym"))
        self.assertEqual(out[0]["status"], "wrong")
        self.assertIsNone(out[0]["hotel_id"])

    def test_price_uses_live_prices_not_inventory(self):
        ok = self.check(claim("Treebo Edha Suites", "price", "2100"))[0]
        stale = self.check(claim("Treebo Edha Suites", "price", "999"))[0]
        self.assertEqual(ok["status"], "correct")
        self.assertEqual(stale["status"], "outdated")
        self.assertIn("2026-11-06", stale["truth"])

    def test_price_without_live_price_is_dropped(self):
        # inventory says 2000 for 332, but that is a placeholder: no verdict
        self.assertEqual(self.check(claim("Treebo Terminus", "price", "5000")), [])

    def test_unverifiable_claims_dropped(self):
        self.assertEqual(self.check(claim("Treebo Edha Suites", "other", "friendly staff"),
                                    claim("Treebo Edha Suites", "amenity", "great vibes")), [])

    def test_locality_and_rating(self):
        out = self.check(claim("Treebo Terminus", "locality", "Indiranagar"), claim("Treebo Terminus", "rating", "4.1"))
        self.assertEqual([c["status"] for c in out], ["wrong", "correct"])


class HotelMapOverlayTest(unittest.TestCase):
    def test_production_truth_wins_over_staging(self):
        staging = hotel("332", "Treebo Terminus", "Koramangala", active=True)
        prod = {"found": True, "name": "Treebo Trip Terminus Churned", "active": False, "locality": "Koramangala"}
        inv = Inventory([overlay(staging, prod)])
        self.assertFalse(inv.by_id["332"]["active"])
        self.assertEqual(inv.match_name("Treebo Terminus")["hotel_id"], "332")  # staging name still matches
        out = check_claims([claim("Treebo Terminus", "amenity", "parking")], inv, "Bengaluru", LIVE)
        self.assertEqual(out[0]["status"], "outdated")
        self.assertIn("not active on treebo.com", out[0]["truth"])

    def test_production_only_hotel(self):
        prod = {"found": True, "name": "Treebo Sahara Suites", "active": True, "city": "Bengaluru",
                "locality": "Madiwala", "lat": 12.92, "lng": 77.62, "amenities": ["free-breakfast", "parking"]}
        inv = Inventory(INV.hotels + [production_only("1460", prod)])
        # Unknown categories don't exclude it from a budget question...
        self.assertEqual([h["hotel_id"] for h in inv.matching("Bengaluru", locality="Madiwala", category="budget-hotels")], ["1460"])
        out = check_claims([claim("Treebo Sahara Suites", "amenity", "free breakfast"),
                            claim("Treebo Sahara Suites", "amenity", "gym"),
                            claim("Treebo Sahara Suites", "category", "couple friendly")], inv, "Bengaluru", LIVE)
        # ...and category claims about it are dropped, not judged.
        self.assertEqual([c["status"] for c in out], ["correct", "wrong"])

    def test_production_amenities_replace_staging(self):
        h = overlay(hotel("444", "Treebo Edha Suites", "Koramangala", amenities=["gym"]),
                    {"found": True, "active": True, "amenities": ["gym", "restaurant"]})
        self.assertEqual(h["amenities"], ["gym", "restaurant"])

    def test_missing_from_production_is_inactive(self):
        h = overlay(hotel("9", "Treebo Ghost", "Ulsoor"), {"prod_hotel_id": None, "found": False})
        self.assertEqual((h["active"], h["source"]), (False, "not_in_production"))


class MissReasonTest(unittest.TestCase):
    def q(self, type_, place, intent="general", amenity=None):
        return {"query_id": "blr-900", "city": "Bengaluru", "text": "?",
                "tags": {"type": type_, "place": place, "intent": intent, "amenity": amenity}}

    def test_reasons(self):
        sm = Sitemap({"/hotels-in-koramangala-bangalore/", "/budget-hotels-in-koramangala-bangalore/"}, "test")
        cases = [
            (self.q("locality", "HSR Layout"), "no_inventory"),  # only an inactive hotel there
            (self.q("locality", "Koramangala", "budget"), "ai_trusts_others"),
            (self.q("locality_amenity", "Koramangala", amenity="free-breakfast"), "weak_page"),
            (self.q("need", "Koramangala", "family"), "weak_page"),
        ]
        for query, expected in cases:
            self.assertEqual(miss_reason(query, BLR, INV, sm), expected, query["tags"])
        self.assertEqual(miss_reason(self.q("locality", "Koramangala"), BLR, INV, Sitemap(set(), "t")), "no_page")


class ExtractAndScoreTest(unittest.TestCase):
    TEXT = ("1. **OYO Townhouse Koramangala** (Koramangala) — parking. From ₹1,500/night.\n"
            "2. **Treebo Edha Suites** (Koramangala) — free breakfast, a gym. From ₹2,100/night. Rated 4.0/5.\n")

    def test_rule_extract_and_rank(self):
        facts = finalize(rule_extract(self.TEXT), ["https://example.com/a"])
        self.assertEqual(facts["brands_mentioned"], ["OYO", "Treebo"])
        self.assertEqual(facts["treebo_rank"], 2)
        attrs = sorted(c["attribute"] for c in facts["treebo_claims"])
        self.assertEqual(attrs, ["amenity", "amenity", "locality", "price", "rating"])
        checked = check_claims(facts["treebo_claims"], INV, "Bengaluru", LIVE)
        self.assertTrue(all(c["status"] == "correct" for c in checked), checked)

    def test_city_score(self):
        rows = [
            {"engine": "chatgpt", "brands_mentioned": ["OYO", "Treebo"], "treebo_mentioned": True,
             "treebo_claims": [{"status": "wrong"}, {"status": "correct"}]},
            {"engine": "chatgpt", "brands_mentioned": ["OYO", "FabHotels"], "treebo_mentioned": False, "treebo_claims": []},
        ]
        s = city_score("Bengaluru", rows)
        self.assertEqual((s["mention_rate"], s["share_of_voice"], s["wrong_facts"], s["top_competitor"]),
                         (0.5, 0.25, 1, "OYO"))
        self.assertEqual(s["by_engine"], {"chatgpt": 0.5, "gemini": None, "perplexity": None, "claude": None})


class QueryTest(unittest.TestCase):
    def test_twenty_tagged_queries_per_city(self):
        inv = Inventory.load()
        for city in CITIES.values():
            qs = generate_queries(city, inv)
            self.assertEqual(len(qs), 20)
            self.assertEqual(len({q["query_id"] for q in qs}), 20)
            types = {q["tags"]["type"] for q in qs}
            self.assertEqual(types, {"locality", "locality_amenity", "landmark", "city_amenity", "need", "price"})
            self.assertEqual(generate_queries(city, inv), qs)  # deterministic
            for q in qs:
                self.assertEqual(list(q), ["query_id", "city", "text", "tags"])
                self.assertEqual(list(q["tags"]), ["type", "place", "intent", "amenity"])


if __name__ == "__main__":
    unittest.main()
