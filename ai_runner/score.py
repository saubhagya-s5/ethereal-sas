"""City scores in the contracts/city_score.json shape.

One "answer" = one query x engine x run.
  mention_rate   answers mentioning Treebo / all answers
  share_of_voice Treebo brand mentions / all brand mentions (one per brand per answer)
  wrong_facts    Treebo claims with status wrong or outdated
  top_competitor non-Treebo brand mentioned in the most answers
  by_engine      mention_rate per engine; null for engines that were skipped
"""
from __future__ import annotations

from collections import Counter

from .config import ENGINES


def _rate(rows: list[dict]) -> float | None:
    return round(sum(r["treebo_mentioned"] for r in rows) / len(rows), 3) if rows else None


def city_score(city: str, results: list[dict]) -> dict:
    total_brands = sum(len(r["brands_mentioned"]) for r in results)
    treebo = sum(1 for r in results if "Treebo" in r["brands_mentioned"])
    competitors = Counter(b for r in results for b in r["brands_mentioned"] if b != "Treebo")
    return {
        "city": city,
        "mention_rate": _rate(results) or 0.0,
        "share_of_voice": round(treebo / total_brands, 3) if total_brands else 0.0,
        "wrong_facts": sum(1 for r in results for c in r["treebo_claims"] if c["status"] in ("wrong", "outdated")),
        "top_competitor": competitors.most_common(1)[0][0] if competitors else None,
        "by_engine": {e: _rate([r for r in results if r["engine"] == e]) for e in ENGINES},
    }
