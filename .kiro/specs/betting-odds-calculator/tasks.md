# Implementation Plan: Betting Odds Calculator — Adapter Architecture

## Overview

Replace the single-source Odds API approach with a pluggable multi-source web scraping extraction layer. The existing math pipeline (MarginEliminator → LambdaOptimizer → ScoreMatrixGenerator → DataQualityAnalyzer → TerminalOutput) remains unchanged. New work covers: bookmaker investigation, adapter infrastructure, scraping layer, concrete adapters, OddsExtractor rewrite, model updates, and CLI integration.

**Already implemented (do NOT re-implement):** `src/models.py`, `src/cache_store.py`, `src/margin_eliminator.py`, `src/lambda_optimizer.py`, `src/score_matrix.py`, `src/data_quality.py`, `src/output.py`.

## Tasks

- [x] 1. Bookmaker Investigation and Discovery
  - [x] 1.1 Research and document scrapable bookmakers
    - Investigate at least 5 Target_Bookmakers (Pinnacle, Bet365, Betfair, William Hill, 1xBet, DraftKings, Unibet)
    - For each bookmaker, document: URL patterns for soccer odds pages, HTML structure (server-rendered vs JS-heavy), CSS selectors or XPath for odds elements, odds format used (decimal/fractional/American)
    - Classify each into Tier 1 (simple HTTP scraping with requests+BS4), Tier 2 (requires Playwright headless browser), Tier 3 (heavily protected / infeasible)
    - Document anti-bot measures observed (CloudFlare, CAPTCHAs, IP blocking, rate limits) and recommended countermeasures
    - Output: Create `docs/bookmaker_investigation.md` with structured findings
    - _Requirements: 2.1, 2.2, 2.3, 2.5_

- [x] 2. Checkpoint — Review investigation findings
  - Ensure investigation document is complete and at least 3 bookmakers are classified as Tier 1 or Tier 2. Ask the user if questions arise.

- [x] 3. Adapter Infrastructure
  - [x] 3.1 Create adapter base module with ABC and data types
    - Create `src/adapters/__init__.py` (package init)
    - Create `src/adapters/base.py` with: `ExtractionMethod` enum (HTTP, HEADLESS, API), `AdapterHealth` enum (REACHABLE, UNREACHABLE, RATE_LIMITED, DEGRADED), `MarketOutcome` dataclass, `ScrapedMatch` dataclass, `OddsAdapter` ABC with abstract methods (`bookmaker_id`, `bookmaker_name`, `priority`, `extraction_method`, `base_url`, `fetch_1x2`, `fetch_over_under`, `health_check`)
    - Create `OddsExtractionError` custom exception in `src/adapters/base.py`
    - _Requirements: 1.1, 1.4, 1.5_

  - [x] 3.2 Create adapter registry with auto-discovery
    - Create `src/adapters/registry.py` with `AdapterRegistry` class
    - Implement `discover()`: scan the `src/adapters/` package using `pkgutil`/`importlib`, find all classes inheriting from `OddsAdapter`, instantiate and register them
    - Implement `get_all()`: return all adapters sorted by priority (ascending)
    - Implement `get_healthy()`: return only adapters with health == REACHABLE
    - Implement `list_with_status()`: return adapter info dicts with id, name, health
    - Implement `record_failure(adapter_id)`: increment consecutive failure count, mark DEGRADED at 3
    - Implement `record_success(adapter_id)`: reset failure count
    - _Requirements: 1.2, 1.3, 1.7_

  - [ ]* 3.3 Write property test for auto-discovery completeness
    - **Property 1: Auto-Discovery Completeness**
    - Use Hypothesis to generate varying numbers of mock adapter modules, verify `discover()` finds all conforming classes
    - **Validates: Requirements 1.2, 1.3**

  - [ ]* 3.4 Write property test for adapter fault isolation
    - **Property 2: Adapter Fault Isolation**
    - Use Hypothesis to generate adapter sets where a random subset raises exceptions, verify OddsExtractor returns results from non-failing adapters only
    - **Validates: Requirements 1.6, 7.1**

  - [ ]* 3.5 Write property test for degradation after consecutive failures
    - **Property 9: Degradation After Consecutive Failures**
    - Use Hypothesis to generate sequences of success/failure calls, verify 3 consecutive failures triggers DEGRADED and success resets counter
    - **Validates: Requirements 5.6**

  - [ ]* 3.6 Write unit tests for adapter registry
    - Test discovery with 0, 1, and N adapters
    - Test health status transitions (REACHABLE → DEGRADED after 3 failures, reset on success)
    - Test priority sorting
    - _Requirements: 1.2, 1.3, 1.7_

- [x] 4. Scraping Layer
  - [x] 4.1 Create resilience module
    - Create `src/scraping/__init__.py` (package init)
    - Create `src/scraping/resilience.py` with: `RateLimiterConfig` dataclass, `ResilienceConfig` dataclass, `RateLimiter` class (per-domain token-bucket with jitter ±30%), `UserAgentRotator` class (pool of 10+ realistic UA strings with cycling), `RetryHandler` class (exponential backoff: 2, 4, 8 seconds; respects Retry-After on 429; max 3 retries)
    - Support optional proxy via `SCRAPER_PROXY` environment variable
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.7_

  - [x] 4.2 Create HTTP scraper module
    - Create `src/scraping/http_scraper.py` with `HTTPScraper` class
    - Implement `fetch_page(url, domain)`: uses `requests.get` with UA rotation, rate limiting, 30s timeout; raises `OddsExtractionError` on failure
    - Implement `extract_by_css(soup, selectors)`: extract elements using CSS selectors via BeautifulSoup
    - Implement `extract_by_xpath(soup, expressions)`: extract elements using XPath via lxml
    - Implement `parse_odds_value(raw)`: parse decimal ('2.50'), fractional ('3/2'), and American ('+150', '-200') formats into decimal float
    - _Requirements: 3.1, 3.2, 3.5, 3.6, 3.7_

  - [x] 4.3 Create headless browser scraper module (optional dependency)
    - Create `src/scraping/headless.py` with `HeadlessScraper` class
    - Implement with try/except ImportError for Playwright — if not installed, raise ImportError on instantiation
    - Implement `fetch_rendered_page(url, wait_selector)`: load page, wait for selector (default timeout 60s), return rendered HTML
    - Implement `close()`: close shared browser instance
    - Reuse a single browser instance across multiple adapter extractions
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 4.4 Write property test for odds format conversion round-trip
    - **Property 3: Odds Format Conversion Round-Trip**
    - Use Hypothesis to generate valid decimal odds, convert to fractional/American and back, verify round-trip within ±0.01
    - **Validates: Requirements 3.7**

  - [ ]* 4.5 Write property test for rate limiter timing invariant
    - **Property 6: Rate Limiter Timing Invariant**
    - Use Hypothesis to generate sequences of requests, verify inter-request delays fall within [d*0.7, d*1.3]
    - **Validates: Requirements 5.1, 5.5**

  - [ ]* 4.6 Write property test for user-agent rotation coverage
    - **Property 7: User-Agent Rotation Coverage**
    - Use Hypothesis to generate K requests where K equals pool size, verify all UA strings used exactly once per cycle
    - **Validates: Requirements 5.2**

  - [ ]* 4.7 Write property test for retry exponential backoff
    - **Property 8: Retry Exponential Backoff**
    - Use Hypothesis to generate transient error sequences, verify retry count ≤ 3 and wait durations follow 2^i seconds (±30% jitter)
    - **Validates: Requirements 5.4**

  - [ ]* 4.8 Write unit tests for HTTP scraper and resilience
    - Test `parse_odds_value` with decimal, fractional, American formats and edge cases
    - Test `fetch_page` timeout and HTTP error handling (mock responses)
    - Test CSS and XPath extraction with HTML fixtures
    - Test rate limiter delay enforcement
    - Test retry exhaustion behavior
    - _Requirements: 3.1, 3.2, 3.5, 3.6, 3.7, 5.1, 5.3, 5.4_

- [x] 5. Checkpoint — Ensure infrastructure tests pass
  - Ensure all tests pass (`uv run pytest tests/ -v`), ask the user if questions arise.

- [x] 6. Concrete Bookmaker Adapters
  - [x] 6.1 Implement first bookmaker adapter (Tier 1 — HTTP)
    - Create `src/adapters/<bookmaker_1>.py` implementing `OddsAdapter`
    - Implement `fetch_1x2()` and `fetch_over_under()` using HTTPScraper with bookmaker-specific CSS selectors/URL patterns identified in investigation
    - Implement `health_check()` with a lightweight HEAD request
    - Include bookmaker-specific odds page URL construction and parsing logic
    - _Requirements: 1.1, 1.4, 1.5, 2.4, 3.3, 3.4, 3.6_

  - [x] 6.2 Implement second bookmaker adapter (Tier 1 — HTTP)
    - Create `src/adapters/<bookmaker_2>.py` implementing `OddsAdapter`
    - Implement `fetch_1x2()` and `fetch_over_under()` using HTTPScraper with bookmaker-specific selectors
    - Implement `health_check()`
    - _Requirements: 1.1, 1.4, 1.5, 2.4, 3.3, 3.4, 3.6_

  - [x] 6.3 Implement third bookmaker adapter (Tier 1 or Tier 2)
    - Create `src/adapters/<bookmaker_3>.py` implementing `OddsAdapter`
    - If Tier 2, use HeadlessScraper; if Tier 1, use HTTPScraper
    - Implement `fetch_1x2()`, `fetch_over_under()`, and `health_check()`
    - _Requirements: 1.1, 1.4, 1.5, 2.4, 3.3, 3.4, 4.1, 4.2_

  - [x] 6.4 Implement Odds API adapter (optional fallback)
    - Create `src/adapters/odds_api.py` implementing `OddsAdapter`
    - Activate only when `ODDS_API_KEY` environment variable is set
    - Set higher priority number (lower priority) than scraping adapters
    - Reuse existing The Odds API v4 logic from current `src/odds_extractor.py`
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [ ]* 6.5 Write property test for HTML extraction correctness
    - **Property 4: HTML Extraction Correctness**
    - Use Hypothesis to generate HTML documents with odds at known CSS paths, verify extraction produces correct decimal values (±0.001)
    - **Validates: Requirements 3.3, 3.4, 3.6**

  - [ ]* 6.6 Write property test for HTTP error surfaces correctly
    - **Property 5: HTTP Error Surfaces Correctly**
    - Use Hypothesis to generate HTTP status codes in [400, 599] and timeouts, verify OddsExtractionError is raised with correct status
    - **Validates: Requirements 3.5**

- [x] 7. Rewrite OddsExtractor and Update Models
  - [x] 7.1 Add new data models to `src/models.py`
    - Add `AggregatedEvent` dataclass (home_team, away_team, event_timestamp, bookmaker_odds_1x2 dict, bookmaker_odds_over_under dict, source_count)
    - Add `AdapterStatus` dataclass (adapter_id, adapter_name, priority, health, extraction_method, consecutive_failures)
    - _Requirements: 7.2, 7.3_

  - [x] 7.2 Update CacheStore schema
    - Add `extraction_method` column (TEXT DEFAULT 'api') to the `cache` table via ALTER TABLE or recreate logic
    - Values: 'scraping_http', 'scraping_headless', 'api'
    - Ensure backward compatibility with existing cached data
    - _Requirements: 8.3_

  - [x] 7.3 Rewrite `src/odds_extractor.py` as adapter orchestrator
    - Replace the single-API approach with adapter-based orchestration
    - Initialize with `AdapterRegistry` and `CacheStore`
    - Implement `extract(sport, date, round_id)`: check cache → query healthy adapters → correlate events → enforce minimum threshold → cache results → return `list[AggregatedEvent]`
    - Implement `_correlate_events()`: match events across adapters using normalized team names + ±2h timestamp tolerance
    - Implement `normalize_team_name()`: lowercase, strip FC/CF/SC/AFC suffixes, collapse whitespace, strip accents
    - Enforce `MIN_BOOKMAKERS = 3` threshold (skip events with fewer sources, log warning)
    - Handle adapter failures gracefully (skip failed adapter, continue with others)
    - Fall back to stale cache when all adapters fail
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.2, 8.4, 8.5_

  - [ ]* 7.4 Write property test for event correlation
    - **Property 10: Event Correlation by Name and Timestamp**
    - Use Hypothesis to generate ScrapedMatch pairs, verify correlation iff normalized names match AND timestamps within 2 hours
    - **Validates: Requirements 7.2**

  - [ ]* 7.5 Write property test for team name normalization idempotence
    - **Property 11: Team Name Normalization Idempotence**
    - Use Hypothesis to generate team name strings, verify `normalize(normalize(s)) == normalize(s)` and suffix stripping
    - **Validates: Requirements 7.4**

  - [ ]* 7.6 Write property test for minimum bookmaker threshold enforcement
    - **Property 12: Minimum Bookmaker Threshold Enforcement**
    - Use Hypothesis to generate aggregated events with varying source counts, verify events with <3 sources excluded and >=3 included
    - **Validates: Requirements 7.3**

  - [ ]* 7.7 Write property test for cache persistence after extraction
    - **Property 13: Cache Persistence After Extraction**
    - Use Hypothesis to generate extraction runs, verify all events are cached and retrievable via same keys
    - **Validates: Requirements 7.5**

  - [ ]* 7.8 Write unit tests for OddsExtractor orchestration
    - Test event correlation with matching/non-matching team names and timestamps
    - Test normalize_team_name with FC/CF/SC/AFC suffixes, accents, whitespace
    - Test minimum bookmaker threshold filtering
    - Test stale cache fallback when all adapters fail
    - Test adapter failure isolation
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 8.5_

- [x] 8. Checkpoint — Ensure all tests pass
  - Ensure all tests pass (`uv run pytest tests/ -v`), ask the user if questions arise.

- [x] 9. CLI and Integration Wiring
  - [x] 9.1 Update `src/main.py` CLI to use adapter system
    - Initialize `AdapterRegistry` and call `discover()` at startup
    - Replace direct Odds API usage with new `OddsExtractor(registry, cache_store)`
    - Maintain existing CLI arguments (--sport, --date, --round, --retro, --bookmaker-report, --enable-bookmaker)
    - Add new flag: `--list-adapters` to display registered adapters and their health status
    - Pass excluded bookmakers from `bookmaker_scorer` to OddsExtractor
    - Wire full pipeline: OddsExtractor → MarginEliminator → LambdaOptimizer → ScoreMatrixGenerator → DataQualityAnalyzer → TerminalOutput
    - _Requirements: 13.1, 13.2, 13.3, 13.4, 13.5, 12.1_

  - [x] 9.2 Update `src/retro_feedback.py` for multi-source adapters
    - Update retro-feedback logic to work with `AggregatedEvent` instead of single API response
    - Ensure per-bookmaker tracking works with adapter-sourced data
    - _Requirements: 15.1, 15.2, 15.3, 15.4_

  - [x] 9.3 Update `src/bookmaker_scorer.py` for multi-source adapters
    - Ensure bookmaker scoring works with adapter IDs from the new system
    - Ensure excluded bookmaker list is passed to OddsExtractor at extraction time
    - _Requirements: 16.1, 16.2, 16.3, 16.4_

  - [ ]* 9.4 Write integration tests
    - End-to-end extraction with mocked HTTP responses (HTML fixture files in `tests/fixtures/`)
    - Cache round-trip: extract → cache → re-extract from cache
    - Full pipeline test: extraction → margin elimination → optimization → score matrix → output
    - Test `--list-adapters` CLI flag
    - Test graceful degradation when no adapters are available
    - _Requirements: 7.1, 7.5, 8.2, 12.1, 13.1_

- [x] 10. Final Checkpoint — Full validation
  - Ensure all tests pass (`uv run pytest tests/ -v`), ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties from the design document
- Unit tests validate specific examples and edge cases
- The existing 102 passing tests for `models.py`, `cache_store.py`, `margin_eliminator.py`, `lambda_optimizer.py`, `score_matrix.py`, `data_quality.py`, and `output.py` must continue to pass throughout all phases
- Concrete bookmaker adapter filenames (tasks 6.1–6.3) depend on investigation findings from task 1.1
- The `beautifulsoup4` and `lxml` packages must be added to `requirements.txt` before implementing the scraping layer
- `playwright` is an optional dependency — the system must work without it

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["3.1", "3.2"] },
    { "id": 2, "tasks": ["3.3", "3.4", "3.5", "3.6", "4.1"] },
    { "id": 3, "tasks": ["4.2", "4.3"] },
    { "id": 4, "tasks": ["4.4", "4.5", "4.6", "4.7", "4.8"] },
    { "id": 5, "tasks": ["6.1", "6.2", "6.3", "6.4", "7.1", "7.2"] },
    { "id": 6, "tasks": ["6.5", "6.6", "7.3"] },
    { "id": 7, "tasks": ["7.4", "7.5", "7.6", "7.7", "7.8"] },
    { "id": 8, "tasks": ["9.1"] },
    { "id": 9, "tasks": ["9.2", "9.3"] },
    { "id": 10, "tasks": ["9.4"] }
  ]
}
```
