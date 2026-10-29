"""Thin Claude wrapper. Returns None when no key is set or the call fails, so callers can
fall back to template text and the demo still works offline."""
import json
import logging
import os

import anthropic
from dotenv import load_dotenv

from generator.data import ROOT

load_dotenv(ROOT / ".env")
log = logging.getLogger(__name__)

MODEL = os.getenv("AEO_MODEL", "claude-opus-5-5")


def available() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def _call(system: str, prompt: str, output_config: dict, max_tokens: int = 16000) -> str | None:
    if not available():
        return None
    try:
        response = anthropic.Anthropic().beta.messages.create(
            model=MODEL,
            max_tokens=max_tokens,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config=output_config,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
    except anthropic.APIConnectionError as e:
        log.warning("Claude unreachable: %s", e)
        return None
    except anthropic.APIStatusError as e:
        log.warning("Claude API error %s: %s", e.status_code, e.message)
        return None
    if response.stop_reason in ("refusal", "max_tokens"):
        log.warning("Claude stopped early: %s", response.stop_reason)
        return None
    return "".join(b.text for b in response.content if b.type == "text")


def complete_json(system: str, prompt: str, schema: dict, effort: str = "medium") -> dict | None:
    """Structured output: the reply is guaranteed to match `schema`."""
    text = _call(system, prompt, {"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    return json.loads(text) if text else None


def complete_text(system: str, prompt: str, effort: str = "low") -> str | None:
    return _call(system, prompt, {"effort": effort})
