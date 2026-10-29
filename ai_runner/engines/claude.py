"""Claude via the Anthropic Messages API with the server-side web search tool."""
from __future__ import annotations

from .base import Engine, EngineAnswer, dedupe

MAX_CONTINUATIONS = 3


class ClaudeEngine(Engine):
    name = "claude"

    def __init__(self, model: str):
        super().__init__(model)
        import anthropic  # reads ANTHROPIC_API_KEY

        self.client = anthropic.AsyncAnthropic()

    async def ask(self, question: str, city: str, run: int = 1) -> EngineAnswer:
        messages = [{"role": "user", "content": question}]
        tools = [{
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": 5,
            "user_location": {"type": "approximate", "city": city, "country": "IN", "timezone": "Asia/Kolkata"},
        }]
        raws, text_parts, cited, searched = [], [], [], []
        for _ in range(MAX_CONTINUATIONS + 1):
            resp = await self.client.messages.create(
                model=self.model,
                max_tokens=8000,
                tools=tools,
                messages=messages,
                # On a safety decline, re-run on Anthropic's recommended fallback model.
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
                extra_body={"fallbacks": "default"},
            )
            raws.append(resp.model_dump(mode="json"))
            for block in resp.content:
                if block.type == "text":
                    text_parts.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        if getattr(c, "url", None):
                            cited.append(c.url)
                elif block.type == "web_search_tool_result" and isinstance(block.content, list):
                    searched += [r.url for r in block.content if getattr(r, "url", None)]
            if resp.stop_reason != "pause_turn":
                break
            # Long server-tool turn paused: send it back so Claude continues.
            messages = messages + [{"role": "assistant", "content": resp.content}]
        if resp.stop_reason == "refusal":
            text_parts = []
        return EngineAnswer(
            text="".join(text_parts).strip(),
            # Prefer URLs Claude actually cited; fall back to what it searched.
            citations=dedupe(cited or searched),
            model=resp.model,
            raw={"responses": raws, "stop_reason": resp.stop_reason},
        )
