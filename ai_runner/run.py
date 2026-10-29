"""Run the AI visibility check for one city.

    python -m ai_runner.run --city bengaluru           # real engines (keys from .env)
    python -m ai_runner.run --city pune --dry-run      # fake answers, no keys or network

Hotel truth: data/hotel_id_map.json (production names / active flags) and
data/live_prices.json (prices); both are refreshed by real runs, never by dry-run.

Writes output/queries.json, output/ai_results.json and output/city_scores.json
(dry-run: output/dryrun/...). Raw answers go to output/raw/{engine}/{query_id}_run{n}.json.
"""
from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from .cache import RawCache, read_json, write_json
from .config import (
    CONCURRENCY_PER_ENGINE,
    CONTRACTS_DIR,
    DRYRUN_DIR,
    DRYRUN_RAW_DIR,
    ENGINES,
    HOTEL_MAP_PATH,
    LIVE_PRICES_PATH,
    OUTPUT_DIR,
    RAW_DIR,
    RUNS,
    City,
    get_city,
    has_key,
    load_env,
    rel,
)
from .engines import make_engine
from .engines.fake import FakeEngine
from .extract import finalize, llm_extract, rule_extract
from .factcheck import check_claims
from .hotel_map import city_hotel_ids, ensure_hotel_map
from .inventory import Inventory
from .live_prices import ensure_live_prices, fake_live_prices
from .miss_reason import miss_reason
from .queries import generate_queries
from .score import city_score
from .sitemap import CACHE_PATH, Sitemap, load_hotel_pages, load_sitemap, offline_sitemap

RETRIES = 3


def log(msg: str) -> None:
    print(msg, flush=True)


def _is_rate_limit(ex: Exception) -> bool:
    text = f"{type(ex).__name__} {ex}"
    return "429" in text or "RESOURCE_EXHAUSTED" in text or "RateLimit" in text


def short_error(ex: Exception) -> str:
    return f"{type(ex).__name__}: {str(ex)[:160]}"


async def with_retries(make_call):
    """Retry an API call. Rate limits (429) wait ~a minute, since free tiers count per minute;
    other errors back off briefly."""
    for attempt in range(1, RETRIES + 1):
        try:
            return await make_call()
        except Exception as ex:
            if attempt == RETRIES:
                raise
            if _is_rate_limit(ex):
                m = re.search(r"retry in ([\d.]+)s", str(ex))
                await asyncio.sleep((float(m[1]) if m else 60) + 2)
            else:
                await asyncio.sleep(2 ** attempt)


def merge_rows(path: Path, new: list[dict], key) -> list[dict]:
    """Replace rows with the same key, keep everything else (e.g. the other city)."""
    rows = read_json(path) if path.exists() else []
    fresh = {key(r) for r in new}
    merged = [r for r in rows if key(r) not in fresh] + new
    write_json(path, merged)
    return merged


def check_contract(row: dict, name: str) -> None:
    sample = read_json(CONTRACTS_DIR / f"{name}.json")
    if list(row) != list(sample):
        raise ValueError(f"{name} keys {list(row)} do not match contract {list(sample)}")
    if name == "ai_result":
        for c in row["treebo_claims"]:
            if list(c) != list(sample["treebo_claims"][0]):
                raise ValueError(f"treebo_claims keys {list(c)} do not match contract")
    if name == "city_score" and list(row["by_engine"]) != list(sample["by_engine"]):
        raise ValueError("by_engine keys do not match contract")


def load_inventory(city: City, dry: bool, hotel_pages=None) -> Inventory:
    """Staging + treebo.com hotels, with production truth from data/hotel_id_map.json."""
    if not dry:
        try:
            ensure_hotel_map(city_hotel_ids(city, hotel_pages))
        except Exception as e:
            log(f"WARNING: could not refresh hotel map ({e}); using the existing file if any.")
    if not HOTEL_MAP_PATH.exists():
        log("WARNING: no data/hotel_id_map.json; falling back to staging names and active flags.")
    inv = Inventory.load()
    hotels = inv.city(city.name)
    src = Counter(h.get("source") for h in hotels if h["active"])
    log(f"{city.name}: {sum(h['active'] for h in hotels)} active hotels in production "
        f"({src['production']} from staging, {src['production_only']} only on treebo.com; {rel(HOTEL_MAP_PATH)})")
    return inv


def load_live_prices(city: City, inv: Inventory, dry: bool) -> dict:
    if dry:
        return fake_live_prices(city.name, inv)
    try:
        data = ensure_live_prices(city.name, inv)
        log(f"Live prices: {sum(1 for p in data['prices'].values() if p.get('city') == city.name)} {city.name} hotels "
            f"for {data['checkin']} ({rel(LIVE_PRICES_PATH)})")
        return data
    except Exception as e:  # network/API trouble: price claims are skipped, nothing else breaks
        log(f"WARNING: live prices unavailable ({e}). Price claims will not be checked.")
        return read_json(LIVE_PRICES_PATH) if LIVE_PRICES_PATH.exists() else {"prices": {}}


def load_pages(inv: Inventory, dry: bool) -> Sitemap:
    if dry:
        return offline_sitemap(inv)
    try:
        sm = load_sitemap()
    except Exception as e:
        if not CACHE_PATH.exists():
            raise SystemExit(f"Could not fetch Treebo sitemaps ({e}) and no cache exists; miss reasons need them.")
        log(f"WARNING: sitemap fetch failed ({e}); using the older cached copy.")
        sm = Sitemap(set(read_json(CACHE_PATH)["paths"]), "stale cache")
    log(f"Treebo sitemap: {len(sm.paths)} listing pages ({sm.source})")
    return sm


async def run_city(args) -> None:
    load_env()
    city = get_city(args.city)
    dry = args.dry_run
    out_dir = DRYRUN_DIR if dry else OUTPUT_DIR
    cache = RawCache(DRYRUN_RAW_DIR if dry else RAW_DIR)
    hotel_pages = None
    if not dry:
        try:
            hotel_pages = load_hotel_pages()
            log(f"Treebo hotel pages: {len(hotel_pages.ids(city.slug))} in {city.name} on treebo.com")
        except Exception as e:
            log(f"WARNING: hotel sitemap unavailable ({e}); using staging hotels and unknown names are checked against inventory only.")
    inv = load_inventory(city, dry, hotel_pages)

    queries = generate_queries(city, inv)
    if args.limit:
        queries = queries[: args.limit]
    merge_rows(out_dir / "queries.json", queries, key=lambda q: q["query_id"])
    log(f"{city.name}: {len(queries)} queries -> {rel(out_dir / 'queries.json')}")

    live = load_live_prices(city, inv, dry)
    sm = load_pages(inv, dry)

    names = args.engines.split(",") if args.engines else ENGINES
    engines = {}
    for n in names:
        if n not in ENGINES:
            raise SystemExit(f"Unknown engine '{n}'. Choose from: {', '.join(ENGINES)}")
        if dry:
            engines[n] = FakeEngine(n, inv, {q["text"]: q for q in queries}, live)
        elif not has_key(n):
            log(f"Skipping {n}: no key in .env (cached answers are still used)")
        else:
            try:
                engines[n] = make_engine(n)
                log(f"Using {n}: {engines[n].model} ({engines[n].variant})")
            except ImportError as e:
                log(f"Skipping {n}: {e}. Run: pip install -r requirements.txt")

    engine_sems = {n: asyncio.Semaphore(args.concurrency) for n in names}
    extract_sem = asyncio.Semaphore(args.concurrency)
    stats = Counter()

    async def get_answer(q: dict, e: str, run: int) -> dict | None:
        cached = cache.get_answer(e, q["query_id"], run)
        same_variant = e not in engines or cached and cached.get("variant", "default") == engines[e].variant
        if cached and cached.get("question") == q["text"] and same_variant:
            stats["cached"] += 1
            return cached
        if e not in engines:
            return None
        async with engine_sems[e]:
            try:
                ans = await with_retries(lambda: engines[e].ask(q["text"], city.name, run=run))
            except Exception as ex:
                log(f"  {e} {q['query_id']} run{run} failed: {short_error(ex)}")
                stats["failed"] += 1
                return None
        record = {
            "query_id": q["query_id"], "engine": e, "run": run, "city": city.name,
            "question": q["text"], "model": ans.model, "variant": engines[e].variant,
            "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "answer": ans.text, "citations": ans.citations, "raw_response": ans.raw,
        }
        cache.put_answer(e, q["query_id"], run, record)
        stats["called"] += 1
        return record

    async def get_extract(q: dict, e: str, run: int, rec: dict) -> dict:
        cached = cache.get_extract(e, q["query_id"], run)
        # Reuse only if it came from this exact answer; re-extract rule-based results
        # once an LLM extractor is available.
        if (cached and cached.get("answer_asked_at") == rec["asked_at"]
                and (dry or cached.get("extractor") != "rules" or not engines_can_extract())):
            return cached
        async with extract_sem:
            if dry:
                raw, extractor = rule_extract(rec["answer"]), "rules"
            else:
                raw, extractor = await with_retries(lambda: llm_extract(rec["answer"], city.name))
        ext = {"extractor": extractor, "answer_asked_at": rec["asked_at"], **raw}
        cache.put_extract(e, q["query_id"], run, ext)
        return ext

    async def process(q: dict, e: str, run: int) -> dict | None:
        rec = await get_answer(q, e, run)
        if rec is None:
            return None
        try:
            ext = await get_extract(q, e, run, rec)
        except Exception as ex:
            log(f"  extract {e} {q['query_id']} run{run} failed: {short_error(ex)}")
            stats["extract_failed"] += 1
            return None
        facts = finalize(ext, rec.get("citations", []))
        claims = check_claims(facts["treebo_claims"], inv, city.name, live, hotel_pages)
        row = {
            "query_id": q["query_id"],
            "engine": e,
            "run": run,
            "raw_answer_path": rel(cache.answer_path(e, q["query_id"], run)),
            "brands_mentioned": facts["brands_mentioned"],
            "treebo_mentioned": facts["treebo_mentioned"],
            "treebo_rank": facts["treebo_rank"],
            "cited_urls": facts["cited_urls"],
            "treebo_claims": claims,
            "miss_reason": None if facts["treebo_mentioned"] else miss_reason(q, city, inv, sm),
        }
        check_contract(row, "ai_result")
        return row

    jobs = [process(q, e, r) for q in queries for e in names for r in range(1, args.runs + 1)]
    log(f"Running {len(jobs)} answers ({len(queries)} queries x {len(names)} engines x {args.runs} runs)...")
    rows = [r for r in await asyncio.gather(*jobs) if r]
    if not rows:
        raise SystemExit("No answers: add API keys to .env, or use --dry-run.")

    results = merge_rows(out_dir / "ai_results.json", rows, key=lambda r: (r["query_id"], r["engine"], r["run"]))
    city_rows = [r for r in results if r["query_id"].startswith(city.prefix + "-")]
    score = city_score(city.name, city_rows)
    check_contract(score, "city_score")
    merge_rows(out_dir / "city_scores.json", [score], key=lambda s: s["city"])

    log(f"\nAnswers: {stats['called']} new, {stats['cached']} from cache, {stats['failed']} failed")
    log(f"Wrote {rel(out_dir / 'ai_results.json')} and {rel(out_dir / 'city_scores.json')}")
    log(f"\n{city.name}: mention_rate {score['mention_rate']:.0%}, share_of_voice {score['share_of_voice']:.0%}, "
        f"wrong_facts {score['wrong_facts']}, top_competitor {score['top_competitor']}")
    for e, rate in score["by_engine"].items():
        log(f"  {e:<11} {'skipped' if rate is None else f'{rate:.0%}'}")
    misses = Counter(r["miss_reason"] for r in city_rows if r["miss_reason"])
    log("Miss reasons: " + ", ".join(f"{k} {v}" for k, v in misses.most_common()))
    statuses = Counter(c["status"] for r in city_rows for c in r["treebo_claims"])
    log("Claims: " + ", ".join(f"{k} {v}" for k, v in statuses.most_common()))


def engines_can_extract() -> bool:
    return has_key("claude") or has_key("chatgpt") or has_key("gemini")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # ₹ on Windows consoles
    ap = argparse.ArgumentParser(description="Ask AI engines Treebo traveller questions and score the answers.")
    ap.add_argument("--city", default="bengaluru", help="bengaluru (default) | pune")
    ap.add_argument("--dry-run", action="store_true", help="fake answers; no API keys or network needed")
    ap.add_argument("--runs", type=int, default=RUNS)
    ap.add_argument("--engines", help="comma list, e.g. chatgpt,claude (default: all four)")
    ap.add_argument("--limit", type=int, help="only the first N queries (quick tests)")
    ap.add_argument("--concurrency", type=int, default=CONCURRENCY_PER_ENGINE, help="parallel calls per engine")
    asyncio.run(run_city(ap.parse_args()))


if __name__ == "__main__":
    main()
