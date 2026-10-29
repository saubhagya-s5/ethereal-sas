"""Hand-picked landmarks per city, used only in question text and for "near X" hotel matching.

inventory.json has no landmark data. Slugs follow Treebo's landmark pages
(/hotels-near-{slug}/); most were taken from sitemap-landmark.xml, and a few are
deliberately ones Treebo has no page for. Coordinates are approximate public locations.
These are never used as hotel facts.
"""
from __future__ import annotations

LANDMARKS = {
    "Bengaluru": [
        {"name": "Kempegowda International Airport", "slug": "kempegowda-international-airport-bangalore", "lat": 13.1986, "lng": 77.7066},
        {"name": "Manyata Tech Park", "slug": "manyata-tech-park-bangalore", "lat": 13.0477, "lng": 77.6210},
        {"name": "Majestic Bus Stand", "slug": "majestic-bus-stand-bangalore", "lat": 12.9767, "lng": 77.5713},
        {"name": "Cubbon Park", "slug": "cubbon-park-bangalore", "lat": 12.9763, "lng": 77.5929},
        {"name": "NIMHANS Hospital", "slug": "nimhans-hospital-bangalore", "lat": 12.9406, "lng": 77.5960},
        {"name": "Electronic City", "slug": "electronic-city-bangalore", "lat": 12.8452, "lng": 77.6602},
        {"name": "Phoenix Marketcity Whitefield", "slug": "phoenix-mall-bangalore", "lat": 12.9975, "lng": 77.6966},
    ],
    "Pune": [
        {"name": "Pune Airport", "slug": "pune-airport-pune", "lat": 18.5793, "lng": 73.9089},
        {"name": "Pune Railway Station", "slug": "pune-railway-station-pune", "lat": 18.5289, "lng": 73.8744},
        {"name": "Hinjewadi IT Park", "slug": "hinjewadi-it-park-pune", "lat": 18.5912, "lng": 73.7389},
        {"name": "EON IT Park Kharadi", "slug": "eon-it-park-pune", "lat": 18.5515, "lng": 73.9476},
        {"name": "Shaniwar Wada", "slug": "shaniwar-wada-pune", "lat": 18.5195, "lng": 73.8553},
        {"name": "Magarpatta City", "slug": "magarpatta-sez-pune", "lat": 18.5158, "lng": 73.9272},
        {"name": "Osho Ashram Koregaon Park", "slug": "osho-koregaon-park-pune", "lat": 18.5362, "lng": 73.8890},
    ],
}


def landmarks_for(city: str) -> list[dict]:
    return LANDMARKS.get(city, [])


def find_landmark(city: str, name: str) -> dict | None:
    for lm in landmarks_for(city):
        if lm["name"].lower() == (name or "").lower():
            return lm
    return None
