"""Paths, cities, engines and model settings for ai_runner."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INVENTORY_PATH = ROOT / "data" / "inventory.json"
LIVE_PRICES_PATH = ROOT / "data" / "live_prices.json"
HOTEL_MAP_PATH = ROOT / "data" / "hotel_id_map.json"
CONTRACTS_DIR = ROOT / "contracts"
OUTPUT_DIR = ROOT / "output"
DRYRUN_DIR = OUTPUT_DIR / "dryrun"
RAW_DIR = OUTPUT_DIR / "raw"
DRYRUN_RAW_DIR = RAW_DIR / "_dryrun"

TREEBO_BASE = "https://www.treebo.com"


@dataclass(frozen=True)
class City:
    name: str  # as in inventory.json
    slug: str  # as in Treebo URLs
    prefix: str  # query_id prefix


CITIES = {
    "pune": City("Pune", "pune", "pun"),
    "bengaluru": City("Bengaluru", "bangalore", "blr"),
}
CITY_ALIASES = {"bangalore": "bengaluru", "blr": "bengaluru", "pun": "pune"}


def get_city(key: str) -> City:
    key = key.strip().lower()
    key = CITY_ALIASES.get(key, key)
    if key not in CITIES:
        raise SystemExit(f"Unknown city '{key}'. Choose from: {', '.join(CITIES)}")
    return CITIES[key]


ENGINES = ["chatgpt", "gemini", "perplexity", "claude"]
ENGINE_KEYS = {
    "chatgpt": "OPENAI_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}

# Model names can be overridden in .env without code changes.
DEFAULT_MODELS = {
    "chatgpt": ("OPENAI_MODEL", "gpt-5"),
    # Alias for Google's current Flash model; gemini-2.5-flash is retired for new keys.
    "gemini": ("GEMINI_MODEL", "gemini-flash-latest"),
    "perplexity": ("PERPLEXITY_MODEL", "sonar"),
    "claude": ("CLAUDE_MODEL", "claude-opus-5-5"),
}
EXTRACT_CLAUDE_MODEL = ("EXTRACT_CLAUDE_MODEL", "claude-opus-5-5")
EXTRACT_OPENAI_MODEL = ("EXTRACT_OPENAI_MODEL", "gpt-5-mini")
EXTRACT_GEMINI_MODEL = ("EXTRACT_GEMINI_MODEL", "gemini-flash-latest")


def model_for(engine: str) -> str:
    env, default = DEFAULT_MODELS[engine]
    return os.environ.get(env) or default


def env_or(pair: tuple[str, str]) -> str:
    return os.environ.get(pair[0]) or pair[1]


RUNS = 2
CONCURRENCY_PER_ENGINE = 4

# Live prices: one night, one adult, a week out (avoids last-minute pricing).
LIVE_PRICE_DAYS_AHEAD = 7
LIVE_PRICE_MAX_AGE_HOURS = 24
# A quoted price within this fraction of the live price counts as correct.
PRICE_TOLERANCE = 0.25

SITEMAP_MAX_AGE_HOURS = 24
HOTEL_MAP_MAX_AGE_HOURS = 24
LANDMARK_RADIUS_KM = 4.0


def load_env() -> None:
    """Load .env from the repo root. Uses python-dotenv if installed, else a tiny parser."""
    path = ROOT / ".env"
    if not path.exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(path, override=False)
        return
    except ImportError:
        pass
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def has_key(engine: str) -> bool:
    return bool(os.environ.get(ENGINE_KEYS[engine], "").strip())


def rel(path: Path) -> str:
    """Repo-relative POSIX path, as stored in contracts (e.g. output/raw/...)."""
    return path.resolve().relative_to(ROOT).as_posix()
