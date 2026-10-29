"""Build data/inventory.json (Pune + Bengaluru) from the Treebo STAGING web DB.

Safety:
- Connection settings come from .env (STAGING_DB_*). Never hardcode credentials.
- Refuses any host that does not contain "staging" (or contains "prod").
- Session is forced read-only; only SELECT queries are run.

Staging is test data: room prices are placeholders (mostly 2000) and hotel URLs
are often empty. Test/junk hotels are filtered out below. Swap the source later
(Metabase / real export) without changing the output shape (contracts/hotel.json).

Usage: python data/export_inventory.py
"""
import json
import math
import os
import re
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "inventory.json"

# Treebo staging city ids (hotels_city). Bengaluru's hotels live under id 1, slug "bangalore".
CITIES = {
    "Pune": {"id": 14, "slug": "pune", "center": (18.5204, 73.8567)},
    "Bengaluru": {"id": 1, "slug": "bangalore", "center": (12.9716, 77.5946)},
}
MAX_KM_FROM_CENTER = 60
TEST_NAME = re.compile(r"test|automation|script\d|demo|tenant|house account|new\d|#|@|\bCS \d", re.I)
# Staging locality typos -> clean names. Junk values map to None.
LOCALITY_FIX = {
    "ITPL Whitefieldsss": "Whitefield",
    "ITPL Whitefields": "Whitefield",
    "HOYSALA NAAGR": "Hoysala Nagar",
    "abc": None,
}

HOTELS_SQL = """
select h.id, h.name, h.city_id, l.name as locality, h.latitude, h.longitude, h.status,
  (select min(nullif(r.price, 0)) from hotels_room r where r.hotel_id = h.id) as price_from,
  (select max(o.overall_rating) from trip_advisor_hotel_mapping m
     join trip_advisor_hotel_overall_ratings o on o.hotel_ta_mapping_id = m.id
    where m.hotel_id = h.id) as rating,
  (select array_agg(distinct f.name) from hotels_facility_hotels fh
     join hotels_facility f on f.id = fh.facility_id
    where fh.hotel_id = h.id and not f.is_deleted) as amenities,
  (select array_agg(distinct dc.name) from hotels_hotel_category hc
     join dbcommon_categories dc on dc.id = hc.categories_id
    where hc.hotel_id = h.id) as categories,
  (select s.hotel_canonical_url from hotels_seodetails s where s.hotel_id = h.id limit 1) as url
from hotels_hotel h
left join hotels_locality l on l.id = h.locality_id
where h.city_id = any(%s)
order by h.id
"""


def slugify(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def km(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def clean_locality(name, city_slug):
    if not name:
        return None
    name = name.split(",")[0]
    name = re.sub(r"\b(bangalore|bengaluru|pune)\b", "", name, flags=re.I).strip()
    name = LOCALITY_FIX.get(name, name)
    if not name or re.search(r"\d", name):
        return None
    return name


def connect():
    load_dotenv(ROOT / ".env")
    host = os.getenv("STAGING_DB_HOST", "")
    if "staging" not in host.lower() or "prod" in host.lower():
        sys.exit(f"Refusing to connect: STAGING_DB_HOST={host!r} is not a staging host.")
    return psycopg2.connect(
        host=host,
        port=os.getenv("STAGING_DB_PORT", "5432"),
        dbname=os.getenv("STAGING_DB_NAME"),
        user=os.getenv("STAGING_DB_USER"),
        password=os.getenv("STAGING_DB_PASSWORD"),
        options="-c default_transaction_read_only=on",
        connect_timeout=15,
    )


def main():
    by_id = {c["id"]: (name, c) for name, c in CITIES.items()}
    with connect() as conn, conn.cursor() as cur:
        cur.execute(HOTELS_SQL, (list(by_id),))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    hotels, skipped = [], 0
    for r in rows:
        city, cfg = by_id[r["city_id"]]
        lat, lng = float(r["latitude"] or 0), float(r["longitude"] or 0)
        if TEST_NAME.search(r["name"]) or km((lat, lng), cfg["center"]) > MAX_KM_FROM_CENTER:
            skipped += 1
            continue
        hotels.append({
            "hotel_id": str(r["id"]),
            "name": r["name"].strip(),
            "city": city,
            "locality": clean_locality(r["locality"], cfg["slug"]),
            "lat": round(lat, 6),
            "lng": round(lng, 6),
            "price_from": float(r["price_from"]) if r["price_from"] else None,
            "rating": float(r["rating"]) if r["rating"] else None,
            "amenities": sorted({slugify(a) for a in r["amenities"] or []}),
            "categories": sorted({slugify(c) for c in r["categories"] or []}),
            "active": r["status"] == 1,
            # Staging canonical URLs are mostly empty; fall back to Hotel.get_slugified_url
            # (treebo/webapp/dbcommon/models/hotel.py:210-212).
            "url": r["url"] or f"https://www.treebo.com/hotels-in-{cfg['slug']}/{slugify(r['name'])}-{r['id']}/",
        })

    OUT.write_text(json.dumps(hotels, indent=2, ensure_ascii=False))
    active = sum(h["active"] for h in hotels)
    print(f"Wrote {len(hotels)} hotels ({active} active) to {OUT}; skipped {skipped} test/invalid rows.")


if __name__ == "__main__":
    main()
