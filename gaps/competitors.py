"""Competitor URLs for Pune and Bengaluru, split into place + filter.

Source priority (first that exists wins):
  1. data/competitor_urls.csv          real dataset dropped in by the team/organisers
  2. data/competitor_urls_fetched.csv  cache of FabHotels + OYO public sitemaps
  3. fetch FabHotels + OYO sitemaps now (robots.txt respected, delay between requests)
  4. data/competitor_urls_sample.csv   hand-made sample rows (only if fetching is blocked)

CSV columns: competitor,url,city,place_type,place,filter_type,filter,source
  place  = place name slug WITHOUT the city (e.g. "koramangala"); empty for city level
  filter = Treebo-style slug ("budget-hotels", "couple-friendly-hotels", "gym"); empty if none
  source = fetched | sample | provided (provided rows may leave place/filter empty; they are parsed)
"""
import csv
import re

from gaps import fetch
from gaps.config import COMPETITOR_CSV, COMPETITOR_FETCHED, COMPETITOR_SAMPLE
from gaps.util import locs

FIELDS = ["competitor", "url", "city", "place_type", "place", "filter_type", "filter", "source"]
CITY_WORDS = {"Pune": ["pune"], "Bengaluru": ["bangalore", "bengaluru"]}

SITEMAPS = {
    "FabHotels": ["https://www.fabhotels.com/city-sitemap.xml",
                  "https://www.fabhotels.com/locality-sitemap.xml",
                  "https://www.fabhotels.com/landmark-sitemap.xml",
                  "https://www.fabhotels.com/thematic-sitemap.xml"],
    "OYO": ["https://www.oyorooms.com/sitemapmarketplace.xml",
            "https://www.oyorooms.com/sitemapcollection.xml",
            "https://www.oyorooms.com/sitemapamenity.xml"],
}

# Competitor filter words -> Treebo filter (type, slug)
FILTER_MAP = {
    "couple-friendly": ("category", "couple-friendly-hotels"),
    "oyo-welcomes-couples": ("category", "couple-friendly-hotels"),
    "budget": ("category", "budget-hotels"),
    "business": ("category", "business-hotels"),
    "business-travellers": ("category", "business-hotels"),
    "family": ("category", "family-hotels"),
    "family-oyos": ("category", "family-hotels"),
    "2-star": ("category", "2-star-hotels"),
    "3-star": ("category", "3-star-hotels"),
    "4-star": ("category", "4-star-hotels"),
    "luxury-oyos": ("category", "4-star-hotels"),
    "swimming-pool": ("amenity", "swimming-pool"),
    "gym": ("amenity", "gym"),
    "bar": ("amenity", "bar"),
    "restaurant": ("amenity", "restaurant"),
    "parking": ("amenity", "parking"),
    "breakfast": ("amenity", "free-breakfast"),
    "free-breakfast": ("amenity", "free-breakfast"),
}


def _city_of(path):
    for city, words in CITY_WORDS.items():
        if any(re.search(rf"(^|[-/]){w}([-/]|$)", path) for w in words):
            return city
    return None


def _strip_city(slug, city):
    for w in CITY_WORDS[city]:
        slug = re.sub(rf"(^|-){w}(-|$)", "-", slug)
    return slug.strip("-")


def parse_url(competitor, url):
    """Best-effort split of a FabHotels/OYO URL into place + filter. Returns a row dict or None."""
    path = re.sub(r"^https?://[^/]+", "", url).rstrip("/") + "/"
    city = _city_of(path)
    if not city:
        return None
    row = dict(competitor=competitor, url=url, city=city, place_type="city", place="",
               filter_type="", filter="")

    # FabHotels thematic: /list-of-{filter}-hotels-in-{place}/ or /list-of-hotels-in-{city}-with-{amenity}/
    m = re.match(r"^/list-of-(?:(.+?)-)?hotels-(in|near)-(.+?)(?:-with-(.+?))?(?:-below-\d+)?/$", path)
    if m:
        word, joiner, place, amenity = m.groups()
        f = FILTER_MAP.get(amenity or word or "")
        if f:
            row["filter_type"], row["filter"] = f
        place = _strip_city(place, city)
        if place:
            row["place_type"] = "landmark" if joiner == "near" else "locality"
            row["place"] = place
        return row
    # FabHotels place pages: /hotels-in-{city}/{place}/ ; OYO collections: /hotels-in-{city}/{collection}/
    m = re.match(r"^/hotels-in-([a-z0-9-]+)/(?:([a-z0-9-]+)/)?$", path)
    if m:
        sub = m.group(2)
        if sub and sub in FILTER_MAP:
            row["filter_type"], row["filter"] = FILTER_MAP[sub]
        elif sub and competitor == "OYO":
            return None  # other OYO collections (local-ids-accepted, ...) have no Treebo filter
        elif sub:
            row["place_type"], row["place"] = "locality", sub
        return row
    # OYO marketplace: /hotels-near-{place}-{city}/
    m = re.match(r"^/hotels-near-(.+)/$", path)
    if m:
        row["place_type"], row["place"] = "locality", _strip_city(m.group(1), city)
        return row
    return None


def _write(path, rows):
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)


def _read(path, source_default):
    with path.open() as f:
        rows = list(csv.DictReader(f))
    out = []
    for r in rows:
        if not r.get("place_type") and r.get("url"):
            parsed = parse_url(r.get("competitor", ""), r["url"])
            if not parsed:
                continue
            r = {**parsed, **{k: v for k, v in r.items() if v}}
        r.setdefault("source", source_default)
        r["source"] = r.get("source") or source_default
        out.append(r)
    return out


def fetch_competitors():
    rows = []
    for comp, urls in SITEMAPS.items():
        for sm in urls:
            print(f"Fetching {sm}")
            xml = fetch.get(sm)
            if not xml:
                continue
            for url in locs(xml):
                r = parse_url(comp, url)
                if r:
                    rows.append({**r, "source": "fetched"})
    return rows


def load(refresh=False):
    """Return (rows, source_label)."""
    if COMPETITOR_CSV.exists():
        return _read(COMPETITOR_CSV, "provided"), "provided (data/competitor_urls.csv)"
    if COMPETITOR_FETCHED.exists() and not refresh:
        return _read(COMPETITOR_FETCHED, "fetched"), "fetched (FabHotels/OYO public sitemaps, cached)"
    rows = fetch_competitors()
    if rows:
        _write(COMPETITOR_FETCHED, rows)
        return rows, "fetched (FabHotels/OYO public sitemaps)"
    print("Competitor sitemaps blocked/unavailable; using sample rows.")
    return _read(COMPETITOR_SAMPLE, "sample"), "sample (data/competitor_urls_sample.csv)"
