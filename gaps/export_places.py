"""Export locality + landmark coordinates for Pune and Bengaluru to data/places.json.

READ-ONLY: connects to the Treebo STAGING DB (STAGING_DB_* in .env) in a read-only
session and runs SELECTs only. Refuses non-staging hosts. Nothing is written to any DB.

Slugs follow Treebo's URL builders (docs/treebo_findings.md):
  locality: slugify(free_text_url) or slugify(name)-{city.slug}
  landmark: slugify(free_text_url) or slugify(seo_url_name)-{city.slug}

Usage: python -m gaps.export_places
"""
import os
import sys

import psycopg2
from dotenv import load_dotenv

from gaps.config import CITIES, PLACES, ROOT
from gaps.util import slugify, write_json

SQL = """
select 'locality' as type, l.name, l.free_text_url, null as seo_url_name, l.latitude, l.longitude,
       l.is_popular, c.slug as city_slug, l.city_id
  from hotels_locality l join hotels_city c on c.id = l.city_id
 where l.city_id = any(%(ids)s)
union all
select 'landmark', s.search_label, s.free_text_url, s.seo_url_name, s.latitude, s.longitude,
       s.is_popular, c.slug, s.city_id
  from search_landmark s join hotels_city c on c.id = s.city_id
 where s.city_id = any(%(ids)s)
"""


def main():
    load_dotenv(ROOT / ".env")
    host = os.getenv("STAGING_DB_HOST", "")
    if "staging" not in host.lower() or "prod" in host.lower():
        sys.exit(f"Refusing to connect: STAGING_DB_HOST={host!r} is not a staging host.")
    conn = psycopg2.connect(
        host=host, port=os.getenv("STAGING_DB_PORT", "5432"), dbname=os.getenv("STAGING_DB_NAME"),
        user=os.getenv("STAGING_DB_USER"), password=os.getenv("STAGING_DB_PASSWORD"),
        options="-c default_transaction_read_only=on", connect_timeout=15,
    )
    conn.set_session(readonly=True)
    by_id = {c["staging_id"]: c["name"] for c in CITIES.values()}
    with conn, conn.cursor() as cur:
        cur.execute(SQL, {"ids": list(by_id)})
        rows = cur.fetchall()
    conn.close()

    places = []
    for typ, name, free_text, seo_name, lat, lng, popular, city_slug, city_id in rows:
        if free_text:
            slug = slugify(free_text)
        else:
            slug = f"{slugify(seo_name or name)}-{city_slug}"
        places.append({
            "city": by_id[city_id], "type": typ, "name": (name or "").strip(), "slug": slug,
            "lat": float(lat) if lat else None, "lng": float(lng) if lng else None,
            "is_popular": bool(popular),
        })
    write_json(PLACES, places)
    print(f"Wrote {len(places)} places to {PLACES}")


if __name__ == "__main__":
    main()
