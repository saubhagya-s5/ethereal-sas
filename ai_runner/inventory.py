"""Load data/inventory.json and look hotels up by place, amenity, category and name."""
from __future__ import annotations

import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path

from .config import CITIES, HOTEL_MAP_PATH, INVENTORY_PATH

# Readable words for amenity / category slugs, used in question text and claim matching.
AMENITY_WORDS = {
    "free-breakfast": "free breakfast",
    "complimentary-breakfast": "free breakfast",
    "parking": "parking",
    "free-wifi": "free Wi-Fi",
    "gym": "a gym",
    "swimming-pool": "a swimming pool",
    "restaurant": "a restaurant",
    "bar": "a bar",
    "room-service": "room service",
    "24-hour-security": "24-hour security",
    "24-hour-front-desk": "a 24-hour front desk",
    "ac-room": "AC rooms",
    "elevator": "a lift",
    "kitchenette": "a kitchenette",
    "pet-friendly": "pet-friendly rooms",
    "banquet-hall": "a banquet hall",
    "conference-hall": "a conference hall",
    "meeting-facility": "meeting rooms",
    "spa": "a spa",
    "wheel-chair": "wheelchair access",
    "cab-service": "cab service",
    "guest-laundry": "laundry",
    "luggage-storage": "luggage storage",
}

# Phrases an AI answer may use -> inventory amenity slugs. Any of the slugs counts as a match.
AMENITY_SYNONYMS = {
    "breakfast": ["free-breakfast", "complimentary-breakfast"],
    "wifi": ["free-wifi"],
    "wi-fi": ["free-wifi"],
    "internet": ["free-wifi"],
    "parking": ["parking"],
    "gym": ["gym", "garden-with-open-gym"],
    "fitness": ["gym", "garden-with-open-gym"],
    "pool": ["swimming-pool"],
    "swimming": ["swimming-pool"],
    "restaurant": ["restaurant", "roof-top-restaurant"],
    "rooftop": ["roof-top-restaurant"],
    "bar": ["bar"],
    "room service": ["room-service"],
    "security": ["24-hour-security", "security"],
    "front desk": ["24-hour-front-desk"],
    "reception": ["24-hour-front-desk"],
    "air condition": ["ac-room"],
    "air-condition": ["ac-room"],
    " ac ": ["ac-room"],
    "lift": ["elevator"],
    "elevator": ["elevator"],
    "kitchen": ["kitchenette", "stove", "microwave"],
    "pet": ["pet-friendly"],
    "banquet": ["banquet-hall"],
    "conference": ["conference-hall", "meeting-facility"],
    "meeting": ["meeting-facility", "conference-hall"],
    "spa": ["spa"],
    "wheelchair": ["wheel-chair"],
    "cab": ["cab-service"],
    "taxi": ["cab-service"],
    "airport pickup": ["cab-service"],
    "laundry": ["guest-laundry"],
    "luggage": ["luggage-storage"],
    "power backup": ["24-hours-power-supply"],
    "power supply": ["24-hours-power-supply"],
    "tv": ["flat-screen-tv"],
    "television": ["flat-screen-tv"],
    "kettle": ["hot-water-kettle", "complimentary-tea-coffee"],
    "tea": ["complimentary-tea-coffee"],
    "coffee": ["complimentary-tea-coffee"],
    "fridge": ["mini-fridge"],
    "locker": ["safety-locker"],
    "balcony": ["balcony"],
    "geyser": ["geyser"],
    "hot water": ["geyser"],
    "toiletries": ["complimentary-toiletries"],
    "card payment": ["card-payment-accepted"],
    "travel desk": ["travel-desk"],
    "smoking": ["smoking-room"],
}

CATEGORY_WORDS = {
    "budget-hotels": "budget hotels",
    "family-hotels": "family hotels",
    "business-hotels": "business hotels",
    "couple-friendly-hotels": "couple-friendly hotels",
    "service-apartments": "service apartments",
    "near-airport": "hotels near the airport",
    "near-railway-station": "hotels near the railway station",
}

CATEGORY_SYNONYMS = {
    "couple": ["couple-friendly-hotels", "romantic-stays"],
    "unmarried": ["couple-friendly-hotels"],
    "family": ["family-hotels"],
    "business": ["business-hotels"],
    "budget": ["budget-hotels"],
    "service apartment": ["service-apartments"],
    "boutique": ["boutique-hotels"],
    "guest house": ["guest-house"],
    "homestay": ["homestay"],
    "near the airport": ["near-airport"],
    "near airport": ["near-airport"],
    "railway station": ["near-railway-station"],
    "3-star": ["3-star-hotels"],
    "3 star": ["3-star-hotels"],
    "2-star": ["2-star-hotels"],
    "2 star": ["2-star-hotels"],
    "4-star": ["4-star-hotels"],
    "4 star": ["4-star-hotels"],
}

# Treebo sub-brand words dropped before fuzzy name matching.
_BRAND_WORDS = {"treebo", "trend", "trip", "edge", "premium", "hotels", "hotel", "the", "churned"}


def slugify(text: str) -> str:
    text = text.lower().replace("&", "and")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def _name_key(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", name.lower())
    return " ".join(w for w in words if w not in _BRAND_WORDS)


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def amenity_slugs_for(phrase: str) -> list[str]:
    """Map a free-text amenity phrase to candidate inventory slugs ([] if unknown)."""
    text = f" {phrase.lower().strip()} "
    if slugify(phrase) in AMENITY_WORDS or slugify(phrase) in _ALL_AMENITY_SLUGS:
        return [slugify(phrase)]
    found: list[str] = []
    for key, slugs in AMENITY_SYNONYMS.items():
        if key in text:
            found.extend(s for s in slugs if s not in found)
    return found


def category_slugs_for(phrase: str) -> list[str]:
    text = phrase.lower()
    if slugify(phrase) in _ALL_CATEGORY_SLUGS:
        return [slugify(phrase)]
    found: list[str] = []
    for key, slugs in CATEGORY_SYNONYMS.items():
        if key in text:
            found.extend(s for s in slugs if s not in found)
    return found


def overlay(hotel: dict, prod: dict | None) -> dict:
    """Replace staging name / active / locality / coordinates / url with production values.

    Amenities come from production facilities when the map has them.
    Adds: staging_name, and source = "production" | "not_in_production" | "staging_only"
    (staging_only = id not looked up yet, so staging values are kept).
    """
    h = dict(hotel, staging_name=hotel["name"])
    if prod is None:
        h["source"] = "staging_only"
    elif not prod.get("found"):
        h.update(active=False, source="not_in_production")
    else:
        h.update(
            name=prod.get("name") or h["name"],
            active=prod["active"],
            locality=prod.get("locality") or h.get("locality"),
            lat=prod.get("lat") if prod.get("lat") is not None else h.get("lat"),
            lng=prod.get("lng") if prod.get("lng") is not None else h.get("lng"),
            url=prod.get("url") or h.get("url"),
            amenities=prod.get("amenities") or h.get("amenities") or [],
            source="production",
        )
    return h


def production_only(hotel_id: str, prod: dict) -> dict:
    """A live hotel missing from staging, built only from the map. No categories, rating or
    price are known: categories [] means unknown (not "none"), and rating/price stay None."""
    return {
        "hotel_id": hotel_id,
        "name": prod.get("name") or "",
        "city": prod["city"],
        "locality": prod.get("locality"),
        "lat": prod.get("lat"),
        "lng": prod.get("lng"),
        "price_from": None,
        "rating": None,
        "amenities": prod.get("amenities") or [],
        "categories": [],
        "active": bool(prod.get("active")),
        "url": prod.get("url"),
        "staging_name": None,
        "source": "production_only",
    }


_CITY_NAMES = {c.name for c in CITIES.values()}


_ALL_AMENITY_SLUGS: set[str] = set()
_ALL_CATEGORY_SLUGS: set[str] = set()


class Inventory:
    def __init__(self, hotels: list[dict]):
        self.hotels = hotels
        self.by_id = {h["hotel_id"]: h for h in hotels}
        for h in hotels:
            _ALL_AMENITY_SLUGS.update(h.get("amenities") or [])
            _ALL_CATEGORY_SLUGS.update(h.get("categories") or [])

    @classmethod
    def load(cls, path: Path = INVENTORY_PATH, apply_map: bool = True, map_path: Path = HOTEL_MAP_PATH) -> "Inventory":
        """Staging inventory with production truth from data/hotel_id_map.json laid over it."""
        hotels = json.loads(path.read_text(encoding="utf-8"))
        if apply_map and map_path.exists():
            hmap = json.loads(map_path.read_text(encoding="utf-8")).get("hotels", {})
            staging_ids = {h["hotel_id"] for h in hotels}
            hotels = [overlay(h, hmap.get(h["hotel_id"])) for h in hotels]
            # Live hotels that staging never had (found via treebo.com's hotel sitemap).
            hotels += [
                production_only(hid, p) for hid, p in hmap.items()
                if hid not in staging_ids and p.get("found") and p.get("city") in _CITY_NAMES
            ]
        return cls(hotels)

    def city(self, city: str, active_only: bool = False) -> list[dict]:
        return [h for h in self.hotels if h["city"] == city and (h["active"] or not active_only)]

    def localities(self, city: str, active_only: bool = True) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for h in self.city(city, active_only):
            if h.get("locality"):
                out.setdefault(h["locality"], []).append(h)
        return out

    def matching(
        self,
        city: str,
        locality: str | None = None,
        amenity: str | None = None,
        category: str | None = None,
        near: tuple[float, float] | None = None,
        radius_km: float = 4.0,
    ) -> list[dict]:
        """Active hotels in a city that match every filter given."""
        out = []
        for h in self.city(city, active_only=True):
            if locality and (h.get("locality") or "").lower() != locality.lower():
                continue
            if amenity and amenity not in (h.get("amenities") or []):
                continue
            # Empty categories = unknown (production-only hotel): don't exclude on them.
            if category and h.get("categories") and category not in h["categories"]:
                continue
            if near and (h.get("lat") is None or haversine_km(near[0], near[1], h["lat"], h["lng"]) > radius_km):
                continue
            out.append(h)
        return out

    def match_name(self, name: str, city: str | None = None) -> dict | None:
        """Best inventory hotel for a name an AI used. Prefers the given city, then any city."""
        key = _name_key(name)
        if not key:
            return None
        best, best_score = None, 0.0
        for h in self.hotels:
            score = 0.0
            for hname in {h["name"], h.get("staging_name") or h["name"]}:
                hkey = _name_key(hname)
                if not hkey:
                    continue
                shorter = min(key, hkey, key=len)
                if key == hkey:
                    s = 1.0
                elif len(shorter.split()) >= 2 and (key in hkey or hkey in key):
                    # Multi-word containment only: "edha suites" in "edha suites koramangala" is
                    # a match, but a single word ("lotus" in "purple lotus") is not.
                    s = 0.9
                else:
                    s = SequenceMatcher(None, key, hkey).ratio()
                score = max(score, s)
            if not score:
                continue
            if city and h["city"] == city:
                score += 0.05
            if h["active"]:
                score += 0.01
            if score > best_score:
                best, best_score = h, score
        if best and city and best["city"] != city and best_score < 1.0:
            return None  # a near-match in another city is a different hotel
        return best if best_score >= 0.85 else None
