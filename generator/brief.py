"""Opportunity + AI findings -> content brief (contracts/brief.json).

Hotel lists, links and URLs are computed from inventory. The LLM only writes the quick answer,
meta description and FAQ wording, and the guardrail checks that text before it is saved.

Usage: python -m generator.brief --opp blr-koramangala-free-breakfast [--no-llm]
"""
import argparse
import json
from collections import Counter

from generator import guardrail, llm
from generator.data import (BRIEFS, CITY_AMENITY_PAGES, CITY_SLUGS, SITE, inventory, label, opportunity,
                            page_hotels, related_results, slugify)

SECTIONS = ["Quick answer", "Hotel list", "Why stay in {place}", "Amenities compared", "FAQs", "Nearby localities"]
ARTICLE = {"gym": "a ", "restaurant": "a ", "swimming-pool": "a "}
NUMBERS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine"}

SYSTEM = """You write landing-page copy for Treebo Hotels, an Indian budget hotel chain.
Travellers ask AI assistants these questions; the page must answer them directly and accurately.
Hard rules:
- State hotel facts ONLY from the FACTS JSON (names, localities, ratings, amenities, categories). Never add any other hotel fact.
- Never mention prices, discounts, distances, check-in times or anything not in FACTS.
- Refer to hotels by their exact names from FACTS.
- If an AI engine stated a wrong fact (see WRONG_CLAIMS), write an FAQ whose answer states the correct fact.
- Plain, helpful tone. No superlatives you can't back with FACTS."""

BRIEF_SCHEMA = {
    "type": "object",
    "properties": {
        "quick_answer": {"type": "string", "description": "1-2 sentences that directly answer the main question, naming the hotels"},
        "meta_description": {"type": "string", "description": "Under 155 characters"},
        "faqs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"q": {"type": "string"}, "a": {"type": "string"}},
                "required": ["q", "a"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["quick_answer", "meta_description", "faqs"],
    "additionalProperties": False,
}


def names(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def filter_phrase(opp: dict) -> str:
    return label(opp["filter"]).lower()


def headline(opp: dict) -> str:
    place = opp["place"] if opp["place_type"] == "city" else f"{opp['place']}, {opp['city']}"
    near = "near" if opp["place_type"] == "landmark" else "in"
    if opp["filter_type"] == "category":
        return f"{label(opp['filter'])} {near} {place}"
    return f"Hotels {near} {place} with {label(opp['filter'])}"


def place_url(opp: dict) -> str:
    city = CITY_SLUGS.get(opp["city"], slugify(opp["city"]))
    if opp["place_type"] == "landmark":
        return f"{SITE}/hotels-near-{slugify(opp['place'])}/"
    if opp["place_type"] == "city":
        return f"{SITE}/hotels-in-{city}/"
    return f"{SITE}/hotels-in-{slugify(opp['place'])}-{city}/"


def internal_links(opp: dict, hotels: list[dict], inv: dict) -> list[str]:
    city = CITY_SLUGS.get(opp["city"], slugify(opp["city"]))
    links = [place_url(opp), f"{SITE}/hotels-in-{city}/"]
    if opp["filter_type"] == "amenity" and opp["filter"] in CITY_AMENITY_PAGES:
        links.append(f"{SITE}/hotels-in-{city}-with-{opp['filter']}/")
    for cat, _ in Counter(c for h in hotels for c in h["categories"]).most_common(2):
        links.append(f"{SITE}/{cat}-in-{city}/")
    for loc in nearby_localities(opp, inv)[:3]:
        links.append(f"{SITE}/hotels-in-{slugify(loc)}-{city}/")
    return list(dict.fromkeys(links))


def nearby_localities(opp: dict, inv: dict) -> list[str]:
    """Other localities in the city with active hotels that also match the filter, most hotels first."""
    field = "amenities" if opp["filter_type"] == "amenity" else "categories"
    counts = Counter(
        h["locality"] for h in inv.values()
        if h["active"] and h["city"] == opp["city"] and h["locality"]
        and h["locality"] != opp["place"] and opp["filter"] in h[field]
    )
    return [loc for loc, _ in counts.most_common()]


def facts(hotels: list[dict]) -> list[dict]:
    keep = ("hotel_id", "name", "locality", "rating", "amenities", "categories")
    return [{k: h[k] for k in keep} for h in hotels]


def template_text(opp: dict, hotels: list[dict], queries: list[dict]) -> dict:
    """Deterministic copy built straight from inventory. Used offline and when the guardrail blocks LLM text."""
    hotel_names = [h["name"] for h in hotels]
    count = NUMBERS.get(len(hotels), str(len(hotels)))
    near = "near" if opp["place_type"] == "landmark" else "in"
    what = f"with {filter_phrase(opp)}" if opp["filter_type"] == "amenity" else f"listed as {filter_phrase(opp)}"
    quick = f"Treebo has {count} hotel{'s' if len(hotels) != 1 else ''} {near} {opp['place']} {what}: {names(hotel_names)}."

    faqs = []
    for q in queries:  # real traveller questions that this page answers exactly
        tags = q["tags"]
        if (tags.get("place") or "").lower() == opp["place"].lower() and opp["filter"] in (tags.get("amenity"), tags.get("intent")):
            # An intent like "budget" narrows the answer to hotels in that category.
            category = f"{tags['intent']}-hotels" if tags.get("intent") and tags["intent"] != opp["filter"] else None
            fit = [h["name"] for h in hotels if not category or category in h["categories"]]
            if fit:
                faqs.append({"q": q["text"], "a": f"{names(fit)}."})
    main_q = f"Which Treebo hotels {near} {opp['place']} {'offer' if opp['filter_type'] == 'amenity' else 'are'} {filter_phrase(opp)}?"
    faqs.append({"q": main_q, "a": f"{names(hotel_names)}."})
    verb = "have" if opp["filter_type"] == "amenity" else "are"
    faqs.append({"q": f"How many Treebo hotels {near} {opp['place']} {verb} {filter_phrase(opp)}?",
                 "a": f"{count.capitalize()}: {names(hotel_names)}."})
    rated = [h for h in hotels if h["rating"]]
    if rated:
        top = max(h["rating"] for h in rated)
        best = [h["name"] for h in rated if h["rating"] == top]
        faqs.append({"q": "Which of these hotels is rated highest?", "a": f"{names(best)}, rated {top:g} out of 5."})
    for amenity in ("parking", "gym", "free-wifi", "room-service", "swimming-pool", "restaurant"):
        have = [h["name"] for h in hotels if amenity in h["amenities"]]
        if have and amenity != opp["filter"] and len(faqs) < 7:
            faqs.append({"q": f"Which of these hotels have {ARTICLE.get(amenity, '')}{label(amenity).lower()}?", "a": f"{names(have)}."})
    couple = [h["name"] for h in hotels if "couple-friendly-hotels" in h["categories"]]
    if couple and len(faqs) < 8:
        faqs.append({"q": "Which of these hotels are couple-friendly?", "a": f"{names(couple)}."})
    faqs.append({"q": "How do I check the latest price and book?",
                 "a": "Choose a hotel above and tap Book Now to see live prices and availability on treebo.com."})

    meta = f"Book Treebo hotels {near} {opp['place']}, {opp['city']} {what}. Compare {len(hotels)} hotels, ratings and amenities."
    return {"quick_answer": quick, "meta_description": meta[:155], "faqs": dedupe(faqs)[:8]}


def dedupe(faqs: list[dict]) -> list[dict]:
    seen, out = set(), []
    for f in faqs:
        key = f["q"].lower().rstrip("?")
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def llm_text(opp: dict, hotels: list[dict], queries: list[dict], results: list[dict]) -> dict | None:
    competitors = Counter(b for r in results for b in r["brands_mentioned"] if b.lower() != "treebo")
    wrong = [c for r in results for c in r["treebo_claims"] if c["status"] != "correct"]
    mention_rate = sum(r["treebo_mentioned"] for r in results) / len(results) if results else None
    prompt = f"""PAGE: {headline(opp)}
URL: {opp['proposed_url']}

FACTS (the only hotel facts you may use):
{json.dumps(facts(hotels), indent=1)}

TRAVELLER_QUESTIONS (real questions people ask AI assistants):
{json.dumps([q['text'] for q in queries], indent=1)}

AI_FINDINGS:
- Treebo mention rate for these questions: {mention_rate if mention_rate is not None else 'unknown'}
- Competitors AI engines recommend instead: {dict(competitors.most_common(5))}
- Pages AI engines cite: {sorted({u for r in results for u in r['cited_urls']})[:10]}
WRONG_CLAIMS (AI said this about Treebo; 'truth' is correct):
{json.dumps([{'claim': c['claim'], 'truth': c['truth']} for c in wrong], indent=1)}

Write:
1. quick_answer: 1-2 sentences answering "{headline(opp)}" directly, naming every hotel in FACTS.
2. meta_description: under 155 characters.
3. faqs: 5 to 8 FAQs. Base the questions on TRAVELLER_QUESTIONS, rephrased to fit this page. Answers 1-2 sentences, from FACTS only."""
    return llm.complete_json(SYSTEM, prompt, BRIEF_SCHEMA)


def build_brief(opp: dict, use_llm: bool = True) -> tuple[dict, dict]:
    """Return (brief, report). report = {'source': 'llm'|'template', 'guardrail': {...}}."""
    inv = inventory()
    hotels = page_hotels(opp, inv)
    if len(hotels) < 2:
        raise ValueError(f"{opp['opp_id']}: needs at least 2 active Treebo hotels, found {len(hotels)}")
    queries, results = related_results(opp)

    text = llm_text(opp, hotels, queries, results) if use_llm else None
    source = "llm" if text else "template"
    check = guardrail.check_brief(text, hotels, opp, inv) if text else None
    if not text or check["blocked"]:
        if text:
            source = "template (LLM text blocked by guardrail)"
        text = template_text(opp, hotels, queries)

    title = headline(opp)
    brief = {
        "opp_id": opp["opp_id"],
        "title": f"{title} | Treebo",
        "meta_description": text["meta_description"][:155],
        "h1": title,
        "quick_answer": text["quick_answer"],
        "sections": [s.format(place=opp["place"]) for s in SECTIONS],
        "faqs": dedupe(text["faqs"])[:8],
        "internal_links": internal_links(opp, hotels, inv),
        "hotel_ids": [h["hotel_id"] for h in hotels],
        "sort_by": "rating",
    }
    return brief, {"source": source, "guardrail": check or guardrail.check_brief(brief, hotels, opp, inv)}


def save_brief(brief: dict) -> str:
    BRIEFS.mkdir(parents=True, exist_ok=True)
    path = BRIEFS / f"{brief['opp_id']}.json"
    path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def load_brief(opp_id: str) -> dict | None:
    path = BRIEFS / f"{opp_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--opp", required=True)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()
    brief, report = build_brief(opportunity(args.opp), use_llm=not args.no_llm)
    print(save_brief(brief), report["source"], f"{len(report['guardrail']['flags'])} flags")
