"""1xBet bookmaker adapter using LineFeed JSON API.

Extracts 1X2 and Over/Under 2.5 odds from 1xBet's public JSON endpoints.
This is a Tier 1 (HTTP) adapter — no headless browser required.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import requests

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    MarketOutcome,
    OddsAdapter,
    OddsExtractionError,
    ScrapedMatch,
)
from src.scraping.http_scraper import HTTPScraper
from src.scraping.resilience import ResilienceConfig

logger = logging.getLogger(__name__)


# Mapping from sport keys to 1xBet league (championship) IDs
_LEAGUE_IDS: dict[str, int] = {
    "soccer_epl": 88637,
    "soccer_spain_la_liga": 127733,
    "soccer_germany_bundesliga": 96463,
    "soccer_italy_serie_a": 110163,
    "soccer_france_ligue_one": 12821,
    "soccer_uefa_champs_league": 118587,
    "soccer_world_cup": 16801,
}

# 1xBet domain for rate limiting
_DOMAIN = "1xbet.com"

# Base URL
_BASE_URL = "https://1xbet.com"

# LineFeed endpoints
_LEAGUE_EVENTS_URL = (
    "https://1xbet.com/LineFeed/GetChampZip?sport=1&champ={league_id}&lng=en"
)
_EVENT_DETAILS_URL = (
    "https://1xbet.com/LineFeed/GetGameZip?id={event_id}&lng=en"
)


class OneXBetAdapter(OddsAdapter):
    """1xBet adapter using the public LineFeed JSON API.

    Extracts odds from 1xBet's JSON feed endpoints. These endpoints
    return structured data without requiring authentication or HTML
    parsing.
    """

    def __init__(self, resilience: ResilienceConfig | None = None) -> None:
        """Initialize the 1xBet adapter.

        Args:
            resilience: Optional resilience configuration. Uses defaults
                if not provided.
        """
        self._resilience = resilience or ResilienceConfig()
        self._scraper = HTTPScraper(self._resilience)

    @property
    def bookmaker_id(self) -> str:
        """Unique identifier for 1xBet."""
        return "onexbet"

    @property
    def bookmaker_name(self) -> str:
        """Human-readable name."""
        return "1xBet"

    @property
    def priority(self) -> int:
        """Priority level — highest priority (1) among all adapters."""
        return 1

    @property
    def extraction_method(self) -> ExtractionMethod:
        """Uses HTTP extraction via JSON API."""
        return ExtractionMethod.HTTP

    @property
    def base_url(self) -> str:
        """Root URL for 1xBet."""
        return _BASE_URL

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch 1X2 (match winner) odds from 1xBet LineFeed API.

        Retrieves events for the specified league and extracts home/draw/away
        odds from the JSON response.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter. Only events on this
                date are returned.

        Returns:
            List of ScrapedMatch with market_type='1x2'.

        Raises:
            OddsExtractionError: On network failure or missing league mapping.
        """
        league_id = self._get_league_id(sport)
        events = self._fetch_league_events(league_id)
        matches: list[ScrapedMatch] = []

        for event in events:
            try:
                match = self._parse_1x2_event(event)
                if match is None:
                    continue
                if date and not self._matches_date(match.event_timestamp, date):
                    continue
                matches.append(match)
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug(
                    "Skipping 1xBet event (1x2 parse error): %s", exc
                )
                continue

        return matches

    def fetch_over_under(
        self, sport: str, date: str | None = None
    ) -> list[ScrapedMatch]:
        """Fetch Over/Under 2.5 goals odds from 1xBet LineFeed API.

        Retrieves events for the specified league and extracts over/under
        2.5 total goals odds from the JSON response.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='over_under'.

        Raises:
            OddsExtractionError: On network failure or missing league mapping.
        """
        league_id = self._get_league_id(sport)
        events = self._fetch_league_events(league_id)
        matches: list[ScrapedMatch] = []

        for event in events:
            try:
                match = self._parse_over_under_event(event)
                if match is None:
                    continue
                if date and not self._matches_date(match.event_timestamp, date):
                    continue
                matches.append(match)
            except (KeyError, ValueError, TypeError) as exc:
                logger.debug(
                    "Skipping 1xBet event (over/under parse error): %s", exc
                )
                continue

        return matches

    def health_check(self) -> AdapterHealth:
        """Check adapter health with a lightweight HEAD request.

        Makes a HEAD request to the 1xBet LineFeed endpoint to verify
        connectivity without downloading a full response.

        Returns:
            AdapterHealth.REACHABLE if the endpoint responds,
            AdapterHealth.RATE_LIMITED on 429,
            AdapterHealth.UNREACHABLE on failure.
        """
        try:
            self._scraper._rate_limiter.wait(_DOMAIN)
            headers = {"User-Agent": self._scraper._ua_rotator.next()}
            response = requests.head(
                f"{_BASE_URL}/LineFeed/Get1x2_VZip?sports=1&count=1&lng=en&tf=2200000&tz=0",
                headers=headers,
                timeout=10,
            )
            if response.status_code == 429:
                return AdapterHealth.RATE_LIMITED
            if response.status_code < 400:
                return AdapterHealth.REACHABLE
            return AdapterHealth.UNREACHABLE
        except (requests.exceptions.RequestException, OddsExtractionError):
            return AdapterHealth.UNREACHABLE

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _get_league_id(self, sport: str) -> int:
        """Resolve sport key to 1xBet league ID.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').

        Returns:
            The 1xBet championship ID.

        Raises:
            OddsExtractionError: If the sport key has no mapping.
        """
        league_id = _LEAGUE_IDS.get(sport)
        if league_id is None:
            raise OddsExtractionError(
                message=f"Unsupported sport key for 1xBet: '{sport}'. "
                f"Supported: {list(_LEAGUE_IDS.keys())}",
                adapter_id=self.bookmaker_id,
            )
        return league_id

    def _fetch_league_events(self, league_id: int) -> list[dict[str, Any]]:
        """Fetch events for a league from the LineFeed API.

        Uses the HTTPScraper's rate limiter and user-agent rotation
        for the raw HTTP request, then parses the JSON response.

        Args:
            league_id: 1xBet championship ID.

        Returns:
            List of event dictionaries from the JSON response.

        Raises:
            OddsExtractionError: On network or parsing failure.
        """
        url = _LEAGUE_EVENTS_URL.format(league_id=league_id)

        self._scraper._rate_limiter.wait(_DOMAIN)
        headers = {"User-Agent": self._scraper._ua_rotator.next()}
        proxies = None
        if self._resilience.proxy:
            proxies = {"http": self._resilience.proxy, "https": self._resilience.proxy}

        def _do_request() -> requests.Response:
            response = requests.get(
                url,
                headers=headers,
                timeout=30,
                proxies=proxies,
            )
            response.raise_for_status()
            return response

        try:
            response = self._scraper._retry_handler.execute(_do_request)
        except OddsExtractionError:
            raise
        except requests.exceptions.RequestException as exc:
            raise OddsExtractionError(
                message=f"Failed to fetch 1xBet league events: {exc}",
                adapter_id=self.bookmaker_id,
            ) from exc

        try:
            data = response.json()
        except (ValueError, AttributeError) as exc:
            raise OddsExtractionError(
                message=f"Invalid JSON response from 1xBet: {exc}",
                adapter_id=self.bookmaker_id,
            ) from exc

        # The response structure: {"Value": [...events...]} or direct list
        if isinstance(data, dict):
            return data.get("Value", [])
        if isinstance(data, list):
            return data
        return []

    def _parse_1x2_event(self, event: dict[str, Any]) -> ScrapedMatch | None:
        """Parse a single event dict into a 1X2 ScrapedMatch.

        The 1xBet JSON structure for events typically contains:
        - "O1" / "OX" / "O2": team 1 win / draw / team 2 win (legacy fields)
        - "E" list of market entries with "T" (type) and "C" (coefficient/odds)
        - "O1E", "OXE", "O2E": direct odds fields in some responses
        - "SC": start time as unix timestamp

        Args:
            event: Raw event dictionary from the JSON response.

        Returns:
            ScrapedMatch with 1x2 outcomes, or None if odds are missing.
        """
        home_team = self._extract_team_name(event, "home")
        away_team = self._extract_team_name(event, "away")
        timestamp = self._extract_timestamp(event)

        # Extract 1X2 odds from the event data
        home_odds = self._extract_1x2_odds(event, "home")
        draw_odds = self._extract_1x2_odds(event, "draw")
        away_odds = self._extract_1x2_odds(event, "away")

        if home_odds is None or draw_odds is None or away_odds is None:
            return None

        # Validate odds are positive
        if home_odds <= 1.0 or draw_odds <= 1.0 or away_odds <= 1.0:
            return None

        outcomes = [
            MarketOutcome(name="Home", odds=home_odds),
            MarketOutcome(name="Draw", odds=draw_odds),
            MarketOutcome(name="Away", odds=away_odds),
        ]

        return ScrapedMatch(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=timestamp,
            market_type="1x2",
            outcomes=outcomes,
        )

    def _parse_over_under_event(
        self, event: dict[str, Any]
    ) -> ScrapedMatch | None:
        """Parse a single event dict into an Over/Under 2.5 ScrapedMatch.

        Looks for total goals market (over/under 2.5) in the event data.
        In the 1xBet JSON, over/under markets are stored in the "GE" (game
        events) or "E" (events/markets) arrays with specific type identifiers.

        Args:
            event: Raw event dictionary from the JSON response.

        Returns:
            ScrapedMatch with over_under outcomes, or None if data missing.
        """
        home_team = self._extract_team_name(event, "home")
        away_team = self._extract_team_name(event, "away")
        timestamp = self._extract_timestamp(event)

        over_odds, under_odds = self._extract_over_under_odds(event)

        if over_odds is None or under_odds is None:
            return None

        # Validate odds are positive
        if over_odds <= 1.0 or under_odds <= 1.0:
            return None

        outcomes = [
            MarketOutcome(name="Over", odds=over_odds, point=2.5),
            MarketOutcome(name="Under", odds=under_odds, point=2.5),
        ]

        return ScrapedMatch(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=timestamp,
            market_type="over_under",
            outcomes=outcomes,
        )

    def _extract_team_name(self, event: dict[str, Any], side: str) -> str:
        """Extract team name from event data.

        1xBet JSON uses several naming conventions:
        - "O1" / "O2" for team names in some endpoints
        - "Home" / "Away" in some responses
        - "Opp1" / "Opp2" for opponents

        Args:
            event: Event dictionary.
            side: Either "home" or "away".

        Returns:
            Team name string.

        Raises:
            KeyError: If no team name can be extracted.
        """
        if side == "home":
            # Try common field names for home team
            for key in ("O1", "Home", "Opp1", "O1E"):
                if key in event and isinstance(event[key], str) and event[key]:
                    return event[key]
            # Nested structure
            if "Opp1" in event and isinstance(event["Opp1"], dict):
                return event["Opp1"].get("Name", "")
        else:
            # Try common field names for away team
            for key in ("O2", "Away", "Opp2", "O2E"):
                if key in event and isinstance(event[key], str) and event[key]:
                    return event[key]
            if "Opp2" in event and isinstance(event["Opp2"], dict):
                return event["Opp2"].get("Name", "")

        raise KeyError(f"Cannot extract {side} team name from event")

    def _extract_timestamp(self, event: dict[str, Any]) -> datetime:
        """Extract event start timestamp.

        1xBet uses unix timestamps in the "SC" (start count) field,
        or "S" (start) field in some responses.

        Args:
            event: Event dictionary.

        Returns:
            UTC datetime of the event.

        Raises:
            KeyError: If no timestamp field is found.
            ValueError: If the timestamp cannot be parsed.
        """
        for key in ("SC", "S", "StartTime"):
            if key in event:
                value = event[key]
                if isinstance(value, (int, float)):
                    return datetime.fromtimestamp(value, tz=timezone.utc)
                if isinstance(value, str):
                    # Try parsing ISO format
                    return datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
        raise KeyError("Cannot extract timestamp from event")

    def _extract_1x2_odds(
        self, event: dict[str, Any], outcome: str
    ) -> float | None:
        """Extract a single 1X2 odds value from event data.

        1xBet JSON formats vary. Common structures:
        - Direct fields: "O1" (home odds), "OX" (draw), "O2" (away odds)
          when these are floats rather than strings
        - "E" array with entries having "T" (type) and "C" (coefficient)
          - Type 1 = home win, Type 2 = draw, Type 3 = away win

        Args:
            event: Event dictionary.
            outcome: One of "home", "draw", "away".

        Returns:
            Decimal odds value, or None if not found.
        """
        # Map outcome to field names and type IDs
        field_map = {
            "home": ("O1", 1),
            "draw": ("OX", 2),
            "away": ("O2", 3),
        }
        field_name, type_id = field_map[outcome]

        # Strategy 1: Direct float fields (GetChampZip response format)
        if field_name in event:
            value = event[field_name]
            if isinstance(value, (int, float)) and value > 1.0:
                return float(value)

        # Strategy 2: "E" array with typed entries
        entries = event.get("E", [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry_type = entry.get("T")
                coefficient = entry.get("C")
                if entry_type == type_id and coefficient is not None:
                    try:
                        return float(coefficient)
                    except (ValueError, TypeError):
                        continue

        # Strategy 3: "GE" (game events) array — another format
        game_events = event.get("GE", [])
        if isinstance(game_events, list):
            for ge in game_events:
                if not isinstance(ge, dict):
                    continue
                # Look for 1X2 market group (type 1)
                if ge.get("G") == 1 or ge.get("T") == 1:
                    ge_entries = ge.get("E", [])
                    if isinstance(ge_entries, list):
                        for entry in ge_entries:
                            if not isinstance(entry, dict):
                                continue
                            if entry.get("T") == type_id:
                                try:
                                    return float(entry.get("C", 0))
                                except (ValueError, TypeError):
                                    continue

        return None

    def _extract_over_under_odds(
        self, event: dict[str, Any]
    ) -> tuple[float | None, float | None]:
        """Extract Over/Under 2.5 goals odds from event data.

        Looks for the total goals market with a 2.5 line in the event.
        1xBet structures this differently across endpoints:
        - "TotalOver" / "TotalUnder" direct fields
        - "E" array with type 9 (over) and type 10 (under)
        - "GE" array with market group for totals

        Args:
            event: Event dictionary.

        Returns:
            Tuple of (over_odds, under_odds), either may be None.
        """
        over_odds: float | None = None
        under_odds: float | None = None

        # Strategy 1: Direct fields
        if "TotalOver" in event and "TotalUnder" in event:
            try:
                over_val = float(event["TotalOver"])
                under_val = float(event["TotalUnder"])
                if over_val > 1.0 and under_val > 1.0:
                    return over_val, under_val
            except (ValueError, TypeError):
                pass

        # Strategy 2: "E" array with type 9 (over) and 10 (under)
        entries = event.get("E", [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry_type = entry.get("T")
                coefficient = entry.get("C")
                point = entry.get("P")

                # Check for 2.5 point line (or accept if no point specified)
                if point is not None:
                    try:
                        if float(point) != 2.5:
                            continue
                    except (ValueError, TypeError):
                        continue

                if entry_type == 9 and coefficient is not None:
                    try:
                        over_odds = float(coefficient)
                    except (ValueError, TypeError):
                        pass
                elif entry_type == 10 and coefficient is not None:
                    try:
                        under_odds = float(coefficient)
                    except (ValueError, TypeError):
                        pass

        if over_odds is not None and under_odds is not None:
            return over_odds, under_odds

        # Strategy 3: "GE" array — totals market group
        game_events = event.get("GE", [])
        if isinstance(game_events, list):
            for ge in game_events:
                if not isinstance(ge, dict):
                    continue
                # Totals market group (G=17 or T=17 for totals in 1xBet)
                if ge.get("G") == 17 or ge.get("T") == 17:
                    ge_entries = ge.get("E", [])
                    if isinstance(ge_entries, list):
                        for entry in ge_entries:
                            if not isinstance(entry, dict):
                                continue
                            point = entry.get("P")
                            if point is not None:
                                try:
                                    if float(point) != 2.5:
                                        continue
                                except (ValueError, TypeError):
                                    continue
                            entry_type = entry.get("T")
                            coefficient = entry.get("C")
                            if entry_type == 9 and coefficient is not None:
                                try:
                                    over_odds = float(coefficient)
                                except (ValueError, TypeError):
                                    pass
                            elif entry_type == 10 and coefficient is not None:
                                try:
                                    under_odds = float(coefficient)
                                except (ValueError, TypeError):
                                    pass

        return over_odds, under_odds

    @staticmethod
    def _matches_date(timestamp: datetime, date_str: str) -> bool:
        """Check if a timestamp falls on the given date (UTC).

        Args:
            timestamp: Event datetime (UTC).
            date_str: Date string in YYYY-MM-DD format.

        Returns:
            True if the event is on the specified date.
        """
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d").date()
            return timestamp.date() == target
        except ValueError:
            return False
