"""Check each Treebo claim against production truth.

Hotel names, active flags, locality: data/hotel_id_map.json (laid over inventory.json by
Inventory.load). Prices: data/live_prices.json only. Amenities, categories, rating: inventory.json.

Output items match contracts/ai_result.json treebo_claims: {hotel_id, claim, status, truth},
status in correct | wrong | outdated. Claims that can't be verified are dropped, never guessed:
'other' claims, amenity/category words we can't map to a slug, missing ratings, and prices
for hotels with no live price.
"""
from __future__ import annotations

import re

from .config import CITIES, PRICE_TOLERANCE
from .extract import is_generic_name
from .inventory import Inventory, amenity_slugs_for, category_slugs_for, slugify

CLOSED_WORDS = ("closed", "shut", "not operating", "no longer", "permanently")
INACTIVE_TRUTH = {
    "production": "hotel is not active on treebo.com (data/hotel_id_map.json)",
    "not_in_production": "hotel id not found on treebo.com (data/hotel_id_map.json)",
}


def _num(text: str) -> float | None:
    m = re.search(r"\d[\d,]*(?:\.\d+)?", text or "")
    return float(m.group().replace(",", "")) if m else None


def check_claims(claims: list[dict], inv: Inventory, city: str, live_prices: dict, hotel_pages=None) -> list[dict]:
    """hotel_pages: sitemap.HotelPages. With it, a name missing from our inventory but listed on
    treebo.com is a real hotel we have no data for, so its claims are dropped, not marked wrong."""
    out: list[dict] = []
    flagged_missing: set[str] = set()
    flagged_inactive: set[str] = set()
    prices = live_prices.get("prices", {})
    price_date = live_prices.get("checkin")
    city_slug = next((c.slug for c in CITIES.values() if c.name == city), slugify(city))

    for c in claims:
        name = c.get("hotel_name", "")
        if is_generic_name(name):
            continue  # brand mention, not a specific hotel: nothing to check
        hotel = inv.match_name(name, city)
        if hotel is None:
            if hotel_pages is not None and hotel_pages.find(name, city_slug):
                continue  # real Treebo hotel outside our inventory: can't verify
            if name not in flagged_missing:
                flagged_missing.add(name)
                where = f"on treebo.com in {city}" if hotel_pages is not None else "in inventory"
                out.append({
                    "hotel_id": None,
                    "claim": f"{name} is a Treebo hotel in {city}",
                    "status": "wrong",
                    "truth": f"no Treebo hotel named '{name}' {where}",
                })
            continue
        hid = hotel["hotel_id"]
        attr = c.get("attribute")
        value = c.get("value", "")
        claim = c.get("claim") or f"{name}: {value}"

        if hotel["city"] != city and attr != "open_status":
            out.append({"hotel_id": hid, "claim": claim, "status": "wrong",
                        "truth": f"{hotel['name']} is in {hotel['city']}, not {city}"})
            continue

        if not hotel["active"]:
            # Any recommendation of an inactive hotel is stale, whatever the claim.
            if attr == "open_status" and any(w in value.lower() for w in CLOSED_WORDS):
                out.append({"hotel_id": hid, "claim": claim, "status": "correct", "truth": "hotel is inactive in inventory"})
            elif hid not in flagged_inactive:
                flagged_inactive.add(hid)
                out.append({"hotel_id": hid, "claim": f"{hotel['name']} is open for booking", "status": "outdated",
                            "truth": INACTIVE_TRUTH.get(hotel.get("source"), "hotel is inactive in inventory")})
            continue

        result = _check_active(hotel, attr, value, prices.get(hid), price_date)
        if result:
            status, truth = result
            out.append({"hotel_id": hid, "claim": claim, "status": status, "truth": truth})
    return out


def _check_active(hotel: dict, attr: str, value: str, live: dict | None, price_date: str | None) -> tuple[str, str] | None:
    amenities = hotel.get("amenities") or []
    categories = hotel.get("categories") or []
    hid = hotel["hotel_id"]

    if attr == "amenity":
        slugs = amenity_slugs_for(value)
        if not slugs or not amenities:  # no facility data for this hotel
            return None
        hit = next((s for s in slugs if s in amenities), None)
        if hit:
            return "correct", f"{hit} is in amenities"
        return "wrong", f"{'/'.join(slugs)} is not in amenities for hotel {hid}"

    if attr == "category":
        slugs = category_slugs_for(value)
        if not slugs or not categories:  # no category data for production-only hotels
            return None
        hit = next((s for s in slugs if s in categories), None)
        if hit:
            return "correct", f"{hit} is in categories"
        return "wrong", f"{'/'.join(slugs)} is not in categories for hotel {hid}"

    if attr == "locality":
        truth = hotel.get("locality")
        if not truth or not value:
            return None
        a, b = slugify(value), slugify(truth)
        if a == b or a in b or b in a:
            return "correct", f"locality is {truth}"
        return "wrong", f"locality is {truth}"

    if attr == "rating":
        claimed, truth = _num(value), hotel.get("rating")
        if claimed is None or truth is None:
            return None
        if abs(claimed - truth) <= 0.3:
            return "correct", f"rating is {truth}"
        return "wrong", f"rating is {truth}"

    if attr == "price":
        claimed = _num(value)
        if claimed is None or not live:
            return None  # inventory prices are placeholders; never use them
        refs = [p for p in (live.get("pretax_price"), live.get("sell_price")) if p]
        if not refs:
            return None
        truth = (f"live price for {price_date or 'dry-run'}: ₹{live.get('pretax_price'):,.0f} pre-tax"
                 f" / ₹{live.get('sell_price'):,.0f} with tax" if live.get("sell_price") and live.get("pretax_price")
                 else f"live price ₹{refs[0]:,.0f}")
        if any(abs(claimed - p) / p <= PRICE_TOLERANCE for p in refs):
            return "correct", truth
        # Prices move; a quote far from today's price is stale rather than invented.
        return "outdated", truth

    if attr == "open_status":
        if any(w in value.lower() for w in CLOSED_WORDS):
            return "wrong", "hotel is active in inventory"
        return "correct", "hotel is active in inventory"

    return None
