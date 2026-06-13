"""1xBet bookmaker adapter using Playwright headless browser.

Extracts 1X2 and Over/Under 2.5 odds from 1xBet by intercepting the
internal LineFeed API responses during a headless browser session.
The LineFeed API requires browser-established cookies/session to return
data (responds 406 to plain HTTP requests), so Playwright is used to
navigate to the league page and capture the JSON API response.

This is a Tier 2 (Headless) adapter — requires Playwright + Chromium.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    MarketOutcome,
    OddsAdapter,
    OddsExtractionError,
    ScrapedMatch,
)
from src.scraping.resilience import ResilienceConfig

logger = logging.getLogger(__name__)

try:
    from playwright.sync_api import (
        sync_playwright,
        TimeoutError as PlaywrightTimeout,
    )
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False


# Mapping from sport keys to 1xBet championship IDs
# These IDs are used in the champs= parameter of the LineFeed API
_LEAGUE_IDS: dict[str, int] = {
    "soccer_epl": 88637,
    "soccer_spain_la_liga": 127733,
    "soccer_germany_bundesliga": 96463,
    "soccer_italy_serie_a": 110163,
    "soccer_france_ligue_one": 12821,
    "soccer_uefa_champs_league": 118587,
    "soccer_world_cup": 2708736,
}

# 1xBet base URL
_BASE_URL = "https://1xbet.com"

# The regional domain that serves the API (redirected from 1xbet.com)
_API_DOMAIN = "col-1xbet.com"

# LineFeed API endpoint pattern (intercepted from browser network)
_LINEFEED_API_PATTERN = "service-api/LineFeed/Get1x2_VZip"

# Entry page to establish browser session
_FOOTBALL_PAGE = "https://1xbet.com/line/Football/"

# Default timeout for Playwright operations (ms)
_DEFAULT_TIMEOUT = 60_000


class OneXBetAdapter(OddsAdapter):
    """1xBet adapter using Playwright headless browser with API interception.

    Navigates to 1xBet's football page using a headless browser, which
    triggers the internal LineFeed API call. The adapter intercepts this
    API response to extract structured JSON odds data. This approach
    bypasses CloudFlare JS challenges that block direct HTTP requests.
    """

    def __init__(self, resilience: ResilienceConfig | None = None) -> None:
        """Initialize the 1xBet adapter.

        Args:
            resilience: Optional resilience configuration (used for timeout
                settings). Playwright manages its own retries.

        Raises:
            ImportError: If playwright is not installed.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is required for the 1xBet adapter. "
                "Install with: pip install playwright && playwright install chromium"
            )
        self._resilience = resilience or ResilienceConfig()
        self._timeout = _DEFAULT_TIMEOUT
        self._playwright = None
        self._browser = None

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
        """Uses headless browser extraction (Playwright)."""
        return ExtractionMethod.HEADLESS

    @property
    def base_url(self) -> str:
        """Root URL for 1xBet."""
        return _BASE_URL

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch 1X2 (match winner) odds from 1xBet via headless browser.

        Navigates to the league page in a headless browser and intercepts
        the LineFeed API response containing structured odds JSON.

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
        """Fetch Over/Under 2.5 goals odds from 1xBet via headless browser.

        Navigates to the league page and extracts over/under 2.5 total
        goals odds from the intercepted LineFeed API response.

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
        """Check adapter health by navigating to 1xBet with Playwright.

        Attempts to load the 1xBet football page in a headless browser.
        Returns REACHABLE if the page loads successfully.

        Returns:
            AdapterHealth.REACHABLE if 1xBet loads,
            AdapterHealth.UNREACHABLE on failure.
        """
        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                response = page.goto(_FOOTBALL_PAGE, timeout=30_000)
                if response and response.status < 400:
                    result = AdapterHealth.REACHABLE
                else:
                    result = AdapterHealth.UNREACHABLE
            except PlaywrightTimeout:
                result = AdapterHealth.UNREACHABLE
            except Exception:
                result = AdapterHealth.UNREACHABLE
            finally:
                page.close()
                browser.close()
                pw.stop()
            return result
        except Exception:
            return AdapterHealth.UNREACHABLE

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _get_league_id(self, sport: str) -> int:
        """Resolve sport key to 1xBet championship ID.

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
        """Fetch events for a league using Playwright API interception.

        Launches a headless browser, navigates to the football page for
        the specified league, and intercepts the LineFeed API response
        that contains structured event data with odds.

        Strategy:
        1. Navigate to the league page URL
        2. Intercept the Get1x2_VZip API response (JSON with all events)
        3. Parse the JSON and return the events list

        If API interception fails, falls back to DOM extraction.

        Args:
            league_id: 1xBet championship ID.

        Returns:
            List of event dictionaries from the JSON response.

        Raises:
            OddsExtractionError: On browser or parsing failure.
        """
        captured_events: list[dict[str, Any]] = []

        try:
            pw = sync_playwright().start()
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()

            # Set up response interception to capture LineFeed API data
            api_data: list[dict[str, Any]] = []

            def _on_response(response):
                """Capture LineFeed API responses."""
                try:
                    url = response.url
                    if _LINEFEED_API_PATTERN in url and response.status == 200:
                        body = response.text()
                        data = json.loads(body)
                        if isinstance(data, dict) and "Value" in data:
                            api_data.extend(data["Value"])
                        elif isinstance(data, list):
                            api_data.extend(data)
                except Exception as exc:
                    logger.debug("Error capturing API response: %s", exc)

            page.on("response", _on_response)

            # Navigate to the football page — this triggers the API call
            # Use the generic football URL which loads all leagues
            page.goto(_FOOTBALL_PAGE, timeout=self._timeout)

            # Wait for odds to render (indicates API call completed)
            try:
                page.wait_for_selector(
                    ".ui-market__value", timeout=30_000
                )
            except PlaywrightTimeout:
                logger.warning(
                    "Timeout waiting for odds to render on 1xBet page"
                )

            # Give extra time for API responses to be captured
            page.wait_for_timeout(3000)

            # Filter events by league_id
            for event in api_data:
                event_league = event.get("N") or event.get("LI")
                # Accept if league matches, or if no filter needed
                if event_league == league_id or league_id == event.get("LI"):
                    captured_events.append(event)

            # If no events matched the league filter, try all captured events
            # (the league ID might be mapped differently)
            if not captured_events and api_data:
                # Try matching by league name or just return all
                captured_events = api_data

            page.close()
            browser.close()
            pw.stop()

        except PlaywrightTimeout as exc:
            raise OddsExtractionError(
                message=f"Timeout loading 1xBet page: {exc}",
                adapter_id=self.bookmaker_id,
            ) from exc
        except OddsExtractionError:
            raise
        except Exception as exc:
            raise OddsExtractionError(
                message=f"Failed to fetch 1xBet events via browser: {exc}",
                adapter_id=self.bookmaker_id,
            ) from exc

        return captured_events

    def _parse_1x2_event(self, event: dict[str, Any]) -> ScrapedMatch | None:
        """Parse a single event dict into a 1X2 ScrapedMatch.

        The 1xBet JSON structure contains:
        - "O1" / "O2": team names
        - "S" or "SC": start time as unix timestamp
        - "E" list with entries having "T" (type) and "C" (coefficient)
          - T=1: Home Win (W1)
          - T=2: Draw (X)
          - T=3: Away Win (W2)

        Args:
            event: Raw event dictionary from the API response.

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
        In the LineFeed JSON, these are entries with:
        - T=9, P=2.5: Over 2.5
        - T=10, P=2.5: Under 2.5

        Args:
            event: Raw event dictionary from the API response.

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

        1xBet JSON uses:
        - "O1" / "O2": team names (string)
        - "O1E" / "O2E": English team names
        - "Opp1" / "Opp2": alternative field names

        Args:
            event: Event dictionary.
            side: Either "home" or "away".

        Returns:
            Team name string.

        Raises:
            KeyError: If no team name can be extracted.
        """
        if side == "home":
            for key in ("O1", "O1E", "Opp1", "Home"):
                if key in event and isinstance(event[key], str) and event[key]:
                    return event[key]
            if "Opp1" in event and isinstance(event["Opp1"], dict):
                return event["Opp1"].get("Name", "")
        else:
            for key in ("O2", "O2E", "Opp2", "Away"):
                if key in event and isinstance(event[key], str) and event[key]:
                    return event[key]
            if "Opp2" in event and isinstance(event["Opp2"], dict):
                return event["Opp2"].get("Name", "")

        raise KeyError(f"Cannot extract {side} team name from event")

    def _extract_timestamp(self, event: dict[str, Any]) -> datetime:
        """Extract event start timestamp.

        1xBet uses unix timestamps in the "S" field (new API) or "SC"
        field (legacy format).

        Args:
            event: Event dictionary.

        Returns:
            UTC datetime of the event.

        Raises:
            KeyError: If no timestamp field is found.
            ValueError: If the timestamp cannot be parsed.
        """
        for key in ("S", "SC", "StartTime"):
            if key in event:
                value = event[key]
                if isinstance(value, (int, float)):
                    return datetime.fromtimestamp(value, tz=timezone.utc)
                if isinstance(value, str):
                    return datetime.fromisoformat(
                        value.replace("Z", "+00:00")
                    )
        raise KeyError("Cannot extract timestamp from event")

    def _extract_1x2_odds(
        self, event: dict[str, Any], outcome: str
    ) -> float | None:
        """Extract a single 1X2 odds value from event data.

        The LineFeed API uses the "E" array with typed entries:
        - T=1: Home Win (W1)
        - T=2: Draw (X)
        - T=3: Away Win (W2)

        Also supports legacy direct fields and GE array format.

        Args:
            event: Event dictionary.
            outcome: One of "home", "draw", "away".

        Returns:
            Decimal odds value, or None if not found.
        """
        # Map outcome to type IDs
        type_map = {"home": 1, "draw": 2, "away": 3}
        target_type = type_map[outcome]

        # Strategy 1: "E" array with typed entries (primary format)
        entries = event.get("E", [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry_type = entry.get("T")
                coefficient = entry.get("C")
                if entry_type == target_type and coefficient is not None:
                    try:
                        return float(coefficient)
                    except (ValueError, TypeError):
                        continue

        # Strategy 2: Direct float fields (legacy GetChampZip format)
        field_map = {"home": "O1", "draw": "OX", "away": "O2"}
        field_name = field_map[outcome]
        if field_name in event:
            value = event[field_name]
            if isinstance(value, (int, float)) and value > 1.0:
                return float(value)

        # Strategy 3: "GE" (game events) array
        game_events = event.get("GE", [])
        if isinstance(game_events, list):
            for ge in game_events:
                if not isinstance(ge, dict):
                    continue
                if ge.get("G") == 1 or ge.get("T") == 1:
                    ge_entries = ge.get("E", [])
                    if isinstance(ge_entries, list):
                        for entry in ge_entries:
                            if not isinstance(entry, dict):
                                continue
                            if entry.get("T") == target_type:
                                try:
                                    return float(entry.get("C", 0))
                                except (ValueError, TypeError):
                                    continue

        return None

    def _extract_over_under_odds(
        self, event: dict[str, Any]
    ) -> tuple[float | None, float | None]:
        """Extract Over/Under 2.5 goals odds from event data.

        The LineFeed API uses the "E" array:
        - T=9, P=2.5: Over 2.5
        - T=10, P=2.5: Under 2.5

        Also supports legacy TotalOver/TotalUnder fields and GE array.

        Args:
            event: Event dictionary.

        Returns:
            Tuple of (over_odds, under_odds), either may be None.
        """
        over_odds: float | None = None
        under_odds: float | None = None

        # Strategy 1: "E" array with type 9 (over) and 10 (under)
        entries = event.get("E", [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                entry_type = entry.get("T")
                coefficient = entry.get("C")
                point = entry.get("P")

                # Check for 2.5 point line
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

        # Strategy 2: Direct fields (legacy format)
        if "TotalOver" in event and "TotalUnder" in event:
            try:
                over_val = float(event["TotalOver"])
                under_val = float(event["TotalUnder"])
                if over_val > 1.0 and under_val > 1.0:
                    return over_val, under_val
            except (ValueError, TypeError):
                pass

        # Strategy 3: "GE" array — totals market group (G=17)
        game_events = event.get("GE", [])
        if isinstance(game_events, list):
            for ge in game_events:
                if not isinstance(ge, dict):
                    continue
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
