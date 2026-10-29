"""Search demand per (city, place, filter).

Uses real numbers from data/keyword_volumes.csv when that file exists and has a matching row
(columns: city,place,filter,search_volume; place = place name slug without city, empty for city level).
Otherwise falls back to the ESTIMATE in data/demand_weights.json.
"""
import csv

from gaps.config import DEMAND_WEIGHTS, KEYWORD_VOLUMES
from gaps.util import read_json, slugify


class Demand:
    def __init__(self):
        self.w = read_json(DEMAND_WEIGHTS)
        self.real = {}
        if KEYWORD_VOLUMES.exists():
            with KEYWORD_VOLUMES.open() as f:
                for r in csv.DictReader(f):
                    key = (r["city"].strip().lower(), slugify(r.get("place", "")), slugify(r["filter"]))
                    self.real[key] = int(float(r["search_volume"]))

    def volume(self, city, place_type, place_name, filter_slug, popular=False):
        """Return (search_volume, is_estimated)."""
        place = "" if place_type == "city" else slugify(place_name)
        key = (city.lower(), place, filter_slug)
        if key in self.real:
            return self.real[key], False
        w = self.w
        f = w["filter"].get(filter_slug, w["filter"]["_default"])
        v = w["city_base"][city] * w["place_type"][place_type] * f
        if popular:
            v *= w["popular_place_boost"]
        return max(10, int(round(v, -1))), True
