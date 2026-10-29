"""Shared loaders for the generator and the app.

Reads results from output/. When a file is missing (Person A or B hasn't produced it yet),
falls back to the matching contracts/ sample so everything still runs.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output"
CONTRACTS = ROOT / "contracts"
INVENTORY = ROOT / "data" / "inventory.json"
LIVE_PRICES = ROOT / "data" / "live_prices.json"
BRIEFS = OUTPUT / "briefs"
PAGES = OUTPUT / "pages"
SIMULATION = OUTPUT / "simulation"

SITE = "https://www.treebo.com"
CITY_SLUGS = {"Pune": "pune", "Bengaluru": "bangalore"}
QUERY_PREFIX = {"pun": "Pune", "pune": "Pune", "blr": "Bengaluru"}
# City-amenity pages Treebo already has (dbcommon_cityamenitymapforautopagecreation).
CITY_AMENITY_PAGES = {"restaurant", "bar", "swimming-pool", "gym"}

# name -> (output file, contract sample)
SOURCES = {
    "city_scores": ("city_scores.json", "city_score.json"),
    "ai_results": ("ai_results.json", "ai_result.json"),
    "opportunities": ("opportunities.json", "opportunity.json"),
    "page_issues": ("page_issues.json", "page_issue.json"),
    "queries": ("queries.json", "query.json"),
}


def _read(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load(name: str) -> tuple[list[dict], bool]:
    """Return (rows, is_sample). is_sample is True when the contract fallback was used."""
    out_name, sample_name = SOURCES[name]
    path = OUTPUT / out_name
    if path.exists():
        rows, is_sample = _read(path), False
    else:
        rows, is_sample = _read(CONTRACTS / sample_name), True
    return (rows if isinstance(rows, list) else [rows]), is_sample


def inventory() -> dict[str, dict]:
    return {str(h["hotel_id"]): h for h in _read(INVENTORY)}


def live_prices() -> dict[str, dict]:
    """hotel_id -> {"price": float, "as_of": str|None} from data/live_prices.json.

    Accepts {"<hotel_id>": price} or a list of {hotel_id, price|price_from, as_of?}. Missing file -> {}.
    inventory.json's price_from is a staging placeholder (2000), so it is never shown on a page.
    """
    if not LIVE_PRICES.exists():
        return {}
    raw = _read(LIVE_PRICES)
    rows = [{"hotel_id": k, "price": v} for k, v in raw.items()] if isinstance(raw, dict) else raw
    prices = {}
    for r in rows:
        price = r.get("price", r.get("price_from"))
        if isinstance(price, (int, float)) and price > 0:
            prices[str(r["hotel_id"])] = {"price": float(price), "as_of": r.get("as_of")}
    return prices


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def label(slug: str) -> str:
    """'free-breakfast' -> 'Free Breakfast'."""
    return slug.replace("-", " ").title().replace("Wifi", "WiFi").replace("Ac ", "AC ")


def city_of_query(query_id: str, queries: list[dict] | None = None) -> str | None:
    for q in queries or []:
        if q["query_id"] == query_id:
            return q["city"]
    return QUERY_PREFIX.get(query_id.split("-")[0].lower())


def opportunity(opp_id: str) -> dict:
    for opp in load("opportunities")[0]:
        if opp["opp_id"] == opp_id:
            return opp
    raise KeyError(f"opportunity {opp_id!r} not found")


def page_hotels(opp: dict, inv: dict | None = None) -> list[dict]:
    """Active inventory hotels for an opportunity, best rated first. Inactive hotels never go on a page."""
    inv = inv or inventory()
    hotels = [inv[i] for i in map(str, opp["matching_hotel_ids"]) if i in inv and inv[i]["active"]]
    return sorted(hotels, key=lambda h: (-(h["rating"] or 0), h["name"]))


def related_results(opp: dict) -> tuple[list[dict], list[dict]]:
    """(queries, ai_results) about this opportunity's place or filter, in its city."""
    queries, _ = load("queries")
    results, _ = load("ai_results")
    place, filt = opp["place"].lower(), opp["filter"].lower()
    city_queries = [q for q in queries if q["city"] == opp["city"]]
    matched = [
        q for q in city_queries
        if (q["tags"].get("place") or "").lower() == place
        or filt in {(q["tags"].get("amenity") or "").lower(), (q["tags"].get("intent") or "").lower(),
                    f"{(q['tags'].get('intent') or '').lower()}-hotels"}
    ] or city_queries
    ids = {q["query_id"] for q in matched}
    return matched, [r for r in results if r["query_id"] in ids]
