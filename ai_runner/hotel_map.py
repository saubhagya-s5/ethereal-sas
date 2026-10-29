"""Build data/hotel_id_map.json: production truth for every Treebo hotel in our cities.

Covers every staging hotel_id plus every hotel on treebo.com's hotel sitemap for the city
(staging is missing most live hotels). Staging names, active flags and prices are unreliable
(e.g. hotels churned in production still show active: true in staging). Hotel ids are shared
with production, so each id is looked up on Treebo's public hotel details API. Shape:
{
  "generated_at": "...", "source": "https://www.treebo.com/api/v2/hotels/{id}/details/",
  "hotels": {
    "444": {"prod_hotel_id": "444", "found": true, "name": "Treebo Edha Suites Koramangala",
            "active": true, "city": "Bengaluru", "locality": "Koramangala",
            "lat": 12.9382, "lng": 77.6306, "url": "https://www.treebo.com/hotels-in-bangalore/...-444/",
            "facilities": ["elevator", ...],             # as named on treebo.com
            "amenities": ["free-breakfast", "gym", ...]}, # hotel + room facilities, as slugs
    "9999": {"prod_hotel_id": null, "found": false}       # no such hotel in production
  }
}

Run on its own: python -m ai_runner.hotel_map --city bengaluru
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .cache import read_json, write_json
from .config import CITIES, HOTEL_MAP_MAX_AGE_HOURS, HOTEL_MAP_PATH, TREEBO_BASE, City, get_city
from .inventory import slugify

API = f"{TREEBO_BASE}/api/v2/hotels/{{id}}/details/"
WORKERS = 2
PAUSE_S = 0.5
PROD_CITY_IDS = {1: "Bengaluru", 14: "Pune"}


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _get_details(hotel_id: str) -> dict | None:
    """Details payload, or None if the id doesn't exist. Backs off on 429 / 5xx."""
    req = urllib.request.Request(API.format(id=hotel_id), headers={"User-Agent": "Mozilla/5.0 (treebo-aeo-agent)"})
    for attempt in range(6):
        time.sleep(PAUSE_S)  # stay polite: the site rate-limits bursts
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return (json.loads(r.read().decode("utf-8")) or {}).get("data") or {}
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if e.code != 429 and e.code < 500:
                raise
            wait = float(e.headers.get("Retry-After") or 0) or min(60, 5 * 2 ** attempt)
            time.sleep(wait)
    raise RuntimeError(f"hotel {hotel_id}: still rate-limited after retries")


def _lookup(hotel_id: str) -> dict:
    d = _get_details(hotel_id)
    if d is None:
        return {"prod_hotel_id": None, "found": False}
    if not d.get("id"):
        return {"prod_hotel_id": None, "found": False}
    addr = d.get("address") or {}
    city = PROD_CITY_IDS.get(addr.get("city_id"), addr.get("city"))
    city_slug = next((c.slug for c in CITIES.values() if c.name == city), slugify(addr.get("city") or ""))
    coords = d.get("coordinates") or {}
    return {
        "prod_hotel_id": str(d["id"]),
        "found": True,
        "name": d.get("name"),
        "active": bool(d.get("is_active")),
        "city": city,
        "locality": addr.get("locality") or None,
        "lat": _float(coords.get("lat")),
        "lng": _float(coords.get("lng")),
        "url": f"{TREEBO_BASE}/hotels-in-{city_slug}/{slugify(d.get('name') or '')}-{d['id']}/",
        "facilities": [f.get("name") for f in d.get("facilities") or [] if f.get("name")],
        "amenities": _amenities(d),
    }


# Production facility slugs that differ from the staging amenity slugs.
FACILITY_ALIASES = {"fridge": "mini-fridge", "wifi": "free-wifi", "complimentary-breakfast": "free-breakfast"}


def _amenities(d: dict) -> list[str]:
    """Hotel facilities plus room facilities that are available, as staging-style slugs."""
    names = [f.get("name") for f in d.get("facilities") or []]
    names += [f.get("name") for r in d.get("rooms") or [] for f in r.get("facilities") or [] if f.get("availability")]
    slugs = {FACILITY_ALIASES.get(slugify(n), slugify(n)) for n in names if n}
    return sorted(slugs)


def city_hotel_ids(city: City, hotel_pages=None) -> list[str]:
    """Staging ids for the city plus every hotel id on treebo.com's hotel sitemap for it."""
    from .inventory import Inventory

    ids = [h["hotel_id"] for h in Inventory.load(apply_map=False).city(city.name)]
    if hotel_pages is not None:
        ids += [i for i in hotel_pages.ids(city.slug) if i not in ids]
    return ids


def load_hotel_map(path: Path = HOTEL_MAP_PATH) -> dict:
    return read_json(path).get("hotels", {}) if path.exists() else {}


def ensure_hotel_map(hotel_ids: list[str], path: Path = HOTEL_MAP_PATH, force: bool = False) -> dict:
    """Look up any ids missing from the map (or all of them when the file is stale)."""
    data = read_json(path) if path.exists() else {"hotels": {}}
    stale = True
    if data.get("generated_at"):
        stale = datetime.now(timezone.utc) - datetime.fromisoformat(data["generated_at"]) > timedelta(hours=HOTEL_MAP_MAX_AGE_HOURS)
    todo = hotel_ids if (force or stale) else [i for i in hotel_ids if i not in data["hotels"]]
    if not todo:
        return data["hotels"]
    failed = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(_lookup, hid): hid for hid in todo}
        for i, fut in enumerate(as_completed(futures), start=1):
            hid = futures[fut]
            try:
                data["hotels"][hid] = fut.result()
            except Exception as e:
                failed.append(hid)
                print(f"  hotel {hid}: lookup failed ({e})", flush=True)
            if i % 20 == 0:
                write_json(path, data)  # keep progress if the run is interrupted
                print(f"  {i}/{len(todo)} hotels looked up", flush=True)
    # Only mark the map fresh when every lookup worked; failed ids are retried next run.
    if not failed:
        data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["source"] = API
    write_json(path, data)
    return data["hotels"]


def main() -> None:
    from .inventory import Inventory
    from .sitemap import load_hotel_pages

    ap = argparse.ArgumentParser(description="Map staging + treebo.com hotel ids to production truth")
    ap.add_argument("--city", default="bengaluru")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    city = get_city(args.city)
    staging = Inventory.load(apply_map=False)
    staging_ids = {h["hotel_id"] for h in staging.city(city.name)}
    ids = city_hotel_ids(city, load_hotel_pages())
    hmap = ensure_hotel_map(ids, force=args.force)
    rows = [hmap[i] for i in ids]
    flipped = sum(1 for i in staging_ids if hmap[i].get("found") and hmap[i]["active"] != staging.by_id[i]["active"])
    print(f"{city.name}: {len(ids)} hotel ids ({len(staging_ids)} staging, {len(ids) - len(staging_ids)} only on treebo.com), "
          f"{sum(r.get('active', False) for r in rows)} active in production; "
          f"{flipped} staging active flags were wrong -> {HOTEL_MAP_PATH}")


if __name__ == "__main__":
    main()
