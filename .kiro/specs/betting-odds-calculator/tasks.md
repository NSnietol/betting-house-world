# Implementation Plan: Betting Odds Calculator

## Overview

This plan implements a local Python CLI tool that extracts soccer betting odds from The Odds API v4, removes bookmaker commission, reverse-engineers Expected Goals (λ and μ) via scipy optimization against a Poisson model, and outputs the most probable exact score per match. The implementation proceeds module-by-module following the data pipeline flow, with property-based tests validating each component's correctness properties using Hypothesis.

## Tasks

- [x] 1. Project setup and core data models
  - [x] 1.1 Create project directory structure and configuration files
    - Create `src/` directory with `__init__.py`
    - Create `tests/` directory with `__init__.py`
    - Create `requirements.txt` with: `requests`, `scipy`, `numpy`, `hypothesis` (dev)
    - Create `pyproject.toml` or `setup.cfg` with project metadata and pytest configuration
    - _Requirements: 11.4, 11.5_

  - [x] 1.2 Define all data models and type definitions
    - Create `src/models.py` with all dataclasses: `OddsEvent`, `BookmakerOdds`, `Market`, `Outcome`, `MatchResult`, `CacheEntry`, `Config`, `MatchComparison`, `AggregateMetrics`, `BookmakerStats`
    - Use Python `dataclasses` module
    - Include type annotations for all fields
    - _Requirements: 1.2, 1.3, 4.4, 5.5, 9.3, 10.2_

- [x] 2. Cache Store module (SQLite persistence layer)
  - [x] 2.1 Implement CacheStore class with SQLite schema
    - Create `src/cache_store.py`
    - Implement `__init__` with database creation (cache, predictions, actual_results, bookmaker_scores tables)
    - Implement `get()` method for cache lookups by sport_key, market_type, event_id, bookmaker_keys
    - Implement `put()` method for storing raw JSON responses with metadata
    - Implement `is_expired()` method for TTL checking
    - Handle SQLite locked DB with 3 retries and 1-second backoff
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5_

  - [ ]* 2.2 Write property test for cache round-trip (Property 1)
    - **Property 1: Cache Store Round-Trip**
    - For any valid JSON string and metadata, storing and retrieving with the same keys returns identical JSON
    - **Validates: Requirements 1.7, 2.1, 2.3**

  - [ ]* 2.3 Write property test for cache TTL invalidation (Property 2)
    - **Property 2: Cache TTL Invalidation**
    - For any entry with known retrieved_at and TTL 1-48h, entry is expired iff current_time - retrieved_at > TTL
    - **Validates: Requirements 2.4**

  - [ ]* 2.4 Write property test for cache hit prevents API call (Property 3)
    - **Property 3: Cache Hit Prevents API Call**
    - For any request matching a non-expired cache entry, cached data is returned without HTTP request
    - **Validates: Requirements 2.2**

- [x] 3. Margin Eliminator module
  - [x] 3.1 Implement MarginEliminator with Shin and Logarithmic methods
    - Create `src/margin_eliminator.py`
    - Implement `__init__` accepting method parameter ("shin" or "logarithmic")
    - Implement `eliminate()` converting decimal odds to real probabilities
    - Implement `_shin_method()` with insider trading proportion z and true probability formula
    - Implement `_logarithmic_method()` with log-odds normalization
    - Raise `AnomalousOddsError` when implied probabilities sum ≤ 1.0
    - Define custom exception class `AnomalousOddsError`
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [ ]* 3.2 Write property test for sum-to-one invariant (Property 4)
    - **Property 4: Margin Elimination Sum-to-One Invariant**
    - For any 3 decimal odds where sum of reciprocals > 1.0, real probabilities sum to 1.0 ±0.001
    - **Validates: Requirements 3.2, 3.3**

  - [ ]* 3.3 Write property test for output range (Property 5)
    - **Property 5: Margin Elimination Output Range**
    - For any valid decimal odds with overround > 0, each real probability is in [0.0, 1.0]
    - **Validates: Requirements 3.6**

  - [ ]* 3.4 Write property test for anomalous odds exclusion (Property 6)
    - **Property 6: Anomalous Odds Exclusion**
    - For any odds where sum of reciprocals ≤ 1.0, MarginEliminator rejects them
    - **Validates: Requirements 3.5**

- [x] 4. Lambda Optimizer module
  - [x] 4.1 Implement LambdaOptimizer with scipy L-BFGS-B
    - Create `src/lambda_optimizer.py`
    - Implement `optimize()` with scipy.optimize.minimize using L-BFGS-B method
    - Set bounds ((0.1, 5.0), (0.1, 5.0)), initial guess (1.5, 1.2)
    - Implement objective function: sum of squared differences for 1X2 + over 2.5
    - Implement `_poisson_1x2()` computing P(home), P(draw), P(away) from λ, μ
    - Implement `_poisson_over_2_5()` computing P(total goals > 2.5)
    - Return None on convergence failure or objective ≥ 1e-6
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

  - [ ]* 4.2 Write property test for optimization round-trip (Property 7)
    - **Property 7: Optimization Round-Trip**
    - For any λ ∈ [0.3, 4.0] and μ ∈ [0.3, 4.0], computing Poisson probabilities then optimizing recovers λ, μ within 0.05
    - **Validates: Requirements 4.1, 4.2**

  - [ ]* 4.3 Write property test for optimization bounds (Property 8)
    - **Property 8: Optimization Bounds Constraint**
    - For any valid real probabilities, if optimization succeeds, λ and μ are in [0.1, 5.0]
    - **Validates: Requirements 4.6**

- [x] 5. Score Matrix Generator module
  - [x] 5.1 Implement ScoreMatrixGenerator with 6×6 Poisson grid
    - Create `src/score_matrix.py`
    - Implement `generate()` producing 6×6 matrix, suggested_score, and score_probability
    - Implement `_select_max_cell()` with tiebreaker: lowest total goals, then fewer home goals
    - Use `scipy.stats.poisson.pmf` or manual Poisson PMF calculation
    - _Requirements: 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

  - [ ]* 5.2 Write property test for score matrix structure (Property 9)
    - **Property 9: Score Matrix Structure and Correctness**
    - For any λ, μ ∈ (0, 5.0], each cell (i,j) equals poisson_pmf(i,λ) × poisson_pmf(j,μ)
    - **Validates: Requirements 5.1, 5.2**

  - [ ]* 5.3 Write property test for argmax with tiebreaker (Property 10)
    - **Property 10: Score Matrix Argmax with Tiebreaker**
    - For any 6×6 matrix, selected cell has max probability; ties broken by lowest total goals then fewer home goals
    - **Validates: Requirements 5.3, 5.4**

- [x] 6. Checkpoint - Core mathematical modules
  - Ensure all tests pass, ask the user if questions arise.

- [x] 7. Data Quality Analyzer module
  - [x] 7.1 Implement DataQualityAnalyzer with variance, margin, and coverage flags
    - Create `src/data_quality.py`
    - Implement `analyze()` returning variance_flag, high_margin, low_coverage
    - Implement `_compute_std_dev()` for max std dev across 1X2 outcomes
    - Implement `_compute_overround()` as sum of reciprocals minus 1.0
    - Use thresholds: VARIANCE=0.03, HIGH_MARGIN=0.10, LOW_COVERAGE=3
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 8.7_

  - [ ]* 7.2 Write property test for standard deviation computation (Property 14)
    - **Property 14: Standard Deviation Computation**
    - For any list of bookmaker probability tuples, computed std dev equals population std dev per outcome position
    - **Validates: Requirements 8.1**

  - [ ]* 7.3 Write property test for variance flag threshold (Property 15)
    - **Property 15: Variance Flag Threshold**
    - Max std dev > 0.03 → flag set to max value; max std dev ≤ 0.03 → flag is 0.00
    - **Validates: Requirements 8.2**

  - [ ]* 7.4 Write property test for overround computation (Property 16)
    - **Property 16: Overround Computation**
    - For any 3 decimal odds, overround equals sum of reciprocals minus 1.0
    - **Validates: Requirements 8.3**

  - [ ]* 7.5 Write property test for high margin flag (Property 17)
    - **Property 17: High Margin Flag Threshold**
    - Any bookmaker overround > 0.10 → flag set to max overround; else flag is 0.00
    - **Validates: Requirements 8.4**

- [x] 8. Terminal Output module
  - [x] 8.1 Implement TerminalOutput with pipe-delimited rendering
    - Create `src/output.py`
    - Implement `render()` producing pipe-delimited table with header and data rows
    - Include all columns in order: Match, Real Home Prob %, Real Draw Prob %, Real Away Prob %, xG Home (λ), xG Away (μ), Suggested Score, Score Prob %, Variance Flag, High Margin, Low Coverage
    - All values strictly numeric; flags show 0.00 when not triggered
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [ ]* 8.2 Write property test for numeric-only output (Property 11)
    - **Property 11: Output Contains Only Numeric Values**
    - For any MatchResult set, output contains no qualitative text descriptors
    - **Validates: Requirements 6.7, 8.6**

  - [ ]* 8.3 Write property test for successful matches only in output (Property 12)
    - **Property 12: Only Successful Matches Appear in Output**
    - Output contains exactly one row per successful match, zero rows for failures
    - **Validates: Requirements 6.8**

- [ ] 9. Odds Extractor module
  - [x] 9.1 Implement OddsExtractor with API integration and caching
    - Create `src/odds_extractor.py`
    - Implement `__init__` with api_key, cache_store, ttl_hours
    - Implement `fetch_odds()` with cache-first strategy (check cache → API call → store in cache)
    - Implement `_build_request_params()` for The Odds API v4 query parameters
    - Implement `_filter_events()` ensuring min 3 bookmakers with both h2h and totals markets
    - Handle HTTP timeout (30s), 4xx, 5xx errors with `OddsExtractionError`
    - Handle stale cache fallback on API failure for expired entries
    - Support bookmaker exclusion list from BookmakerScorer
    - Define custom exception `OddsExtractionError`
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.2, 2.4, 2.5_

  - [ ]* 9.2 Write property test for date filtering (Property 13)
    - **Property 13: Date Filtering Correctness**
    - For any events with varying commence_time and a target date, only events within that date (UTC) are processed
    - **Validates: Requirements 7.2**

- [ ] 10. RetroFeedback module
  - [ ] 10.1 Implement RetroFeedback with prediction comparison and aggregate metrics
    - Create `src/retro_feedback.py`
    - Implement `run()` orchestrating the full retro-feedback flow for a date
    - Implement `fetch_actual_scores()` from The Odds API scores endpoint
    - Implement `compare_prediction()` computing x1x2_correct, score_correct, λ/μ errors
    - Implement `compute_aggregates()` for hit rates and mean errors
    - Implement `render_retro_table()` with pipe-delimited output
    - Add methods to store predictions and load them for comparison
    - Handle edge cases: no predictions found, scores not available, division by zero
    - _Requirements: 9.1, 9.2, 9.3, 9.4, 9.5, 9.6_

  - [ ]* 10.2 Write property test for retro accuracy computation (Property 18)
    - **Property 18: Retro-Feedback Accuracy Computation**
    - For any predicted/actual score pair, correctly computes 1X2 correctness, exact score match, λ and μ errors
    - **Validates: Requirements 9.3**

  - [ ]* 10.3 Write property test for aggregate metrics (Property 19)
    - **Property 19: Aggregate Metrics Correctness**
    - For any non-empty comparison list, hit rates and mean errors are computed correctly
    - **Validates: Requirements 9.5**

  - [ ]* 10.4 Write property test for retro output numeric-only (Property 20)
    - **Property 20: Retro-Feedback Output Contains Only Numeric Values**
    - Retro table contains no qualitative text; booleans as 1/0, errors as decimals
    - **Validates: Requirements 9.4, 9.6**

- [ ] 11. BookmakerScorer module
  - [ ] 11.1 Implement BookmakerScorer with reliability scoring and exclusion
    - Create `src/bookmaker_scorer.py`
    - Implement `update_scores()` accumulating per-bookmaker statistics from retro runs
    - Implement `compute_reliability_score()` with weighted formula: 0.5*hit_rate + 0.25*(1-norm_λ_err) + 0.25*(1-norm_μ_err)
    - Implement `flag_unreliable()` checking against configurable threshold (default 0.30)
    - Implement `get_excluded_bookmakers()` returning list of flagged keys
    - Implement `enable_bookmaker()` for manual re-enablement
    - Implement `render_report()` with pipe-delimited bookmaker report table
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7_

  - [ ]* 11.2 Write property test for bookmaker stats accumulation (Property 21)
    - **Property 21: Bookmaker Statistics Accumulation**
    - For any sequence of retro runs, total_matches, correct_1x2, and mean errors accumulate correctly
    - **Validates: Requirements 10.2**

  - [ ]* 11.3 Write property test for reliability score and threshold (Property 22)
    - **Property 22: Reliability Score Formula and Threshold Flagging**
    - Score equals weighted formula; bookmaker flagged iff score < threshold
    - **Validates: Requirements 10.3, 10.4**

  - [ ]* 11.4 Write property test for bookmaker report numeric-only (Property 23)
    - **Property 23: Bookmaker Report Output Contains Only Numeric Values**
    - Report contains no qualitative text; Status as 1 (active) or 0 (excluded)
    - **Validates: Requirements 10.5, 10.7**

- [ ] 12. Checkpoint - All modules implemented
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 13. CLI entry point and pipeline wiring
  - [ ] 13.1 Implement CLI argument parsing and main pipeline orchestration
    - Create `src/main.py` as the single entry point
    - Implement argument parsing: `--sport` (required), `--date`, `--round`, `--method`, `--ttl`, `--bookmakers`, `--retro`, `--bookmaker-report`, `--enable-bookmaker`
    - Implement input validation with proper error messages and exit codes
    - Read `ODDS_API_KEY` from environment variable
    - Wire the full processing pipeline: OddsExtractor → MarginEliminator → LambdaOptimizer → ScoreMatrixGenerator → DataQualityAnalyzer → TerminalOutput
    - Implement per-bookmaker processing with median aggregation across bookmakers
    - Handle `--retro` mode triggering RetroFeedback flow
    - Handle `--bookmaker-report` mode triggering BookmakerScorer report
    - Handle `--enable-bookmaker` mode for re-enabling flagged bookmakers
    - Store predictions in SQLite for later retro-feedback comparison
    - Skip failed matches with stderr logging
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 11.4_

  - [ ] 13.2 Implement median aggregation across bookmakers
    - In the pipeline, process each bookmaker's odds independently through MarginEliminator
    - Compute median real probability for each outcome across all valid bookmakers
    - Pass median probabilities to LambdaOptimizer
    - Exclude bookmakers flagged by BookmakerScorer
    - _Requirements: 1.4, 3.4, 10.4_

- [ ] 14. Integration tests
  - [ ]* 14.1 Write integration tests for end-to-end pipeline
    - Create `tests/test_integration.py`
    - Test full pipeline with mocked API responses (recorded fixtures)
    - Test SQLite persistence across script restarts
    - Test retro-feedback end-to-end with mocked scores API
    - Test bookmaker scoring persistence across multiple retro sessions
    - Test bookmaker exclusion and re-enablement flow
    - _Requirements: 1.1, 2.1, 6.1, 9.1, 10.1_

- [ ] 15. Final checkpoint - Complete system validation
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- Each task references specific requirements for traceability
- Checkpoints ensure incremental validation
- Property tests validate universal correctness properties using the `hypothesis` library
- Unit tests validate specific examples and edge cases
- The implementation language is Python 3.9+ as specified in the design
- All 23 correctness properties from the design document are covered by property test sub-tasks
- The median aggregation strategy processes each bookmaker independently before computing median probabilities

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2"] },
    { "id": 2, "tasks": ["2.1", "3.1", "4.1", "5.1"] },
    { "id": 3, "tasks": ["2.2", "2.3", "3.2", "3.3", "3.4", "4.2", "4.3", "5.2", "5.3"] },
    { "id": 4, "tasks": ["7.1", "8.1"] },
    { "id": 5, "tasks": ["2.4", "7.2", "7.3", "7.4", "7.5", "8.2", "8.3"] },
    { "id": 6, "tasks": ["9.1"] },
    { "id": 7, "tasks": ["9.2", "10.1"] },
    { "id": 8, "tasks": ["10.2", "10.3", "10.4", "11.1"] },
    { "id": 9, "tasks": ["11.2", "11.3", "11.4"] },
    { "id": 10, "tasks": ["13.1", "13.2"] },
    { "id": 11, "tasks": ["14.1"] }
  ]
}
```
