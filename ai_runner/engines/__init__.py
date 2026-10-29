"""Engine factory. Real engines import their SDK lazily so dry-run needs no packages."""
from __future__ import annotations

from ..config import model_for
from .base import Engine, EngineAnswer


def make_engine(name: str) -> Engine:
    model = model_for(name)
    if name == "chatgpt":
        from .chatgpt import ChatGPTEngine
        return ChatGPTEngine(model)
    if name == "gemini":
        from .gemini import GeminiEngine
        return GeminiEngine(model)
    if name == "perplexity":
        from .perplexity import PerplexityEngine
        return PerplexityEngine(model)
    if name == "claude":
        from .claude import ClaudeEngine
        return ClaudeEngine(model)
    raise ValueError(f"unknown engine {name}")


__all__ = ["Engine", "EngineAnswer", "make_engine"]
