"""Download Treebo's public sitemaps and split every URL into place + filter.

URL patterns (docs/treebo_findings.md, section 2):
  city              /hotels-in-{city}/
  city_amenity      /hotels-in-{city}-with-{amenity}/
  category          /{category}-in-{city}/
  locality          /hotels-in-{locality-slug}/          (slug ends with -{city})
  locality_category /{category}-in-{locality-slug}/
  landmark          /hotels-near-{landmark-slug}/        (slug ends with -{city})
  landmark_category /{category}-near-{landmark-slug}/
  nearme            /hotels-near-me/, /{category}-near-me
"""
import re

from gaps import fetch
from gaps.config import SITEMAP_DIR, TREEBO
from gaps.util import locs

SITEMAP_TYPES = ["city", "locality", "landmark", "nearme", "hotel", "category", "locality_category",
                 "landmark_category", "city_amenity", "nearme_category", "stat", "old_urls"]
LISTING_TYPES = ["city", "locality", "landmark", "nearme", "category", "locality_category",
                 "landmark_category", "city_amenity", "nearme_category"]

# Category slugs used in Treebo URLs vs. the slugs in data/inventory.json.
URL_TO_INVENTORY_CATEGORY = {
    "near-city-railway-station": "near-railway-station",
    "near-the-airport": "near-airport",
}
INVENTORY_TO_URL_CATEGORY = {v: k for k, v in URL_TO_INVENTORY_CATEGORY.items()}


def load(refresh=False):
    """Return {sitemap_type: [url, ...]}, using data/sitemaps/*.xml as cache."""
    SITEMAP_DIR.mkdir(parents=True, exist_ok=True)
    out = {}
    for t in LISTING_TYPES + ["hotel"]:
        path = SITEMAP_DIR / f"{t}.xml"
        if refresh or not path.exists():
            print(f"Fetching sitemap-{t}.xml")
            xml = fetch.get(f"{TREEBO}/sitemap-{t}.xml")
            if xml:
                path.write_text(xml)
        out[t] = locs(path.read_text()) if path.exists() else []
    return out


def category_slugs(sitemaps):
    """All category slugs Treebo uses in URLs (longest first, for unambiguous prefix matching)."""
    cats = set()
    for url in sitemaps["category"]:
        m = re.match(r"^/(.+)-in-[a-z0-9-]+/$", _path(url))
        if m:
            cats.add(m.group(1))
    return sorted(cats, key=len, reverse=True)


def _path(url):
    return re.sub(r"^https?://[^/]+", "", url).rstrip("/") + "/"


def parse(url, sitemap_type, city_slug, categories):
    """Return {place_type, place_slug, filter_type, filter} for URLs in this city, else None.

    place_slug is the exact slug Treebo uses after "hotels-in-"/"hotels-near-" (includes -{city}).
    """
    p = _path(url)
    sfx = f"-{city_slug}"

    def in_city(slug):
        return slug == city_slug or slug.endswith(sfx)

    if sitemap_type == "city_amenity":
        m = re.match(r"^/hotels-in-(.+)-with-(.+)/$", p)
        if m and m.group(1) == city_slug:
            return dict(place_type="city", place_slug=city_slug, filter_type="amenity", filter=m.group(2))
        return None
    if sitemap_type in ("city", "locality"):
        m = re.match(r"^/hotels-in-(.+)/$", p)
        if m and in_city(m.group(1)):
            pt = "city" if m.group(1) == city_slug else "locality"
            return dict(place_type=pt, place_slug=m.group(1), filter_type=None, filter=None)
        return None
    if sitemap_type == "landmark":
        m = re.match(r"^/hotels-near-(.+)/$", p)
        if m and m.group(1) != "me" and in_city(m.group(1)):
            return dict(place_type="landmark", place_slug=m.group(1), filter_type=None, filter=None)
        return None
    if sitemap_type in ("category", "locality_category", "landmark_category"):
        joiner = "near" if sitemap_type == "landmark_category" else "in"
        for cat in categories:
            prefix = f"/{cat}-{joiner}-"
            if p.startswith(prefix):
                slug = p[len(prefix):-1]
                if not in_city(slug):
                    return None
                pt = {"category": "city", "locality_category": "locality", "landmark_category": "landmark"}[sitemap_type]
                return dict(place_type=pt, place_slug=slug, filter_type="category",
                            filter=URL_TO_INVENTORY_CATEGORY.get(cat, cat))
        return None
    return None  # nearme / nearme_category are not city-specific


def coverage(sitemaps, city_slug):
    """Set of (place_type, place_slug, filter_type, filter) that exist on treebo.com, plus place lists."""
    cats = category_slugs(sitemaps)
    existing, places = set(), {"locality": set(), "landmark": set()}
    urls = {}
    for t in LISTING_TYPES:
        for url in sitemaps.get(t, []):
            r = parse(url, t, city_slug, cats)
            if not r:
                continue
            key = (r["place_type"], r["place_slug"], r["filter_type"], r["filter"])
            existing.add(key)
            urls[key] = url
            if r["place_type"] in places:
                places[r["place_type"]].add(r["place_slug"])
    return existing, places, urls, cats
