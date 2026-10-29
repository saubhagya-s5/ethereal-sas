"""Thin LLM wrapper for Claude or Gemini. Returns None when no key is set or the call fails, so
callers can fall back to template text and the demo still works offline.

Provider: AEO_LLM=claude|gemini in .env, else Claude when ANTHROPIC_API_KEY is set, else Gemini
when GEMINI_API_KEY is set (Google AI Studio free tier).
"""
import json
import logging
import os
import time

import anthropic
import httpx
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types

from generator.data import ROOT

load_dotenv(ROOT / ".env")
log = logging.getLogger(__name__)


def _provider() -> str | None:
    chosen = os.getenv("AEO_LLM", "").lower()
    if chosen in ("claude", "gemini"):
        return chosen
    if os.getenv("ANTHROPIC_API_KEY"):
        return "claude"
    if os.getenv("GEMINI_API_KEY"):
        return "gemini"
    return None


PROVIDER = _provider()
MODEL = (os.getenv("AEO_MODEL", "claude-opus-5-5") if PROVIDER == "claude"
         else os.getenv("GEMINI_MODEL", "gemini-3.5-flash"))
# Used when the main Gemini model is overloaded (503/504), which happens often on the free tier.
GEMINI_FALLBACK = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
LABEL = {"claude": "Claude", "gemini": "Gemini"}.get(PROVIDER, "no LLM")
# Free-tier Gemini allows only a few requests a minute: retry rate limits a couple of times.
GEMINI_RETRIES = 3


def available() -> bool:
    return PROVIDER is not None


def _claude(system: str, prompt: str, output_config: dict, max_tokens: int = 16000) -> str | None:
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


def _gemini(system: str, prompt: str, schema: dict | None = None) -> str | None:
    config = genai_types.GenerateContentConfig(system_instruction=system)
    if schema:
        config.response_mime_type = "application/json"
        config.response_json_schema = schema
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=genai_types.HttpOptions(timeout=60_000))
    model = MODEL
    for attempt in range(GEMINI_RETRIES):
        try:
            return client.models.generate_content(model=model, contents=prompt, config=config).text
        except genai_errors.APIError as e:
            if attempt == GEMINI_RETRIES - 1:
                log.warning("Gemini API error %s: %s", e.code, e.message)
                return None
            if e.code in (503, 504) and model != GEMINI_FALLBACK:
                log.warning("%s overloaded (%s), switching to %s", model, e.code, GEMINI_FALLBACK)
                model = GEMINI_FALLBACK
            elif e.code == 429:
                wait = 15 * (attempt + 1)
                log.warning("Gemini rate limited, retrying in %ss", wait)
                time.sleep(wait)
            else:
                log.warning("Gemini API error %s: %s", e.code, e.message)
                return None
        except httpx.HTTPError as e:  # client-side timeout or connection drop
            log.warning("Gemini request failed on %s: %s", model, e)
            model = GEMINI_FALLBACK
    return None


def complete_json(system: str, prompt: str, schema: dict, effort: str = "medium") -> dict | None:
    """Structured output: the reply matches `schema`."""
    if PROVIDER == "claude":
        text = _claude(system, prompt, {"effort": effort, "format": {"type": "json_schema", "schema": schema}})
    elif PROVIDER == "gemini":
        text = _gemini(system, prompt, schema)
    else:
        return None
    try:
        return json.loads(text) if text else None
    except json.JSONDecodeError:
        log.warning("%s returned invalid JSON", LABEL)
        return None


def complete_text(system: str, prompt: str, effort: str = "low") -> str | None:
    if PROVIDER == "claude":
        return _claude(system, prompt, {"effort": effort})
    if PROVIDER == "gemini":
        return _gemini(system, prompt)
    return None
