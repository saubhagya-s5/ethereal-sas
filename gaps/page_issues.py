"""Check existing Treebo listing pages for AEO issues.

Reads the server-rendered page (window.__INITIAL_STATE__ / __APOLLO_STATE__, JSON-LD) — no API calls.
Checks:
  lists_inactive_hotels  page lists a hotel id that is not in sitemap-hotel.xml (source of truth for
                         which hotels exist; staging active flags are stale)
  outdated_price         page "from ₹X" (minPrice) differs from the lowest live starting price of the
                         hotels it lists (data/live_prices.json). Skipped if no live price. LOW confidence.
  no_quick_answer        "About" text doesn't open with a short (<=60 words) answer containing a number
  no_faqs                no FAQPage schema / FAQ content on the page
  breakfast_not_stated   city/locality page where most matching hotels include breakfast, but neither the
                         quick answer nor the FAQs mention breakfast
Parsed page signals are cached in data/page_snapshots.json.
"""
import json
import re
from datetime import date

from gaps import fetch
from gaps.config import BREAKFAST_SHARE, PAGE_SNAPSHOTS
from gaps.util import read_json, slugify, write_json

MAX_PAGES_PER_CITY = 30


def _state(html, name):
    i = html.find(f"window.{name}")
    if i < 0:
        return {}
    j = html.find("=", i) + 1
    try:
        return json.JSONDecoder().raw_decode(html[j:].lstrip())[0]
    except ValueError:
        return {}


def _faq_text(html, qna):
    parts = [json.dumps(qna)] if qna else []
    for block in re.findall(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        if '"FAQPage"' in block:
            parts.append(re.sub(r"<[^>]+>", " ", block))
    return " ".join(parts)[:5000]


def snapshot(html):
    st, apollo = _state(html, "__INITIAL_STATE__"), _state(html, "__APOLLO_STATE__")
    content = st.get("content", {}).get("seo", {}).get("content", {})
    desc = (content.get("description") or {}).get("content") or ""
    first_p = re.search(r"<p>(.*?)</p>", desc, re.S)
    first_p = re.sub(r"<[^>]+>", " ", first_p.group(1)).strip() if first_p else ""
    qna = (content.get("qna") or {}).get("content") or []
    items = {k.split(":")[1]: v.get("name") for k, v in apollo.items() if re.match(r"^HotelSearchItem:\d+$", k)}
    return {
        "listed_hotels": dict(sorted(items.items(), key=lambda kv: int(kv[0]))),
        "min_price": int(content["minPrice"]) if str(content.get("minPrice") or "").isdigit() else None,
        "has_faq": '"FAQPage"' in html or bool(qna) or bool(content.get("nearMeFAQs")),
        "faq_text": _faq_text(html, qna),
        "first_paragraph": first_p[:600],
        "fetched": date.today().isoformat(),
    }


def pages_to_check(urls_by_key, city_slug, hotels):
    """City page, city-amenity and city-category pages, then locality pages where we have hotels.

    Returns [(url, key)] where key = (place_type, place_slug, filter_type, filter).
    """
    inv_localities = {f"{slugify(h['locality'])}-{city_slug}" for h in hotels if h["locality"]}
    ranked = []
    for key, url in urls_by_key.items():
        ptype, slug, ftype, _ = key
        if ptype == "city":
            ranked.append((0 if ftype is None else 1 if ftype == "amenity" else 2, url, key))
        elif ptype == "locality" and slug in inv_localities:
            ranked.append((3 if ftype is None else 4, url, key))
    return [(u, k) for _, u, k in sorted(ranked)][:MAX_PAGES_PER_CITY]


def check(pages, live_prices, hotel_sitemap, hotels_for_key, refresh=False):
    """pages: [(url, key)]; hotels_for_key(key) -> inventory hotels (existing live) for that page's place."""
    cache = read_json(PAGE_SNAPSHOTS, {})
    live_ids = {m.group(1) for u in hotel_sitemap for m in [re.search(r"-(\d+)/?$", u)] if m}
    # Listing pages use live hotel ids; live_prices is keyed by inventory id, so key by the live URL's id.
    live_by_page_id = {re.search(r"-(\d+)/?$", v["url"]).group(1): v["price_from"] for v in live_prices.values()}
    issues = []
    for url, key in pages:
        if url not in cache or refresh or "faq_text" not in cache[url]:
            html = fetch.get(url)
            if not html:
                continue
            cache[url] = snapshot(html)
            write_json(PAGE_SNAPSHOTS, cache)
        s = cache[url]
        listed = s["listed_hotels"]

        gone = [i for i in listed if i not in live_ids]
        if gone:
            names = ", ".join(f"{listed[i]} ({i})" for i in gone)
            issues.append({"url": url, "issue_type": "lists_inactive_hotels",
                           "detail": f"Page lists hotel(s) not in Treebo's live hotel sitemap: {names}.",
                           "suggested_fix": "Remove hotels that are no longer live from this listing."})

        live = [live_by_page_id[i] for i in listed if i in live_by_page_id]
        if s["min_price"] and live and s["min_price"] != min(live):
            issues.append({"url": url, "issue_type": "outdated_price",
                           "detail": f"Page says hotels from ₹{s['min_price']}, but the lowest live starting price "
                                     f"of the hotels it lists is ₹{min(live)}.",
                           "suggested_fix": f"Update the 'from' price to ₹{min(live)} or compute it from live prices."})

        words = len(s["first_paragraph"].split())
        if not s["first_paragraph"] or words > 60 or not re.search(r"\d", s["first_paragraph"]):
            why = "no About/intro text" if not s["first_paragraph"] else \
                f"intro is {words} words of general text with no concrete facts"
            issues.append({"url": url, "issue_type": "no_quick_answer",
                           "detail": f"No direct answer near the top ({why}).",
                           "suggested_fix": "Add a 1-2 sentence quick answer under the H1: how many Treebo hotels, "
                                            "starting price and 2-3 hotel names, from inventory data."})

        if not s["has_faq"]:
            issues.append({"url": url, "issue_type": "no_faqs",
                           "detail": "No FAQ section or FAQPage schema on the page.",
                           "suggested_fix": "Add 4-5 FAQs with FAQPage schema answering common traveller questions "
                                            "for this place + filter."})

        if key[0] in ("city", "locality") and key[2] is None:
            ph = hotels_for_key(key)
            bf = [h for h in ph if "free-breakfast" in h["amenities"]]
            text = (s["first_paragraph"] + " " + s["faq_text"]).lower()
            if len(ph) >= 2 and len(bf) / len(ph) > BREAKFAST_SHARE and "breakfast" not in text:
                issues.append({"url": url, "issue_type": "breakfast_not_stated",
                               "detail": f"{len(bf)} of {len(ph)} Treebo hotels here include breakfast, but "
                                         "breakfast included is not stated clearly in the quick answer or FAQs.",
                               "suggested_fix": "State 'breakfast included' in the quick answer and add an FAQ "
                                                "such as 'Do Treebo hotels here include breakfast?' with the "
                                                "hotel names, from inventory data."})
    return issues
