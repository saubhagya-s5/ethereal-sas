"""Brief + inventory -> Treebo-style landing page (output/pages/<opp_id>.html).

Section order follows cybertron-hotrod's SeoResultsPage: H1, breadcrumbs, hotel cards with
sort/filters, about the place, FAQs, related links. Every hotel fact on the page (names,
localities, ratings, amenities, URLs) is read from inventory by the template; the LLM only
writes the copy around them, and the guardrail checks the rendered page before it is saved.

Usage: python -m generator.page --opp blr-koramangala-free-breakfast [--no-llm]
"""
import argparse
import json
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

from generator import brief as brief_mod
from generator import guardrail, llm
from generator.data import CITY_SLUGS, PAGES, SITE, inventory, label, live_prices, opportunity, page_hotels, slugify

env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape())
# Amenities worth comparing in cards and the quick-facts table, in priority order.
COMPARE = ["parking", "free-wifi", "gym", "room-service", "restaurant", "swimming-pool", "ac-room", "elevator", "free-breakfast"]


def about_area_text(opp: dict, hotels: list[dict], use_llm: bool) -> str:
    """General copy about the place. Hotel facts stay out of it; the guardrail flags any that slip in."""
    count = len(hotels)
    fallback = (f"{opp['place']} is one of {opp['city']}'s popular areas to stay in. "
                f"Treebo has {count} hotels here with {label(opp['filter']).lower()}, listed above by guest rating. "
                f"Use the links below to compare stays in nearby localities.")
    if opp["filter_type"] == "category":
        fallback = fallback.replace(f"with {label(opp['filter']).lower()}", f"listed as {label(opp['filter']).lower()}")
    if not use_llm:
        return fallback
    text = llm.complete_text(
        "You write short, factual neighbourhood descriptions for a hotel website. "
        "Never mention specific hotels, prices, ratings, amenities or hotel counts.",
        f"Write 2-3 sentences on why travellers stay in {opp['place']}, {opp['city']}, India: "
        "what the area is known for and who it suits (business, families, nightlife, transit). Plain text only.",
    )
    return text.strip() if text else fallback


def hotel_view(h: dict, opp: dict, compare: list[str], prices: dict) -> dict:
    words = [w for w in h["name"].replace("Treebo", "").split() if w[:1].isalpha()]
    chips = []
    chips.append({"label": label(opp["filter"]), "hit": True})
    chips += [{"label": label(a), "hit": False} for a in compare if a in h["amenities"] and a != opp["filter"]][:5]
    live = prices.get(str(h["hotel_id"]))
    return {**h, "initials": "".join(w[0] for w in words[:2]).upper() or "T", "chips": chips,
            "price": f"₹{live['price']:,.0f}" if live else None, "price_as_of": live and live["as_of"]}


def breadcrumbs(opp: dict, brief: dict) -> list[dict]:
    city = CITY_SLUGS.get(opp["city"], slugify(opp["city"]))
    crumbs = [{"name": "Home", "url": f"{SITE}/"}, {"name": f"Hotels in {opp['city']}", "url": f"{SITE}/hotels-in-{city}/"}]
    if opp["place_type"] != "city":
        near = "near" if opp["place_type"] == "landmark" else "in"
        crumbs.append({"name": f"Hotels {near} {opp['place']}", "url": brief_mod.place_url(opp)})
    crumbs.append({"name": brief["h1"], "url": opp["proposed_url"]})
    return crumbs


def link_text(url: str) -> str:
    return urlparse(url).path.strip("/").replace("-", " ").capitalize()


def jsonld(opp: dict, brief: dict, hotels: list[dict], crumbs: list[dict], prices: dict) -> dict:
    graph = [
        {"@type": "BreadcrumbList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "name": c["name"], "item": c["url"]} for i, c in enumerate(crumbs)]},
        {"@type": "FAQPage", "mainEntity": [
            {"@type": "Question", "name": f["q"], "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in brief["faqs"]]},
    ]
    for h in hotels:
        node = {
            "@type": "Hotel", "name": h["name"], "url": h["url"], "brand": {"@type": "Brand", "name": "Treebo Hotels"},
            "address": {"@type": "PostalAddress", "addressLocality": h["locality"], "addressRegion": h["city"], "addressCountry": "IN"},
            "geo": {"@type": "GeoCoordinates", "latitude": h["lat"], "longitude": h["lng"]},
            "amenityFeature": [{"@type": "LocationFeatureSpecification", "name": label(a), "value": True} for a in h["amenities"]],
        }
        if str(h["hotel_id"]) in prices:
            node["priceRange"] = f"From ₹{prices[str(h['hotel_id'])]['price']:,.0f}"
        if h["rating"]:
            node["aggregateRating"] = {"@type": "AggregateRating", "ratingValue": h["rating"], "bestRating": 5}
        graph.append(node)
    return {"@context": "https://schema.org", "@graph": graph}


def render_page(brief: dict, opp: dict, about_area: str, inv: dict | None = None) -> str:
    inv = inv or inventory()
    hotels = [inv[i] for i in brief["hotel_ids"] if i in inv and inv[i]["active"]]
    present = Counter(a for h in hotels for a in h["amenities"])
    compare = [a for a in COMPARE if present[a] and a != opp["filter"]][:4]
    crumbs = breadcrumbs(opp, brief)
    prices = live_prices()
    return env.get_template("listing.html.j2").render(
        brief=brief, opp=opp, about_area=about_area,
        hotels=[hotel_view(h, opp, compare, prices) for h in hotels],
        compare=[{"slug": a, "label": label(a)} for a in compare],
        filter_label=label(opp["filter"]),
        city_slug=CITY_SLUGS.get(opp["city"], slugify(opp["city"])),
        breadcrumbs=crumbs,
        links=[{"url": u, "text": link_text(u)} for u in brief["internal_links"]],
        jsonld=jsonld(opp, brief, hotels, crumbs, prices),
    )


def generate(opp: dict, use_llm: bool = True, deep_check: bool = False, save: bool = True) -> dict:
    """Brief -> page -> guardrail. If the guardrail blocks LLM copy, it is replaced with template copy.

    Returns {html, brief, report: {brief_source, about_source, guardrail}, path}.
    """
    inv = inventory()
    hotels = page_hotels(opp, inv)
    brief, brief_report = brief_mod.build_brief(opp, use_llm=use_llm)
    about = about_area_text(opp, hotels, use_llm)
    about_source = "llm" if use_llm and llm.available() else "template"

    page_html = render_page(brief, opp, about, inv)
    check = guardrail.check_page(page_html, hotels, opp, inv, use_llm=deep_check)
    blocked_before = brief_report["guardrail"]["flags"] if "blocked" in brief_report["source"] else []
    if check["blocked"] and "llm" in (brief_report["source"], about_source):
        # Some LLM copy failed the page check: fall back to template copy, which is built from inventory.
        blocked_before += check["flags"]
        brief, _ = brief_mod.build_brief(opp, use_llm=False)
        about = about_area_text(opp, hotels, use_llm=False)
        brief_report["source"] = about_source = "template (LLM text blocked by guardrail)"
        page_html = render_page(brief, opp, about, inv)
        check = guardrail.check_page(page_html, hotels, opp, inv)
    check["blocked_before_fallback"] = blocked_before

    report = {"brief_source": brief_report["source"], "about_source": about_source, "guardrail": check}
    path = None
    if save and not check["blocked"]:
        brief_mod.save_brief(brief)
        PAGES.mkdir(parents=True, exist_ok=True)
        path = PAGES / f"{opp['opp_id']}.html"
        path.write_text(page_html, encoding="utf-8")
        (PAGES / f"{opp['opp_id']}.guardrail.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        path = str(path)
    return {"html": page_html, "brief": brief, "report": report, "path": path}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--opp", required=True)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--deep-check", action="store_true", help="also extract claims with the LLM")
    args = parser.parse_args()
    result = generate(opportunity(args.opp), use_llm=not args.no_llm, deep_check=args.deep_check)
    g = result["report"]["guardrail"]
    print(result["path"] or "BLOCKED - not saved", f"brief={result['report']['brief_source']}",
          f"flags={len(g['flags'])}", "blocked" if g["blocked"] else "ok")
