# Technical Design: Enhanced Goals Extraction for xG Estimation

## Overview

This design extends the existing Kambi/Unibet adapter and LambdaOptimizer to extract richer Over/Under market data (multiple goal lines plus team-specific totals), apply a weighted multi-line optimization objective, produce dual xG estimates with confidence validation, and optionally extract correct score markets. The changes touch four layers: the adapter (parsing), the data models (transport), the optimizer (math), and the pipeline (wiring/output).

## Architecture

The pipeline remains linear but gains richer data flow:

```
UnibetAdapter (enhanced extraction)
    │
    ├── fetch_over_under_total()    → ScrapedMatch[market_type="over_under_total"]
    ├── fetch_over_under_team()     → ScrapedMatch[market_type="over_under_team"]
    └── fetch_correct_score()       → ScrapedMatch[market_type="correct_score"]
    │
OddsExtractor._correlate_events()  → AggregatedEvent (extended fields)
    │
MarginEliminator.eliminate()       → fair probabilities per line
    │
LambdaOptimizer.optimize_weighted() → OptimizationResult(primary, secondary)
    │
DivergenceReporter.compute()       → MatchResult (extended with dual estimates)
```

No new external dependencies are introduced. All computation uses `scipy`, `numpy`, `math` (stdlib), and existing project patterns.

## Components and Interfaces

### 1. UnibetAdapter Extensions

The adapter gains three new public methods alongside the existing `fetch_over_under()` (kept for backward compatibility):

```python
# src/adapters/unibet.py

def fetch_over_under_total(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
    """Fetch all available overall O/U lines (0.5 through 4.5)."""
    ...

def fetch_over_under_team(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
    """Fetch team-specific O/U lines for home and away."""
    ...

def fetch_correct_score(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
    """Fetch correct score market odds."""
    ...
```

#### Internal Parsing Methods

```python
_OVERALL_THRESHOLDS: set[float] = {0.5, 1.5, 2.5, 3.5, 4.5}
_TEAM_THRESHOLDS: set[float] = {0.5, 1.5, 2.5}

def _find_overall_ou_outcomes(self, bet_offers: list[dict]) -> list[MarketOutcome]:
    """Extract all overall O/U outcomes across multiple thresholds.

    Iterates bet offers looking for criterion.label == "Total Goals"
    (exactly, not containing team names). For each matching offer,
    extracts Over/Under outcomes at all available thresholds in
    _OVERALL_THRESHOLDS.

    Returns:
        List of MarketOutcome with name="Over"/"Under" and point set
        to the threshold value (e.g., 0.5, 2.5).
    """
    ...

def _find_team_ou_outcomes(
    self, bet_offers: list[dict], home_name: str, away_name: str
) -> list[MarketOutcome]:
    """Extract team-specific O/U outcomes.

    Identifies bet offers where criterion.label matches pattern
    "Total Goals by {TeamName}". Maps the team name to "home" or
    "away" by case-insensitive comparison with home_name / away_name.

    Returns:
        List of MarketOutcome with name="Over {team}"/"Under {team}"
        where team is "home" or "away", and point set to threshold.
    """
    ...

def _find_correct_score_outcomes(self, bet_offers: list[dict]) -> list[MarketOutcome]:
    """Extract correct score outcomes.

    Identifies bet offers where criterion.label contains "Correct Score".
    Parses outcome labels in format "X-Y" (e.g., "1-0", "2-1") into
    MarketOutcome with name="{home_goals}-{away_goals}", odds as decimal,
    and point=None.

    Returns:
        List of MarketOutcome for each scoreline.
    """
    ...

def _map_multi_line_ou_outcomes(
    self, outcomes: list[dict], allowed_thresholds: set[float]
) -> list[MarketOutcome]:
    """Convert Kambi outcome dicts to MarketOutcome for multiple O/U lines.

    Converts milliodds to decimal (÷1000) and milligoals to float (÷1000).
    Only includes outcomes whose line value falls within allowed_thresholds.

    Args:
        outcomes: Raw Kambi outcome dicts with odds, line, label fields.
        allowed_thresholds: Set of valid threshold values to extract.

    Returns:
        List of MarketOutcome with correct name, odds, and point.
    """
    ...

def _classify_team(
    self, criterion_label: str, home_name: str, away_name: str
) -> str | None:
    """Determine if a criterion label refers to home or away team.

    Extracts the team name from patterns like "Total Goals by Argentina"
    and compares case-insensitively against home_name and away_name.

    Returns:
        "home", "away", or None if no match.
    """
    ...
```

### 2. AggregatedEvent Model Extension

```python
# src/models.py

@dataclass
class AggregatedEvent:
    """A single match with odds aggregated from multiple bookmaker adapters."""

    home_team: str
    away_team: str
    event_timestamp: datetime
    bookmaker_odds_1x2: dict[str, tuple[float, float, float]]
    bookmaker_odds_over_under: dict[str, tuple[float, float]]  # kept for backward compat
    source_count: int

    # New fields for enhanced extraction
    overall_ou_lines: dict[str, dict[float, tuple[float, float]]] = field(default_factory=dict)
    # bookmaker_id -> {threshold: (over_odds, under_odds)}

    home_team_ou_lines: dict[str, dict[float, tuple[float, float]]] = field(default_factory=dict)
    # bookmaker_id -> {threshold: (over_odds, under_odds)}

    away_team_ou_lines: dict[str, dict[float, tuple[float, float]]] = field(default_factory=dict)
    # bookmaker_id -> {threshold: (over_odds, under_odds)}

    correct_score_odds: dict[str, dict[tuple[int, int], float]] | None = field(default=None)
    # bookmaker_id -> {(home_goals, away_goals): decimal_odds}
```

### 3. OptimizationResult Dataclass

```python
# src/models.py

@dataclass
class OptimizationResult:
    """Result of the weighted multi-line optimization."""

    primary_lambda: float
    primary_mu: float
    secondary_lambda: float | None = None
    secondary_mu: float | None = None
```

### 4. Weighted Multi-Line LambdaOptimizer

```python
# src/lambda_optimizer.py

class LambdaOptimizer:
    """Fits expected goals parameters using scipy L-BFGS-B optimization."""

    INITIAL_GUESS = (1.5, 1.2)
    BOUNDS = ((0.1, 5.0), (0.1, 5.0))
    CONVERGENCE_THRESHOLD = 0.01

    WEIGHT_OVERALL: float = 1.0
    WEIGHT_TEAM: float = 2.0
    WEIGHT_1X2: float = 1.0

    def optimize(
        self,
        real_probs_1x2: tuple[float, float, float],
        real_prob_over_2_5: float,
    ) -> tuple[float, float] | None:
        """Legacy interface — backward compatible."""
        ...

    def optimize_weighted(
        self,
        real_probs_1x2: tuple[float, float, float],
        overall_ou_targets: list[tuple[float, float]] | None = None,
        home_team_ou_targets: list[tuple[float, float]] | None = None,
        away_team_ou_targets: list[tuple[float, float]] | None = None,
    ) -> OptimizationResult | None:
        """Find λ and μ using weighted multi-line objective.

        Produces a Primary_Estimate using all available data and optionally
        a Secondary_Estimate using only team-specific lines + 1X2.

        Objective (primary):
            f(λ, μ) = WEIGHT_1X2 * Σ(P_1x2_poisson_i - p_1x2_i)²
                     + WEIGHT_OVERALL * Σ(P_over_poisson_t - p_over_t)²
                     + WEIGHT_TEAM * Σ(P_home_over_poisson_t - p_home_over_t)²
                     + WEIGHT_TEAM * Σ(P_away_over_poisson_t - p_away_over_t)²

        Args:
            real_probs_1x2: (p_home, p_draw, p_away) fair probabilities.
            overall_ou_targets: List of (threshold, over_probability) for total goals.
            home_team_ou_targets: List of (threshold, over_probability) for home team.
            away_team_ou_targets: List of (threshold, over_probability) for away team.

        Returns:
            OptimizationResult with primary and optional secondary estimates,
            or None on convergence failure.
        """
        ...

    def _weighted_objective(
        self,
        params: list[float],
        real_probs_1x2: tuple[float, float, float],
        overall_ou_targets: list[tuple[float, float]],
        home_team_ou_targets: list[tuple[float, float]],
        away_team_ou_targets: list[tuple[float, float]],
    ) -> float:
        """Compute weighted sum of squared errors.

        For overall O/U lines: computes P(total > threshold) under independent
        Poisson(λ+μ) and penalizes squared difference with WEIGHT_OVERALL.

        For team O/U lines: computes P(team > threshold) under Poisson(λ) or
        Poisson(μ) and penalizes with WEIGHT_TEAM.
        """
        ...

    def _secondary_objective(
        self,
        params: list[float],
        real_probs_1x2: tuple[float, float, float],
        home_team_ou_targets: list[tuple[float, float]],
        away_team_ou_targets: list[tuple[float, float]],
    ) -> float:
        """Objective using only team O/U lines + 1X2 for secondary estimate."""
        ...

    def _poisson_over_threshold(self, lam: float, mu: float, threshold: float) -> float:
        """P(total goals > threshold) under independent Poisson.

        P(over T) = 1 - Σ P(X=i)*P(Y=j) for all i+j <= floor(T)
        """
        ...

    def _poisson_team_over_threshold(self, rate: float, threshold: float) -> float:
        """P(team goals > threshold) under Poisson(rate).

        P(over T) = 1 - Σ P(X=k) for k = 0..floor(T)
        """
        ...
```

### 5. Divergence Reporter

```python
# src/divergence.py

from __future__ import annotations

from dataclasses import dataclass

from src.models import OptimizationResult

DIVERGENCE_THRESHOLD: float = 0.3


@dataclass
class DivergenceResult:
    """Divergence between primary and secondary xG estimates."""

    lambda_divergence: float
    mu_divergence: float
    is_high_divergence: bool


def compute_divergence(result: OptimizationResult) -> DivergenceResult | None:
    """Compute divergence between primary and secondary estimates.

    Args:
        result: OptimizationResult with both primary and secondary values.

    Returns:
        DivergenceResult with absolute differences and flag,
        or None if secondary estimate is not available.
    """
    if result.secondary_lambda is None or result.secondary_mu is None:
        return None

    lambda_div = abs(result.primary_lambda - result.secondary_lambda)
    mu_div = abs(result.primary_mu - result.secondary_mu)
    is_high = lambda_div > DIVERGENCE_THRESHOLD or mu_div > DIVERGENCE_THRESHOLD

    return DivergenceResult(
        lambda_divergence=lambda_div,
        mu_divergence=mu_div,
        is_high_divergence=is_high,
    )
```

### 6. Extended MatchResult

```python
# src/models.py

@dataclass
class MatchResult:
    """Result of processing a single match through the full pipeline."""

    home_team: str
    away_team: str
    real_probs: tuple[float, float, float]
    lambda_home: float
    mu_away: float
    suggested_score: tuple[int, int]
    score_probability: float
    variance_flag: float
    high_margin: float
    low_coverage: int

    # New fields for dual estimates
    secondary_lambda: float | None = None
    secondary_mu: float | None = None
    lambda_divergence: float | None = None
    mu_divergence: float | None = None
    is_high_divergence: bool = False
```

### 7. OddsExtractor Enhancement

The `_build_aggregated_event` method in `OddsExtractor` is extended to populate the new `AggregatedEvent` fields from `over_under_total`, `over_under_team`, and `correct_score` market types:

```python
def _build_aggregated_event(
    self,
    cluster: list[tuple[str, ScrapedMatch]],
) -> AggregatedEvent | None:
    """Build an AggregatedEvent from a cluster of correlated matches.

    Extended to handle new market types:
    - "over_under_total" → overall_ou_lines
    - "over_under_team" → home_team_ou_lines / away_team_ou_lines
    - "correct_score" → correct_score_odds
    """
    ...
    # Existing logic for 1x2 and over_under...

    overall_ou: dict[str, dict[float, tuple[float, float]]] = {}
    home_ou: dict[str, dict[float, tuple[float, float]]] = {}
    away_ou: dict[str, dict[float, tuple[float, float]]] = {}
    correct_scores: dict[str, dict[tuple[int, int], float]] = {}

    for adapter_id, match in cluster:
        if match.market_type == "over_under_total":
            lines: dict[float, tuple[float, float]] = {}
            for i in range(0, len(match.outcomes), 2):
                over_out = match.outcomes[i]
                under_out = match.outcomes[i + 1] if i + 1 < len(match.outcomes) else None
                if over_out.point is not None and under_out is not None:
                    lines[over_out.point] = (over_out.odds, under_out.odds)
            if lines:
                overall_ou[adapter_id] = lines

        elif match.market_type == "over_under_team":
            for outcome in match.outcomes:
                if outcome.point is not None:
                    team = "home" if "home" in outcome.name.lower() else "away"
                    target = home_ou if team == "home" else away_ou
                    # Group by adapter and threshold
                    ...

        elif match.market_type == "correct_score":
            scores: dict[tuple[int, int], float] = {}
            for outcome in match.outcomes:
                parts = outcome.name.split("-")
                if len(parts) == 2:
                    h, a = int(parts[0]), int(parts[1])
                    scores[(h, a)] = outcome.odds
            if scores:
                correct_scores[adapter_id] = scores
    ...
```

### 8. Pipeline Integration (process_event Enhancement)

```python
def process_event(
    event: AggregatedEvent,
    margin_eliminator: MarginEliminator,
    lambda_optimizer: LambdaOptimizer,
    score_matrix_generator: ScoreMatrixGenerator,
    data_quality_analyzer: DataQualityAnalyzer,
) -> MatchResult | None:
    """Process a single aggregated event through the full math pipeline.

    Enhanced to:
    1. Compute fair probabilities for all O/U lines (not just 2.5)
    2. Call optimize_weighted() when multi-line data is available
    3. Compute divergence between primary and secondary estimates
    4. Fall back to legacy optimize() when no multi-line data
    """
    ...
    # After margin elimination for 1X2...

    # Compute overall O/U fair probabilities
    overall_ou_targets = _compute_multi_line_ou_probs(event.overall_ou_lines)

    # Compute team O/U fair probabilities
    home_team_targets = _compute_multi_line_ou_probs(event.home_team_ou_lines)
    away_team_targets = _compute_multi_line_ou_probs(event.away_team_ou_lines)

    # Choose optimization path
    if overall_ou_targets or home_team_targets or away_team_targets:
        opt_result = lambda_optimizer.optimize_weighted(
            avg_probs,
            overall_ou_targets=overall_ou_targets,
            home_team_ou_targets=home_team_targets,
            away_team_ou_targets=away_team_targets,
        )
    else:
        # Legacy fallback
        legacy = lambda_optimizer.optimize(avg_probs, real_prob_over_2_5)
        ...

    # Compute divergence
    divergence = compute_divergence(opt_result)
    ...
```

## Data Models

### AggregatedEvent (Extended)

| Field | Type | Description |
|-------|------|-------------|
| `home_team` | `str` | Home team name |
| `away_team` | `str` | Away team name |
| `event_timestamp` | `datetime` | Match kickoff time (UTC) |
| `bookmaker_odds_1x2` | `dict[str, tuple[float, float, float]]` | bookmaker → (home, draw, away) |
| `bookmaker_odds_over_under` | `dict[str, tuple[float, float]]` | bookmaker → (over_2.5, under_2.5) — **kept for backward compat** |
| `source_count` | `int` | Number of distinct bookmaker sources |
| `overall_ou_lines` | `dict[str, dict[float, tuple[float, float]]]` | bookmaker → {threshold: (over, under)} |
| `home_team_ou_lines` | `dict[str, dict[float, tuple[float, float]]]` | bookmaker → {threshold: (over, under)} |
| `away_team_ou_lines` | `dict[str, dict[float, tuple[float, float]]]` | bookmaker → {threshold: (over, under)} |
| `correct_score_odds` | `dict[str, dict[tuple[int,int], float]] \| None` | bookmaker → {(h,a): odds} |

### OptimizationResult (New)

| Field | Type | Description |
|-------|------|-------------|
| `primary_lambda` | `float` | λ from full weighted optimization |
| `primary_mu` | `float` | μ from full weighted optimization |
| `secondary_lambda` | `float \| None` | λ from team-only optimization (None if unavailable) |
| `secondary_mu` | `float \| None` | μ from team-only optimization (None if unavailable) |

### DivergenceResult (New)

| Field | Type | Description |
|-------|------|-------------|
| `lambda_divergence` | `float` | \|primary_λ - secondary_λ\| |
| `mu_divergence` | `float` | \|primary_μ - secondary_μ\| |
| `is_high_divergence` | `bool` | True if either divergence > 0.3 |

### MatchResult (Extended)

| Field | Type | Description |
|-------|------|-------------|
| *(existing fields)* | — | Unchanged |
| `secondary_lambda` | `float \| None` | Secondary λ estimate |
| `secondary_mu` | `float \| None` | Secondary μ estimate |
| `lambda_divergence` | `float \| None` | Absolute λ divergence |
| `mu_divergence` | `float \| None` | Absolute μ divergence |
| `is_high_divergence` | `bool` | High divergence flag |

## Data Flow

```
Kambi API Response
    │
    ▼
UnibetAdapter._extract_events_from_betoffer()
    │
    ├── _find_overall_ou_outcomes()  → [MarketOutcome(name="Over", point=0.5, odds=X), ...]
    ├── _find_team_ou_outcomes()     → [MarketOutcome(name="Over home", point=1.5, odds=Y), ...]
    └── _find_correct_score_outcomes() → [MarketOutcome(name="1-0", odds=Z), ...]
    │
    ▼
OddsExtractor._build_aggregated_event()
    │
    ▼
AggregatedEvent(
    overall_ou_lines={"unibet": {0.5: (1.05, 12.0), 2.5: (2.10, 1.72), ...}},
    home_team_ou_lines={"unibet": {0.5: (1.30, 3.50), 1.5: (2.80, 1.40)}},
    away_team_ou_lines={"unibet": {0.5: (1.50, 2.60), 1.5: (3.20, 1.35)}},
    correct_score_odds={"unibet": {(1,0): 6.50, (0,0): 8.00, ...}},
)
    │
    ▼
_compute_multi_line_ou_probs() → [(0.5, 0.92), (1.5, 0.73), (2.5, 0.52), ...]
    │
    ▼
LambdaOptimizer.optimize_weighted()
    │
    ▼
OptimizationResult(primary_lambda=1.45, primary_mu=1.10, secondary_lambda=1.42, secondary_mu=1.08)
    │
    ▼
compute_divergence() → DivergenceResult(lambda_div=0.03, mu_div=0.02, is_high=False)
    │
    ▼
MatchResult (extended)
```

### Public API Changes (Interfaces)

| Component | Method | Change |
|-----------|--------|--------|
| `UnibetAdapter` | `fetch_over_under_total()` | **New** — returns multi-line O/U |
| `UnibetAdapter` | `fetch_over_under_team()` | **New** — returns team-specific O/U |
| `UnibetAdapter` | `fetch_correct_score()` | **New** — returns correct score odds |
| `UnibetAdapter` | `fetch_over_under()` | **Unchanged** — still returns O/U 2.5 only |
| `LambdaOptimizer` | `optimize()` | **Unchanged** — backward compatible |
| `LambdaOptimizer` | `optimize_weighted()` | **New** — accepts multi-line targets |
| `OddsExtractor` | `extract()` | **Enhanced** — calls new adapter methods |
| `AggregatedEvent` | dataclass | **Extended** — new optional fields with defaults |
| `MatchResult` | dataclass | **Extended** — new optional fields with defaults |

### MarketOutcome Conventions

| Market Type | MarketOutcome.name | MarketOutcome.point | MarketOutcome.odds |
|-------------|-------------------|--------------------|--------------------|
| over_under_total | "Over" / "Under" | threshold (e.g., 2.5) | decimal odds |
| over_under_team | "Over home" / "Under home" / "Over away" / "Under away" | threshold | decimal odds |
| correct_score | "H-A" (e.g., "1-0") | None | decimal odds |

## Error Handling

1. **Missing thresholds**: When a specific O/U threshold is absent from the Kambi response, the adapter skips it silently. The optimizer works with whatever subset of targets is available — even a single line suffices for the primary estimate.

2. **Missing team markets**: When team-specific O/U data is absent for one or both teams, `optimize_weighted()` sets `secondary_lambda` and `secondary_mu` to `None`. The pipeline continues with the primary estimate only.

3. **Missing correct score**: When no correct score market exists, `correct_score_odds` remains `None`. No downstream computation depends on it (it's for validation only).

4. **Convergence failure**: If the optimizer fails to converge (objective > `CONVERGENCE_THRESHOLD`), returns `None` and the match is skipped — same behavior as current.

5. **Milliodds / milligoals conversion**: All conversions divide by 1000. Values ≤ 100 are treated as already in decimal form (safeguard against API format changes).

## Testing Strategy

- **Unit tests** (`tests/test_unibet_adapter.py`): Test new parsing methods with concrete Kambi API fixtures. Cover correct extraction of multi-line O/U, team-specific O/U, and correct score markets. Verify backward compatibility of existing `fetch_over_under()`.
- **Unit tests** (`tests/test_lambda_optimizer.py`): Test `optimize_weighted()` with known Poisson-derived targets. Verify legacy `optimize()` remains unchanged. Test edge cases (empty targets, single line).
- **Unit tests** (`tests/test_divergence.py`): Test `compute_divergence()` with various estimate pairs. Verify threshold flagging logic.
- **Property-based tests** (`tests/test_enhanced_extraction_props.py`): Use `hypothesis` to validate correctness properties with generated inputs — random Kambi responses, random λ/μ pairs, random O/U probability targets.
- **Integration tests** (`tests/test_integration.py`): End-to-end pipeline test with mock adapter data verifying the full flow from extraction through dual estimate production.

## Correctness Properties

*A property is a characteristic or behavior that should hold true across all valid executions of a system — essentially, a formal statement about what the system should do. Properties serve as the bridge between human-readable specifications and machine-verifiable correctness guarantees.*

### Property 1: Overall O/U extraction completeness and correctness

*For any* valid Kambi bet offer response containing overall "Total Goals" O/U outcomes at any subset of thresholds {0.5, 1.5, 2.5, 3.5, 4.5}, the adapter SHALL extract exactly the available thresholds, producing MarketOutcome objects with name "Over"/"Under", correct decimal odds (milliodds ÷ 1000), point equal to the threshold value, and market_type "over_under_total" — and SHALL NOT raise an error for missing thresholds.

**Validates: Requirements 1.1, 1.2, 1.3, 1.4**

### Property 2: Team O/U extraction with correct team mapping

*For any* valid Kambi bet offer response containing team-specific "Total Goals by {TeamName}" O/U outcomes, and given known home and away team names, the adapter SHALL correctly classify each criterion as "home" or "away" by matching the label's team name against the event's homeName/awayName, extract outcomes at available thresholds {0.5, 1.5, 2.5}, and produce MarketOutcome objects with correct team identifier in the name, correct odds, and point set to the threshold.

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

### Property 3: Correct score parsing round-trip

*For any* valid Kambi correct score bet offer with outcome labels in "H-A" format (e.g., "1-0", "2-1") and milliodds, the adapter SHALL parse each scoreline into a MarketOutcome with name equal to the original "H-A" label, odds equal to milliodds ÷ 1000, and the parsed integer pair (home_goals, away_goals) reconstructible from the name.

**Validates: Requirements 3.1, 3.2, 3.3**

### Property 4: Weighted objective function correctness

*For any* valid λ, μ in [0.1, 5.0] and any set of O/U probability targets (overall and team), the optimizer's weighted objective function SHALL produce a value equal to: WEIGHT_1X2 × Σ(1x2 squared errors) + WEIGHT_OVERALL × Σ(overall O/U squared errors) + WEIGHT_TEAM × Σ(team O/U squared errors), where each Poisson probability is computed from the given λ and μ.

**Validates: Requirements 4.4, 4.5**

### Property 5: Primary estimate recovery from Poisson-generated targets

*For any* λ, μ in [0.3, 4.0], if we compute exact Poisson probabilities for 1X2, overall O/U at {0.5, 1.5, 2.5, 3.5, 4.5}, and team O/U at {0.5, 1.5, 2.5}, then `optimize_weighted()` SHALL recover λ and μ within ±0.05 of the original values.

**Validates: Requirements 5.2**

### Property 6: Secondary estimate recovery from team-only targets

*For any* λ, μ in [0.3, 4.0], if we compute exact Poisson probabilities for 1X2 and team-specific O/U at {0.5, 1.5, 2.5}, then the secondary optimization (using only team lines + 1X2) SHALL recover λ and μ within ±0.10 of the original values.

**Validates: Requirements 5.1**

### Property 7: Secondary estimate is None when team data is absent

*For any* call to `optimize_weighted()` where home_team_ou_targets or away_team_ou_targets is None or empty, the returned OptimizationResult SHALL have secondary_lambda = None and secondary_mu = None.

**Validates: Requirements 5.4**

### Property 8: Divergence computation and flagging

*For any* OptimizationResult with non-None secondary estimates, the divergence SHALL equal |primary_lambda - secondary_lambda| and |primary_mu - secondary_mu|, and the high divergence flag SHALL be True if and only if either divergence exceeds 0.3.

**Validates: Requirements 7.1, 7.2**

### Property 9: Backward compatibility of legacy optimize()

*For any* valid 1X2 probabilities and over 2.5 probability, calling the legacy `optimize(real_probs_1x2, real_prob_over_2_5)` SHALL continue to return a (λ, μ) tuple (or None) with the same behavior as before the enhancement.

**Validates: Requirements 4.6**
