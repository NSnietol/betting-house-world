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

        Averages odds across ALL bookmakers for each event to produce
        one ScrapedMatch per match (not one per bookmaker).

        Args:
            events: Raw event dicts from the API.
            date: Optional date filter (YYYY-MM-DD).

        Returns:
            List of ScrapedMatch with market_type='1x2' (one per event).
        """
        matches: list[ScrapedMatch] = []
        for event in events:
            event_time = self._parse_commence_time(event.get("commence_time", ""))
            if date and not self._matches_date(event_time, date):
                continue

            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")

            # Collect odds across all bookmakers for this event
            home_odds_all: list[float] = []
            draw_odds_all: list[float] = []
            away_odds_all: list[float] = []

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "h2h":
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price")
                        if price is None:
                            continue
                        if name.lower() == "draw" or name.lower() == "x":
                            draw_odds_all.append(float(price))
                        elif name == home_team:
                            home_odds_all.append(float(price))
                        elif name == away_team:
                            away_odds_all.append(float(price))

            if not home_odds_all or not draw_odds_all or not away_odds_all:
                continue

            # Average across all bookmakers
            avg_home = sum(home_odds_all) / len(home_odds_all)
            avg_draw = sum(draw_odds_all) / len(draw_odds_all)
            avg_away = sum(away_odds_all) / len(away_odds_all)

            outcomes = [
                MarketOutcome(name="Home", odds=avg_home),
                MarketOutcome(name="Draw", odds=avg_draw),
                MarketOutcome(name="Away", odds=avg_away),
            ]
            matches.append(
                ScrapedMatch(
                    home_team=home_team,
                    away_team=away_team,
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

            home_team = event.get("home_team", "")
            away_team = event.get("away_team", "")

            # Collect O/U 2.5 odds across all bookmakers
            over_odds_all: list[float] = []
            under_odds_all: list[float] = []

            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market.get("key") != "totals":
                        continue
                    for outcome in market.get("outcomes", []):
                        name = outcome.get("name", "")
                        price = outcome.get("price")
                        point = outcome.get("point")
                        if price is None or point != 2.5:
                            continue
                        if name == "Over":
                            over_odds_all.append(float(price))
                        elif name == "Under":
                            under_odds_all.append(float(price))

            if not over_odds_all or not under_odds_all:
                continue

            avg_over = sum(over_odds_all) / len(over_odds_all)
            avg_under = sum(under_odds_all) / len(under_odds_all)

            outcomes = [
                MarketOutcome(name="Over", odds=avg_over, point=2.5),
                MarketOutcome(name="Under", odds=avg_under, point=2.5),
            ]
            matches.append(
                ScrapedMatch(
                    home_team=home_team,
                    away_team=away_team,
                    event_timestamp=event_time,
                    market_type="over_under",
                    outcomes=outcomes,
                )
            )
        return matches

    @staticmethod
    def _extract_h2h_outcomes(
        raw_outcomes: list[dict], home_team: str = "", away_team: str = ""
    ) -> list[MarketOutcome]:
        """Convert API h2h outcomes into MarketOutcome objects.

        The Odds API returns outcomes as [home_team_name, away_team_name, Draw]
        but our pipeline expects [Home, Draw, Away] order.

        Args:
            raw_outcomes: List of outcome dicts with 'name' and 'price' keys.
            home_team: Home team name for mapping.
            away_team: Away team name for mapping.

        Returns:
            List of MarketOutcome ordered as [Home, Draw, Away].
        """
        home_odds: float | None = None
        draw_odds: float | None = None
        away_odds: float | None = None

        for outcome in raw_outcomes:
            name = outcome.get("name", "")
            price = outcome.get("price")
            if price is None:
                continue

            name_lower = name.lower()
            if name_lower == "draw" or name_lower == "x":
                draw_odds = float(price)
            elif name_lower == home_team.lower() or (home_odds is None and name_lower != away_team.lower()):
                home_odds = float(price)
            else:
                away_odds = float(price)

        if home_odds is None or draw_odds is None or away_odds is None:
            # Fallback: return in original order with generic names
            return [
                MarketOutcome(name=o.get("name", ""), odds=float(o["price"]))
                for o in raw_outcomes
                if o.get("price") is not None
            ]

        return [
            MarketOutcome(name="Home", odds=home_odds),
            MarketOutcome(name="Draw", odds=draw_odds),
            MarketOutcome(name="Away", odds=away_odds),
        ]

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
