"""Outputs must match contracts/ exactly (same keys, same value types).

Run: python -m unittest gaps.tests.test_contracts   (after python -m gaps.run)
"""
import json
import unittest

from gaps.config import EXCLUDED_FILTERS, MIN_HOTELS, OPPORTUNITIES_OUT, PAGE_ISSUES_OUT, ROOT
from gaps.opportunities import proposed_url

CONTRACTS = ROOT / "contracts"


def _type_ok(sample, value):
    if value is None:
        return True
    if isinstance(sample, bool):
        return isinstance(value, bool)
    if isinstance(sample, (int, float)):
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, type(sample))


class ContractTest(unittest.TestCase):
    def check(self, contract, out_path):
        sample = json.loads((CONTRACTS / contract).read_text())
        if not out_path.exists():
            self.skipTest(f"{out_path.name} not generated yet; run python -m gaps.run")
        items = json.loads(out_path.read_text())
        self.assertIsInstance(items, list)
        for item in items:
            self.assertEqual(set(item), set(sample), f"keys differ in {item}")
            for k, v in item.items():
                self.assertTrue(_type_ok(sample[k], v), f"{k}={v!r} has wrong type")
        return items

    def test_opportunities(self):
        opps = self.check("opportunity.json", OPPORTUNITIES_OUT)
        levels = [o["place_type"] == "city" for o in opps]
        self.assertEqual(levels, sorted(levels), "locality/landmark opportunities must come before city ones")
        for o in opps:
            self.assertGreaterEqual(len(o["matching_hotel_ids"]), MIN_HOTELS[o["place_type"]])
            self.assertNotIn(o["filter"], EXCLUDED_FILTERS)
            self.assertTrue(0 <= o["score"] <= 100)
            self.assertTrue(all(isinstance(i, str) for i in o["matching_hotel_ids"]))
            self.assertTrue(o["proposed_url"].startswith("https://www.treebo.com/") and o["proposed_url"].endswith("/"))

    def test_page_issues(self):
        types = {"lists_inactive_hotels", "outdated_price", "no_quick_answer", "no_faqs", "breakfast_not_stated"}
        for i in self.check("page_issue.json", PAGE_ISSUES_OUT):
            self.assertIn(i["issue_type"], types)

    def test_url_patterns(self):
        self.assertEqual(proposed_url("locality", "koramangala-bangalore", "amenity", "gym"),
                         "https://www.treebo.com/hotels-in-koramangala-bangalore-with-gym/")
        self.assertEqual(proposed_url("landmark", "mg-road-metro-bangalore", "amenity", "parking"),
                         "https://www.treebo.com/hotels-near-mg-road-metro-bangalore-with-parking/")
        self.assertEqual(proposed_url("locality", "wakad-pune", "category", "budget-hotels"),
                         "https://www.treebo.com/budget-hotels-in-wakad-pune/")
        self.assertEqual(proposed_url("landmark", "pune-airport-pune", "category", "near-railway-station"),
                         "https://www.treebo.com/near-city-railway-station-near-pune-airport-pune/")


if __name__ == "__main__":
    unittest.main()
