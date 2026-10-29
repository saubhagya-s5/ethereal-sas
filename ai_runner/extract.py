"""Turn a raw AI answer into structured facts: brands, hotels, Treebo claims, URLs.

Real runs use an LLM with a strict JSON schema: Claude if ANTHROPIC_API_KEY is set, else
OpenAI, else Gemini. Dry-run, or a run with none of those keys, uses rule_extract.

Sampling temperature is not sent: Claude rejects it on every model since 2026-10-21 and
OpenAI's reasoning models reject it too. The fixed schema plus the extraction cache keep
results stable instead.
"""
from __future__ import annotations

import json
import os
import re

from .config import EXTRACT_CLAUDE_MODEL, EXTRACT_GEMINI_MODEL, EXTRACT_OPENAI_MODEL, env_or

ATTRIBUTES = ["amenity", "category", "locality", "rating", "price", "open_status", "other"]

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["brands_mentioned", "hotels", "treebo_claims", "urls_in_text"],
    "properties": {
        "brands_mentioned": {"type": "array", "items": {"type": "string"}},
        "hotels": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "brand", "locality"],
                "properties": {"name": {"type": "string"}, "brand": {"type": "string"}, "locality": {"type": "string"}},
            },
        },
        "treebo_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["hotel_name", "claim", "attribute", "value"],
                "properties": {
                    "hotel_name": {"type": "string"},
                    "claim": {"type": "string"},
                    "attribute": {"type": "string", "enum": ATTRIBUTES},
                    "value": {"type": "string"},
                },
            },
        },
        "urls_in_text": {"type": "array", "items": {"type": "string"}},
    },
}

INSTRUCTIONS = """You extract facts from a travel assistant's answer about hotels in {city}, India.
Return JSON only, matching the schema. Use only what the answer says; do not add knowledge.

- brands_mentioned: hotel brands/chains in order of first mention (e.g. "Treebo", "OYO", "FabHotels").
  For an independent hotel use its own name. Exclude booking sites (MakeMyTrip, Booking.com, Goibibo, Agoda).
- hotels: every hotel named, with its brand ("Treebo" for any Treebo hotel, including Treebo Trend/Trip/Edge/Premium
  and Itsy Hotels, which is a Treebo brand)
  and the locality the answer gives ("" if none).
- treebo_claims: one item per factual statement about a specific, named Treebo (or Itsy) hotel.
  A generic brand mention ("Treebo Trend / FabHotels, various locations") is not a hotel: list the
  brand in brands_mentioned, but add no hotels or claims for it.
  attribute is one of: amenity (e.g. free breakfast, gym), category (budget, family, couple-friendly, business),
  locality (where it is), rating (a score), price (a nightly price, value = the number in rupees),
  open_status (open / closed / operating), other. value is the short fact, e.g. "free breakfast", "Koramangala", "4.2", "1899".
  claim is a short sentence like "Treebo Edha Suites offers free breakfast".
- urls_in_text: URLs written in the answer text.
If the answer names no Treebo hotel, treebo_claims is []."""

BRAND_ALIASES = {
    # Itsy Hotels is Treebo's own brand and is sold on treebo.com.
    "treebo": "Treebo", "itsy": "Treebo", "oyo": "OYO", "fab hotel": "FabHotels", "fabhotel": "FabHotels",
    "zostel": "Zostel", "lemon tree": "Lemon Tree", "ginger": "Ginger", "ibis": "Ibis",
    "taj": "Taj", "vivanta": "Vivanta", "itc": "ITC", "marriott": "Marriott", "radisson": "Radisson",
    "hyatt": "Hyatt", "hilton": "Hilton", "novotel": "Novotel", "holiday inn": "Holiday Inn",
    "royal orchid": "Royal Orchid", "regenta": "Regenta", "keys hotel": "Keys Hotels", "bloom": "Bloom Hotels",
    "sterling": "Sterling", "citrus": "Citrus Hotels", "the park": "The Park", "airbnb": "Airbnb",
}


_BRAND_WORDS = {w for k in BRAND_ALIASES for w in k.split()} | {w.lower() for v in BRAND_ALIASES.values() for w in v.split()} | {
    "trend", "trip", "edge", "premium", "rooms", "townhouse", "hotels", "hotel", "fabhotels", "chain", "chains",
    "properties", "property", "various", "several", "multiple", "options", "branches", "outlets", "locations", "stays"}


def is_generic_name(name: str) -> bool:
    """True for brand-only "names" like "Treebo Trend / FabHotels" that point at no specific hotel."""
    words = re.findall(r"[a-z0-9]+", name.lower())
    return not [w for w in words if w not in _BRAND_WORDS]


def canon_brand(name: str) -> str:
    low = name.lower().strip()
    for alias, canon in BRAND_ALIASES.items():
        if low.startswith(alias) or f" {alias}" in f" {low}":
            return canon
    return name.strip()


def finalize(raw: dict, citations: list[str]) -> dict:
    """Normalise brands, derive treebo_mentioned / treebo_rank, merge cited URLs."""
    brands: list[str] = []
    for b in raw.get("brands_mentioned", []) + [h.get("brand", "") for h in raw.get("hotels", [])]:
        b = canon_brand(b) if b else ""
        if b and b not in brands:
            brands.append(b)
    treebo = "Treebo" in brands or bool(raw.get("treebo_claims"))
    if treebo and "Treebo" not in brands:
        brands.append("Treebo")
    cited = []
    for u in list(citations) + list(raw.get("urls_in_text", [])):
        if u and u not in cited:
            cited.append(u)
    return {
        "brands_mentioned": brands,
        "treebo_mentioned": treebo,
        "treebo_rank": brands.index("Treebo") + 1 if treebo else None,
        "hotels": raw.get("hotels", []),
        "treebo_claims": raw.get("treebo_claims", []),
        "cited_urls": cited,
    }


async def llm_extract(answer_text: str, city: str) -> tuple[dict, str]:
    """Returns (raw extraction, extractor name)."""
    system = INSTRUCTIONS.format(city=city)
    user = f"Answer to analyse:\n<answer>\n{answer_text}\n</answer>"
    if os.environ.get("ANTHROPIC_API_KEY"):
        import anthropic

        model = env_or(EXTRACT_CLAUDE_MODEL)
        resp = await anthropic.AsyncAnthropic().messages.create(
            model=model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError("extraction refused")
        text = next(b.text for b in resp.content if b.type == "text")
        return json.loads(text), f"claude:{model}"
    if os.environ.get("OPENAI_API_KEY"):
        from openai import AsyncOpenAI

        model = env_or(EXTRACT_OPENAI_MODEL)
        resp = await AsyncOpenAI().responses.create(
            model=model,
            input=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            text={"format": {"type": "json_schema", "name": "extraction", "schema": SCHEMA, "strict": True}},
        )
        return json.loads(resp.output_text), f"openai:{model}"
    if os.environ.get("GEMINI_API_KEY"):
        from google import genai
        from google.genai import types

        model = env_or(EXTRACT_GEMINI_MODEL)
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(timeout=120_000))
        resp = await client.aio.models.generate_content(
            model=model,
            contents=user,
            config=types.GenerateContentConfig(
                system_instruction=system,
                response_mime_type="application/json",
                response_json_schema=SCHEMA,
            ),
        )
        return json.loads(resp.text), f"gemini:{model}"
    return rule_extract(answer_text), "rules"


LINE = re.compile(r"^\s*\d+\.\s+\*\*(?P<name>.+?)\*\*\s*(?:\((?P<loc>[^)]*)\))?\s*[—–-]\s*(?P<rest>.*)$")
PRICE = re.compile(r"₹\s?([\d,]+)")
RATING = re.compile(r"[Rr]ated\s+([\d.]+)\s*/\s*5")
URL = re.compile(r"https?://[^\s)\]>\"']+")


def rule_extract(text: str) -> dict:
    """Parse the numbered "**Name** (Locality) — amenities. From ₹X/night." format."""
    out = {"brands_mentioned": [], "hotels": [], "treebo_claims": [], "urls_in_text": URL.findall(text)}
    for line in text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        name, loc, rest = m["name"].strip(), (m["loc"] or "").strip(), m["rest"]
        brand = canon_brand(name)
        if brand not in out["brands_mentioned"]:
            out["brands_mentioned"].append(brand)
        out["hotels"].append({"name": name, "brand": brand, "locality": loc})
        if brand != "Treebo":
            continue
        claims = out["treebo_claims"]
        if loc:
            claims.append({"hotel_name": name, "claim": f"{name} is in {loc}", "attribute": "locality", "value": loc})
        amenity_part = re.split(r"\.\s|\.$", rest, maxsplit=1)[0]
        for phrase in [p.strip() for p in amenity_part.split(",") if p.strip()]:
            if "₹" in phrase or "rated" in phrase.lower():
                continue
            claims.append({"hotel_name": name, "claim": f"{name} offers {phrase}", "attribute": "amenity", "value": phrase})
        if pm := PRICE.search(rest):
            v = pm[1].replace(",", "")
            claims.append({"hotel_name": name, "claim": f"{name} costs from ₹{pm[1]} a night", "attribute": "price", "value": v})
        if rm := RATING.search(rest):
            claims.append({"hotel_name": name, "claim": f"{name} is rated {rm[1]}/5", "attribute": "rating", "value": rm[1]})
    return out
