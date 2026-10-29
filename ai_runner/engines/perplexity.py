"""Perplexity Sonar via its chat completions HTTP API."""
from __future__ import annotations

import os

from .base import Engine, EngineAnswer, dedupe

URL = "https://api.perplexity.ai/chat/completions"


class PerplexityEngine(Engine):
    name = "perplexity"

    def __init__(self, model: str):
        super().__init__(model)
        import httpx

        self.client = httpx.AsyncClient(
            timeout=120,
            headers={"Authorization": f"Bearer {os.environ['PERPLEXITY_API_KEY']}"},
        )

    async def ask(self, question: str, city: str, run: int = 1) -> EngineAnswer:
        r = await self.client.post(URL, json={"model": self.model, "messages": [{"role": "user", "content": question}]})
        r.raise_for_status()
        data = r.json()
        urls = list(data.get("citations") or [])
        urls += [s.get("url") for s in data.get("search_results") or [] if s.get("url")]
        return EngineAnswer(
            text=data["choices"][0]["message"]["content"],
            citations=dedupe(urls),
            model=data.get("model", self.model),
            raw=data,
        )
