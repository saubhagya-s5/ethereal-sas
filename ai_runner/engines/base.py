"""Common engine interface. Each engine asks one question and returns text + cited URLs."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class EngineAnswer:
    text: str
    citations: list[str]
    model: str
    raw: dict = field(default_factory=dict)  # full provider response, JSON-safe


class Engine:
    name = ""
    # How the engine was asked (e.g. "grounded" vs "no_search"). Cached answers are only
    # reused when the variant matches, so a no-search answer never stands in for a searched one.
    variant = "default"

    def __init__(self, model: str):
        self.model = model

    async def ask(self, question: str, city: str, run: int = 1) -> EngineAnswer:
        raise NotImplementedError


def dedupe(urls: list[str]) -> list[str]:
    seen, out = set(), []
    for u in urls:
        if u and u not in seen:
            seen.add(u)
            out.append(u)
    return out
