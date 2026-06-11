"""Odds API adapter — optional fallback using The Odds API v4."""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import requests

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    MarketOutcome,
    OddsAdapter,
    OddsExtractionError,
    ScrapedMatch,
)

logger = logging.getLogger(__name__)


class OddsAPIAdapter(OddsAdapter):
    """Adapter for The Odds API v4 (https://the-odds-api.com).

    Activates only when the ``ODDS_API_KEY`` environment variable is set.
    Uses a higher priority number (10) so web scraping adapters are preferred.
    """

    _BASE_URL = "https://api.the-odds-api.com"
    _TIMEOUT = 30  # seconds

    def __init__(self) -> None:
        """Initialize the adapter, reading the API key from the environment."""
        self._api_key: str | None = os.environ.get("ODDS_API_KEY")

    # ------------------------------------------------------------------
    # OddsAdapter interface
    # ------------------------------------------------------------------

    @property
    def bookmaker_id(self) -> str:
        """Unique identifier for this adapter."""
        return "the_odds_api"

    @property
    def bookmaker_name(self) -> str:
        """Human-readable name."""
        return "The Odds API"

    @property
    def priority(self) -> int:
        """Lower priority than scraping adapters (they use 1-3)."""
        return 10

    @property
    def extraction_method(self) -> ExtractionMethod:
        """This adapter uses a REST API."""
        return ExtractionMethod.API

    @property
    def base_url(self) -> str:
        """Root URL for The Odds API."""
        return self._BASE_URL

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch 1X2 (match winner) odds from The Odds API.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter (currently unused by API,
                  filtering is done client-side).

        Returns:
            List of ScrapedMatch with market_type='1x2'.

        Raises:
            OddsExtractionError: On HTTP errors, timeouts, or missing API key.
        """
        self._ensure_api_key()
        events = self._call_odds_endpoint(sport, markets="h2h")
        return self._parse_h2h_events(events, date)

    def fetch_over_under(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch Over/Under 2.5 goals odds from The Odds API.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter (currently unused by API,
                  filtering is done client-side).

        Returns:
            List of ScrapedMatch with market_type='over_under'.

        Raises:
            OddsExtractionError: On HTTP errors, timeouts, or missing API key.
        """
        self._ensure_api_key()
        events = self._call_odds_endpoint(sport, markets="totals")
        return self._parse_totals_events(events, date)

    def health_check(self) -> AdapterHealth:
        """Check API reachability by listing available sports.

        Returns:
            REACHABLE if the API responds successfully, UNREACHABLE if the key
            is missing or invalid, RATE_LIMITED on 429.
        """
        if not self._api_key:
            return AdapterHealth.UNREACHABLE

        url = f"{self._BASE_URL}/v4/sports/"
        params = {"apiKey": self._api_key}

        try:
            response = requests.get(url, params=params, timeout=self._TIMEOUT)
        except requests.RequestException:
            return AdapterHealth.UNREACHABLE

        if response.status_code == 200:
            return AdapterHealth.REACHABLE
        if response.status_code == 429:
            return AdapterHealth.RATE_LIMITED
        return AdapterHealth.UNREACHABLE

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _ensure_api_key(self) -> None:
        """Raise if the API key is not configured."""
        if not self._api_key:
            raise OddsExtractionError(
                "ODDS_API_KEY environment variable is not set",
                adapter_id=self.bookmaker_id,
            )

    def _call_odds_endpoint(self, sport: str, markets: str) -> list[dict]:
        """Call The Odds API v4 odds endpoint.

        Args:
            sport: Sport/league key.
            markets: Market key ('h2h' or 'totals').

        Returns:
            List of event dicts from the JSON response.

        Raises:
            OddsExtractionError: On network errors, HTTP 4xx/5xx, or timeouts.
        """
        url = f"{self._BASE_URL}/v4/sports/{sport}/odds/"
        params = {
            "apiKey": self._api_key,
            "regions": "eu",
            "markets": markets,
            "oddsFormat": "decimal",
        }

        try:
            response = requests.get(url, params=params, timeout=self._TIMEOUT)
        except requests.Timeout:
            msg = (
                f"TIMEOUT: The Odds API did not respond within "
                f"{self._TIMEOUT}s for {sport}"
            )
            logger.error(msg)
            raise OddsExtractionError(
                msg, adapter_id=self.bookmaker_id
            )
        except requests.RequestException as exc:
            msg = f"Network error fetching odds for {sport}: {exc}"
            logger.error(msg)
            raise OddsExtractionError(
                msg, adapter_id=self.bookmaker_id
            )

        if response.status_code == 401:
            msg = "Invalid ODDS_API_KEY — received 401 Unauthorized"
            logger.error(msg)
            raise OddsExtractionError(
                msg, status_code=401, adapter_id=self.bookmaker_id
            )
        if response.status_code == 429:
            msg = "Rate limited by The Odds API — received 429"
            logger.error(msg)
            raise OddsExtractionError(
                msg, status_code=429, adapter_id=self.bookmaker_id
            )
        if response.status_code >= 400:
            msg = (
                f"HTTP {response.status_code} error from The Odds API for "
                f"{sport}: {response.text[:200]}"
            )
            logger.error(msg)
            raise OddsExtractionError(
                msg, status_code=response.status_code, adapter_id=self.bookmaker_id
            )

        return response.json()

    def _parse_h2h_events(
        self, events: list[dict], date: str | None
    ) -> list[ScrapedMatch]:
        """Parse h2h market events into ScrapedMatch objects.

        Args:
            events: Raw event dicts from the API.
            date: Optional date filter (YYYY-MM-DD).

        Returns:
            List of ScrapedMatch with market_type='1x2'.
        """
        matches: list[ScrapedMatch] = []
        for event in events:
            event_time = self._parse_commence_time(event.get("commence_time", ""))
            if date and not self._matches_date(event_time, date):
                continue

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    outcomes = self._extract_h2h_outcomes(market.get("outcomes", []))
                    if outcomes:
                        matches.append(
                            ScrapedMatch(
                                home_team=event.get("home_team", ""),
                                away_team=event.get("away_team", ""),
                                event_timestamp=event_time,
                                market_type="1x2",
                                outcomes=outcomes,
                            )
                        )
        return matches

    def _parse_totals_events(
        self, events: list[dict], date: str | None
    ) -> list[ScrapedMatch]:
        """Parse totals market events into ScrapedMatch objects.

        Args:
            events: Raw event dicts from the API.
            date: Optional date filter (YYYY-MM-DD).

        Returns:
            List of ScrapedMatch with market_type='over_under'.
        """
        matches: list[ScrapedMatch] = []
        for event in events:
            event_time = self._parse_commence_time(event.get("commence_time", ""))
            if date and not self._matches_date(event_time, date):
                continue

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "totals":
                        continue
                    outcomes = self._extract_totals_outcomes(
                        market.get("outcomes", [])
                    )
                    if outcomes:
                        matches.append(
                            ScrapedMatch(
                                home_team=event.get("home_team", ""),
                                away_team=event.get("away_team", ""),
                                event_timestamp=event_time,
                                market_type="over_under",
                                outcomes=outcomes,
                            )
                        )
        return matches

    @staticmethod
    def _extract_h2h_outcomes(raw_outcomes: list[dict]) -> list[MarketOutcome]:
        """Convert API h2h outcomes into MarketOutcome objects.

        Args:
            raw_outcomes: List of outcome dicts with 'name' and 'price' keys.

        Returns:
            List of MarketOutcome (Home, Draw, Away).
        """
        outcomes: list[MarketOutcome] = []
        for outcome in raw_outcomes:
            name = outcome.get("name", "")
            price = outcome.get("price")
            if price is not None:
                outcomes.append(MarketOutcome(name=name, odds=float(price)))
        return outcomes

    @staticmethod
    def _extract_totals_outcomes(raw_outcomes: list[dict]) -> list[MarketOutcome]:
        """Convert API totals outcomes into MarketOutcome objects.

        Args:
            raw_outcomes: List of outcome dicts with 'name', 'price', 'point'.

        Returns:
            List of MarketOutcome (Over, Under) with point values.
        """
        outcomes: list[MarketOutcome] = []
        for outcome in raw_outcomes:
            name = outcome.get("name", "")
            price = outcome.get("price")
            point = outcome.get("point")
            if price is not None:
                outcomes.append(
                    MarketOutcome(
                        name=name,
                        odds=float(price),
                        point=float(point) if point is not None else None,
                    )
                )
        return outcomes

    @staticmethod
    def _parse_commence_time(iso_str: str) -> datetime:
        """Parse an ISO 8601 commence_time string into a datetime.

        Args:
            iso_str: ISO formatted timestamp (e.g., '2024-06-01T15:00:00Z').

        Returns:
            Timezone-aware datetime in UTC.
        """
        if not iso_str:
            return datetime.now(timezone.utc)
        # Handle 'Z' suffix and standard ISO format
        cleaned = iso_str.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            return datetime.now(timezone.utc)

    @staticmethod
    def _matches_date(event_time: datetime, date: str) -> bool:
        """Check if an event's timestamp falls on the given date.

        Args:
            event_time: Event datetime (UTC).
            date: Date string in YYYY-MM-DD format.

        Returns:
            True if the event is on the given date.
        """
        try:
            target = datetime.strptime(date, "%Y-%m-%d").date()
            return event_time.date() == target
        except ValueError:
            return True  # If date is malformed, don't filter
