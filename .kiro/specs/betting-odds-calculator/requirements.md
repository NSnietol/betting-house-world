# Requirements Document

## Introduction

A local Python script that extracts betting odds from The Odds API, removes the bookmaker's commission (overround), and performs reverse engineering to calculate Expected Goals (λ and μ) and the most probable Exact Score for each match. The system produces strictly numeric output — no text-based analysis or qualitative descriptors.

## Glossary

- **Odds_Extractor**: The module responsible for fetching odds data from The Odds API via HTTP GET requests.
- **Cache_Store**: The local SQLite database that stores raw JSON responses from The Odds API to prevent duplicate requests and conserve API credits.
- **Margin_Eliminator**: The module that removes the bookmaker's overround (commission) from raw odds to obtain real probabilities.
- **Lambda_Optimizer**: The module that uses scipy.optimize.minimize to fit a bivariate Poisson distribution and calculate λ (home expected goals) and μ (away expected goals).
- **Score_Matrix_Generator**: The module that produces a Poisson probability grid (0–5 goals per team) and selects the most probable exact score.
- **Overround**: The built-in commission that bookmakers embed in their odds, causing implied probabilities to sum to more than 100%.
- **Shin_Method**: A mathematical method for removing bookmaker margin that accounts for the presence of informed bettors, producing more accurate true probabilities.
- **Logarithmic_Method**: An alternative margin removal method that distributes the overround proportionally across all outcomes using logarithmic normalization.
- **Bivariate_Poisson_Distribution**: A probability distribution modeling the number of goals scored by each team as correlated Poisson random variables.
- **1X2_Market**: The match winner market offering three outcomes — home win (1), draw (X), and away win (2).
- **Over_Under_Market**: The total goals market offering two outcomes — over 2.5 goals or under 2.5 goals in a match.
- **The_Odds_API**: The external RESTful API service used as the sole data source for extracting bookmaker odds.

## Requirements

### Requirement 1: Odds Extraction from The Odds API

**User Story:** As a quantitative data engineer, I want to extract odds from The Odds API, so that I have raw market data to perform mathematical transformations.

#### Acceptance Criteria

1. THE Odds_Extractor SHALL fetch odds data exclusively from The Odds API via HTTP GET requests to RESTful endpoints.
2. WHEN fetching odds, THE Odds_Extractor SHALL retrieve data from the 1X2_Market (match winner: home, draw, away).
3. WHEN fetching odds, THE Odds_Extractor SHALL retrieve data from the Over_Under_Market (total goals over/under 2.5).
4. THE Odds_Extractor SHALL filter and retain odds from at least 3 recognized bookmakers (Pinnacle, DraftKings, BetMGM, or equivalents available in the API).
5. IF The_Odds_API does not respond within 30 seconds or returns an HTTP error status code, THEN THE Odds_Extractor SHALL report the error with the status code (or timeout indication) and cease processing for that request without persisting partial data.
6. IF The_Odds_API returns a response where a match is missing odds for either the 1X2_Market or the Over_Under_Market from at least 3 bookmakers, THEN THE Odds_Extractor SHALL skip that match and log a warning message identifying the match and the missing market.
7. WHEN odds data is successfully fetched, THE Odds_Extractor SHALL store the raw JSON response in a local SQLite database to avoid duplicate API requests for the same event and date.

### Requirement 2: Local Data Caching in SQLite

**User Story:** As a quantitative data engineer, I want to store API responses locally in SQLite, so that I avoid exhausting API credits with duplicate requests.

#### Acceptance Criteria

1. THE Cache_Store SHALL persist all raw JSON responses from The_Odds_API in a local SQLite database.
2. WHEN a request is made for data matching an existing Cache_Store entry by sport key, market type, event identifier, and set of bookmaker keys, THE Odds_Extractor SHALL return the cached data instead of making a new API call.
3. THE Cache_Store SHALL store each cached response with the following metadata fields: sport key, market type, event identifier, bookmaker keys, and the UTC timestamp of when the response was retrieved.
4. WHEN cached data is older than the configured time-to-live threshold (default: 24 hours, configurable between 1 hour and 48 hours), THE Odds_Extractor SHALL fetch fresh data from The_Odds_API and update the Cache_Store.
5. IF the fresh data fetch from The_Odds_API fails when refreshing expired cache entries, THEN THE Odds_Extractor SHALL return the stale cached data and indicate via a warning that the data may be outdated.

### Requirement 3: Margin Elimination (Overround Removal)

**User Story:** As a quantitative data engineer, I want to remove the bookmaker's margin from raw odds, so that I obtain real win/draw/loss probabilities.

#### Acceptance Criteria

1. THE Margin_Eliminator SHALL convert raw decimal odds from the 1X2_Market into implied probabilities by computing the reciprocal of each decimal odd (1 / odd).
2. THE Margin_Eliminator SHALL remove the overround using a configurable method (Shin_Method or Logarithmic_Method, defaulting to Shin_Method) to produce real probabilities.
3. WHEN the Margin_Eliminator completes processing, THE resulting real probabilities for home win, draw, and away win SHALL sum to 1.0 (within a tolerance of ±0.001).
4. THE Margin_Eliminator SHALL accept odds from each filtered bookmaker and produce one set of real probabilities per bookmaker per match.
5. IF the implied probabilities from raw odds sum to less than or equal to 1.0, THEN THE Margin_Eliminator SHALL log the match identifier and bookmaker as anomalous and exclude that bookmaker's odds from further processing for that match.
6. THE Margin_Eliminator SHALL produce each individual real probability as a value within the range [0.0, 1.0] inclusive.

### Requirement 4: Lambda and Mu Calculation via Optimization

**User Story:** As a quantitative data engineer, I want to reverse-engineer expected goals (λ and μ) from real probabilities, so that I can model match outcomes using a Poisson distribution.

#### Acceptance Criteria

1. THE Lambda_Optimizer SHALL use scipy.optimize.minimize to find λ and μ values that minimize the sum of squared differences between the Bivariate_Poisson_Distribution-predicted 1X2 probabilities and the real probabilities obtained from the Margin_Eliminator.
2. THE Lambda_Optimizer SHALL use real probabilities from the 1X2_Market and the Over/Under 2.5 goals probability derived from the Over_Under_Market odds as terms in the objective function, where the Poisson-predicted probability of total goals exceeding 2.5 is also compared against the real over probability.
3. THE Lambda_Optimizer SHALL use initial guess values of λ=1.5 and μ=1.2 for each match optimization.
4. WHEN scipy.optimize.minimize returns a result with success=True and the objective function value is below 1e-6, THE Lambda_Optimizer SHALL return λ (home team expected goals) and μ (away team expected goals) as positive floating-point numbers rounded to 4 decimal places.
5. IF scipy.optimize.minimize fails to converge or the objective function value is 1e-6 or above, THEN THE Lambda_Optimizer SHALL log a convergence failure message to stdout indicating the match identifier and skip processing to the next match.
6. THE Lambda_Optimizer SHALL constrain λ and μ to the range [0.1, 5.0] during optimization to ensure physically meaningful results.

### Requirement 5: Exact Score Matrix Generation

**User Story:** As a quantitative data engineer, I want to generate a Poisson probability grid and identify the most probable exact score, so that I have a concrete match prediction.

#### Acceptance Criteria

1. THE Score_Matrix_Generator SHALL produce a Poisson probability grid covering 0 to 5 goals for each team (a 6×6 matrix of 36 cells).
2. THE Score_Matrix_Generator SHALL use λ and μ from the Lambda_Optimizer to calculate the probability of each exact scoreline as P(home=i) × P(away=j) using the Poisson probability mass function.
3. WHEN the grid is complete, THE Score_Matrix_Generator SHALL select the cell with the highest probability as the suggested exact score.
4. IF two or more cells share the same highest probability, THEN THE Score_Matrix_Generator SHALL select the cell with the lowest total goals (home + away), and if still tied, the cell with the fewer home goals.
5. THE Score_Matrix_Generator SHALL output the suggested score in the format "X-Y" where X is home goals and Y is away goals.
6. THE Score_Matrix_Generator SHALL output the probability of the suggested score as a percentage value rounded to 2 decimal places.
7. IF λ or μ is unavailable or non-positive, THEN THE Score_Matrix_Generator SHALL skip the match and indicate that the score matrix could not be generated for that match.

### Requirement 6: Terminal Output Table

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

### Requirement 7: Execution Mode (By Date or By Round)

**User Story:** As a quantitative data engineer, I want to run the simulation for a specific date or league round, so that I can process matches in batches that align with the competition schedule.

#### Acceptance Criteria

1. THE system SHALL accept a command-line argument specifying the execution mode: either a specific date (format YYYY-MM-DD) or a league round identifier.
2. WHEN a date is provided, THE Odds_Extractor SHALL fetch and process only matches scheduled for that date.
3. WHEN a round identifier is provided, THE Odds_Extractor SHALL fetch and process all matches belonging to that round regardless of their individual dates.
4. IF no date or round argument is provided, THEN THE system SHALL default to processing all matches available for the current date.
5. THE system SHALL accept a sport/league key as a required argument to scope which competition's matches are retrieved (e.g., "soccer_epl", "soccer_spain_la_liga").

### Requirement 8: Data Quality and Bias Indicators

**User Story:** As a quantitative data engineer, I want the system to flag when extracted data may contain noise or biases, so that I can interpret the results with appropriate caution.

#### Acceptance Criteria

1. THE system SHALL compute the standard deviation of implied probabilities across all filtered bookmakers for each 1X2 outcome per match.
2. IF the standard deviation of any 1X2 outcome probability across bookmakers exceeds 0.03 (3 percentage points), THEN THE system SHALL append a numeric flag column [Variance Flag] with the maximum standard deviation value for that match.
3. THE system SHALL compute the overround percentage (sum of implied probabilities minus 1.0) for each bookmaker per match.
4. IF any bookmaker's overround exceeds 0.10 (10%), THEN THE system SHALL flag that match with a [High Margin] indicator showing the maximum overround value, signaling potential low-liquidity or biased pricing.
5. IF fewer than 3 bookmakers provide odds for a match (but the match is still processed), THEN THE system SHALL flag that match with a [Low Coverage] indicator showing the number of bookmakers available.
6. THE system SHALL display all flag values as strictly numeric values (no text labels) in additional columns appended to the output table.
7. IF no flags are triggered for a match, THEN THE flag columns SHALL display 0.00 or be left empty.

### Requirement 9: Retro-Feedback — Prediction Accuracy Tracking

**User Story:** As a quantitative data engineer, I want the system to compare my predictions against actual match results after each matchday, so that I can measure accuracy and identify systematic errors.

#### Acceptance Criteria

1. THE system SHALL provide a `--retro` command-line mode that accepts a date (YYYY-MM-DD) and retrieves the actual final scores for matches previously predicted on that date.
2. THE system SHALL fetch actual match results from The Odds API scores endpoint (or equivalent source) and store them in the local SQLite database alongside the original predictions.
3. FOR each predicted match, THE system SHALL compute: (a) whether the 1X2 outcome was correctly predicted, (b) whether the exact score was correctly predicted, and (c) the absolute error between predicted λ/μ and actual goals scored by each team.
4. THE system SHALL output a retro-feedback table with columns: [Match] | [Predicted Score] | [Actual Score] | [1X2 Correct] | [Score Correct] | [λ Error] | [μ Error] | [Bookmaker Source].
5. THE system SHALL compute and display aggregate accuracy metrics at the bottom of the table: 1X2 hit rate (%), exact score hit rate (%), mean absolute error for λ, and mean absolute error for μ.
6. ALL retro-feedback values SHALL be strictly numeric (1 for correct, 0 for incorrect, decimal values for errors).

### Requirement 10: Retro-Feedback — Bookmaker Reliability Scoring

**User Story:** As a quantitative data engineer, I want the system to track which bookmakers produce odds that lead to accurate predictions over time, so that I can discard or down-weight unreliable bookmakers.

#### Acceptance Criteria

1. THE system SHALL maintain a `bookmaker_scores` table in SQLite that accumulates per-bookmaker accuracy statistics across all retro-feedback runs.
2. FOR each bookmaker, THE system SHALL track: total matches processed, number of correct 1X2 predictions derived from that bookmaker's odds, mean λ error, and mean μ error.
3. THE system SHALL compute a reliability score per bookmaker as the weighted combination: `0.5 * (1X2_hit_rate) + 0.25 * (1 - normalized_lambda_error) + 0.25 * (1 - normalized_mu_error)`, where normalized errors are scaled to [0, 1] relative to the worst-performing bookmaker.
4. WHEN a bookmaker's reliability score falls below a configurable threshold (default: 0.30), THE system SHALL flag that bookmaker as "unreliable" and exclude it from future odds processing until manually re-enabled.
5. THE system SHALL provide a `--bookmaker-report` flag that outputs a table showing all tracked bookmakers with columns: [Bookmaker] | [Matches] | [1X2 Hit Rate %] | [Mean λ Error] | [Mean μ Error] | [Reliability Score] | [Status].
6. THE system SHALL allow manual override to re-enable a flagged bookmaker via a `--enable-bookmaker <key>` command-line argument.
7. ALL bookmaker scoring values SHALL be strictly numeric with no text-based qualitative descriptors in the output.

### Requirement 11: Technology Stack Compliance

**User Story:** As a quantitative data engineer, I want the script to use only the specified Python libraries, so that the project remains lightweight and focused.

#### Acceptance Criteria

1. THE system SHALL use the Python `requests` library for all HTTP communication with The_Odds_API.
2. THE system SHALL use the Python `sqlite3` standard library module for all database operations with the Cache_Store.
3. THE system SHALL use the Python `scipy.optimize.minimize` function for all numerical optimization in the Lambda_Optimizer.
4. THE system SHALL operate as a single-entry-point Python script that requires no external services, servers, or infrastructure beyond The_Odds_API and a local Python 3.9+ interpreter.
5. THE system SHALL limit third-party dependencies to `requests` and `scipy` (including its transitive dependency `numpy`), and MAY use any Python standard library module (e.g., `json`, `math`, `datetime`, `os`).
6. IF a module not listed in the allowed dependencies is imported, THEN THE system SHALL fail a dependency audit that compares the import list against the allowed set of `requests`, `scipy`, `numpy`, and Python standard library modules.