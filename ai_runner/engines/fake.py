"""Dry-run engine: deterministic fake answers built from the inventory, no API calls.

Answers use one fixed list format that extract.rule_extract can parse. They mix correct
Treebo facts with planted errors (wrong amenity, stale price, inactive hotel) so every
fact-check and miss-reason path runs.
"""
from __future__ import annotations

import random
import zlib

from ..inventory import AMENITY_WORDS, Inventory, slugify
from ..queries import matching_hotels
from .base import Engine, EngineAnswer

# How often each fake engine mentions Treebo (differences make by_engine interesting).
MENTION_RATE = {"chatgpt": 0.4, "gemini": 0.3, "perplexity": 0.6, "claude": 0.35}

COMPETITORS = [
    ("FabHotels", "FabHotel {place} Residency", "https://www.fabhotels.com/hotels-in-{place_slug}-{city_slug}/"),
    ("OYO", "OYO Townhouse {place}", "https://www.oyorooms.com/hotels-in-{place_slug}-{city_slug}/"),
    ("Lemon Tree", "Lemon Tree Hotel {place}", "https://www.lemontreehotels.com/{city_slug}/"),
    ("Ginger", "Ginger {city} {place}", "https://www.gingerhotels.com/{city_slug}"),
    ("Zostel", "Zostel {city}", "https://www.zostel.com/zostel/{city_slug}/"),
    ("Ibis", "Ibis {city} {place}", "https://all.accor.com/{city_slug}/"),
]
OTA_URLS = [
    "https://www.makemytrip.com/hotels/hotels_in_{place_slug}-{city_slug}.html",
    "https://www.booking.com/district/in/{city_slug}/{place_slug}.html",
    "https://www.goibibo.com/hotels/hotels-in-{place_slug}-{city_slug}/",
]
FAKE_AMENITIES = ["free-breakfast", "parking", "gym", "swimming-pool", "free-wifi", "room-service", "restaurant"]


class FakeEngine(Engine):
    def __init__(self, name: str, inv: Inventory, queries_by_text: dict[str, dict], live_prices: dict):
        super().__init__(model=f"fake-{name}")
        self.name = name
        self.inv = inv
        self.queries = queries_by_text
        self.prices = live_prices.get("prices", {})

    async def ask(self, question: str, city: str, run: int = 1) -> EngineAnswer:
        q = self.queries[question]
        rng = random.Random(zlib.crc32(f"{self.name}|{q['query_id']}|{run}".encode()))
        place = q["tags"]["place"] or city
        city_slug = "bangalore" if city == "Bengaluru" else slugify(city)
        fmt = {"place": place, "city": city, "place_slug": slugify(place), "city_slug": city_slug}

        lines: list[str] = []
        urls: list[str] = []
        for brand, name_tpl, url_tpl in rng.sample(COMPETITORS, rng.randint(2, 4)):
            ams = ", ".join(AMENITY_WORDS[a] for a in rng.sample(FAKE_AMENITIES, 2))
            lines.append(f"**{name_tpl.format(**fmt)}** ({place}) — {ams}. From ₹{rng.randrange(1100, 3500, 50):,}/night.")
            urls.append(url_tpl.format(**fmt))
        urls.append(rng.choice(OTA_URLS).format(**fmt))

        if rng.random() < MENTION_RATE.get(self.name, 0.4):
            treebo = matching_hotels(q, self.inv)
            if not treebo:
                treebo = self.inv.city(city, active_only=True)
            for h in rng.sample(treebo, min(len(treebo), rng.randint(1, 2))):
                lines.insert(rng.randint(0, len(lines)), self._treebo_line(h, rng))
                urls.append(h["url"])
            if rng.random() < 0.25:
                closed = [h for h in self.inv.city(city) if not h["active"] and h.get("locality")]
                if closed:
                    h = rng.choice(closed)
                    lines.append(self._treebo_line(h, rng))

        body = "\n".join(f"{i}. {line}" for i, line in enumerate(lines, start=1))
        text = f"Here are some options for: {question}\n\n{body}\n\nPrices change often, so check before you book."
        return EngineAnswer(text=text, citations=urls, model=self.model, raw={"fake": True})

    def _treebo_line(self, h: dict, rng: random.Random) -> str:
        own = [a for a in h.get("amenities", []) if a in AMENITY_WORDS]
        ams = rng.sample(own, min(2, len(own)))
        if rng.random() < 0.3:  # planted error: amenity the hotel doesn't have
            missing = [a for a in FAKE_AMENITIES if a not in h.get("amenities", [])]
            if missing:
                ams.append(rng.choice(missing))
        parts = [f"**{h['name']}** ({h.get('locality') or h['city']}) — " + ", ".join(AMENITY_WORDS[a] for a in ams) + "."]
        live = self.prices.get(h["hotel_id"], {}).get("pretax_price")
        if live:
            # planted error: a stale price well below today's live price
            price = live * (0.5 if rng.random() < 0.3 else rng.uniform(0.95, 1.05))
            parts.append(f"From ₹{round(price, -1):,.0f}/night.")
        if h.get("rating") and rng.random() < 0.5:
            parts.append(f"Rated {h['rating']}/5.")
        return " ".join(parts)
