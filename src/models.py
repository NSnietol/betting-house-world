"""Data models for the Betting Odds Calculator."""

from __future__ import annotations

from dataclasses import dataclass, field


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
