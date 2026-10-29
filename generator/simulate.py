"""Before/after test: does our new page get Treebo mentioned?

For each traveller question behind an opportunity, give the LLM the pages AI engines actually
cited (from ai_results.json) and ask the question - once without our page ("before") and once
with it added ("after"). Count how often Treebo is mentioned and how many of the page's hotels
are named. Fetched pages are cached in output/raw/sim/ (git-ignored).

Usage: python -m generator.simulate --opp blr-koramangala-free-breakfast [--runs 3]
"""
import argparse
import hashlib
import json
import random
import re
from datetime import datetime, timezone

import httpx
from lxml import html as lxml_html

from generator import llm
from generator.data import OUTPUT, PAGES, SIMULATION, inventory, opportunity, page_hotels, related_results

CACHE = OUTPUT / "raw" / "sim"
MAX_CHARS = 8000
MAX_SOURCES = 6

SYSTEM = """You are an AI travel assistant like ChatGPT or Perplexity. Answer the traveller's question
using ONLY the web pages provided. Recommend specific hotels by name, best first, and say briefly why.
If the pages don't answer the question, say so."""


def page_text(raw_html: str) -> str:
    """Visible text first, then structured data (JSON-LD), so the visible copy always fits."""
    doc = lxml_html.fromstring(raw_html)
    structured = [s.text_content() for s in doc.xpath("//script[@type='application/ld+json']")]
    for bad in doc.xpath("//script|//style|//noscript|//svg|//head"):
        bad.drop_tree()
    text = re.sub(r"\s+", " ", doc.text_content()).strip()
    return " ".join([text, *structured])[:MAX_CHARS]


def fetch(url: str) -> str | None:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{hashlib.sha1(url.encode()).hexdigest()[:16]}.txt"
    if path.exists():
        return path.read_text(encoding="utf-8") or None
    try:
        r = httpx.get(url, timeout=15, follow_redirects=True,
                      headers={"User-Agent": "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/126 Safari/537.36"})
        r.raise_for_status()
        text = page_text(r.text)
    except (httpx.HTTPError, ValueError):
        text = ""
    path.write_text(text, encoding="utf-8")  # cache failures too, so a dead URL isn't retried every run
    return text or None


def ask(question: str, sources: list[tuple[str, str]]) -> str | None:
    docs = "\n\n".join(f"<page url=\"{url}\">\n{text}\n</page>" for url, text in sources)
    return llm.complete_text(SYSTEM, f"WEB PAGES:\n{docs}\n\nQUESTION: {question}", effort="low")


def score(answer: str, hotel_names: list[str]) -> dict:
    low = answer.lower()
    named = [n for n in hotel_names if n.lower() in low]
    first = low.find("treebo")
    return {"treebo_mentioned": first >= 0, "hotels_named": named,
            "treebo_first": first >= 0 and not re.search(r"\b(fabhotels|oyo|zostel|itsy|mmt|makemytrip)\b", low[:first])}


def run(opp: dict, runs: int = 3, max_questions: int = 4, seed: int = 7) -> dict:
    if not llm.available():
        raise RuntimeError("Simulation needs ANTHROPIC_API_KEY in .env")
    page_path = PAGES / f"{opp['opp_id']}.html"
    if not page_path.exists():
        raise FileNotFoundError(f"Generate the page first: {page_path}")
    ours = (opp["proposed_url"], page_text(page_path.read_text(encoding="utf-8")))
    hotel_names = [h["name"] for h in page_hotels(opp, inventory())]
    queries, results = related_results(opp)
    rng = random.Random(seed)

    rows = []
    for q in queries[:max_questions]:
        cited = list(dict.fromkeys(u for r in results if r["query_id"] == q["query_id"] for u in r["cited_urls"]))
        cited = [u for u in cited if u.rstrip("/") != opp["proposed_url"].rstrip("/")]
        sources = [(u, t) for u in cited[:MAX_SOURCES] if (t := fetch(u))]
        row = {"query_id": q["query_id"], "question": q["text"], "sources": [u for u, _ in sources], "before": [], "after": []}
        for i in range(runs):
            after_sources = sources + [ours]
            rng.shuffle(after_sources)  # don't let our page always sit last
            for arm, srcs in (("before", sources), ("after", after_sources)):
                answer = ask(q["text"], srcs)
                if answer:
                    row[arm].append({"run": i + 1, **score(answer, hotel_names), "answer": answer})
        rows.append(row)

    def rate(arm: str) -> float | None:
        runs_ = [r for row in rows for r in row[arm]]
        return round(sum(r["treebo_mentioned"] for r in runs_) / len(runs_), 3) if runs_ else None

    result = {
        "opp_id": opp["opp_id"], "model": llm.MODEL, "runs": runs,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "before_mention_rate": rate("before"), "after_mention_rate": rate("after"),
        "queries": rows,
    }
    SIMULATION.mkdir(parents=True, exist_ok=True)
    (SIMULATION / f"{opp['opp_id']}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


def load_result(opp_id: str) -> dict | None:
    path = SIMULATION / f"{opp_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--opp", required=True)
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    out = run(opportunity(args.opp), runs=args.runs)
    print(f"Treebo mention rate: before {out['before_mention_rate']} -> after {out['after_mention_rate']}")
