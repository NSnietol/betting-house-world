"""Abstract base class and data types for bookmaker odds adapters."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class ExtractionMethod(Enum):
    """Declares how an adapter fetches data."""

    HTTP = "http"
    HEADLESS = "headless"
    API = "api"


class AdapterHealth(Enum):
    """Health status of an adapter."""

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    RATE_LIMITED = "rate_limited"
    DEGRADED = "degraded"


@dataclass
class MarketOutcome:
    """A single outcome in a betting market."""

    name: str
    odds: float
    point: float | None = None


@dataclass
class ScrapedMatch:
    """Standardized match data returned by any adapter."""

    home_team: str
    away_team: str
    event_timestamp: datetime
    market_type: str
    outcomes: list[MarketOutcome] = field(default_factory=list)


class OddsExtractionError(Exception):
    """Raised when odds extraction fails (HTTP error, timeout, parsing failure)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        adapter_id: str | None = None,
    ) -> None:
        self.status_code = status_code
        self.adapter_id = adapter_id
        super().__init__(message)


class AdapterDiscoveryError(Exception):
    """Raised when the adapter registry fails to scan the adapters directory."""

    pass


class OddsAdapter(ABC):
    """Abstract base class for all bookmaker data extraction adapters."""

    @property
    @abstractmethod
    def bookmaker_id(self) -> str:
        """Unique identifier for this bookmaker (e.g., 'pinnacle')."""
        ...

    @property
    @abstractmethod
    def bookmaker_name(self) -> str:
        """Human-readable name (e.g., 'Pinnacle Sports')."""
        ...

    @property
    @abstractmethod
    def priority(self) -> int:
        """Priority level. Lower number = higher priority."""
        ...

    @property
    @abstractmethod
    def extraction_method(self) -> ExtractionMethod:
        """Declares whether this adapter uses HTTP, headless, or API."""
        ...

    @property
    @abstractmethod
    def base_url(self) -> str:
        """Root URL for the bookmaker site."""
        ...

    @abstractmethod
    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch 1X2 (match winner) odds.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='1x2'.

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        ...

    @abstractmethod
    def fetch_over_under(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch Over/Under 2.5 goals odds.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='over_under'.

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        ...

    @abstractmethod
    def health_check(self) -> AdapterHealth:
        """Report current adapter health (reachable, rate-limited, etc.)."""
        ...
