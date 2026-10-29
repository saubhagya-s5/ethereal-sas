"""ChatGPT via the OpenAI Responses API with the web_search tool."""
from __future__ import annotations

from .base import Engine, EngineAnswer, dedupe


class ChatGPTEngine(Engine):
    name = "chatgpt"

    def __init__(self, model: str):
        super().__init__(model)
        from openai import AsyncOpenAI  # reads OPENAI_API_KEY

        self.client = AsyncOpenAI()

    async def ask(self, question: str, city: str, run: int = 1) -> EngineAnswer:
        resp = await self.client.responses.create(
            model=self.model,
            input=question,
            tools=[{"type": "web_search", "user_location": {"type": "approximate", "country": "IN", "city": city}}],
        )
        urls = []
        for item in resp.output or []:
            if getattr(item, "type", "") != "message":
                continue
            for part in item.content or []:
                for ann in getattr(part, "annotations", None) or []:
                    if getattr(ann, "type", "") == "url_citation":
                        urls.append(ann.url)
        return EngineAnswer(
            text=resp.output_text or "",
            citations=dedupe(urls),
            model=getattr(resp, "model", self.model),
            raw=resp.model_dump(mode="json"),
        )
