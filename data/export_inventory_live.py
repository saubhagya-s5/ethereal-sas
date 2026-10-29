"""Build data/inventory.json and data/places.json for Pune + Bengaluru from treebo.com only.

Sources (public pages and sitemaps; /api/ is disallowed by robots.txt so it is not used):
  hotels      every Pune/Bengaluru URL in sitemap-hotel.xml (=> active: true)
              fields from each hotel page's schema.org Hotel JSON-LD:
              name, addressLocality, geo, priceRange (lower bound), aggregateRating, amenityFeature
  categories  city category pages (/budget-hotels-in-pune/ ...): a hotel gets the category if listed
  places      localities/landmarks from the live sitemaps. Page coordinates are fetched only for places
              "near hotels": name matches a hotel's addressLocality, or the place slug / hotel street
              address shares the locality name. Others fall back to the average of hotels whose
              addressLocality matches, else none.

Politeness: robots.txt respected, fixed delay, every raw page cached in data/raw_pages/ and never
re-fetched. Stops after 3 consecutive blocked/error responses.

Usage: python data/export_inventory_live.py
"""
import hashlib
import json
import re
import sys
import time
import urllib.robotparser
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw_pages"
SITEMAPS = DATA / "sitemaps"
TREEBO = "https://www.treebo.com"
UA = "Mozilla/5.0 (compatible; TreeboAEOAgent/0.1; hackathon research)"
DELAY_S = 1.5
CITIES = {"Pune": "pune", "Bengaluru": "bangalore"}

client = httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True)
robots = urllib.robotparser.RobotFileParser()
robots.parse(client.get(TREEBO + "/robots.txt").text.splitlines())
state = {"last": 0.0, "fails": 0, "fetched": 0}


class Blocked(Exception):
    pass


def get(url):
    """Cached GET. Returns HTML or None (404 etc.). Raises Blocked after 3 consecutive bad responses."""
    path = RAW / (hashlib.sha1(url.encode()).hexdigest() + ".html")
    if path.exists():
        return path.read_text() or None
    if not robots.can_fetch(UA, url):
        return None
    wait = DELAY_S - (time.time() - state["last"])
    if wait > 0:
        time.sleep(wait)
    state["last"] = time.time()
    r = client.get(url)
    state["fetched"] += 1
    bad = r.status_code in (403, 429) or r.status_code >= 500 or "captcha" in r.text[:5000].lower()
    if bad:
        state["fails"] += 1
        if state["fails"] >= 3:
            raise Blocked(f"HTTP {r.status_code} on {url}")
        return None
    state["fails"] = 0
    RAW.mkdir(parents=True, exist_ok=True)
    path.write_text(r.text if r.status_code == 200 else "")
    return r.text if r.status_code == 200 else None


def slugify(t):
    return re.sub(r"[^a-z0-9]+", "-", (t or "").lower()).strip("-")


def locs(name):
    return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", (SITEMAPS / f"{name}.xml").read_text())


def jsonld_hotel(html):
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data])
        for d in nodes:
            t = d.get("@type")
            if "Hotel" in (t if isinstance(t, list) else [t]):
                return d
    return None


def initial_state(html):
    i = html.find("window.__INITIAL_STATE__")
    if i < 0:
        return {}
    try:
        return json.JSONDecoder().raw_decode(html[html.find("=", i) + 1:].lstrip())[0]
    except ValueError:
        return {}


def listed_ids(html):
    i = html.find("window.__APOLLO_STATE__")
    if i < 0:
        return set()
    try:
        a = json.JSONDecoder().raw_decode(html[html.find("=", i) + 1:].lstrip())[0]
    except ValueError:
        return set()
    return {k.split(":")[1] for k in a if re.match(r"^HotelSearchItem:\d+$", k)}


def build_hotels():
    hotels, missing = [], []
    for city, cslug in CITIES.items():
        urls = [u for u in locs("hotel") if re.search(rf"/hotels-in-{cslug}/[a-z0-9-]+-\d+/?$", u)]
        print(f"{city}: {len(urls)} hotel pages")
        for u in urls:
            html = get(u)
            d = jsonld_hotel(html) if html else None
            if not d:
                missing.append(u)
                continue
            addr = d.get("address") or {}
            if isinstance(addr, str):
                addr = json.loads(addr)
            geo = d.get("geo") or {}
            amen = d.get("amenityFeature") or []
            if isinstance(amen, str):
                amen = json.loads(amen)
            rating = (d.get("aggregateRating") or {}).get("ratingValue")
            prices = [int(n.replace(",", "")) for n in re.findall(r"₹\s?([\d,]+)", d.get("priceRange") or "")]
            hotels.append({
                "hotel_id": re.search(r"-(\d+)/?$", u).group(1),
                "name": d.get("name", "").strip(),
                "city": city,
                "locality": (addr.get("addressLocality") or "").strip() or None,
                "lat": geo.get("latitude"),
                "lng": geo.get("longitude"),
                "price_from": float(min(prices)) if prices else None,
                "rating": float(rating) if rating else None,
                "amenities": sorted({slugify(a["name"]) for a in amen if a.get("value") and a.get("name")}),
                "categories": [],
                "active": True,
                "url": u,
                "_street": addr.get("streetAddress") or "",
            })
    return hotels, missing


def add_categories(hotels):
    by_id = {h["hotel_id"]: h for h in hotels}
    for city, cslug in CITIES.items():
        for u in locs("category"):
            m = re.match(rf"^{TREEBO}/(.+)-in-{cslug}/?$", u)
            if not m:
                continue
            cat = {"near-city-railway-station": "near-railway-station",
                   "near-the-airport": "near-airport"}.get(m.group(1), m.group(1))
            html = get(u)
            for hid in listed_ids(html) if html else []:
                if hid in by_id and by_id[hid]["city"] == city and cat not in by_id[hid]["categories"]:
                    by_id[hid]["categories"].append(cat)
    for h in hotels:
        h["categories"].sort()


def build_places(hotels):
    places = []
    for city, cslug in CITIES.items():
        ch = [h for h in hotels if h["city"] == city]
        loc_slugs = {slugify(h["locality"]) for h in ch if h["locality"]}
        street = " ".join(slugify(h["_street"]) for h in ch)
        for ptype, sm, prefix in (("locality", "locality", "hotels-in-"), ("landmark", "landmark", "hotels-near-")):
            for u in locs(sm):
                m = re.match(rf"^{TREEBO}/{prefix}(.+)-{cslug}/?$", u)
                if not m or m.group(1) == "me":
                    continue
                base = m.group(1)
                name_match = base in loc_slugs
                near = name_match or any(ls and (f"-{ls}" in f"-{base}-" or base == ls) for ls in loc_slugs) \
                    or (len(base) > 4 and base in street)
                lat = lng = None
                method = "none"
                if near:
                    html = get(u)
                    c = (initial_state(html).get("content", {}).get("seo", {}).get("content", {})
                         .get("coordinates") or {}) if html else {}
                    if c.get("lat") and c.get("lng"):
                        lat, lng, method = c["lat"], c["lng"], "page"
                if method == "none":
                    hs = [h for h in ch if h["locality"] and slugify(h["locality"]) == base and h["lat"]]
                    if hs:
                        lat = sum(h["lat"] for h in hs) / len(hs)
                        lng = sum(h["lng"] for h in hs) / len(hs)
                        method = "hotel_average"
                name = next((h["locality"] for h in ch if h["locality"] and slugify(h["locality"]) == base),
                            base.replace("-", " ").title())
                places.append({"city": city, "type": ptype, "name": name,
                               "slug": f"{base}-{cslug}", "lat": lat, "lng": lng,
                               "is_popular": name_match, "coord_method": method})
    return places


def report(hotels, places, missing):
    rep = {}
    for city in CITIES:
        ch = [h for h in hotels if h["city"] == city]
        cp = [p for p in places if p["city"] == city]
        rep[city] = {
            "hotels": len(ch),
            "with_coordinates": sum(1 for h in ch if h["lat"] and h["lng"]),
            "with_price_from": sum(1 for h in ch if h["price_from"]),
            "with_amenities": sum(1 for h in ch if h["amenities"]),
            "with_rating": sum(1 for h in ch if h["rating"]),
            "with_locality": sum(1 for h in ch if h["locality"]),
            "with_categories": sum(1 for h in ch if h["categories"]),
            "places": len(cp),
            "place_coord_method": {m: sum(1 for p in cp if p["coord_method"] == m)
                                   for m in ("page", "hotel_average", "none")},
        }
    rep["hotel_pages_without_jsonld"] = missing
    return rep


def main():
    try:
        hotels, missing = build_hotels()
        add_categories(hotels)
        places = build_places(hotels)
    except Blocked as e:
        sys.exit(f"STOPPED: treebo.com appears to be blocking requests ({e}). Cached pages are kept.")
    rep = report(hotels, places, missing)
    for h in hotels:
        h.pop("_street")
    (DATA / "inventory.json").write_text(json.dumps(hotels, indent=2, ensure_ascii=False))
    (DATA / "places.json").write_text(json.dumps(places, indent=2, ensure_ascii=False))
    notes = {
        "source": "treebo.com public sitemaps + hotel/category/place pages (no staging, no API)",
        "active": "true for every hotel in sitemap-hotel.xml (Treebo lists only enabled hotels there)",
        "hotel_id": "live treebo.com hotel id (last number in the hotel URL)",
        "price_from": "Lower bound of the hotel page's schema.org priceRange. It is a brand-tier band "
                      "(e.g. Itsy 999, Treebo 1200, Premium 1300), not an exact room price. Pages and "
                      "fact-checks must say 'from ₹X', never an exact nightly rate.",
        "rating": "schema.org aggregateRating.ratingValue on the hotel page",
        "amenities": "schema.org amenityFeature with value=true, slugified",
        "categories": "hotel listed on the live city category page (e.g. /budget-hotels-in-pune/)",
        "places": "localities/landmarks from live sitemaps; coord_method = page | hotel_average | none",
        "report": rep,
    }
    (DATA / "inventory_notes.json").write_text(json.dumps(notes, indent=2, ensure_ascii=False))
    print(json.dumps(rep, indent=2))
    print(f"fetched {state['fetched']} new pages")


if __name__ == "__main__":
    main()
