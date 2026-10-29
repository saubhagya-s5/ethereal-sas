"""Treebo landing-page URLs from the live sitemaps, cached for 24h in output/raw/_sitemap/.

Only listing-page sitemaps are read (hotel and old_urls are skipped). Dry-run uses an
offline sample built from the inventory and Treebo's URL patterns.
"""
from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from .cache import read_json, write_json
from .config import CITIES, RAW_DIR, SITEMAP_MAX_AGE_HOURS, TREEBO_BASE
from .inventory import Inventory, slugify
from .landmarks import LANDMARKS

TYPES = ["city", "locality", "landmark", "category", "locality_category", "landmark_category", "city_amenity"]
CACHE_PATH = RAW_DIR / "_sitemap" / "treebo_urls.json"
NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (treebo-aeo-agent)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.read()


def _paths(xml: bytes) -> list[str]:
    root = ET.fromstring(xml)
    return [urlparse(loc.text.strip()).path for loc in root.iter(f"{NS}loc") if loc.text]


class Sitemap:
    def __init__(self, paths: set[str], source: str):
        self.paths = {p if p.endswith("/") else p + "/" for p in paths}
        self.source = source

    def has(self, path: str) -> bool:
        return path in self.paths

    def has_locality_page(self, locality: str, city_slug: str) -> str | None:
        """Locality pages are /hotels-in-{locality}-{city}/ or /hotels-in-{free_text_url}/."""
        exact = f"/hotels-in-{slugify(locality)}-{city_slug}/"
        if exact in self.paths:
            return exact
        # e.g. /hotels-in-koramangala-1st-block-bangalore/ or a bare /hotels-in-koramangala/
        loc = re.escape(slugify(locality))
        pat = re.compile(rf"^/hotels-in-{loc}((-[a-z0-9]+)*-{re.escape(city_slug)})?/$")
        return next((p for p in sorted(self.paths) if pat.match(p)), None)

    def url(self, path: str) -> str:
        return TREEBO_BASE + path


def load_sitemap(cache_path: Path = CACHE_PATH, force: bool = False) -> Sitemap:
    if cache_path.exists() and not force:
        data = read_json(cache_path)
        age = datetime.now(timezone.utc) - datetime.fromisoformat(data["fetched_at"])
        if age < timedelta(hours=SITEMAP_MAX_AGE_HOURS):
            return Sitemap(set(data["paths"]), "cache")
    paths: list[str] = []
    for t in TYPES:
        paths += _paths(_get(f"{TREEBO_BASE}/sitemap-{t}.xml"))
    write_json(cache_path, {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "types": TYPES,
        "paths": sorted(set(paths)),
    })
    return Sitemap(set(paths), "live")


HOTEL_CACHE_PATH = RAW_DIR / "_sitemap" / "treebo_hotels.json"
_STOP = {"treebo", "trend", "trip", "edge", "premium", "hotel", "hotels", "the", "and", "by"}


class HotelPages:
    """Every live hotel page on treebo.com (sitemap-hotel.xml), to tell a real Treebo hotel that
    is missing from our inventory apart from a hallucinated one."""

    def __init__(self, paths: list[str]):
        self.pages = []  # (city_slug, set of name tokens, path)
        for p in paths:
            m = re.match(r"^/hotels-in-([a-z0-9-]+)/([a-z0-9-]+)-(\d+)/$", p)
            if m:
                self.pages.append((m[1], set(m[2].split("-")) - _STOP, p))

    def ids(self, city_slug: str) -> list[str]:
        return [re.search(r"-(\d+)/$", p)[1] for c, _, p in self.pages if c == city_slug]

    def find(self, name: str, city_slug: str) -> str | None:
        """Path of a hotel page in this city whose slug contains every distinctive word of name."""
        words = set(slugify(name).split("-")) - _STOP
        if not words:
            return None
        return next((p for c, toks, p in self.pages if c == city_slug and words <= toks), None)


def load_hotel_pages(cache_path: Path = HOTEL_CACHE_PATH, force: bool = False) -> HotelPages:
    if cache_path.exists() and not force:
        data = read_json(cache_path)
        if datetime.now(timezone.utc) - datetime.fromisoformat(data["fetched_at"]) < timedelta(hours=SITEMAP_MAX_AGE_HOURS):
            return HotelPages(data["paths"])
    paths = sorted(set(_paths(_get(f"{TREEBO_BASE}/sitemap-hotel.xml"))))
    write_json(cache_path, {"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "paths": paths})
    return HotelPages(paths)


def offline_sitemap(inv: Inventory) -> Sitemap:
    """Dry-run sample: what the sitemap rules would list for our two cities."""
    paths = set()
    for city in CITIES.values():
        paths.add(f"/hotels-in-{city.slug}/")
        for loc in inv.localities(city.name, active_only=True):
            paths.add(f"/hotels-in-{slugify(loc)}-{city.slug}/")
        for cat in ["budget-hotels", "family-hotels", "business-hotels", "couple-friendly-hotels"]:
            paths.add(f"/{cat}-in-{city.slug}/")
        for amenity in ["restaurant", "bar", "swimming-pool", "gym"]:
            paths.add(f"/hotels-in-{city.slug}-with-{amenity}/")
        for lm in LANDMARKS.get(city.name, [])[:4]:
            paths.add(f"/hotels-near-{lm['slug']}/")
    return Sitemap(paths, "offline")
