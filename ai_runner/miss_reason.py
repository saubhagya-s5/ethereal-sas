"""Why Treebo was not mentioned: no_inventory | no_page | weak_page | ai_trusts_others.

1. no_inventory     - no active Treebo hotel matches the question's place + filters.
2. ai_trusts_others - Treebo has the exact landing page for the question, but the AI used others.
3. weak_page        - only a broader page exists (e.g. locality page, but no locality+amenity page).
4. no_page          - hotels match but no relevant Treebo page exists. The city page alone
                      doesn't count: it is too generic to answer a specific question.
"""
from __future__ import annotations

from .config import City
from .inventory import Inventory, slugify
from .queries import matching_hotels, query_filters
from .sitemap import Sitemap


def candidate_pages(query: dict, city: City, sm: Sitemap) -> tuple[list[str], list[str]]:
    """(exact pages, broader pages) present in the sitemap for this query."""
    f = query_filters(query)
    cs = city.slug
    cat, amenity = f["category"], f["amenity"]
    exact: list[str] = []
    broader: list[str] = []

    def add(bucket: list[str], path: str | None) -> None:
        if path and sm.has(path) and path not in bucket:
            bucket.append(path)

    if f["locality"]:
        loc = slugify(f["locality"])
        loc_page = sm.has_locality_page(f["locality"], cs)
        if amenity:  # locality+amenity, or a need that maps to an amenity (solo woman -> security)
            add(exact, f"/hotels-in-{loc}-{cs}-with-{amenity}/")  # proposed pattern
            if loc_page:
                broader.append(loc_page)
            add(broader, f"/hotels-in-{cs}-with-{amenity}/")
        elif cat:
            add(exact, f"/{cat}-in-{loc}-{cs}/")
            if loc_page:
                broader.append(loc_page)
            add(broader, f"/{cat}-in-{cs}/")
        elif loc_page:
            exact.append(loc_page)
    elif f["landmark"]:
        lm = f["landmark"]["slug"]
        if cat:
            add(exact, f"/{cat}-near-{lm}/")
            add(broader, f"/hotels-near-{lm}/")
        else:
            add(exact, f"/hotels-near-{lm}/")
    else:  # whole city
        if amenity:
            add(exact, f"/hotels-in-{cs}-with-{amenity}/")
        if cat:
            add(exact if not amenity else broader, f"/{cat}-in-{cs}/")
    return exact, broader


def miss_reason(query: dict, city: City, inv: Inventory, sm: Sitemap) -> str:
    if not matching_hotels(query, inv):
        return "no_inventory"
    exact, broader = candidate_pages(query, city, sm)
    if exact:
        return "ai_trusts_others"
    if broader:
        return "weak_page"
    return "no_page"
