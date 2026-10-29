"""Generate ~20 tagged traveller questions per city from real inventory places.

Tags follow contracts/query.json: {type, place, intent, amenity}.
  type:   locality | locality_amenity | landmark | city_amenity | need | price
  intent: general | budget | family | business | couple | solo_woman
"""
from __future__ import annotations

import random
from collections import Counter

from .config import LANDMARK_RADIUS_KM, City
from .inventory import AMENITY_WORDS, Inventory
from .landmarks import find_landmark, landmarks_for

INTENT_CATEGORY = {
    "budget": "budget-hotels",
    "family": "family-hotels",
    "business": "business-hotels",
    "couple": "couple-friendly-hotels",
}
# Solo women mostly ask about safety; 24-hour security is the closest inventory field.
INTENT_AMENITY = {"solo_woman": "24-hour-security"}

LOCALITY_AMENITIES = ["free-breakfast", "parking", "room-service", "free-wifi", "24-hour-security", "ac-room"]
CITY_AMENITIES = ["free-breakfast", "gym", "parking", "swimming-pool", "restaurant"]


def _q(city: City, n: int, text: str, type_: str, place: str, intent: str = "general", amenity: str | None = None) -> dict:
    return {
        "query_id": f"{city.prefix}-{n:03d}",
        "city": city.name,
        "text": text,
        "tags": {"type": type_, "place": place, "intent": intent, "amenity": amenity},
    }


def query_filters(query: dict) -> dict:
    """Turn a query's tags into inventory filters: locality / landmark / amenity / category."""
    tags = query["tags"]
    city = query["city"]
    place = tags.get("place") or city
    out = {"locality": None, "landmark": None, "amenity": tags.get("amenity"), "category": None}
    if tags.get("intent") in INTENT_CATEGORY:
        out["category"] = INTENT_CATEGORY[tags["intent"]]
    if not out["amenity"] and tags.get("intent") in INTENT_AMENITY:
        out["amenity"] = INTENT_AMENITY[tags["intent"]]
    if place != city:
        lm = find_landmark(city, place)
        if lm:
            out["landmark"] = lm
        else:
            out["locality"] = place
    return out


def matching_hotels(query: dict, inv: Inventory) -> list[dict]:
    """Active Treebo hotels that answer this query."""
    f = query_filters(query)
    near = (f["landmark"]["lat"], f["landmark"]["lng"]) if f["landmark"] else None
    return inv.matching(
        query["city"],
        locality=f["locality"],
        amenity=f["amenity"],
        category=f["category"],
        near=near,
        radius_km=LANDMARK_RADIUS_KM,
    )


def generate_queries(city: City, inv: Inventory) -> list[dict]:
    rng = random.Random(city.slug)
    active = inv.localities(city.name, active_only=True)
    all_locs = inv.localities(city.name, active_only=False)
    # Most hotels first; ties broken by a seeded shuffle so output is stable but not alphabetical.
    # Localities named like landmarks ("Airport", "Railway Station") read badly; landmark questions cover them.
    shuffled = sorted(active)
    rng.shuffle(shuffled)
    locs = sorted(
        (loc for loc in shuffled if not any(w in loc.lower() for w in ("airport", "railway", "station"))),
        key=lambda loc: -len(active[loc]),
    )
    inactive_only = sorted(
        (loc for loc in all_locs if loc not in active),
        key=lambda loc: (-len(all_locs[loc]), loc),
    )

    def has(loc: str, amenity: str | None = None, category: str | None = None) -> int:
        return len(inv.matching(city.name, locality=loc, amenity=amenity, category=category))

    def pick(cands: list[str], used: set[str]) -> str | None:
        for loc in cands:
            if loc not in used:
                used.add(loc)
                return loc
        return cands[0] if cands else None

    qs: list[tuple] = []  # (text, type, place, intent, amenity)
    c = city.name

    # 1. Locality (4)
    locality_templates = [
        ("Which are the best hotels to stay in {loc}, {city}?", "general"),
        ("Suggest a good budget hotel in {loc}, {city}.", "budget"),
        ("Where should I stay in {loc}, {city} for a two-night trip?", "general"),
        ("Which hotel chains have properties in {loc}, {city}?", "general"),
    ]
    used_loc_q: set[str] = set()
    for tpl, intent in locality_templates:
        # A filtered question (e.g. budget) goes to a locality that has a matching hotel.
        cands = [l for l in locs if has(l, category=INTENT_CATEGORY.get(intent))]
        loc = pick(cands, used_loc_q)
        if loc:
            qs.append((tpl.format(loc=loc, city=c), "locality", loc, intent, None))

    # Probe: a locality where Treebo only has inactive hotels (tests no_inventory / closed-hotel claims).
    if inactive_only:
        loc = inactive_only[0]
        qs.append((f"Is there a Treebo or similar budget hotel in {loc}, {c}?", "locality", loc, "budget", None))

    # 2. Locality + amenity (3): prefer combinations with the most matching hotels.
    combos = sorted(
        ((loc, a) for loc in locs for a in LOCALITY_AMENITIES if has(loc, a)),
        key=lambda la: (-has(*la), LOCALITY_AMENITIES.index(la[1])),
    )
    used_locs: set[str] = set()
    used_amenities: set[str] = set()
    la_templates = [
        "Which hotels in {loc}, {city} include {amenity}?",
        "Budget hotel in {loc}, {city} with {amenity}?",
        "I need a hotel in {loc}, {city} that has {amenity}. Any suggestions?",
    ]
    for tpl in la_templates:
        intent = "budget" if "Budget" in tpl else "general"
        ok = [(l, a) for l, a in combos if has(l, a, INTENT_CATEGORY.get(intent))] or combos
        choice = next(
            ((l, a) for l, a in ok if l not in used_locs and a not in used_amenities),
            next(((l, a) for l, a in ok if l not in used_locs), ok[0] if ok else None),
        )
        if not choice:
            break
        used_locs.add(choice[0])
        used_amenities.add(choice[1])
        qs.append((tpl.format(loc=choice[0], city=c, amenity=AMENITY_WORDS[choice[1]]), "locality_amenity", choice[0], intent, choice[1]))

    # 3. Landmark (3): two with Treebo hotels nearby, one without if there is one.
    lms = landmarks_for(c)

    def near_count(lm: dict) -> int:
        return len(inv.matching(c, near=(lm["lat"], lm["lng"]), radius_km=LANDMARK_RADIUS_KM))

    with_hotels = [lm for lm in lms if near_count(lm)]
    without = [lm for lm in lms if not near_count(lm)]
    chosen_lms = (with_hotels[:2] + without[:1]) if without else with_hotels[:3]
    lm_templates = [
        ("Which hotels are closest to {lm}, {city}?", "general"),
        ("Good budget hotel near {lm} in {city} for one night?", "budget"),
        ("Best place to stay near {lm}, {city} for a business trip?", "business"),
    ]
    for (tpl, intent), lm in zip(lm_templates, chosen_lms):
        qs.append((tpl.format(lm=lm["name"], city=c), "landmark", lm["name"], intent, None))

    # 4. City + amenity (2): one Treebo has a city-amenity page type for, one it excludes (free breakfast).
    city_amenity_counts = Counter(a for h in inv.city(c, True) for a in h["amenities"] if a in CITY_AMENITIES)
    for amenity in ["free-breakfast", next((a for a in ["gym", "restaurant", "swimming-pool", "parking"] if city_amenity_counts[a]), "parking")]:
        qs.append((f"Which budget hotels in {c} have {AMENITY_WORDS[amenity]}?", "city_amenity", c, "budget", amenity))

    # 5. Traveller need (4)
    need_templates = [
        ("family", "Which hotel in {loc}, {city} is good for a family with kids?"),
        ("solo_woman", "Which hotel in {loc}, {city} is safe for a solo woman traveller?"),
        ("business", "Best business hotel in {loc}, {city} for a work trip?"),
        ("couple", "Which hotels in {loc}, {city} are couple-friendly?"),
    ]
    used_need: set[str] = set()
    for intent, tpl in need_templates:
        cands = [
            loc for loc in locs
            if has(loc, INTENT_AMENITY.get(intent), INTENT_CATEGORY.get(intent))
        ]
        loc = pick(cands, used_need) or (locs[0] if locs else c)
        qs.append((tpl.format(loc=loc, city=c), "need", loc, intent, INTENT_AMENITY.get(intent)))

    # 6. Price (3)
    price_loc = locs[rng.randrange(min(len(locs), 4))] if locs else c
    qs.append((f"Hotels in {price_loc}, {c} under ₹2,000 a night?", "price", price_loc, "budget", None))
    if with_hotels:
        lm = with_hotels[-1]
        qs.append((f"Cheapest decent hotel near {lm['name']}, {c}?", "price", lm["name"], "budget", None))
    qs.append((f"Best value hotel in {c} under ₹1,500 per night?", "price", c, "budget", None))

    return [_q(city, i, *row) for i, row in enumerate(qs, start=1)]
