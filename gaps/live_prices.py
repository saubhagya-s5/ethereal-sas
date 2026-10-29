"""Starting prices from Treebo's live public hotel pages (never from staging's placeholder prices).

Each hotel page carries schema.org Hotel JSON-LD with "priceRange": "₹1,200 - ₹3,500 per night".
We take the lower bound as price_from. Note: this band appears to be set per brand tier
(Itsy / Treebo / Premium), not per hotel; it is still the public "from" price AI tools read.
Live room rates come from /api/, which robots.txt disallows, so we don't call it.

Hotel URLs come from data/hotel_id_map.json (see gaps/hotel_map.py).
Results are cached in data/live_prices.json; hotels we can't fetch are left out.
"""
import json
import re
from datetime import date

from gaps import fetch
from gaps.config import LIVE_PRICES
from gaps.util import read_json, write_json


def _price_from(html):
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for d in nodes:
            types = d.get("@type") if isinstance(d, dict) else None
            types = types if isinstance(types, list) else [types]
            if "Hotel" in types and d.get("priceRange"):
                nums = [int(n.replace(",", "")) for n in re.findall(r"₹\s?([\d,]+)", d["priceRange"])]
                if nums:
                    return min(nums), d["priceRange"]
    return None, None


def load(hotels, id_map, refresh=False):
    """Return {hotel_id: {url, price_from, price_range, fetched}} for mapped hotels with a live price."""
    cache = read_json(LIVE_PRICES, {})
    for h in hotels:
        m = id_map.get(h["hotel_id"])
        if not m:
            continue
        if h["hotel_id"] in cache and cache[h["hotel_id"]]["url"] == m["url"] and not refresh:
            continue
        html = fetch.get(m["url"])
        price, band = _price_from(html) if html else (None, None)
        if price:
            cache[h["hotel_id"]] = {"url": m["url"], "price_from": price, "price_range": band,
                                    "fetched": date.today().isoformat()}
            write_json(LIVE_PRICES, cache)
        else:
            cache.pop(h["hotel_id"], None)
    ids = {h["hotel_id"] for h in hotels if h["hotel_id"] in id_map}
    return {k: v for k, v in cache.items() if k in ids and v["url"] == id_map[k]["url"]}
