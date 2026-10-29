"""Shared settings for the gaps module."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"

INVENTORY = DATA / "inventory.json"
PLACES = DATA / "places.json"
SITEMAP_DIR = DATA / "sitemaps"
COMPETITOR_CSV = DATA / "competitor_urls.csv"            # real dataset (drop-in, takes priority)
COMPETITOR_FETCHED = DATA / "competitor_urls_fetched.csv"  # from FabHotels/OYO public sitemaps
COMPETITOR_SAMPLE = DATA / "competitor_urls_sample.csv"    # hand-made fallback
DEMAND_WEIGHTS = DATA / "demand_weights.json"
KEYWORD_VOLUMES = DATA / "keyword_volumes.csv"             # real volumes (drop-in, takes priority)
LIVE_PRICES = DATA / "live_prices.json"
HOTEL_ID_MAP = DATA / "hotel_id_map.json"
PAGE_SNAPSHOTS = DATA / "page_snapshots.json"
AI_RESULTS = OUTPUT / "ai_results.json"
QUERIES = OUTPUT / "queries.json"

OPPORTUNITIES_OUT = OUTPUT / "opportunities.json"
PAGE_ISSUES_OUT = OUTPUT / "page_issues.json"
NOTES_OUT = OUTPUT / "gaps_notes.json"

TREEBO = "https://www.treebo.com"
USER_AGENT = "Mozilla/5.0 (compatible; TreeboAEOAgent/0.1; hackathon research)"
REQUEST_DELAY_S = 2.0

# CLI name -> inventory city name, Treebo city slug, staging city id
CITIES = {
    "pune": {"name": "Pune", "slug": "pune", "staging_id": 14},
    "bengaluru": {"name": "Bengaluru", "slug": "bangalore", "staging_id": 1},
}

MATCH_RADIUS_KM = 2.5
MIN_HOTELS = {"city": 5, "locality": 2, "landmark": 2}

# Filters never proposed as new pages, with the reason (recorded in output/gaps_notes.json).
EXCLUDED_FILTERS = {
    "free-breakfast": "Treebo deliberately excludes Free Breakfast from amenity pages "
                      "(treebo/webapp/common/constants/common_constants.py:285). Checked as a page issue instead "
                      "(breakfast_not_stated).",
}
BREAKFAST_SHARE = 0.5  # flag a page if more than this share of its hotels include breakfast

# Amenity filters we consider (inventory slugs). Treebo has city-level pages only for the first four.
AMENITY_FILTERS = [
    "restaurant", "bar", "swimming-pool", "gym", "parking", "banquet-hall", "conference-hall",
    "kitchenette", "balcony", "spa",
]
