# ai_runner (Person B, steps 2-5)

Asks AI engines traveller questions, caches the raw answers, extracts brands and Treebo claims,
fact-checks the claims, explains each miss and scores the city.

```bash
python -m ai_runner.run --dry-run                 # Bengaluru, fake answers, no keys or packages needed
python -m ai_runner.run --city pune --dry-run
python -m ai_runner.run --city bengaluru          # real engines; needs keys in .env and pip install -r requirements.txt
python -m ai_runner.run --engines claude,perplexity --limit 5 --runs 1
python -m unittest discover -s ai_runner/tests -t .
```

## Outputs

| File | Shape |
|---|---|
| `output/queries.json` | `contracts/query.json` (20 per city) |
| `output/ai_results.json` | `contracts/ai_result.json`, one row per query x engine x run |
| `output/city_scores.json` | `contracts/city_score.json`, one row per city |
| `output/raw/{engine}/{query_id}_run{n}.json` | question, answer, citations, model, full provider response |
| `output/raw/_extract/...` | cached extraction for each answer |
| `output/raw/_sitemap/treebo_urls.json` | Treebo listing-page paths (24h cache) |

Dry-run writes to `output/dryrun/` and `output/raw/_dryrun/` so fake data never mixes with real results.
Re-running never re-calls an engine for a cached answer (unless the question text changed).

## Hotel truth (staging is unreliable)

`ai_runner` creates two files in `data/` from Treebo's public website APIs (read-only GETs, no DB):

- `data/hotel_id_map.json`: production name, active flag, city, locality, coordinates, URL and amenities
  (hotel + room facilities) for every staging `hotel_id` **plus every hotel on treebo.com's
  `sitemap-hotel.xml`** for the city (`/api/v2/hotels/{id}/details/`). Staging only had 11 of the 81 live
  Bengaluru hotels (and marked 43 churned ones active); the map has all 81, and all 53 in Pune.
  `python -m ai_runner.hotel_map --city bengaluru`
  Hotels only on treebo.com have no categories, rating or staging price: categories are treated as
  unknown (they don't exclude a hotel from a "budget"/"family" question, and category claims about them
  are dropped). Itsy Hotels count as Treebo.
- `data/live_prices.json`: live price (pre-tax and with tax) for one night a week out, for active hotels
  (`/api/v8/pricing/hotels/`). `python -m ai_runner.live_prices --city bengaluru`

Both refresh after 24h on real runs. Dry-run reads the map if present, but never fetches, and uses fake prices.

## Fact-check rules

| Claim | Checked against | Status |
|---|---|---|
| Hotel name | map (production + staging names), then treebo.com hotel sitemap | not on treebo.com: `wrong`; brand-only mentions ("Treebo / FabHotels") skipped |
| Hotel inactive / not in production | map | `outdated` ("open for booking"), once per hotel |
| Amenity | map (production facilities), staging as fallback | `correct` / `wrong`; unmappable words dropped |
| Category | `inventory.json` slugs | `correct` / `wrong`; dropped when the hotel has no category data |
| Locality | map (falls back to staging) | `correct` / `wrong` |
| Rating | `inventory.json` (TripAdvisor) | within 0.3: `correct`, else `wrong` |
| Price | `live_prices.json` only, never inventory prices | within 25% of pre-tax or with-tax price: `correct`, else `outdated` |
| Anything else | | dropped, never guessed |

## Miss reasons (only when Treebo is not mentioned)

1. `no_inventory`: no active production hotel matches the place + filter.
2. `ai_trusts_others`: Treebo's sitemap has the exact page for the question.
3. `weak_page`: only a broader page exists (e.g. locality page but no locality+amenity page).
4. `no_page`: hotels match but there is no relevant page (the city page alone doesn't count).

## Engines

| Engine | API | Model env (default) |
|---|---|---|
| chatgpt | OpenAI Responses + `web_search` | `OPENAI_MODEL` (`gpt-5`) |
| gemini | google-genai + Google Search grounding | `GEMINI_MODEL` (`gemini-2.5-flash`) |
| perplexity | Sonar chat completions | `PERPLEXITY_MODEL` (`sonar`) |
| claude | Messages + `web_search_20260209`, server-side fallback on refusal | `CLAUDE_MODEL` (`claude-opus-5-5`) |

An engine without a key is skipped (its cached answers are still used). Extraction uses Claude
(`EXTRACT_CLAUDE_MODEL`, default `claude-opus-5-5`, effort low) or OpenAI (`EXTRACT_OPENAI_MODEL`) with a
strict JSON schema. Temperature isn't sent: the Claude API rejects it on every model since 2026-10-21, and
OpenAI's reasoning models reject it too.

Landmarks (`landmarks.py`) are hand-picked, with slugs from Treebo's landmark sitemap; they are used only
for question text and "near X" matching, never as hotel facts.
