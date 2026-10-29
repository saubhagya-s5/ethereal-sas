"""Check every factual claim in generated copy against data/inventory.json.

Claims are pulled out per unit of text (a sentence, or an FAQ question + its answer):
which hotels it names, and what it says about amenities, ratings, prices, hotel counts and
localities. A wrong fact blocks the text; a doubtful one is flagged.
An optional LLM pass extracts free-form claims the rules can miss; they are checked the same way.

Result: {"blocked": bool, "flags": [{severity, field, text, claim, truth}]}
"""
import json
import re

from lxml import html as lxml_html

from generator import llm
from generator.data import label, live_prices

NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
# Phrases a writer might use -> amenity slugs that make the phrase true.
ALIASES = {
    "breakfast": {"free-breakfast", "complimentary-breakfast"},
    "wifi": {"free-wifi"}, "wi-fi": {"free-wifi"},
    "pool": {"swimming-pool"},
    "air-conditioned": {"ac-room"}, "air conditioning": {"ac-room"},
    "lift": {"elevator"},
    "laundry": {"guest-laundry"},
    "pet friendly": {"pet-friendly"}, "pets allowed": {"pet-friendly"},
}
# Slugs too generic to read as claims ("security" in "secure booking", "queen bed" in room types).
SKIP_AMENITIES = {"security", "card-payment-accepted", "cupboards", "geyser", "ironing-board", "coffee-table",
                  "dining-table", "king-bed", "queen-bed", "twin-bed", "two-queen-beds", "flat-screen-tv",
                  "complimentary-toiletries", "complimentary-water", "shower-cabinets", "living-room", "stove",
                  "easily-accessible-from-airport", "easily-accessible-from-railway-station"}
NEGATION = re.compile(r"\b(no|not|without|doesn't|does not|don't|do not|lacks?|isn't|aren't)\b", re.I)
PRICE = re.compile(r"(₹|\bRs\.?\s?\d|\bINR\b|\brupees\b|\bper night\b)", re.I)
RATING = re.compile(r"(?:rated|rating(?: of)?)\s*(\d(?:\.\d)?)|(\d(?:\.\d)?)\s*(?:/\s*5\b|out of 5)", re.I)
# "4 hotels", "four Treebo hotels", "four budget hotels" (one descriptive word allowed).
COUNT = re.compile(r"\b(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:treebo\s+)?(?:[a-z]+\s+)?(?:hotels?|properties|stays)\b", re.I)
TREEBO_NAME = re.compile(r"\bTreebo(?:\s+[A-Z][\w'&.-]*)+")
NOT_HOTEL_NAMES = {"hotels", "hotel", "app", "rewards", "website", "has", "offers", "lists"}


class Vocabulary:
    def __init__(self, inv: dict, city: str):
        self.inv = inv
        self.names = sorted(((h["name"].lower(), h) for h in inv.values()), key=lambda x: -len(x[0]))
        self.localities = {h["locality"] for h in inv.values() if h["city"] == city and h["locality"]}
        slugs = {a for h in inv.values() for a in h["amenities"]} - SKIP_AMENITIES
        phrases = {s.replace("-", " "): {s} for s in slugs}
        phrases.update(ALIASES)
        self.amenities = sorted(phrases.items(), key=lambda x: -len(x[0]))

    def hotels_in(self, text: str) -> list[dict]:
        low, found = text.lower(), []
        for name, hotel in self.names:
            if name in low:
                found.append(hotel)
                low = low.replace(name, " ")
        return found

    def amenities_in(self, text: str) -> list[tuple[str, set]]:
        low, found = text.lower(), []
        for phrase, slugs in self.amenities:
            if re.search(rf"\b{re.escape(phrase)}\b", low):
                found.append((phrase, slugs))
                low = re.sub(rf"\b{re.escape(phrase)}\b", " ", low)
        return found


def sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z])", text) if s.strip()]


def flag(severity: str, field: str, text: str, claim: str, truth: str) -> dict:
    return {"severity": severity, "field": field, "text": text[:240], "claim": claim, "truth": truth}


def check_unit(field: str, text: str, vocab: Vocabulary, page_ids: set[str]) -> list[dict]:
    flags = []
    hotels = vocab.hotels_in(text)
    single = len(hotels) == 1

    # Hotel names: invented, closed, or not on this page.
    leftover = text
    for h in hotels:
        leftover = re.sub(re.escape(h["name"]), " ", leftover, flags=re.I)
        if not h["active"]:
            flags.append(flag("block", field, text, f"mentions {h['name']}", "hotel is inactive (closed) in inventory"))
        elif str(h["hotel_id"]) not in page_ids:
            flags.append(flag("warn", field, text, f"mentions {h['name']}", "hotel is not one of this page's hotels"))
    for m in TREEBO_NAME.finditer(leftover):
        words = m.group(0).split()[1:]
        if words and words[0].lower() not in NOT_HOTEL_NAMES:
            flags.append(flag("block", field, text, f"mentions '{m.group(0)}'", "no hotel with this name in inventory"))

    # Amenities.
    negated = bool(NEGATION.search(text))
    for phrase, slugs in vocab.amenities_in(text):
        for h in hotels:
            has = bool(slugs & set(h["amenities"]))
            if has == negated:
                claim = f"{h['name']} {'does not have' if negated else 'has'} {phrase}"
                truth = f"{phrase} is {'' if has else 'not '}in amenities for hotel {h['hotel_id']}"
                # With several hotels in one sentence we can't be sure who the amenity belongs to.
                flags.append(flag("block" if single or not negated else "warn", field, text, claim, truth))

    # Ratings.
    for m in RATING.finditer(text):
        value = float(m.group(1) or m.group(2))
        ratings = {h["rating"] for h in hotels}
        if hotels and value not in ratings:
            truth = ", ".join(f"{h['name']}: {h['rating'] if h['rating'] is not None else 'no rating'}" for h in hotels)
            flags.append(flag("block", field, text, f"rating {value:g}", truth))
        elif not hotels:
            flags.append(flag("warn", field, text, f"rating {value:g}", "rating not tied to a named hotel"))

    # Prices: staging prices are placeholders and live prices change, so pages never print them.
    if PRICE.search(text):
        flags.append(flag("block", field, text, "states a price", "prices come from the live pricing service; don't print them"))

    # Hotel counts.
    for m in COUNT.finditer(text):
        word = m.group(1).lower()
        n = int(word) if word.isdigit() else NUMBER_WORDS[word]
        if n != len(page_ids):
            flags.append(flag("block", field, text, m.group(0), f"this page lists {len(page_ids)} hotels"))

    # Localities: a single named hotel placed in a different locality.
    named_locs = {loc for loc in vocab.localities if re.search(rf"\b{re.escape(loc)}\b", text, re.I)}
    if single and named_locs and hotels[0]["locality"] not in named_locs:
        flags.append(flag("warn", field, text, f"{hotels[0]['name']} in {', '.join(sorted(named_locs))}",
                          f"inventory locality is {hotels[0]['locality']}"))
    return flags


def check_texts(units: list[tuple[str, str]], hotels: list[dict], opp: dict, inv: dict) -> dict:
    vocab = Vocabulary(inv, opp["city"])
    page_ids = {str(h["hotel_id"]) for h in hotels}
    flags = []
    for field, text in units:
        # An FAQ is checked whole: its answer ("Treebo Edha Suites.") only makes a claim with its question.
        parts = [text] if field.startswith("faq") else sentences(text)
        flags += [f for part in parts for f in check_unit(field, part, vocab, page_ids)]
    flags = list({(f["field"], f["claim"], f["truth"]): f for f in flags}.values())
    return {"blocked": any(f["severity"] == "block" for f in flags), "flags": flags}


def brief_units(text: dict) -> list[tuple[str, str]]:
    units = [("quick_answer", text["quick_answer"]), ("meta_description", text["meta_description"])]
    # An FAQ answer like "Treebo Edha Suites." only makes a claim together with its question.
    units += [(f"faq {i + 1}", f"{f['q']} {f['a']}") for i, f in enumerate(text["faqs"])]
    return units


def check_brief(text: dict, hotels: list[dict], opp: dict, inv: dict) -> dict:
    return check_texts(brief_units(text), hotels, opp, inv)


def check_page(page_html: str, hotels: list[dict], opp: dict, inv: dict, use_llm: bool = False) -> dict:
    """Check the rendered page: visible copy, JSON-LD hotel data, and (optionally) LLM-extracted claims."""
    doc = lxml_html.fromstring(page_html)
    units = []
    for el in doc.xpath("//*[@data-claims]"):  # copy blocks the template marks for checking
        units.append((el.get("data-claims"), " ".join(el.text_content().split())))
    for faq in doc.xpath("//details[@class='faq']"):
        units.append(("faq", " ".join(faq.text_content().split())))
    result = check_texts(units, hotels, opp, inv)
    result["flags"] += check_jsonld(doc, inv)
    if use_llm:
        result["flags"] += check_llm_claims(" ".join(t for _, t in units), opp, inv)
    result["blocked"] = any(f["severity"] == "block" for f in result["flags"])
    return result


def check_jsonld(doc, inv: dict) -> list[dict]:
    by_name = {h["name"]: h for h in inv.values()}
    prices = live_prices()
    flags = []
    for script in doc.xpath("//script[@type='application/ld+json']"):
        data = json.loads(script.text)
        for node in data.get("@graph", [data]):
            if node.get("@type") != "Hotel":
                continue
            hotel = by_name.get(node.get("name"))
            if not hotel:
                flags.append(flag("block", "json-ld", node.get("name", ""), "Hotel entry", "not in inventory"))
                continue
            if "priceRange" in node:
                live = prices.get(str(hotel["hotel_id"]))
                if not live or node["priceRange"] != f"From ₹{live['price']:,.0f}":
                    flags.append(flag("block", "json-ld", hotel["name"], f"priceRange {node['priceRange']}",
                                      f"live price {live['price']:,.0f}" if live else "no live price for this hotel"))
            rating = (node.get("aggregateRating") or {}).get("ratingValue")
            if rating is not None and float(rating) != hotel["rating"]:
                flags.append(flag("block", "json-ld", hotel["name"], f"ratingValue {rating}", f"inventory rating {hotel['rating']}"))
            amenities = {a.get("name") for a in node.get("amenityFeature", [])}
            extra = amenities - {label(a) for a in hotel["amenities"]}
            if extra:
                flags.append(flag("block", "json-ld", hotel["name"], f"amenities {sorted(extra)}", "not in inventory amenities"))
    return flags


CLAIMS_SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hotel_name": {"type": "string"},
                    "attribute": {"type": "string", "enum": ["amenity", "rating", "locality", "price", "category", "other"]},
                    "value": {"type": "string"},
                    "negated": {"type": "boolean"},
                    "quote": {"type": "string"},
                },
                "required": ["hotel_name", "attribute", "value", "negated", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


def check_llm_claims(text: str, opp: dict, inv: dict) -> list[dict]:
    """LLM lists every claim about a specific hotel; each is then checked against inventory in code."""
    out = llm.complete_json(
        "You extract factual claims about specific hotels from web copy. List every claim; do not judge them.",
        "Extract every factual claim about a specific named hotel. For amenities use a short phrase "
        f"(e.g. 'free breakfast', 'gym').\n\nTEXT:\n{text}",
        CLAIMS_SCHEMA, effort="low",
    )
    if not out:
        return []
    vocab = Vocabulary(inv, opp["city"])
    flags = []
    for c in out["claims"]:
        hotels = vocab.hotels_in(c["hotel_name"])
        if not hotels:
            flags.append(flag("block", "llm-check", c["quote"], f"about '{c['hotel_name']}'", "no such hotel in inventory"))
            continue
        h = hotels[0]
        if c["attribute"] == "amenity":
            slugs = dict(vocab.amenities).get(c["value"].lower()) or {c["value"].lower().replace(" ", "-")}
            if bool(slugs & set(h["amenities"])) == c["negated"]:
                flags.append(flag("block", "llm-check", c["quote"], f"{h['name']}: {c['value']}", f"amenities: {', '.join(h['amenities'])}"))
        elif c["attribute"] == "rating":
            try:
                ok = float(re.findall(r"\d(?:\.\d)?", c["value"])[0]) == h["rating"]
            except IndexError:
                ok = False
            if not ok:
                flags.append(flag("block", "llm-check", c["quote"], f"{h['name']} rating {c['value']}", f"inventory rating {h['rating']}"))
        elif c["attribute"] == "locality" and h["locality"].lower() not in c["value"].lower():
            flags.append(flag("warn", "llm-check", c["quote"], f"{h['name']} in {c['value']}", f"inventory locality {h['locality']}"))
        elif c["attribute"] == "price":
            flags.append(flag("block", "llm-check", c["quote"], "states a price", "prices are live; don't print them"))
        elif c["attribute"] == "category":
            slug = c["value"].lower().replace(" ", "-")
            if not any(slug in cat or cat.startswith(slug) for cat in h["categories"]):
                flags.append(flag("warn", "llm-check", c["quote"], f"{h['name']}: {c['value']}", f"categories: {', '.join(h['categories'])}"))
        elif c["attribute"] == "other":
            flags.append(flag("warn", "llm-check", c["quote"], f"{h['name']}: {c['value']}", "can't verify against inventory"))
    return flags
