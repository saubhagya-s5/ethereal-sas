"""Map inventory hotel_ids (staging) to live treebo.com hotel URLs.

sitemap-hotel.xml is the source of truth for which hotels exist (it lists enabled hotels only).
Live URL: /hotels-in-{city}/{name}-{locality}-{live_id}/

Match order, within the same city:
  1. slug   : same id AND the live slug contains the hotel's core name slug
  2. slug   : live slug contains the core name slug (id differs)
  3. fuzzy  : difflib ratio of the core name vs the start of the live slug >= 0.85,
              and the hotel's locality appears in the live slug
Writes data/hotel_id_map.json: {hotel_id: {url, live_id, method}}; unmatched hotels are omitted.

Usage: python -m gaps.hotel_map
"""
import re
from difflib import SequenceMatcher

from gaps import sitemaps
from gaps.config import CITIES, HOTEL_ID_MAP, INVENTORY
from gaps.util import read_json, slugify, write_json

BRAND = re.compile(r"^(treebo|itsy|itsy hotels)( trend| trip| premium| edge| select| tryst)?\s+", re.I)
FUZZY_MIN = 0.85


def core_name(name):
    return slugify(BRAND.sub("", name.strip()))


def live_hotels(hotel_urls, city_slug):
    out = []
    for u in hotel_urls:
        m = re.search(rf"/hotels-in-{city_slug}/([a-z0-9-]+)-(\d+)/?$", u)
        if m:
            out.append({"url": u, "slug": m.group(1), "live_id": m.group(2)})
    return out


def _live_core(slug):
    return re.sub(r"^(treebo|itsy-hotels|itsy)-((trend|trip|premium|edge|select|tryst)-)?", "", slug)


def match(hotel, live):
    core = core_name(hotel["name"])
    locality = slugify(hotel["locality"] or "")
    for lh in live:
        if lh["live_id"] == hotel["hotel_id"] and core and core in lh["slug"]:
            return lh, "slug_and_id"
    # Name must start the live slug (after the brand) and not just be a locality, e.g. "Treebo Viman Nagar".
    if core and len(core) > 3 and core != locality and core not in {slugify(l) for l in KNOWN_LOCALITIES}:
        for lh in live:
            lc = _live_core(lh["slug"])
            if lc == core or lc.startswith(core + "-"):
                return lh, "slug"
    # Fuzzy: compare the name only (locality words inflate similarity), and require the locality in the URL.
    best, best_r = None, 0.0
    for lh in live:
        if not core or (locality and locality not in lh["slug"]):
            continue
        lc = _live_core(lh["slug"])
        r = SequenceMatcher(None, core, lc[: len(core)]).ratio()
        if r > best_r:
            best, best_r = lh, r
    if best and best_r >= FUZZY_MIN:
        return best, f"fuzzy:{best_r:.2f}"
    return None, None


KNOWN_LOCALITIES = set()


def build(hotels, hotel_urls):
    KNOWN_LOCALITIES.update(h["locality"] for h in hotels if h["locality"])
    result, used = {}, set()
    for cfg in CITIES.values():
        live = live_hotels(hotel_urls, cfg["slug"])
        for h in [h for h in hotels if h["city"] == cfg["name"]]:
            lh, method = match(h, [l for l in live if l["url"] not in used])
            if lh:
                used.add(lh["url"])
                result[h["hotel_id"]] = {"url": lh["url"], "live_id": lh["live_id"], "method": method}
    return result


def load(hotels=None, hotel_urls=None, refresh=False):
    cached = read_json(HOTEL_ID_MAP)
    if cached is not None and not refresh:
        return cached
    result = build(hotels or read_json(INVENTORY), hotel_urls or sitemaps.load()["hotel"])
    write_json(HOTEL_ID_MAP, result)
    return result


def report(hotels, hotel_urls, id_map):
    lines = []
    for cfg in CITIES.values():
        inv = [h for h in hotels if h["city"] == cfg["name"]]
        act = [h for h in inv if h["active"]]
        live = live_hotels(hotel_urls, cfg["slug"])
        mapped = [h for h in inv if h["hotel_id"] in id_map]
        mapped_act = [h for h in act if h["hotel_id"] in id_map]
        mapped_live = {id_map[h["hotel_id"]]["url"] for h in mapped}
        lines.append({
            "city": cfg["name"],
            "live_hotels_in_sitemap": len(live),
            "inventory_hotels": len(inv),
            "inventory_active": len(act),
            "matched": len(mapped),
            "matched_active": len(mapped_act),
            "match_rate_all": round(len(mapped) / len(inv), 3) if inv else 0,
            "match_rate_active": round(len(mapped_act) / len(act), 3) if act else 0,
            "live_hotels_missing_from_inventory": len(live) - len(mapped_live),
            "inactive_in_staging_but_live": sum(1 for h in mapped if not h["active"]),
        })
    return lines


if __name__ == "__main__":
    import json
    hotels = read_json(INVENTORY)
    urls = sitemaps.load()["hotel"]
    m = load(hotels, urls, refresh=True)
    print(json.dumps(report(hotels, urls, m), indent=2))
