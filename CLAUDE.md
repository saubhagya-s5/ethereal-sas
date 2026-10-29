# Treebo AEO Agent

Travellers now ask AI tools (ChatGPT, Gemini, Perplexity, Claude) which hotel to book. Treebo is often not mentioned, or the AI states wrong facts (old price, closed hotel, wrong amenity). The main reason is that Treebo lacks specific pages for many traveller questions, so the AI reads competitor pages instead.

## Flow
1. Select a city (Pune and Bengaluru for the demo).
2. Generate ~20 tagged traveller questions (locality, landmark, amenity, traveller need, price).
3. Run them on ChatGPT, Gemini, Perplexity and Claude (2-3 runs each). Cache the raw answers.
4. Score: Treebo mention rate, rank, competitors mentioned, cited sources; city leaderboard.
5. Fact-check every Treebo claim against our hotel data. Flag wrong or outdated facts.
6. Give a reason for each miss: `no_inventory | no_page | weak_page | ai_trusts_others`.
7. Find gaps using Treebo sitemaps and competitor URLs.
   - Look for missing place+filter combinations (e.g. locality+amenity).
   - Rank them by search demand × Treebo inventory × AI invisibility.
   - Propose a page only if ≥2 Treebo hotels match.
   - Also flag weak existing pages.
8. Generate a content brief and a working landing page preview (HTML, Treebo-like layout). Export the HTML.
9. Before/after simulation and weekly tracking.

## Rules
- Never invent hotel facts. Use only `data/inventory.json`.
- New URLs follow Treebo's URL style (see below).
- Save every raw AI answer to `output/raw/`.
- API keys and DB credentials go only in `.env` (never commit it).
- Never touch the production DB. `data/export_inventory.py` reads staging only, in a read-only session.
- Each person edits only their own folder.
- Contracts change only after telling the team.

## Folder ownership
| Folder | Owner | Purpose |
|---|---|---|
| `gaps/` | Person A | Sitemap and competitor gap finding, opportunity scoring, page issues (steps 6-7) |
| `ai_runner/` | Person B | Queries, engine runs, raw cache, scoring, fact-check (steps 2-5) |
| `generator/` | Person C | Briefs and HTML landing pages (step 8) |
| `app/` | Person C | Streamlit UI, before/after, tracking (steps 1, 9) |
| `contracts/` | shared | Sample JSON for every shape (the source of truth) |
| `data/` | shared | `inventory.json` and its export script |
| `output/` | shared | Generated results; `output/raw/` is git-ignored |
| `docs/` | shared | Research notes |

## Contract shapes (samples in `contracts/`)
- **hotel**: hotel_id, name, city, locality, lat, lng, price_from, rating, amenities[], categories[], active, url
- **query**: query_id, city, text, tags{type, place, intent, amenity}
- **ai_result**: query_id, engine, run, raw_answer_path, brands_mentioned[], treebo_mentioned, treebo_rank, cited_urls[], treebo_claims[{hotel_id, claim, status (correct|wrong|outdated), truth}], miss_reason
- **city_score**: city, mention_rate, share_of_voice, wrong_facts, top_competitor, by_engine{chatgpt, gemini, perplexity, claude}
- **opportunity**: opp_id, city, place_type, place, filter_type, filter, proposed_url, exists_on_treebo, competitors_with_page[], search_volume, matching_hotel_ids[], ai_mention_rate, score
- **page_issue**: url, issue_type, detail, suggested_fix
- **brief**: opp_id, title, meta_description, h1, quick_answer, sections[], faqs[{q,a}], internal_links[], hotel_ids[], sort_by

## Inventory
Run `python data/export_inventory.py`. It needs `STAGING_DB_*` in `.env`.

- **Source:** Treebo staging `web_db_old`. Pune is city_id 14 and Bengaluru is city_id 1 (slug `bangalore`).
- **Filtering:** test hotels and bad coordinates are dropped.
- **Inactive hotels** are kept with `active: false`, so fact-checks can catch "closed hotel" claims.
- **Prices are placeholders** (₹2000), so don't treat price mismatches as real errors.
- **Amenity and category values** are slugs (`free-breakfast`, `budget-hotels`).

## Treebo findings (full detail with file paths: `docs/treebo_findings.md`)

**Where the data lives:** Django monolith `treebo-noss/treebo`.
- `Hotel` → `hotels_hotel`; active means `status=1`.
- `City` and `Locality` are in `dbcommon/models/location.py`.
- Amenities: `Facility` via `hotels_facility_hotels`.
- Categories: `dbcommon_categories` via `hotels_hotel_category`.
- Rating: TripAdvisor tables.
- Live prices come from an external pricing service.

**URL patterns:**

| Page type | Pattern |
|---|---|
| city | `/hotels-in-{city}/` |
| locality | `/hotels-in-{locality}-{city}/` |
| landmark | `/hotels-near-{landmark}/` |
| category | `/{category}-in-{city}/` |
| locality_category | `/{category}-in-{locality}-{city}/` |
| landmark_category | `/{category}-near-{landmark}/` |
| city_amenity | `/hotels-in-{city}-with-{amenity}/` |
| hotel | `/hotels-in-{city}/{hotel-name}-{id}/` |

- City-amenity pages exist only where `dbcommon_cityamenitymapforautopagecreation` is enabled (currently restaurant, bar, swimming-pool, gym). Free Breakfast is excluded in code.
- Proposed new pattern: `/hotels-in-{locality}-{city}-with-{amenity}/`.

**Listing page:** `cybertron-hotrod/client/views/SeoResultsPage/SeoResultsPage.js`. Sections in order:
1. SeoHeader (H1)
2. Breadcrumbs
3. ResultList (hotel cards)
4. Sort + SeoFilters sidebar
5. AboutPlace
6. Weather
7. Blogs
8. Reviews
9. FAQs (QuestionsAndAnswers)
10. SeoLinksFooter

SEO text comes from `treebo/webapp/apps/seo/pages_v3/`.

**Sitemaps:**
- Built by `django.contrib.sitemaps` in `treebo/webapp/sitemap.py`.
- Served at `/sitemap.xml` and `/sitemap-{type}.xml`.
- Generated on request and cached for 24h.
- A type is listed only if it's SEO-enabled and has an active hotel.
