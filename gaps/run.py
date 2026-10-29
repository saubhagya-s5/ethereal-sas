"""Gaps pipeline: sitemaps -> coverage -> competitors -> hotel matching -> scores -> page issues.

Usage:
  python -m gaps.run --city pune
  python -m gaps.run --city all [--refresh] [--skip-pages]

Writes output/opportunities.json, output/page_issues.json (contract shapes) and
output/gaps_notes.json (labels such as "estimated"/"sample", score factors, coverage).
Outputs for other cities already in those files are kept.
Reads only public web pages and local files; never writes to any database.
"""
import argparse

from gaps import competitors, opportunities, page_issues, sitemaps
from gaps.config import (AI_RESULTS, CITIES, EXCLUDED_FILTERS, INVENTORY, MIN_HOTELS, NOTES_OUT,
                         OPPORTUNITIES_OUT, PAGE_ISSUES_OUT, PLACES, QUERIES)
from gaps.demand import Demand
from gaps.util import read_json, slugify, write_json


def ai_index():
    """{(city, place_slug, filter): treebo mention rate} from output/ai_results.json + output/queries.json."""
    results, queries = read_json(AI_RESULTS, []), read_json(QUERIES, [])
    if not results or not queries:
        return {}
    qmap = {q["query_id"]: q for q in queries}
    agg = {}
    for r in results:
        q = qmap.get(r["query_id"])
        if not q:
            continue
        tags = q.get("tags", {})
        filt = tags.get("amenity") or tags.get("intent")
        place = "" if (tags.get("place") or "").lower() == q["city"].lower() else slugify(tags.get("place"))
        key = (q["city"], place, filt)
        hit, n = agg.get(key, (0, 0))
        agg[key] = (hit + bool(r["treebo_mentioned"]), n + 1)
    return {k: h / n for k, (h, n) in agg.items()}


def run_city(key, sm, comp_rows, demand, places_db, hotels, ai, refresh, skip_pages):
    cfg = CITIES[key]
    city, city_slug = cfg["name"], cfg["slug"]
    print(f"\n== {city}")
    city_hotels = [h for h in hotels if h["city"] == city and h["active"]]
    existing, sitemap_places, urls_by_key, cats = sitemaps.coverage(sm, city_slug)
    opps, opp_notes, stats = opportunities.find(city, city_slug, existing, sitemap_places, cats, places_db,
                                                city_hotels, comp_rows, demand, ai)
    print(f"{len(existing)} existing place+filter pages; {len(city_hotels)} hotels; "
          f"{stats['opportunities']} opportunities")

    issues = []
    if not skip_pages:
        prices = {h["hotel_id"]: {"url": h["url"], "price_from": h["price_from"]}
                  for h in city_hotels if h["price_from"]}
        place_by_slug = {p["slug"]: p for p in opportunities.build_places(
            city, city_slug, sitemap_places, places_db, city_hotels)}

        def hotels_for_key(k):
            place = place_by_slug.get(k[1])
            return opportunities.hotels_for_place(place, city_hotels, city_slug) if place else []

        pages = page_issues.pages_to_check(urls_by_key, city_slug, city_hotels)
        issues = page_issues.check(pages, prices, sm["hotel"], hotels_for_key, refresh)
        print(f"checked {len(pages)} pages, {len(issues)} issues")

    coverage = {
        "existing_pages": len(existing),
        "by_type": {f"{pt}+{ft or 'none'}": sum(1 for k in existing if k[0] == pt and k[2] == ft)
                    for pt in ("city", "locality", "landmark") for ft in (None, "category", "amenity")},
        "amenity_pages": sorted(k[3] for k in existing if k[2] == "amenity"),
        **stats,
    }
    return opps, issues, {"coverage": coverage, "opportunities": opp_notes}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="all", choices=["all", *CITIES])
    ap.add_argument("--refresh", action="store_true", help="re-download sitemaps, competitor URLs, prices, pages")
    ap.add_argument("--skip-pages", action="store_true", help="skip live price + page-issue checks")
    args = ap.parse_args()

    hotels = read_json(INVENTORY)
    places_db = read_json(PLACES, [])
    sm = sitemaps.load(args.refresh)
    comp_rows, comp_source = competitors.load(args.refresh)
    demand = Demand()
    ai = ai_index()

    keys = list(CITIES) if args.city == "all" else [args.city]
    names = {CITIES[k]["name"] for k in keys}
    all_opps = [o for o in read_json(OPPORTUNITIES_OUT, []) if o["city"] not in names]
    all_issues = [i for i in read_json(PAGE_ISSUES_OUT, []) if not any(
        f"-{CITIES[k]['slug']}" in i["url"] for k in keys)]
    notes = read_json(NOTES_OUT, {}) or {}
    notes.update({
        "sources": {
            "treebo_pages": "treebo.com public sitemaps (data/sitemaps/)",
            "competitor_urls": comp_source,
            "search_volume": "data/keyword_volumes.csv" if demand.real else
            "ESTIMATED from data/demand_weights.json (not real search volume)",
            "ai_mention_rate": "output/ai_results.json" if ai else
            "assumed 0.0 (invisible) - output/ai_results.json not found",
            "inventory": "data/inventory.json built from live treebo.com pages (see data/inventory_notes.json)",
            "prices": "inventory price_from = lower bound of the hotel page's priceRange (brand-tier band)",
        },
        "excluded_filters": EXCLUDED_FILTERS,
        "min_matching_hotels": MIN_HOTELS,
        "ranking": "opportunities.json lists locality/landmark opportunities first, then city-level. "
                   "Each level is scored separately (demand scaled within the level); see cities.*.coverage.ranking.",
        "issue_confidence": {
            "outdated_price": "low - prices change daily and the live price is a brand-tier band",
            "lists_inactive_hotels": "medium - based on sitemap-hotel.xml",
            "no_quick_answer": "medium - heuristic on the first About paragraph",
            "no_faqs": "high",
            "breakfast_not_stated": "medium - amenities come from hotel page structured data",
        },
        "score_formula": "100 x demand(volume / max volume in city) x inventory(min(hotels,5)/5) "
                         "x gap(1.0 competitor has page, else 0.8) x ai_invisibility(1 - mention rate)",
    })
    notes.setdefault("cities", {})

    for k in keys:
        opps, issues, city_notes = run_city(k, sm, comp_rows, demand, places_db, hotels, ai,
                                            args.refresh, args.skip_pages)
        all_opps += opps
        all_issues += issues
        notes["cities"][CITIES[k]["name"]] = city_notes

    write_json(OPPORTUNITIES_OUT, sorted(all_opps, key=lambda o: (o["place_type"] == "city", o["city"], -o["score"])))
    write_json(PAGE_ISSUES_OUT, all_issues)
    write_json(NOTES_OUT, notes)
    print(f"\nWrote {OPPORTUNITIES_OUT}, {PAGE_ISSUES_OUT}, {NOTES_OUT}")


if __name__ == "__main__":
    main()
