# Implementation Plan: Enhanced Goals Extraction for xG Estimation

## Overview

Extend the Kambi/Unibet adapter to extract multi-line O/U, team-specific O/U, and correct score markets. Upgrade the LambdaOptimizer with a weighted multi-line objective producing dual xG estimates. Wire everything through the pipeline with divergence reporting. All changes use Python with existing dependencies (scipy, numpy, requests).

## Tasks

- [ ] 1. Extend data models with new fields and dataclasses
  - [~] 1.1 Add OptimizationResult and DivergenceResult dataclasses to `src/models.py`
    - Add `OptimizationResult` with `primary_lambda`, `primary_mu`, `secondary_lambda`, `secondary_mu`
    - Add `DivergenceResult` with `lambda_divergence`, `mu_divergence`, `is_high_divergence`
    - _Requirements: 5.3, 7.1, 7.2_

  - [~] 1.2 Extend `AggregatedEvent` dataclass in `src/models.py`
    - Add `overall_ou_lines: dict[str, dict[float, tuple[float, float]]]` with `field(default_factory=dict)`
    - Add `home_team_ou_lines: dict[str, dict[float, tuple[float, float]]]` with `field(default_factory=dict)`
    - Add `away_team_ou_lines: dict[str, dict[float, tuple[float, float]]]` with `field(default_factory=dict)`
    - Add `correct_score_odds: dict[str, dict[tuple[int, int], float]] | None = field(default=None)`
    - Preserve existing `bookmaker_odds_over_under` field unchanged
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

  - [~] 1.3 Extend `MatchResult` dataclass in `src/models.py`
    - Add `secondary_lambda: float | None = None`
    - Add `secondary_mu: float | None = None`
    - Add `lambda_divergence: float | None = None`
    - Add `mu_divergence: float | None = None`
    - Add `is_high_divergence: bool = False`
    - _Requirements: 7.3_

- [ ] 2. Implement UnibetAdapter parsing extensions in `src/adapters/unibet.py`
  - [~] 2.1 Add multi-line overall O/U parsing methods
    - Add `_OVERALL_THRESHOLDS: set[float] = {0.5, 1.5, 2.5, 3.5, 4.5}` constant
    - Implement `_find_overall_ou_outcomes(self, bet_offers)` — iterate bet offers for criterion.label == "Total Goals" (exactly, not containing team names), extract Over/Under at all available thresholds
    - Implement `_map_multi_line_ou_outcomes(self, outcomes, allowed_thresholds)` — convert milliodds/milligoals to decimal, filter by allowed thresholds
    - _Requirements: 1.1, 1.2, 1.3_

  - [~] 2.2 Add team-specific O/U parsing methods
    - Add `_TEAM_THRESHOLDS: set[float] = {0.5, 1.5, 2.5}` constant
    - Implement `_classify_team(self, criterion_label, home_name, away_name)` — extract team name from "Total Goals by {TeamName}", compare case-insensitively, return "home"/"away"/None
    - Implement `_find_team_ou_outcomes(self, bet_offers, home_name, away_name)` — find team-specific criterion labels, extract outcomes with correct team identifier
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [~] 2.3 Add correct score parsing method
    - Implement `_find_correct_score_outcomes(self, bet_offers)` — identify criterion.label containing "Correct Score", parse "H-A" labels into MarketOutcome with name="{h}-{a}", odds as decimal
    - _Requirements: 3.1, 3.2_

  - [~] 2.4 Add public fetch methods `fetch_over_under_total`, `fetch_over_under_team`, `fetch_correct_score`
    - Implement `fetch_over_under_total(self, sport, date)` — uses `_find_overall_ou_outcomes`, returns `ScrapedMatch` with market_type="over_under_total"
    - Implement `fetch_over_under_team(self, sport, date)` — uses `_find_team_ou_outcomes`, returns `ScrapedMatch` with market_type="over_under_team"
    - Implement `fetch_correct_score(self, sport, date)` — uses `_find_correct_score_outcomes`, returns `ScrapedMatch` with market_type="correct_score"
    - All methods skip missing markets gracefully (no error on absence)
    - _Requirements: 1.4, 2.5, 3.3, 3.4_

- [~] 3. Checkpoint - Ensure adapter parsing tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 4. Implement weighted multi-line LambdaOptimizer in `src/lambda_optimizer.py`
  - [~] 4.1 Add Poisson helper methods for multi-line
    - Implement `_poisson_over_threshold(self, lam, mu, threshold)` — P(total > threshold) under independent Poisson, general version of existing `_poisson_over_2_5`
    - Implement `_poisson_team_over_threshold(self, rate, threshold)` — P(team goals > threshold) under single Poisson(rate)
    - _Requirements: 4.1, 4.2, 4.3_

  - [~] 4.2 Implement `optimize_weighted` method and objective functions
    - Add class constants `WEIGHT_OVERALL = 1.0`, `WEIGHT_TEAM = 2.0`, `WEIGHT_1X2 = 1.0`
    - Implement `_weighted_objective(self, params, real_probs_1x2, overall_ou_targets, home_team_ou_targets, away_team_ou_targets)` — weighted SSE combining 1X2 + overall O/U + team O/U
    - Implement `_secondary_objective(self, params, real_probs_1x2, home_team_ou_targets, away_team_ou_targets)` — team O/U + 1X2 only
    - Implement `optimize_weighted(self, real_probs_1x2, overall_ou_targets, home_team_ou_targets, away_team_ou_targets)` → `OptimizationResult`
    - When both team targets available, run secondary optimization; set secondary to None otherwise
    - Keep existing `optimize()` method unchanged for backward compatibility
    - _Requirements: 4.4, 4.5, 4.6, 5.1, 5.2, 5.3, 5.4_

  - [ ]* 4.3 Write unit tests for `optimize_weighted` in `tests/test_lambda_optimizer.py`
    - Test with Poisson-generated targets verifying recovery within ±0.05
    - Test secondary estimate is None when team data is missing
    - Test legacy `optimize()` still works unchanged
    - _Requirements: 4.6, 5.1, 5.2, 5.4_

- [ ] 5. Implement divergence reporter in `src/divergence.py`
  - [~] 5.1 Create `src/divergence.py` with `compute_divergence` function
    - Define `DIVERGENCE_THRESHOLD = 0.3`
    - Implement `compute_divergence(result: OptimizationResult) -> DivergenceResult | None`
    - Return None when secondary estimate is unavailable
    - Compute absolute differences and set `is_high_divergence` flag when either exceeds 0.3
    - _Requirements: 7.1, 7.2_

  - [ ]* 5.2 Write unit tests for divergence in `tests/test_divergence.py`
    - Test high divergence flagging at threshold boundary
    - Test None return when secondary is None
    - _Requirements: 7.1, 7.2_

- [~] 6. Checkpoint - Ensure optimizer and divergence tests pass
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 7. Wire pipeline: OddsExtractor and process_event enhancements
  - [~] 7.1 Extend `OddsExtractor.extract()` to call new adapter methods
    - In the adapter fetch loop, additionally call `fetch_over_under_total`, `fetch_over_under_team`, `fetch_correct_score` and append results to `all_matches`
    - _Requirements: 1.4, 2.5, 3.3_

  - [~] 7.2 Extend `_build_aggregated_event` to populate new AggregatedEvent fields
    - Handle market_type "over_under_total" → populate `overall_ou_lines`
    - Handle market_type "over_under_team" → populate `home_team_ou_lines` / `away_team_ou_lines`
    - Handle market_type "correct_score" → populate `correct_score_odds`
    - _Requirements: 6.1, 6.2, 6.3, 6.4_

  - [~] 7.3 Create/update pipeline `process_event` in `src/main.py` to use `optimize_weighted`
    - Add `_compute_multi_line_ou_probs` helper to convert raw odds to fair probabilities (average across bookmakers, apply margin elimination)
    - Call `optimize_weighted()` when multi-line data is available, fall back to legacy `optimize()` otherwise
    - Call `compute_divergence()` and populate extended MatchResult fields
    - _Requirements: 4.1, 4.2, 4.3, 5.2, 5.3, 7.3_

  - [~] 7.4 Update `_serialize_events` / `_deserialize_events` for new AggregatedEvent fields
    - Serialize/deserialize `overall_ou_lines`, `home_team_ou_lines`, `away_team_ou_lines`, `correct_score_odds`
    - Maintain backward compatibility with old cached data (missing fields default to empty/None)
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5_

- [~] 8. Final checkpoint - Ensure all tests pass
  - Ensure all tests pass (`uv run pytest tests/ -v`), ask the user if questions arise.

- [ ]* 9. Property-based tests for enhanced extraction
  - [ ]* 9.1 Write property test for overall O/U extraction completeness
    - **Property 1: Overall O/U extraction completeness and correctness**
    - **Validates: Requirements 1.1, 1.2, 1.3, 1.4**

  - [ ]* 9.2 Write property test for team O/U extraction with correct team mapping
    - **Property 2: Team O/U extraction with correct team mapping**
    - **Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

  - [ ]* 9.3 Write property test for correct score parsing round-trip
    - **Property 3: Correct score parsing round-trip**
    - **Validates: Requirements 3.1, 3.2, 3.3**

  - [ ]* 9.4 Write property test for weighted objective function correctness
    - **Property 4: Weighted objective function correctness**
    - **Validates: Requirements 4.4, 4.5**

  - [ ]* 9.5 Write property test for primary estimate recovery
    - **Property 5: Primary estimate recovery from Poisson-generated targets**
    - **Validates: Requirements 5.2**

  - [ ]* 9.6 Write property test for secondary estimate recovery
    - **Property 6: Secondary estimate recovery from team-only targets**
    - **Validates: Requirements 5.1**

  - [ ]* 9.7 Write property test for secondary estimate None when team data absent
    - **Property 7: Secondary estimate is None when team data is absent**
    - **Validates: Requirements 5.4**

  - [ ]* 9.8 Write property test for divergence computation and flagging
    - **Property 8: Divergence computation and flagging**
    - **Validates: Requirements 7.1, 7.2**

  - [ ]* 9.9 Write property test for backward compatibility of legacy optimize()
    - **Property 9: Backward compatibility of legacy optimize()**
    - **Validates: Requirements 4.6**

## Notes

- Tasks marked with `*` are optional and can be skipped for faster MVP
- The Kambi API is already working (tested live with World Cup 2026 data) — no API setup needed
- Existing `fetch_over_under()` and `optimize()` remain untouched for backward compatibility
- All new adapter methods reuse the existing `_fetch_json` / `_extract_events_from_betoffer` infrastructure
- Run tests with: `uv run pytest tests/ -v`
- Property tests validate universal correctness properties using `hypothesis`
- Unit tests validate specific examples and edge cases

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.2", "1.3"] },
    { "id": 1, "tasks": ["2.1", "2.2", "2.3", "4.1", "5.1"] },
    { "id": 2, "tasks": ["2.4", "4.2", "5.2"] },
    { "id": 3, "tasks": ["4.3", "7.1", "7.2"] },
    { "id": 4, "tasks": ["7.3", "7.4"] },
    { "id": 5, "tasks": ["9.1", "9.2", "9.3", "9.4", "9.5", "9.6", "9.7", "9.8", "9.9"] }
  ]
}
```
