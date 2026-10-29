"""Build data/live_prices.json from Treebo's public pricing API.

Staging prices in inventory.json are placeholders (₹2000), so price claims are checked
against this file instead. Shape:
{
  "generated_at": "...", "source": "<api url>", "checkin": "YYYY-MM-DD", "checkout": "...",
  "roomconfig": "1-0", "currency": "INR",
  "prices": {"444": {"city": "Bengaluru", "pretax_price": 8451.84, "sell_price": 9973.18,
                     "rack_rate": 8804.0, "room_type": "OAK", "free_breakfast": true}},
  "unavailable": {"22": "Bengaluru"}   # no sellable price returned for these dates
}

Run on its own: python -m ai_runner.live_prices --city pune
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.parse
import urllib.request
import zlib
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .cache import read_json, write_json
from .config import (
    LIVE_PRICE_DAYS_AHEAD,
    LIVE_PRICE_MAX_AGE_HOURS,
    LIVE_PRICES_PATH,
    TREEBO_BASE,
    get_city,
)
from .inventory import Inventory

API = f"{TREEBO_BASE}/api/v8/pricing/hotels/"
CHUNK = 20


def _fetch(ids: list[str], checkin: str, checkout: str) -> dict:
    qs = urllib.parse.urlencode({"hotel_id": ",".join(ids), "checkin": checkin, "checkout": checkout, "roomconfig": "1-0"})
    req = urllib.request.Request(f"{API}?{qs}", headers={"User-Agent": "Mozilla/5.0 (treebo-aeo-agent)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read().decode("utf-8"))
    return body.get("data") or {}


def _fetch_chunk(ids: list[str], checkin: str, checkout: str) -> dict:
    """The API rejects a whole batch (400) if any id isn't sellable; then retry one by one."""
    try:
        return _fetch(ids, checkin, checkout)
    except urllib.error.HTTPError as e:
        if e.code != 400:
            raise
    out = {}
    for hid in ids:
        try:
            out.update(_fetch([hid], checkin, checkout))
        except urllib.error.HTTPError as e:
            if e.code != 400:
                raise
    return out


def _num(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _load(path: Path) -> dict:
    if path.exists():
        return read_json(path)
    return {"prices": {}, "unavailable": {}}


def _is_fresh(data: dict, hotels: list[dict]) -> bool:
    ts = data.get("generated_at")
    if not ts:
        return False
    age = datetime.now(timezone.utc) - datetime.fromisoformat(ts)
    if age > timedelta(hours=LIVE_PRICE_MAX_AGE_HOURS):
        return False
    known = set(data.get("prices", {})) | set(data.get("unavailable", {}))
    return all(h["hotel_id"] in known for h in hotels)


def ensure_live_prices(city_name: str, inv: Inventory, path: Path = LIVE_PRICES_PATH, force: bool = False) -> dict:
    """Fetch live prices for the city's active hotels unless a fresh file already covers them."""
    hotels = inv.city(city_name, active_only=True)
    data = _load(path)
    if not force and _is_fresh(data, hotels):
        return data
    checkin = date.today() + timedelta(days=LIVE_PRICE_DAYS_AHEAD)
    checkout = checkin + timedelta(days=1)
    if data.get("checkin") != checkin.isoformat():
        # Different stay dates: prices from other cities are no longer comparable.
        data = {"prices": {}, "unavailable": {}}
    ids = [h["hotel_id"] for h in hotels]
    # Drop this city's hotels that are no longer active in production.
    for bucket in ("prices", "unavailable"):
        for hid in [k for k, v in data[bucket].items() if (v.get("city") if isinstance(v, dict) else v) == city_name]:
            if hid not in ids:
                del data[bucket][hid]
    for i in range(0, len(ids), CHUNK):
        chunk = ids[i:i + CHUNK]
        got = _fetch_chunk(chunk, checkin.isoformat(), checkout.isoformat())
        for hid in chunk:
            row = got.get(hid)
            if not row:
                data["unavailable"][hid] = city_name
                data["prices"].pop(hid, None)
                continue
            data["unavailable"].pop(hid, None)
            data["prices"][hid] = {
                "city": city_name,
                "pretax_price": _num(row.get("pretax_price")),
                "sell_price": _num(row.get("sell_price")),
                "rack_rate": _num(row.get("rack_rate")),
                "room_type": (row.get("cheapest_room") or {}).get("room_type"),
                "free_breakfast": row.get("free_breakfast"),
            }
    data.update({
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": API,
        "checkin": checkin.isoformat(),
        "checkout": checkout.isoformat(),
        "roomconfig": "1-0",
        "currency": "INR",
    })
    write_json(path, data)
    return data


def fake_live_prices(city_name: str, inv: Inventory) -> dict:
    """Dry-run stand-in: deterministic made-up prices. Never written to data/."""
    prices = {}
    for h in inv.city(city_name, active_only=True):
        base = 1200 + zlib.crc32(h["hotel_id"].encode()) % 1800
        prices[h["hotel_id"]] = {"city": city_name, "pretax_price": float(base), "sell_price": round(base * 1.12, 2)}
    return {"source": "dry-run", "checkin": None, "prices": prices, "unavailable": {}}


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch live Treebo prices into data/live_prices.json")
    ap.add_argument("--city", default="bengaluru")
    ap.add_argument("--force", action="store_true", help="refetch even if the file is fresh")
    args = ap.parse_args()
    city = get_city(args.city)
    data = ensure_live_prices(city.name, Inventory.load(), force=args.force)
    n = sum(1 for p in data["prices"].values() if p.get("city") == city.name)
    u = sum(1 for c in data["unavailable"].values() if c == city.name)
    print(f"{city.name}: {n} hotels priced, {u} unavailable for {data['checkin']} -> {LIVE_PRICES_PATH}")


if __name__ == "__main__":
    main()
