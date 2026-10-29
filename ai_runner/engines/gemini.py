"""Gemini via google-genai, with Google Search grounding by default.

Set GEMINI_GROUNDING=off in .env to ask without search (answers from the model's own
knowledge, no citations). Free keys often have no grounding quota and get 429 on every
grounded call.
"""
from __future__ import annotations

import os

from .base import Engine, EngineAnswer, dedupe
from .redirects import resolve_redirects


class GeminiEngine(Engine):
    name = "gemini"

    def __init__(self, model: str):
        super().__init__(model)
        from google import genai
        from google.genai import types

        self.grounded = os.environ.get("GEMINI_GROUNDING", "on").strip().lower() not in ("off", "0", "false", "no")
        self.variant = "grounded" if self.grounded else "no_search"
        # Timeout in ms so a stuck request fails instead of hanging the run. One SDK attempt:
        # run.py already retries, and SDK retries on a quota 429 can stall for minutes.
        self.client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(timeout=120_000, retry_options=types.HttpRetryOptions(attempts=1)),
        )

    async def ask(self, question: str, city: str, run: int = 1) -> EngineAnswer:
        from google.genai import types

        tools = [types.Tool(google_search=types.GoogleSearch())] if self.grounded else None
        resp = await self.client.aio.models.generate_content(
            model=self.model,
            contents=question,
            config=types.GenerateContentConfig(tools=tools),
        )
        chunks = []
        cand = (resp.candidates or [None])[0]
        meta = getattr(cand, "grounding_metadata", None) if cand else None
        for ch in (getattr(meta, "grounding_chunks", None) or []):
            web = getattr(ch, "web", None)
            if web and web.uri:
                chunks.append((web.uri, web.title or ""))
        # Grounding URIs are vertexaisearch redirect links; resolve them to the real source.
        urls = await resolve_redirects(chunks)
        return EngineAnswer(
            text=resp.text or "",
            citations=dedupe(urls),
            model=resp.model_version or self.model,
            raw=resp.model_dump(mode="json"),
        )
