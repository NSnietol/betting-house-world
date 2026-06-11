# Requirements Document

## Introduction

A local Python CLI tool that extracts betting odds directly from world-class bookmaker websites using a pluggable adapter architecture, removes the bookmaker's commission (overround), and performs reverse engineering to calculate Expected Goals (λ and μ) and the most probable Exact Score for each match. The system uses web scraping (HTTP + BeautifulSoup or headless browser) and public/free API endpoints as primary data sources, requiring no paid API keys to operate out of the box. The output is strictly numeric — no text-based analysis or qualitative descriptors.

## Glossary

- **Odds_Adapter**: An abstract base class defining the common interface that all bookmaker data extraction adapters must implement. Each bookmaker has its own concrete adapter class.
- **Adapter_Registry**: The module responsible for discovering, registering, and managing all available Odds_Adapter implementations. Adding a new bookmaker source requires only adding one new adapter file.
- **Odds_Extractor**: The orchestration module that queries multiple Odds_Adapter instances, aggregates their results, and enforces the minimum bookmaker threshold before passing data downstream.
- **HTTP_Scraper**: The extraction mechanism that uses the `requests` library combined with `BeautifulSoup` (and `lxml` parser) to extract odds from bookmaker websites that serve content via server-rendered HTML.
- **Headless_Browser**: The fallback extraction mechanism using `Playwright` to render JavaScript-heavy bookmaker pages and extract odds from the resulting DOM.
- **Cache_Store**: The local SQLite database that stores raw extracted odds data to avoid repeatedly hitting bookmaker websites for the same event and date.
- **Margin_Eliminator**: The module that removes the bookmaker's overround (commission) from raw odds to obtain real probabilities.
- **Lambda_Optimizer**: The module that uses scipy.optimize.minimize to fit a bivariate Poisson distribution and calculate λ (home expected goals) and μ (away expected goals).
- **Score_Matrix_Generator**: The module that produces a Poisson probability grid (0–5 goals per team) and selects the most probable exact score.
- **Overround**: The built-in commission that bookmakers embed in their odds, causing implied probabilities to sum to more than 100%.
- **Shin_Method**: A mathematical method for removing bookmaker margin that accounts for the presence of informed bettors, producing more accurate true probabilities.
- **Logarithmic_Method**: An alternative margin removal method that distributes the overround proportionally across all outcomes using logarithmic normalization.
- **Bivariate_Poisson_Distribution**: A probability distribution modeling the number of goals scored by each team as correlated Poisson random variables.
- **1X2_Market**: The match winner market offering three outcomes — home win (1), draw (X), and away win (2).
- **Over_Under_Market**: The total goals market offering two outcomes — over 2.5 goals or under 2.5 goals in a match.
- **Rate_Limiter**: The mechanism that enforces configurable delays between HTTP requests to a single bookmaker domain to avoid triggering anti-bot protections.
- **User_Agent_Rotator**: The mechanism that cycles through a pool of realistic browser user-agent strings across requests to reduce detection probability.
- **The_Odds_API**: An optional external paid REST API that can serve as a fallback data source when a user provides an API key.
- **Target_Bookmakers**: World-class bookmakers known for pricing accuracy, including Pinnacle, Bet365, Betfair, William Hill, DraftKings, 1xBet, and Unibet.

## Requirements

### Requirement 1: Pluggable Adapter Architecture

**User Story:** As a quantitative data engineer, I want each bookmaker data source to be encapsulated in an independent adapter class implementing a common interface, so that I can add new bookmaker sources by creating a single new file without modifying existing code.

#### Acceptance Criteria

1. THE Odds_Adapter SHALL define an abstract base class (using Python `abc.ABC`) with methods for fetching 1X2_Market odds, fetching Over_Under_Market odds, and reporting adapter health status.
2. THE Adapter_Registry SHALL auto-discover all concrete Odds_Adapter implementations present in the adapters directory at startup without requiring manual registration.
3. WHEN a new adapter file implementing the Odds_Adapter interface is added to the adapters directory, THE Adapter_Registry SHALL include that adapter in subsequent extraction runs without changes to any other file.
4. THE Odds_Adapter interface SHALL require each implementation to declare a unique bookmaker identifier, a human-readable name, and a priority level (integer, lower is higher priority).
5. THE Odds_Adapter interface SHALL require each implementation to return odds data in a standardized format: a list of match objects containing home team, away team, event timestamp, and a list of market outcomes with decimal odds values.
6. IF an Odds_Adapter implementation raises an exception during extraction, THEN THE Odds_Extractor SHALL log the error, skip that adapter, and continue processing with remaining adapters.
7. THE Adapter_Registry SHALL provide a method to list all available adapters with their health status (reachable, unreachable, rate-limited).

### Requirement 2: Bookmaker Investigation and Discovery

**User Story:** As a quantitative data engineer, I want a documented investigation of which world-class bookmakers expose extractable odds data, so that I can prioritize adapter development based on feasibility and data quality.

#### Acceptance Criteria

1. THE system SHALL include a documented investigation report identifying which Target_Bookmakers expose odds data via scrapable HTML, public REST endpoints, GraphQL endpoints, or other free extraction mechanisms.
2. THE investigation report SHALL classify each bookmaker into one of three extraction difficulty tiers: Tier 1 (simple HTTP scraping), Tier 2 (requires headless browser), Tier 3 (heavily protected or infeasible without paid access).
3. THE investigation report SHALL identify at least 5 Target_Bookmakers and document the specific URL patterns, endpoint structures, or page selectors needed for extraction.
4. THE system SHALL implement adapters for at least 3 bookmakers classified as Tier 1 or Tier 2 in the initial release.
5. THE investigation report SHALL document anti-bot measures observed for each bookmaker (CloudFlare, CAPTCHAs, IP blocking, rate limits) and recommended countermeasures.

### Requirement 3: HTTP-Based Odds Extraction (Primary Method)

**User Story:** As a quantitative data engineer, I want the system to extract odds from bookmaker websites using standard HTTP requests and HTML parsing, so that I can obtain data from server-rendered pages without heavyweight browser dependencies.

#### Acceptance Criteria

1. THE HTTP_Scraper SHALL use the Python `requests` library to fetch HTML pages from bookmaker websites.
2. THE HTTP_Scraper SHALL use `BeautifulSoup` with the `lxml` parser to extract odds values from the fetched HTML DOM.
3. WHEN extracting odds, THE HTTP_Scraper SHALL retrieve data from the 1X2_Market (match winner: home, draw, away).
4. WHEN extracting odds, THE HTTP_Scraper SHALL retrieve data from the Over_Under_Market (total goals over/under 2.5).
5. IF a bookmaker website does not respond within 30 seconds or returns an HTTP error status code, THEN THE HTTP_Scraper SHALL report the error with the status code (or timeout indication) and mark that adapter as temporarily unavailable.
6. THE HTTP_Scraper SHALL support configurable CSS selectors or XPath expressions per adapter to locate odds elements within the page structure.
7. THE HTTP_Scraper SHALL parse extracted text values into decimal odds format (floating-point numbers) regardless of the source format (decimal, fractional, or American).

### Requirement 4: Headless Browser Extraction (Fallback Method)

**User Story:** As a quantitative data engineer, I want the system to fall back to a headless browser for bookmaker sites that require JavaScript rendering, so that I can still extract odds from JS-heavy single-page applications.

#### Acceptance Criteria

1. WHEN the HTTP_Scraper fails to extract valid odds from a bookmaker because the page requires JavaScript rendering, THE system SHALL fall back to the Headless_Browser extraction method using Playwright.
2. THE Headless_Browser SHALL use Playwright in headless mode to load the bookmaker page and wait for odds elements to render in the DOM.
3. THE Headless_Browser SHALL apply a configurable page load timeout (default: 60 seconds) after which extraction is considered failed for that adapter.
4. THE Headless_Browser SHALL be an optional dependency — the system SHALL operate without it by skipping adapters that require JavaScript rendering and logging a warning.
5. WHEN the Headless_Browser is not installed, THE system SHALL still function using only HTTP_Scraper-compatible adapters.
6. THE Headless_Browser SHALL reuse a single browser instance across multiple adapter extractions within a single run to minimize resource usage.

### Requirement 5: Scraping Resilience and Anti-Bot Countermeasures

**User Story:** As a quantitative data engineer, I want the system to handle rate limiting, user-agent rotation, and retry logic, so that extraction remains reliable over time without triggering anti-bot protections.

#### Acceptance Criteria

1. THE Rate_Limiter SHALL enforce a configurable minimum delay between consecutive requests to the same bookmaker domain (default: 3 seconds, configurable between 1 and 30 seconds).
2. THE User_Agent_Rotator SHALL maintain a pool of at least 10 realistic browser user-agent strings and rotate through them across requests.
3. WHEN a request receives an HTTP 429 (Too Many Requests) response, THE system SHALL wait for the duration specified in the Retry-After header (or a default backoff of 60 seconds) before retrying.
4. WHEN a request fails due to a transient error (HTTP 5xx, connection timeout, or connection reset), THE system SHALL retry up to 3 times with exponential backoff (2, 4, 8 seconds) before marking the adapter as temporarily unavailable.
5. THE system SHALL randomize request delays by adding jitter (±30% of the configured delay) to avoid predictable request patterns.
6. IF a bookmaker adapter fails on 3 consecutive runs, THEN THE system SHALL mark that adapter as degraded and log a warning recommending investigation.
7. THE system SHALL support optional proxy configuration via environment variable to route requests through a proxy server.

### Requirement 6: Optional Odds API Fallback

**User Story:** As a quantitative data engineer, I want to optionally use The Odds API as a data source when I have an API key, so that I have a reliable fallback when web scraping is insufficient.

#### Acceptance Criteria

1. WHERE an `ODDS_API_KEY` environment variable is set, THE system SHALL register a dedicated Odds_API adapter that fetches odds from The_Odds_API via HTTP GET requests to its RESTful endpoints.
2. WHERE no `ODDS_API_KEY` environment variable is set, THE system SHALL operate exclusively using web scraping adapters without errors or degraded functionality.
3. WHEN using The_Odds_API adapter, THE Odds_Extractor SHALL treat it as one additional bookmaker source with the same interface as all other adapters.
4. THE Odds_API adapter SHALL have a lower priority (higher priority number) than web scraping adapters, serving as a supplementary source rather than the primary one.

### Requirement 7: Odds Aggregation and Minimum Threshold

**User Story:** As a quantitative data engineer, I want the system to aggregate odds from multiple bookmaker adapters and enforce a minimum source count, so that I have enough data points for statistically meaningful analysis.

#### Acceptance Criteria

1. THE Odds_Extractor SHALL query all available and healthy adapters in the Adapter_Registry for each extraction run.
2. THE Odds_Extractor SHALL aggregate odds for each match by correlating events across adapters using team names and event timestamps (with a tolerance of ±2 hours for timestamp matching).
3. IF fewer than 3 distinct bookmaker adapters provide odds for a match in both the 1X2_Market and the Over_Under_Market, THEN THE Odds_Extractor SHALL skip that match and log a warning message identifying the match and the number of available sources.
4. THE Odds_Extractor SHALL deduplicate matches across adapters using normalized team name matching (case-insensitive, ignoring common suffixes like "FC", "CF", "SC").
5. WHEN odds data is successfully aggregated from adapters, THE Odds_Extractor SHALL store the raw extracted data in the Cache_Store to avoid duplicate extractions for the same event and date.

### Requirement 8: Local Data Caching in SQLite

**User Story:** As a quantitative data engineer, I want to store extracted odds data locally in SQLite, so that I avoid repeatedly scraping bookmaker websites for the same events.

#### Acceptance Criteria

1. THE Cache_Store SHALL persist all raw extracted odds data in a local SQLite database.
2. WHEN a request is made for data matching an existing Cache_Store entry by sport key, market type, event identifier, and set of bookmaker keys, THE Odds_Extractor SHALL return the cached data instead of triggering new extraction.
3. THE Cache_Store SHALL store each cached response with the following metadata fields: sport key, market type, event identifier, bookmaker keys, extraction method (scraping/api), and the UTC timestamp of when the data was retrieved.
4. WHEN cached data is older than the configured time-to-live threshold (default: 24 hours, configurable between 1 hour and 48 hours), THE Odds_Extractor SHALL trigger fresh extraction and update the Cache_Store.
5. IF a fresh extraction fails when refreshing expired cache entries, THEN THE Odds_Extractor SHALL return the stale cached data and indicate via a warning that the data may be outdated.

### Requirement 9: Margin Elimination (Overround Removal)

**User Story:** As a quantitative data engineer, I want to remove the bookmaker's margin from raw odds, so that I obtain real win/draw/loss probabilities.

#### Acceptance Criteria

1. THE Margin_Eliminator SHALL convert raw decimal odds from the 1X2_Market into implied probabilities by computing the reciprocal of each decimal odd (1 / odd).
2. THE Margin_Eliminator SHALL remove the overround using a configurable method (Shin_Method or Logarithmic_Method, defaulting to Shin_Method) to produce real probabilities.
3. WHEN the Margin_Eliminator completes processing, THE resulting real probabilities for home win, draw, and away win SHALL sum to 1.0 (within a tolerance of ±0.001).
4. THE Margin_Eliminator SHALL accept odds from each filtered bookmaker and produce one set of real probabilities per bookmaker per match.
5. IF the implied probabilities from raw odds sum to less than or equal to 1.0, THEN THE Margin_Eliminator SHALL log the match identifier and bookmaker as anomalous and exclude that bookmaker's odds from further processing for that match.
6. THE Margin_Eliminator SHALL produce each individual real probability as a value within the range [0.0, 1.0] inclusive.

### Requirement 10: Lambda and Mu Calculation via Optimization

**User Story:** As a quantitative data engineer, I want to reverse-engineer expected goals (λ and μ) from real probabilities, so that I can model match outcomes using a Poisson distribution.

#### Acceptance Criteria

1. THE Lambda_Optimizer SHALL use scipy.optimize.minimize to find λ and μ values that minimize the sum of squared differences between the Bivariate_Poisson_Distribution-predicted 1X2 probabilities and the real probabilities obtained from the Margin_Eliminator.
2. THE Lambda_Optimizer SHALL use real probabilities from the 1X2_Market and the Over/Under 2.5 goals probability derived from the Over_Under_Market odds as terms in the objective function, where the Poisson-predicted probability of total goals exceeding 2.5 is also compared against the real over probability.
3. THE Lambda_Optimizer SHALL use initial guess values of λ=1.5 and μ=1.2 for each match optimization.
4. WHEN scipy.optimize.minimize returns a result with success=True and the objective function value is below 1e-6, THE Lambda_Optimizer SHALL return λ (home team expected goals) and μ (away team expected goals) as positive floating-point numbers rounded to 4 decimal places.
5. IF scipy.optimize.minimize fails to converge or the objective function value is 1e-6 or above, THEN THE Lambda_Optimizer SHALL log a convergence failure message to stdout indicating the match identifier and skip processing to the next match.
6. THE Lambda_Optimizer SHALL constrain λ and μ to the range [0.1, 5.0] during optimization to ensure physically meaningful results.

### Requirement 11: Exact Score Matrix Generation

**User Story:** As a quantitative data engineer, I want to generate a Poisson probability grid and identify the most probable exact score, so that I have a concrete match prediction.

#### Acceptance Criteria

1. THE Score_Matrix_Generator SHALL produce a Poisson probability grid covering 0 to 5 goals for each team (a 6×6 matrix of 36 cells).
2. THE Score_Matrix_Generator SHALL use λ and μ from the Lambda_Optimizer to calculate the probability of each exact scoreline as P(home=i) × P(away=j) using the Poisson probability mass function.
3. WHEN the grid is complete, THE Score_Matrix_Generator SHALL select the cell with the highest probability as the suggested exact score.
4. IF two or more cells share the same highest probability, THEN THE Score_Matrix_Generator SHALL select the cell with the lowest total goals (home + away), and if still tied, the cell with the fewer home goals.
5. THE Score_Matrix_Generator SHALL output the suggested score in the format "X-Y" where X is home goals and Y is away goals.
6. THE Score_Matrix_Generator SHALL output the probability of the suggested score as a percentage value rounded to 2 decimal places.
7. IF λ or μ is unavailable or non-positive, THEN THE Score_Matrix_Generator SHALL skip the match and indicate that the score matrix could not be generated for that match.

### Requirement 12: Terminal Output Table

**User Story:** As a quantitative data engineer, I want to see the final results in a formatted terminal table with exact numeric columns, so that I can quickly review all computed values per match.

#### Acceptance Criteria

1. THE system SHALL output a pipe-delimited table to the terminal containing a header row followed by one row per match that completed all three computation steps (margin elimination, λ/μ calculation, and score matrix generation).
2. THE system SHALL include these exact columns in the specified left-to-right order: [Match] | [Real Home Probability %] | [Real Draw Probability %] | [Real Away Probability %] | [xG Home (λ)] | [xG Away (μ)] | [Suggested Score] | [Score Probability %].
3. THE system SHALL display the [Match] column as "HomeTeam vs AwayTeam" using the team names returned by the data source.
4. THE system SHALL display all probability values as numeric percentages rounded to two decimal places (e.g., 45.23).
5. THE system SHALL display λ and μ values as floating-point numbers rounded to two decimal places (e.g., 1.87).
6. THE system SHALL display the [Suggested Score] column in the format "X-Y" where X is the integer home goals and Y is the integer away goals of the highest-probability cell in the score matrix.
7. THE system SHALL produce strictly numeric output with no qualitative text descriptors (no "high", "medium", "low", or similar labels).
8. IF computation fails for a match during any of the three processing steps, THEN THE system SHALL omit that match row from the output table.

### Requirement 13: Execution Mode (By Date or By Round)

**User Story:** As a quantitative data engineer, I want to run the simulation for a specific date or league round, so that I can process matches in batches that align with the competition schedule.

#### Acceptance Criteria

1. THE system SHALL accept a command-line argument specifying the execution mode: either a specific date (format YYYY-MM-DD) or a league round identifier.
2. WHEN a date is provided, THE Odds_Extractor SHALL fetch and process only matches scheduled for that date.
3. WHEN a round identifier is provided, THE Odds_Extractor SHALL fetch and process all matches belonging to that round regardless of their individual dates.
4. IF no date or round argument is provided, THEN THE system SHALL default to processing all matches available for the current date.
5. THE system SHALL accept a sport/league key as a required argument to scope which competition's matches are retrieved (e.g., "soccer_epl", "soccer_spain_la_liga").

### Requirement 14: Data Quality and Bias Indicators

**User Story:** As a quantitative data engineer, I want the system to flag when extracted data may contain noise or biases, so that I can interpret the results with appropriate caution.

#### Acceptance Criteria

1. THE system SHALL compute the standard deviation of implied probabilities across all filtered bookmakers for each 1X2 outcome per match.
2. IF the standard deviation of any 1X2 outcome probability across bookmakers exceeds 0.03 (3 percentage points), THEN THE system SHALL append a numeric flag column [Variance Flag] with the maximum standard deviation value for that match.
3. THE system SHALL compute the overround percentage (sum of implied probabilities minus 1.0) for each bookmaker per match.
4. IF any bookmaker's overround exceeds 0.10 (10%), THEN THE system SHALL flag that match with a [High Margin] indicator showing the maximum overround value, signaling potential low-liquidity or biased pricing.
5. IF fewer than 3 bookmakers provide odds for a match (but the match is still processed), THEN THE system SHALL flag that match with a [Low Coverage] indicator showing the number of bookmakers available.
6. THE system SHALL display all flag values as strictly numeric values (no text labels) in additional columns appended to the output table.
7. IF no flags are triggered for a match, THEN THE flag columns SHALL display 0.00 or be left empty.

### Requirement 15: Retro-Feedback — Prediction Accuracy Tracking

**User Story:** As a quantitative data engineer, I want the system to compare my predictions against actual match results after each matchday, so that I can measure accuracy and identify systematic errors.

#### Acceptance Criteria

1. THE system SHALL provide a `--retro` command-line mode that accepts a date (YYYY-MM-DD) and retrieves the actual final scores for matches previously predicted on that date.
2. THE system SHALL fetch actual match results from available bookmaker adapters or public football data sources and store them in the local SQLite database alongside the original predictions.
3. FOR each predicted match, THE system SHALL compute: (a) whether the 1X2 outcome was correctly predicted, (b) whether the exact score was correctly predicted, and (c) the absolute error between predicted λ/μ and actual goals scored by each team.
4. THE system SHALL output a retro-feedback table with columns: [Match] | [Predicted Score] | [Actual Score] | [1X2 Correct] | [Score Correct] | [λ Error] | [μ Error] | [Bookmaker Source].
5. THE system SHALL compute and display aggregate accuracy metrics at the bottom of the table: 1X2 hit rate (%), exact score hit rate (%), mean absolute error for λ, and mean absolute error for μ.
6. ALL retro-feedback values SHALL be strictly numeric (1 for correct, 0 for incorrect, decimal values for errors).

### Requirement 16: Retro-Feedback — Bookmaker Reliability Scoring

**User Story:** As a quantitative data engineer, I want the system to track which bookmakers produce odds that lead to accurate predictions over time, so that I can discard or down-weight unreliable bookmakers.

#### Acceptance Criteria

1. THE system SHALL maintain a `bookmaker_scores` table in SQLite that accumulates per-bookmaker accuracy statistics across all retro-feedback runs.
2. FOR each bookmaker, THE system SHALL track: total matches processed, number of correct 1X2 predictions derived from that bookmaker's odds, mean λ error, and mean μ error.
3. THE system SHALL compute a reliability score per bookmaker as the weighted combination: `0.5 * (1X2_hit_rate) + 0.25 * (1 - normalized_lambda_error) + 0.25 * (1 - normalized_mu_error)`, where normalized errors are scaled to [0, 1] relative to the worst-performing bookmaker.
4. WHEN a bookmaker's reliability score falls below a configurable threshold (default: 0.30), THE system SHALL flag that bookmaker as unreliable and exclude it from future odds processing until manually re-enabled.
5. THE system SHALL provide a `--bookmaker-report` flag that outputs a table showing all tracked bookmakers with columns: [Bookmaker] | [Matches] | [1X2 Hit Rate %] | [Mean λ Error] | [Mean μ Error] | [Reliability Score] | [Status].
6. THE system SHALL allow manual override to re-enable a flagged bookmaker via a `--enable-bookmaker <key>` command-line argument.
7. ALL bookmaker scoring values SHALL be strictly numeric with no text-based qualitative descriptors in the output.

### Requirement 17: Technology Stack Compliance

**User Story:** As a quantitative data engineer, I want the script to use only the specified Python libraries, so that the project remains lightweight and focused.

#### Acceptance Criteria

1. THE system SHALL use the Python `requests` library for all HTTP communication with bookmaker websites and optional API endpoints.
2. THE system SHALL use `beautifulsoup4` with the `lxml` parser for HTML parsing and odds element extraction from server-rendered pages.
3. THE system SHALL use the Python `sqlite3` standard library module for all database operations with the Cache_Store.
4. THE system SHALL use the Python `scipy.optimize.minimize` function for all numerical optimization in the Lambda_Optimizer.
5. THE system SHALL operate as a single-entry-point Python CLI tool that requires no external services, servers, or paid API keys to function.
6. THE system SHALL limit runtime third-party dependencies to `requests`, `scipy` (including its transitive dependency `numpy`), `beautifulsoup4`, and `lxml`. The `playwright` package is an optional dependency for Headless_Browser support.
7. THE system SHALL limit development dependencies to `hypothesis` and `pytest`.
8. THE system MAY use any Python standard library module (including `sqlite3`, `json`, `datetime`, `math`, `os`, `argparse`, `logging`, `re`, `abc`).
9. IF a module not listed in the allowed dependencies is imported, THEN THE system SHALL fail a dependency audit that compares the import list against the allowed set.
