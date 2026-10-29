"""Coverage map -> missing place+filter combinations -> matched hotels -> scored opportunities.

Proposed URL patterns (extend Treebo's existing builders, docs/treebo_findings.md section 2):
  city + category      /{category}-in-{city}/                     (existing pattern)
  city + amenity       /hotels-in-{city}-with-{amenity}/          (existing pattern)
  locality + category  /{category}-in-{locality-slug}/            (existing pattern)
  landmark + category  /{category}-near-{landmark-slug}/          (existing pattern)
  locality + amenity   /hotels-in-{locality-slug}-with-{amenity}/ (locality URL + city_amenity's "-with-" suffix)
  landmark + amenity   /hotels-near-{landmark-slug}-with-{amenity}/ (landmark URL + "-with-" suffix)
where {locality-slug}/{landmark-slug} is exactly the slug Treebo already uses (it ends with -{city}).
"""
from gaps.config import AMENITY_FILTERS, EXCLUDED_FILTERS, MATCH_RADIUS_KM, MIN_HOTELS, TREEBO
from gaps.sitemaps import INVENTORY_TO_URL_CATEGORY, URL_TO_INVENTORY_CATEGORY
from gaps.util import km, slugify


def proposed_url(place_type, place_slug, filter_type, filt):
    if filter_type == "category":
        cat = INVENTORY_TO_URL_CATEGORY.get(filt, filt)
        joiner = "near" if place_type == "landmark" else "in"
        return f"{TREEBO}/{cat}-{joiner}-{place_slug}/"
    base = "hotels-near" if place_type == "landmark" else "hotels-in"
    return f"{TREEBO}/{base}-{place_slug}-with-{filt}/"


def _name_from_slug(slug, city_slug):
    return slug[: -len(city_slug) - 1].replace("-", " ").title() if slug.endswith("-" + city_slug) else slug


def build_places(city, city_slug, sitemap_places, places_db, hotels):
    """All candidate places: city + Treebo locality/landmark slugs + inventory localities."""
    db = {p["slug"]: p for p in places_db if p["city"] == city}
    out = [{"place_type": "city", "slug": city_slug, "name": city, "lat": None, "lng": None, "popular": True}]
    seen = set()
    for ptype in ("locality", "landmark"):
        for slug in sorted(sitemap_places[ptype]):
            p = db.get(slug, {})
            out.append({"place_type": ptype, "slug": slug, "name": p.get("name") or _name_from_slug(slug, city_slug),
                        "lat": p.get("lat"), "lng": p.get("lng"), "popular": p.get("is_popular", False)})
            seen.add(slug)
    for h in hotels:
        if h["locality"]:
            slug = f"{slugify(h['locality'])}-{city_slug}"
            if slug not in seen:
                seen.add(slug)
                out.append({"place_type": "locality", "slug": slug, "name": h["locality"],
                            "lat": None, "lng": None, "popular": False})
    return out


def hotels_for_place(place, hotels, city_slug):
    if place["place_type"] == "city":
        return hotels
    name = place["slug"][: -len(city_slug) - 1] if place["slug"].endswith("-" + city_slug) else place["slug"]
    out = []
    for h in hotels:
        if h["locality"] and slugify(h["locality"]) == name:
            out.append(h)
        elif place["lat"] and km(place["lat"], place["lng"], h["lat"], h["lng"]) <= MATCH_RADIUS_KM:
            out.append(h)
    return out


def filters(categories):
    return [("category", c) for c in categories] + [("amenity", a) for a in AMENITY_FILTERS]


def _has(h, filter_type, filt):
    return filt in (h["categories"] if filter_type == "category" else h["amenities"])


def competitor_pages(rows, city, place, city_slug, filter_type, filt):
    want = "" if place["place_type"] == "city" else (
        place["slug"][: -len(city_slug) - 1] if place["slug"].endswith("-" + city_slug) else place["slug"])
    out = []
    for r in rows:
        if r["city"] != city or r["filter"] != filt or r["filter_type"] != filter_type:
            continue
        cp = r["place"] or ""
        if want == "" and cp == "":
            out.append(r["url"])
        elif want and cp and (cp == want or want.startswith(cp + "-") or cp.startswith(want + "-")):
            out.append(r["url"])
    return sorted(set(out))


def ai_mention(ai_index, city, place, filt):
    """Treebo mention rate from output/ai_results.json for queries tagged with this place+filter, else None."""
    key_place = "" if place["place_type"] == "city" else slugify(place["name"])
    return ai_index.get((city, key_place, filt))


def level(place_type):
    return "city" if place_type == "city" else "local"


def find(city, city_slug, existing, sitemap_places, categories, places_db, hotels, comp_rows, demand, ai_index):
    """Return (opportunities, notes, stats).

    `hotels` must already be limited to hotels that exist on the live site (see gaps/hotel_map.py).
    City-level and locality/landmark-level opportunities are scored separately (demand is scaled
    within each level) and locality/landmark ones are listed first.
    """
    active = [h for h in hotels if h["city"] == city]
    inv_categories = sorted({c for h in active for c in h["categories"]} &
                            {URL_TO_INVENTORY_CATEGORY.get(c, c) for c in categories})
    places = build_places(city, city_slug, sitemap_places, places_db, active)
    cands = []
    for place in places:
        need = MIN_HOTELS[place["place_type"]]
        ph = hotels_for_place(place, active, city_slug)
        if len(ph) < need:
            continue
        for ftype, filt in filters(inv_categories):
            if filt in EXCLUDED_FILTERS or (place["place_type"], place["slug"], ftype, filt) in existing:
                continue
            match = sorted({h["hotel_id"] for h in ph if _has(h, ftype, filt)}, key=int)
            if len(match) < need:
                continue
            vol, est = demand.volume(city, place["place_type"], place["name"], filt, place["popular"])
            cands.append(dict(place=place, filter_type=ftype, filter=filt, hotels=match, volume=vol, estimated=est))

    # One page per (filter, identical hotel set): keep the place with the highest demand.
    best = {}
    for c in cands:
        k = (c["filter_type"], c["filter"], tuple(c["hotels"]))
        if k not in best or c["volume"] > best[k]["volume"]:
            best[k] = c
    deduped = list(best.values())
    max_vol = {}
    for c in deduped:
        lv = level(c["place"]["place_type"])
        max_vol[lv] = max(max_vol.get(lv, 1), c["volume"])

    opps, notes = [], {}
    for c in deduped:
        place, ftype, filt = c["place"], c["filter_type"], c["filter"]
        comps = competitor_pages(comp_rows, city, place, city_slug, ftype, filt)
        rate = ai_mention(ai_index, city, place, filt)
        inv = min(len(c["hotels"]), 5) / 5
        gap = 1.0 if comps else 0.8
        invis = 1.0 - rate if rate is not None else 1.0
        dem = c["volume"] / max_vol[level(place["place_type"])]
        score = round(100 * dem * inv * gap * invis, 1)
        opp_id = f"{city_slug[:3]}-{slugify(place['name'])}-{filt}" if place["place_type"] != "city" \
            else f"{city_slug[:3]}-city-{filt}"
        opps.append({
            "opp_id": opp_id,
            "city": city,
            "place_type": place["place_type"],
            "place": place["name"],
            "filter_type": ftype,
            "filter": filt,
            "proposed_url": proposed_url(place["place_type"], place["slug"], ftype, filt),
            "exists_on_treebo": False,
            "competitors_with_page": comps,
            "search_volume": c["volume"],
            "matching_hotel_ids": c["hotels"],
            "ai_mention_rate": round(rate, 3) if rate is not None else 0.0,
            "score": score,
        })
        notes[opp_id] = {
            "level": level(place["place_type"]),
            "search_volume": "estimated" if c["estimated"] else "keyword_volumes.csv",
            "ai_mention_rate": "measured" if rate is not None else "assumed_invisible (no ai_results)",
            "factors": {"demand": round(dem, 3), "inventory": inv, "gap": gap,
                        "ai_invisibility": round(invis, 3)},
        }
    opps.sort(key=lambda o: (o["place_type"] == "city", -o["score"]))
    stats = {"places_considered": len(places), "candidates_before_dedup": len(cands),
             "opportunities": len(opps),
             "ranking": {
                 "local": [o["opp_id"] for o in opps if o["place_type"] != "city"],
                 "city": [o["opp_id"] for o in opps if o["place_type"] == "city"],
             }}
    return opps, notes, stats
