"""Data models for the Betting Odds Calculator."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


# ---------------------------------------------------------------------------
# API Response Models (from The Odds API v4)
# ---------------------------------------------------------------------------


@dataclass
class Outcome:
    """A single betting outcome with price and optional point spread."""

    name: str
    price: float  # decimal odds
    point: float | None = None  # for totals (e.g., 2.5)


@dataclass
class Market:
    """A betting market (e.g., h2h or totals) containing multiple outcomes."""

    key: str  # "h2h" or "totals"
    outcomes: list[Outcome] = field(default_factory=list)


@dataclass
class BookmakerOdds:
    """Odds offered by a single bookmaker for an event."""

    key: str
    title: str
    last_update: str
    markets: list[Market] = field(default_factory=list)


@dataclass
class OddsEvent:
    """A single sporting event with odds from multiple bookmakers."""

    id: str
    sport_key: str
    commence_time: str  # ISO 8601
    home_team: str
    away_team: str
    bookmakers: list[BookmakerOdds] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal Processing Models
# ---------------------------------------------------------------------------


@dataclass
class OptimizationResult:
    """Result of a Poisson parameter optimization from a single source."""

    primary_lambda: float
    primary_mu: float
    secondary_lambda: float | None = None
    secondary_mu: float | None = None


@dataclass
class DivergenceResult:
    """Divergence metrics between primary and secondary optimization results."""

    lambda_divergence: float
    mu_divergence: float
    is_high_divergence: bool


@dataclass
class MatchResult:
    """Result of processing a single match through the full pipeline."""

    home_team: str
    away_team: str
    real_probs: tuple[float, float, float]  # (home, draw, away)
    lambda_home: float
    mu_away: float
    suggested_score: tuple[int, int]
    score_probability: float
    variance_flag: float
    high_margin: float
    low_coverage: int
    secondary_lambda: float | None = None
    secondary_mu: float | None = None
    lambda_divergence: float | None = None
    mu_divergence: float | None = None
    is_high_divergence: bool = False
    polla_optimal_score: tuple[int, int] | None = None
    polla_expected_points: float | None = None


@dataclass
class CacheEntry:
    """Metadata for a cached API response stored in SQLite."""

    sport_key: str
    market_type: str
    event_id: str
    bookmaker_keys: str  # JSON array string of sorted bookmaker keys
    response_json: str
    retrieved_at: str  # ISO 8601 UTC


# ---------------------------------------------------------------------------
# Configuration Model
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Runtime configuration parsed from CLI arguments and environment."""

    api_key: str  # from environment variable ODDS_API_KEY
    sport: str  # e.g., "soccer_epl"
    date: str | None = None  # YYYY-MM-DD or None
    round_id: str | None = None  # round identifier or None
    method: str = "shin"  # "shin" or "logarithmic"
    ttl_hours: int = 24  # 1-48
    bookmakers: list[str] | None = None  # filter list or None for API default
    db_path: str = "odds_cache.db"  # SQLite file path
    retro_mode: bool = False  # True if --retro flag is set
    bookmaker_report: bool = False  # True if --bookmaker-report flag is set
    enable_bookmaker: str | None = None  # bookmaker key to re-enable, or None
    reliability_threshold: float = 0.30  # default threshold


# ---------------------------------------------------------------------------
# RetroFeedback Models
# ---------------------------------------------------------------------------


@dataclass
class MatchComparison:
    """Comparison of a single prediction against the actual match result."""

    home_team: str
    away_team: str
    predicted_score: tuple[int, int]
    actual_score: tuple[int, int]
    predicted_lambda: float
    predicted_mu: float
    x1x2_correct: bool  # 1X2 outcome matches
    score_correct: bool  # exact score matches
    lambda_error: float  # |predicted_lambda - actual_home_goals|
    mu_error: float  # |predicted_mu - actual_away_goals|
    bookmaker_source: str  # bookmaker key used for this prediction


@dataclass
class AggregateMetrics:
    """Aggregate accuracy metrics across all match comparisons."""

    x1x2_hit_rate: float  # percentage (0-100)
    score_hit_rate: float  # percentage (0-100)
    mean_lambda_error: float
    mean_mu_error: float


@dataclass
class BookmakerStats:
    """Per-bookmaker reliability statistics accumulated over retro runs."""

    bookmaker_key: str
    total_matches: int
    correct_1x2: int
    mean_lambda_error: float
    mean_mu_error: float
    reliability_score: float
    is_active: bool  # False if flagged as unreliable


# ---------------------------------------------------------------------------
# Aggregation & Adapter Models
# ---------------------------------------------------------------------------


@dataclass
class AggregatedEvent:
    """A single match with odds aggregated from multiple bookmaker adapters."""

    home_team: str
    away_team: str
    event_timestamp: datetime
    bookmaker_odds_1x2: dict[str, tuple[float, float, float]]
    # bookmaker_id -> (home_odds, draw_odds, away_odds)
    bookmaker_odds_over_under: dict[str, tuple[float, float]]
    # bookmaker_id -> (over_2.5_odds, under_2.5_odds)
    source_count: int  # number of distinct bookmaker sources

    # Enhanced multi-line O/U fields
    overall_ou_lines: dict[str, dict[float, tuple[float, float]]] = field(
        default_factory=dict
    )
    # bookmaker_id -> {threshold: (over_odds, under_odds)}

    home_team_ou_lines: dict[str, dict[float, tuple[float, float]]] = field(
        default_factory=dict
    )
    # bookmaker_id -> {threshold: (over_odds, under_odds)}

    away_team_ou_lines: dict[str, dict[float, tuple[float, float]]] = field(
        default_factory=dict
    )
    # bookmaker_id -> {threshold: (over_odds, under_odds)}

    correct_score_odds: dict[str, dict[tuple[int, int], float]] = field(
        default_factory=dict
    )
    # bookmaker_id -> {(home_goals, away_goals): decimal_odds}


@dataclass
class AdapterStatus:
    """Status snapshot of a registered adapter."""

    adapter_id: str
    adapter_name: str
    priority: int
    health: str  # "reachable" | "unreachable" | "rate_limited" | "degraded"
    extraction_method: str  # "http" | "headless" | "api"
    consecutive_failures: int
