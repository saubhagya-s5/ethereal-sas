# Treebo findings (read-only research, 2026-10-30)

Sources:
- `treebo-noss/treebo`: the Django monolith under `webapp/`. It owns the models, APIs, URL builders and sitemaps.
- `treebo-noss/cybertron`: renders the pages. `cybertron-hotrod` is desktop; `cybertron-rodimus` is the mobile site.
- `treebo-noss/treebo-web-frontend`: the Astro rebuild. It parses the same URL patterns but has no listing page yet.
- Staging Postgres `web_db_old`, inspected with read-only SELECTs.

Items marked *(unverified)* were not confirmed line by line.

## 1. Hotel data: models, tables, fields

All paths start with `treebo/webapp/`.

| Field | Table.column | Model (file:line) |
|---|---|---|
| hotel | `hotels_hotel` | `Hotel`, `dbcommon/models/hotel.py:104` |
| name | `hotels_hotel.name` | `dbcommon/models/hotel.py:105` |
| city | `hotels_hotel.city_id` → `hotels_city` (name, slug, enable_for_seo) | FK at `hotel.py:115`; `City` at `dbcommon/models/location.py:38` |
| locality | `hotels_hotel.locality_id` → `hotels_locality` (name, free_text_url, enable_for_seo, distance_cap) | FK at `hotel.py:116`; `Locality` at `location.py:162` |
| lat/lng | `hotels_hotel.latitude`, `longitude` | `hotel.py:119`, `:125` |
| active | `hotels_hotel.status` (1 = ENABLED, 0 = DISABLED) | `hotel.py:135`; constants at `common/constants/common_constants.py:129-134`; `HotelManager` filters `status=1` (`dbcommon/managers/hotel_manager.py:5-10`) |
| amenities | `hotels_facility_hotels` → `hotels_facility.name` | `Facility`, `dbcommon/models/facilities.py:83` (M2M at `:88`) |
| categories | `hotels_hotel_category` → `dbcommon_categories.name` | M2M at `hotel.py:148`; `Categories` at `hotel.py:39` |
| rating | `trip_advisor_hotel_mapping` → `trip_advisor_hotel_overall_ratings.overall_rating` | `apps/reviews/models.py:9`, `:37` |
| price | `hotels_room.price` (static base) | `dbcommon/models/room.py:28` |
| canonical URL | `hotels_seodetails.hotel_canonical_url` | `hotel.py:388` |
| landmarks | `hotels_landmark` / `search_landmark` | `location.py:231`; `dbcommon/models/landmark.py` |

**Live prices** come from an external pricing service (`apps/pricing/services/pricing_service.py:76-100`, `settings.BASE_PRICING_URL_V1`), not from `hotels_room.price`.

**APIs:**
- `/api/v2/hotels/` → `apps/hotels/api/v2/hotels.py`
- `/api/v2/hotels/<id>/details/` → `apps/hotels/api/v2/hotel_detail.py`
- `/api/v2/hotels/localities/`
- `/api/v5/search/` → `apps/search/api/v5/search_api.py`
- `/api/v8/pricing/hotels/` → `apps/pricing/api/v8/cheapest_price_for_all_hotels.py`
- City lists: `HotelsRepository.get_city_hotels`, `apps/hotels/repositories/hotels_repository.py:226`

**Staging data quality.** Our `data/inventory.json` is built from staging by `data/export_inventory.py`.
- Prices are placeholders (mostly ₹2000).
- Some hotels have test names and junk coordinates. These are filtered out.
- Canonical URLs are empty, so the script builds them with `Hotel.get_slugified_url` (`hotel.py:210-212`).

Staging city ids: Pune = 14 (slug `pune`), Bengaluru = 1 (slug `bangalore`).

## 2. Landing page URL patterns

The sitemap type keys are in `treebo/webapp/urls.py:50`. The classes are in `treebo/webapp/sitemap.py`.

| Type | Pattern | Builder |
|---|---|---|
| city | `/hotels-in-{city_slug}/` | `location.py:132` → `apps/pages/canonical_urls.py:34` |
| locality | `/hotels-in-{free_text_url}/` or `/hotels-in-{locality}-{city_slug}/` | `location.py:189` |
| landmark | `/hotels-near-{landmark_slug}/` | `dbcommon/models/landmark.py:55` |
| nearme | `/hotels-near-me/` | `sitemap.py:173` |
| hotel | `/hotels-in-{city}/{name}-{locality}-{id}/` (sitemap) or `/hotels-in-{city}/{name}-{id}/` | `hotel.py:233`, `:210` |
| category | `/{category}-in-{city}/` e.g. `/budget-hotels-in-pune/` | `hotel.py:53` |
| locality_category | `/{category}-in-{locality}-{city_slug}/` | `sitemap.py:229` |
| landmark_category | `/{category}-near-{landmark_slug}/` | `sitemap.py:271` |
| city_amenity | `/hotels-in-{city}-with-{amenity}/` e.g. `/hotels-in-bangalore-with-gym/` | `facilities.py:109` |
| nearme_category | URLs held in the CMS (`near_me_footer_content`) | `sitemap.py:183` |
| stat | `/aboutus/`, `/faq/`, `/terms/`, … | `sitemap.py:30-36` |
| old_urls | legacy CSV on S3 | `sitemap.py:315` |

Notes:
- City-amenity pages exist only for rows enabled in `dbcommon_cityamenitymapforautopagecreation` (`facilities.py:114`). That is why only 4 amenities have pages.
- "Free Breakfast" is explicitly excluded (`common/constants/common_constants.py:285`).
- No locality+amenity or landmark+amenity patterns exist.

**New-URL style for us:** we extend the existing patterns, e.g. `/hotels-in-{locality}-{city_slug}-with-{amenity}/` → `/hotels-in-koramangala-bangalore-with-free-breakfast/`.

*Caution:* desktop routes match `/hotels-in-:locality-:city(\w+)/`. A new pattern would need its own route registered before that one *(unverified)*.

## 3. Listing page template

**Routes** are in `cybertron/cybertron-hotrod/client/services/route/routes.js:55-116`. All listing types render `SeoResultsPage`.

**Component:** `cybertron/cybertron-hotrod/client/views/SeoResultsPage/SeoResultsPage.js` (`render()` at :368). Sections in order:
1. Helmet / meta, with conditional `noindex` (:401)
2. `SeoHeader`, the hero / H1 (:403)
3. `Breadcrumbs` (:411)
4. `ResultList`, the hotel cards (:419)
5. Sidebar: `Sort` (:447) and `SeoFilters` (:456)
6. SEO content block (:474):
   - `AboutPlace`
   - `WeatherWidget`
   - `Blogs`
   - `SeoReviews`
   - `QuestionsAndAnswers` (FAQs, :497)
   - `SeoLinksFooter` (related links, :504)

**Content source:** the SEO text for each page type is built in `treebo/webapp/apps/seo/pages_v3/`, one file per type, e.g. `city_page.py`, `locality_page.py`, `city_amenity_page.py`. The frontend reads it through `cybertron-hotrod/client/services/content/contentService.js:171`.

**Mobile site:** `cybertron-rodimus/client/views/SeoSearchResultsPage` *(unverified section order)*.

## 4. How sitemaps are built
- They use `django.contrib.sitemaps` classes in `treebo/webapp/sitemap.py`. Each class lists only places that are SEO-enabled and have an active hotel nearby.
- **Serving:**
  - Index: `/sitemap.xml` (`treebo/webapp/urls.py:178`).
  - Sections: `/sitemap-{type}.xml` (`urls.py:180`).
- **Caching:** sitemaps are generated on request and cached for 24h (`cache_page`). No files are written ahead of time.
- **Bing:** `scripts/management/commands/push_bing_index_data.py:23` pushes sitemap URLs to Bing. Its schedule is *(unverified)*.
