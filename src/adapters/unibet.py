"""Unibet/Kambi odds adapter using the public Kambi Offering API.

Extracts 1X2 and Over/Under 2.5 odds from the Kambi CDN endpoints
which serve Unibet's odds data as structured JSON. No authentication
required — publicly accessible.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    MarketOutcome,
    OddsAdapter,
    OddsExtractionError,
    ScrapedMatch,
)
from src.scraping.http_scraper import HTTPScraper
from src.scraping.resilience import RateLimiterConfig, ResilienceConfig

logger = logging.getLogger(__name__)

# Mapping of sport keys to Kambi group IDs (numeric IDs for betoffer/group endpoint)
_LEAGUE_GROUP_IDS: dict[str, int] = {
    "soccer_epl": 1000094985,
    "soccer_spain_la_liga": 1000095001,
    "soccer_germany_bundesliga": 1000094994,
    "soccer_italy_serie_a": 1000094998,
    "soccer_france_ligue_one": 1000094995,
    "soccer_uefa_champs_league": 2000050648,
    "soccer_world_cup": 2010133908,
    "soccer_fifa_world_cup": 2010133908,
}

# Legacy path mapping (kept for reference/fallback)
_LEAGUE_PATHS: dict[str, str] = {
    "soccer_epl": "football/england/premier_league",
    "soccer_spain_la_liga": "football/spain/la_liga",
    "soccer_germany_bundesliga": "football/germany/bundesliga",
    "soccer_italy_serie_a": "football/italy/serie_a",
    "soccer_france_ligue_one": "football/france/ligue_1",
    "soccer_uefa_champs_league": "football/champions_league",
    "soccer_world_cup": "football/world_cup_2026",
}

_KAMBI_DOMAIN = "eu-offering-api.kambicdn.com"
_KAMBI_BASE_URL = "https://eu-offering-api.kambicdn.com"
_BETOFFER_PATH = "/offering/v2018/ub/betoffer/group"


class UnibetAdapter(OddsAdapter):
    """Adapter for Unibet odds via the Kambi Offering API.

    Uses HTTP GET requests to the public Kambi CDN to fetch structured
    JSON with events, markets, and decimal odds. No authentication or
    headless browser required.
    """

    def __init__(self) -> None:
        """Initialize the Unibet adapter with an HTTP scraper."""
        resilience = ResilienceConfig(
            rate_limiter=RateLimiterConfig(min_delay=3.0, jitter_pct=0.30),
            max_retries=3,
            backoff_base=2.0,
        )
        self._scraper = HTTPScraper(resilience)

    @property
    def bookmaker_id(self) -> str:
        """Unique identifier for this bookmaker."""
        return "unibet"

    @property
    def bookmaker_name(self) -> str:
        """Human-readable name."""
        return "Unibet (Kambi)"

    @property
    def priority(self) -> int:
        """Priority level. Lower number = higher priority."""
        return 2

    @property
    def extraction_method(self) -> ExtractionMethod:
        """This adapter uses HTTP (JSON API)."""
        return ExtractionMethod.HTTP

    @property
    def base_url(self) -> str:
        """Root URL for the Kambi offering API."""
        return _KAMBI_BASE_URL

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch 1X2 (match winner) odds from the Kambi API.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl', 'soccer_world_cup').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='1x2'.

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        group_id = self._get_group_id(sport)
        url = f"{_KAMBI_BASE_URL}{_BETOFFER_PATH}/{group_id}.json"

        data = self._fetch_json(url)
        events = self._extract_events_from_betoffer(data)

        matches: list[ScrapedMatch] = []
        for event_id, event_data in events.items():
            try:
                match = self._parse_1x2_from_betoffer(event_data)
                if match is not None:
                    if date is None or self._matches_date(match, date):
                        matches.append(match)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Skipping event due to parsing error: %s", exc
                )
                continue

        return matches

    def fetch_over_under_total(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch ALL Over/Under total goals lines (0.5-4.5) from the Kambi API.

        Extracts outcomes for all available thresholds (0.5, 1.5, 2.5, 3.5, 4.5)
        from the "Total Goals" market (not team-specific).

        Args:
            sport: Sport/league key (e.g., 'soccer_epl', 'soccer_world_cup').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='over_under_total' and
            multiple MarketOutcome objects (one per threshold).

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        group_id = self._get_group_id(sport)
        url = f"{_KAMBI_BASE_URL}{_BETOFFER_PATH}/{group_id}.json"

        data = self._fetch_json(url)
        events = self._extract_events_from_betoffer(data)

        matches: list[ScrapedMatch] = []
        for event_id, event_data in events.items():
            try:
                event_meta = event_data["event"]
                home_team, away_team = self._extract_teams(event_meta)
                timestamp = self._extract_timestamp(event_meta)

                bet_offers = event_data.get("betOffers", [])
                outcomes = self._find_overall_ou_outcomes(bet_offers)

                if not outcomes:
                    continue

                match = ScrapedMatch(
                    home_team=home_team,
                    away_team=away_team,
                    event_timestamp=timestamp,
                    market_type="over_under_total",
                    outcomes=outcomes,
                )
                if date is None or self._matches_date(match, date):
                    matches.append(match)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Skipping event due to parsing error: %s", exc
                )
                continue

        return matches

    def fetch_over_under_team(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch team-specific Over/Under goals lines from the Kambi API.

        Extracts outcomes from "Total Goals by {Team}" markets and maps
        them to home/away based on event homeName/awayName.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl', 'soccer_world_cup').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='over_under_team' and
            MarketOutcome name="Over home"/"Under home"/"Over away"/"Under away".

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        group_id = self._get_group_id(sport)
        url = f"{_KAMBI_BASE_URL}{_BETOFFER_PATH}/{group_id}.json"

        data = self._fetch_json(url)
        events = self._extract_events_from_betoffer(data)

        matches: list[ScrapedMatch] = []
        for event_id, event_data in events.items():
            try:
                event_meta = event_data["event"]
                home_team, away_team = self._extract_teams(event_meta)
                timestamp = self._extract_timestamp(event_meta)

                bet_offers = event_data.get("betOffers", [])
                outcomes = self._find_team_ou_outcomes(bet_offers, home_team, away_team)

                if not outcomes:
                    continue

                match = ScrapedMatch(
                    home_team=home_team,
                    away_team=away_team,
                    event_timestamp=timestamp,
                    market_type="over_under_team",
                    outcomes=outcomes,
                )
                if date is None or self._matches_date(match, date):
                    matches.append(match)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Skipping event due to parsing error: %s", exc
                )
                continue

        return matches

    def fetch_over_under(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch Over/Under 2.5 goals odds from the Kambi API.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl', 'soccer_world_cup').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='over_under'.

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        group_id = self._get_group_id(sport)
        url = f"{_KAMBI_BASE_URL}{_BETOFFER_PATH}/{group_id}.json"

        data = self._fetch_json(url)
        events = self._extract_events_from_betoffer(data)

        matches: list[ScrapedMatch] = []
        for event_id, event_data in events.items():
            try:
                match = self._parse_over_under_from_betoffer(event_data)
                if match is not None:
                    if date is None or self._matches_date(match, date):
                        matches.append(match)
            except (KeyError, TypeError, ValueError) as exc:
                logger.debug(
                    "Skipping event due to parsing error: %s", exc
                )
                continue

        return matches

    def health_check(self) -> AdapterHealth:
        """Check if the Kambi API is reachable.

        Makes a lightweight request to the World Cup endpoint to verify
        the API is responding.

        Returns:
            AdapterHealth.REACHABLE if successful, UNREACHABLE otherwise.
        """
        url = f"{_KAMBI_BASE_URL}{_BETOFFER_PATH}/2010133908.json"
        try:
            self._fetch_json(url)
            return AdapterHealth.REACHABLE
        except OddsExtractionError:
            return AdapterHealth.UNREACHABLE

    def _get_group_id(self, sport: str) -> int:
        """Map a sport key to a Kambi group ID.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl', 'soccer_world_cup').

        Returns:
            Kambi group ID for the betoffer endpoint.

        Raises:
            OddsExtractionError: If the sport key is not supported.
        """
        group_id = _LEAGUE_GROUP_IDS.get(sport)
        if group_id is None:
            raise OddsExtractionError(
                message=f"Unsupported sport key for Unibet: '{sport}'. "
                f"Supported: {list(_LEAGUE_GROUP_IDS.keys())}",
                adapter_id=self.bookmaker_id,
            )
        return group_id

    def _get_league_path(self, sport: str) -> str:
        """Map a sport key to a Kambi league path (legacy).

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').

        Returns:
            Kambi league path (e.g., 'football/england/premier_league').

        Raises:
            OddsExtractionError: If the sport key is not supported.
        """
        path = _LEAGUE_PATHS.get(sport)
        if path is None:
            raise OddsExtractionError(
                message=f"Unsupported sport key for Unibet: '{sport}'. "
                f"Supported: {list(_LEAGUE_PATHS.keys())}",
                adapter_id=self.bookmaker_id,
            )
        return path

    def _fetch_json(self, url: str) -> dict:
        """Fetch JSON data from the Kambi API.

        Uses the HTTPScraper for rate limiting and resilience, then
        parses the response as JSON.

        Args:
            url: Full URL to the Kambi endpoint.

        Returns:
            Parsed JSON response as a dict.

        Raises:
            OddsExtractionError: On network or parsing failure.
        """
        import requests as req

        self._scraper._rate_limiter.wait(_KAMBI_DOMAIN)

        def _do_request() -> req.Response:
            headers = {"User-Agent": self._scraper._ua_rotator.next()}
            proxies = None
            if self._scraper._config.proxy:
                proxies = {
                    "http": self._scraper._config.proxy,
                    "https": self._scraper._config.proxy,
                }
            response = req.get(url, headers=headers, timeout=30, proxies=proxies)
            response.raise_for_status()
            return response

        try:
            response = self._scraper._retry_handler.execute(_do_request)
        except OddsExtractionError:
            raise
        except Exception as exc:
            raise OddsExtractionError(
                message=f"Failed to fetch Kambi API: {exc}",
                adapter_id=self.bookmaker_id,
            ) from exc

        try:
            return response.json()
        except (ValueError, AttributeError) as exc:
            raise OddsExtractionError(
                message=f"Failed to parse JSON from Kambi API: {exc}",
                adapter_id=self.bookmaker_id,
            ) from exc

    def _extract_events_from_betoffer(self, data: dict) -> dict[int, dict]:
        """Extract events with their bet offers from the betoffer/group response.

        The betoffer/group endpoint returns:
        - 'betOffers': flat list of all bet offers across all events
        - 'events': list of event metadata (id, homeName, awayName, start, etc.)

        This method groups bet offers by eventId and merges with event metadata.

        Args:
            data: Parsed JSON response from Kambi betoffer/group endpoint.

        Returns:
            Dict mapping event_id to a dict with 'event' and 'betOffers' keys.
        """
        if not isinstance(data, dict):
            return {}

        events_list = data.get("events", [])
        bet_offers_list = data.get("betOffers", [])

        # Build event metadata lookup by ID
        events_by_id: dict[int, dict] = {}
        for e in events_list:
            eid = e.get("id")
            if eid is not None:
                events_by_id[eid] = {"event": e, "betOffers": []}

        # Group bet offers by eventId
        for bo in bet_offers_list:
            event_id = bo.get("eventId")
            if event_id in events_by_id:
                events_by_id[event_id]["betOffers"].append(bo)

        # Filter out non-match events (e.g., specials without homeName)
        result: dict[int, dict] = {}
        for eid, event_data in events_by_id.items():
            event_meta = event_data["event"]
            if event_meta.get("homeName") and event_meta.get("awayName"):
                result[eid] = event_data

        return result

    def _parse_1x2_from_betoffer(self, event_data: dict) -> ScrapedMatch | None:
        """Parse 1X2 market from betoffer/group response format.

        Args:
            event_data: Dict with 'event' metadata and 'betOffers' list.

        Returns:
            ScrapedMatch with 1X2 outcomes, or None if no valid market found.
        """
        event_meta = event_data["event"]
        home_team, away_team = self._extract_teams(event_meta)
        timestamp = self._extract_timestamp(event_meta)

        bet_offers = event_data.get("betOffers", [])
        outcomes = self._find_1x2_outcomes(bet_offers)

        if not outcomes:
            return None

        return ScrapedMatch(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=timestamp,
            market_type="1x2",
            outcomes=outcomes,
        )

    def _parse_over_under_from_betoffer(self, event_data: dict) -> ScrapedMatch | None:
        """Parse Over/Under 2.5 market from betoffer/group response format.

        Args:
            event_data: Dict with 'event' metadata and 'betOffers' list.

        Returns:
            ScrapedMatch with Over/Under outcomes, or None if not found.
        """
        event_meta = event_data["event"]
        home_team, away_team = self._extract_teams(event_meta)
        timestamp = self._extract_timestamp(event_meta)

        bet_offers = event_data.get("betOffers", [])
        outcomes = self._find_over_under_outcomes(bet_offers)

        if not outcomes:
            return None

        return ScrapedMatch(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=timestamp,
            market_type="over_under",
            outcomes=outcomes,
        )

    def _extract_events(self, data: dict) -> list[dict]:
        """Extract the events list from the Kambi API response.

        The Kambi API response structure contains events nested under
        different possible keys depending on the endpoint version.

        Args:
            data: Parsed JSON response from Kambi.

        Returns:
            List of event dicts from the response.
        """
        if not isinstance(data, dict):
            return []

        # Kambi listView response typically has 'events' at top level
        events = data.get("events", [])
        if events:
            return events

        # Alternative structure: events may be nested under 'result'
        result = data.get("result", [])
        if isinstance(result, list):
            return result

        return []

    def _parse_1x2_event(self, event: dict) -> ScrapedMatch | None:
        """Parse a Kambi event dict into a ScrapedMatch for 1X2 market.

        Args:
            event: A single event dict from the Kambi API.

        Returns:
            ScrapedMatch with 1X2 outcomes, or None if no valid market found.
        """
        event_data = event.get("event", event)
        home_team, away_team = self._extract_teams(event_data)
        timestamp = self._extract_timestamp(event_data)

        # Find 1X2 (Match Winner / Full Time) bet offers
        bet_offers = self._get_bet_offers(event)
        outcomes = self._find_1x2_outcomes(bet_offers)

        if not outcomes:
            return None

        return ScrapedMatch(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=timestamp,
            market_type="1x2",
            outcomes=outcomes,
        )

    def _parse_over_under_event(self, event: dict) -> ScrapedMatch | None:
        """Parse a Kambi event dict into a ScrapedMatch for Over/Under market.

        Args:
            event: A single event dict from the Kambi API.

        Returns:
            ScrapedMatch with Over/Under outcomes, or None if not found.
        """
        event_data = event.get("event", event)
        home_team, away_team = self._extract_teams(event_data)
        timestamp = self._extract_timestamp(event_data)

        bet_offers = self._get_bet_offers(event)
        outcomes = self._find_over_under_outcomes(bet_offers)

        if not outcomes:
            return None

        return ScrapedMatch(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=timestamp,
            market_type="over_under",
            outcomes=outcomes,
        )

    def _extract_teams(self, event_data: dict) -> tuple[str, str]:
        """Extract home and away team names from event data.

        Args:
            event_data: The event dict (may be nested under 'event' key).

        Returns:
            Tuple of (home_team, away_team).

        Raises:
            KeyError: If team names cannot be found.
        """
        # Kambi uses 'homeName' and 'awayName' or participants list
        home = event_data.get("homeName", "")
        away = event_data.get("awayName", "")

        if home and away:
            return home, away

        # Alternative: 'name' field formatted as "Home vs Away" or "Home - Away"
        name = event_data.get("name", "")
        if " vs " in name:
            parts = name.split(" vs ", 1)
            return parts[0].strip(), parts[1].strip()
        if " - " in name:
            parts = name.split(" - ", 1)
            return parts[0].strip(), parts[1].strip()

        # Alternative: participants array
        participants = event_data.get("participants", [])
        if len(participants) >= 2:
            home = participants[0].get("name", "Unknown")
            away = participants[1].get("name", "Unknown")
            return home, away

        raise KeyError(f"Cannot extract team names from event: {event_data.get('id', 'unknown')}")

    def _extract_timestamp(self, event_data: dict) -> datetime:
        """Extract event start time from event data.

        Args:
            event_data: The event dict.

        Returns:
            Event start time as a UTC datetime.
        """
        # Kambi uses 'start' as ISO timestamp or epoch milliseconds
        start = event_data.get("start", "")

        if isinstance(start, str) and start:
            # ISO format: "2024-06-01T15:00:00Z"
            try:
                # Handle 'Z' suffix
                clean = start.replace("Z", "+00:00")
                return datetime.fromisoformat(clean)
            except ValueError:
                pass

        # Epoch milliseconds
        start_ms = event_data.get("start", event_data.get("startTime", 0))
        if isinstance(start_ms, (int, float)) and start_ms > 0:
            return datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc)

        # Fallback to current time
        return datetime.now(tz=timezone.utc)

    def _get_bet_offers(self, event: dict) -> list[dict]:
        """Get bet offers from an event dict.

        Args:
            event: The event dict from the API response.

        Returns:
            List of bet offer dicts.
        """
        # Kambi structures: betOffers at event level or nested
        bet_offers = event.get("betOffers", [])
        if bet_offers:
            return bet_offers

        # Alternative: 'markets' key
        markets = event.get("markets", [])
        if markets:
            return markets

        return []

    def _find_1x2_outcomes(self, bet_offers: list[dict]) -> list[MarketOutcome]:
        """Find 1X2 match winner outcomes from bet offers.

        Looks for the Full Time match winner (1X2) market among the bet offers
        and extracts Home/Draw/Away odds. Prioritizes 'Full Time' criterion
        over other 3-outcome markets.

        Args:
            bet_offers: List of bet offer dicts from the event.

        Returns:
            List of MarketOutcome for Home, Draw, Away. Empty if not found.
        """
        # First pass: look for exact "Full Time" or "Match Winner" criterion
        for offer in bet_offers:
            criterion = offer.get("criterion", {})
            offer_type = offer.get("betOfferType", {})

            label = criterion.get("label", "") if isinstance(criterion, dict) else ""
            type_name = offer_type.get("name", "").lower() if isinstance(offer_type, dict) else ""

            # Strict match: known 1X2 criterion labels with "Match" or "1X2" type
            if label in ("Full Time", "Match Winner", "1X2", "Match Result") and (
                "match" in type_name or "1x2" in type_name
            ):
                outcomes = offer.get("outcomes", [])
                if len(outcomes) >= 3:
                    result = self._map_1x2_outcomes(outcomes)
                    if result:
                        return result

        # Second pass: broader match for "Match Result" or similar
        for offer in bet_offers:
            criterion = offer.get("criterion", {})
            offer_type = offer.get("betOfferType", {})

            label = criterion.get("label", "").lower() if isinstance(criterion, dict) else ""
            type_name = offer_type.get("name", "").lower() if isinstance(offer_type, dict) else ""

            # Skip non-full-time markets
            if "half" in label or "corner" in label or "card" in label or "handicap" in label:
                continue
            if "shot" in label or "goal" in label.replace("goals", ""):
                continue

            is_1x2 = (
                "full time" in label
                or "match result" in label
                or "1x2" in label
                or ("match" in type_name and "handicap" not in type_name)
            )

            if not is_1x2:
                continue

            outcomes = offer.get("outcomes", [])
            if len(outcomes) < 3:
                continue

            result = self._map_1x2_outcomes(outcomes)
            if result:
                return result

        return []

    def _map_1x2_outcomes(self, outcomes: list[dict]) -> list[MarketOutcome]:
        """Map Kambi outcome dicts to MarketOutcome for 1X2.

        Args:
            outcomes: List of outcome dicts from a bet offer.

        Returns:
            List of [Home, Draw, Away] MarketOutcome objects.
        """
        home_odds: float | None = None
        draw_odds: float | None = None
        away_odds: float | None = None

        for outcome in outcomes:
            label = outcome.get("label", "").lower()
            odds_value = outcome.get("odds", 0)
            # Kambi odds are in milliodds (e.g., 2450 = 2.45)
            if isinstance(odds_value, (int, float)):
                decimal_odds = odds_value / 1000.0 if odds_value > 100 else odds_value
            else:
                continue

            if decimal_odds <= 1.0:
                continue

            # Map outcome labels to positions
            outcome_type = outcome.get("type", "").lower()

            if outcome_type == "home" or "1" == label or label in ("home", "1"):
                home_odds = decimal_odds
            elif outcome_type == "draw" or label in ("draw", "x"):
                draw_odds = decimal_odds
            elif outcome_type == "away" or "2" == label or label in ("away", "2"):
                away_odds = decimal_odds

        # Fallback: if types not identified, use positional mapping
        if home_odds is None or draw_odds is None or away_odds is None:
            if len(outcomes) >= 3:
                try:
                    values = []
                    for o in outcomes[:3]:
                        v = o.get("odds", 0)
                        if isinstance(v, (int, float)):
                            decimal = v / 1000.0 if v > 100 else v
                            values.append(decimal)
                        else:
                            values.append(0.0)
                    if all(v > 1.0 for v in values):
                        home_odds, draw_odds, away_odds = values[0], values[1], values[2]
                except (IndexError, TypeError):
                    pass

        if home_odds is None or draw_odds is None or away_odds is None:
            return []

        return [
            MarketOutcome(name="Home", odds=home_odds),
            MarketOutcome(name="Draw", odds=draw_odds),
            MarketOutcome(name="Away", odds=away_odds),
        ]

    def _find_overall_ou_outcomes(self, bet_offers: list[dict]) -> list[MarketOutcome]:
        """Find all Overall O/U lines from bet offers.

        Looks for "Total Goals" criterion (exactly, not "Total Goals by X")
        and returns outcomes at thresholds {0.5, 1.5, 2.5, 3.5, 4.5}.

        Args:
            bet_offers: List of bet offer dicts from the event.

        Returns:
            List of MarketOutcome with name="Over"/"Under" and point set.
        """
        valid_thresholds = {0.5, 1.5, 2.5, 3.5, 4.5}
        results: list[MarketOutcome] = []

        for offer in bet_offers:
            criterion = offer.get("criterion", {})
            label = criterion.get("label", "") if isinstance(criterion, dict) else ""

            # Must be exactly "Total Goals" — not "Total Goals by X"
            label_lower = label.lower().strip()
            is_overall_total = (
                label_lower == "total goals"
                or label_lower == "total goals - over/under"
                or label_lower == "over/under"
            )
            # Skip team-specific labels
            if "total goals by" in label_lower:
                continue
            if not is_overall_total:
                continue

            outcomes = offer.get("outcomes", [])
            for outcome in outcomes:
                odds_value = outcome.get("odds", 0)
                line = outcome.get("line", 0)
                outcome_label = outcome.get("label", "").lower()
                outcome_type = outcome.get("type", "").lower()

                if odds_value is None or odds_value == 0:
                    continue

                # Convert milliodds
                if isinstance(odds_value, (int, float)):
                    decimal_odds = odds_value / 1000.0 if odds_value > 100 else odds_value
                else:
                    continue

                if decimal_odds <= 1.0:
                    continue

                # Convert line from milligoals
                line_value = 0.0
                if isinstance(line, (int, float)) and line is not None:
                    line_value = line / 1000.0 if line > 100 else line

                # Only accept valid thresholds
                if not any(abs(line_value - t) < 0.01 for t in valid_thresholds):
                    continue

                # Round to nearest valid threshold
                threshold = min(valid_thresholds, key=lambda t: abs(line_value - t))

                if "over" in outcome_label or outcome_type == "over":
                    results.append(MarketOutcome(name="Over", odds=decimal_odds, point=threshold))
                elif "under" in outcome_label or outcome_type == "under":
                    results.append(MarketOutcome(name="Under", odds=decimal_odds, point=threshold))

        return results

    def _find_team_ou_outcomes(
        self, bet_offers: list[dict], home_name: str, away_name: str
    ) -> list[MarketOutcome]:
        """Find team-specific O/U lines from bet offers.

        Matches criterion labels like "Total Goals by Mexico" against
        event homeName/awayName. Returns outcomes mapped to home/away.

        Args:
            bet_offers: List of bet offer dicts from the event.
            home_name: Home team name from the event.
            away_name: Away team name from the event.

        Returns:
            List of MarketOutcome with name="Over home"/"Under home"/
            "Over away"/"Under away" and point=threshold.
        """
        results: list[MarketOutcome] = []

        for offer in bet_offers:
            criterion = offer.get("criterion", {})
            label = criterion.get("label", "") if isinstance(criterion, dict) else ""

            # Must match "Total Goals by {Team}"
            team_side = self._classify_team(label, home_name, away_name)
            if team_side is None:
                continue

            outcomes = offer.get("outcomes", [])
            for outcome in outcomes:
                odds_value = outcome.get("odds", 0)
                line = outcome.get("line", 0)
                outcome_label = outcome.get("label", "").lower()
                outcome_type = outcome.get("type", "").lower()

                if odds_value is None or odds_value == 0:
                    continue

                # Convert milliodds
                if isinstance(odds_value, (int, float)):
                    decimal_odds = odds_value / 1000.0 if odds_value > 100 else odds_value
                else:
                    continue

                if decimal_odds <= 1.0:
                    continue

                # Convert line from milligoals
                line_value = 0.0
                if isinstance(line, (int, float)) and line is not None:
                    line_value = line / 1000.0 if line > 100 else line

                if line_value < 0.01:
                    continue

                if "over" in outcome_label or outcome_type == "over":
                    results.append(
                        MarketOutcome(name=f"Over {team_side}", odds=decimal_odds, point=line_value)
                    )
                elif "under" in outcome_label or outcome_type == "under":
                    results.append(
                        MarketOutcome(name=f"Under {team_side}", odds=decimal_odds, point=line_value)
                    )

        return results

    def _classify_team(
        self, criterion_label: str, home_name: str, away_name: str
    ) -> str | None:
        """Classify a criterion label as home/away team.

        Extracts team name from labels like "Total Goals by Mexico"
        and matches against event home/away names.

        Args:
            criterion_label: The criterion label (e.g., "Total Goals by Mexico").
            home_name: Home team name from the event.
            away_name: Away team name from the event.

        Returns:
            "home" if label matches home team, "away" if away, None otherwise.
        """
        label_lower = criterion_label.lower()
        if "total goals by" not in label_lower:
            return None

        # Extract team name after "by"
        parts = criterion_label.split("by", 1)
        if len(parts) < 2:
            return None
        team_in_label = parts[1].strip().lower()

        home_lower = home_name.lower()
        away_lower = away_name.lower()

        # Direct match or substring match (handles partial names)
        if team_in_label == home_lower or team_in_label in home_lower or home_lower in team_in_label:
            return "home"
        if team_in_label == away_lower or team_in_label in away_lower or away_lower in team_in_label:
            return "away"

        return None

    def _find_over_under_outcomes(self, bet_offers: list[dict]) -> list[MarketOutcome]:
        """Find Over/Under 2.5 total goals outcomes from bet offers.

        Specifically looks for the "Total Goals" market (not team-specific
        goals like "Total Goals by Team X") with a 2.5 line.

        Args:
            bet_offers: List of bet offer dicts from the event.

        Returns:
            List of MarketOutcome for Over/Under. Empty if not found.
        """
        for offer in bet_offers:
            criterion = offer.get("criterion", {})
            offer_type = offer.get("betOfferType", {})

            label = criterion.get("label", "") if isinstance(criterion, dict) else ""
            type_name = offer_type.get("name", "").lower() if isinstance(offer_type, dict) else ""

            # Must be a total goals / over-under market
            label_lower = label.lower()
            is_total_goals = (
                "total goals" in label_lower
                or "over/under" in label_lower
                or "over" in label_lower
                or "under" in label_lower
                or "total" in type_name
                or "over" in type_name
            )

            if not is_total_goals:
                continue

            # Skip non-relevant markets
            if "corner" in label_lower or "card" in label_lower or "half" in label_lower:
                continue

            outcomes = offer.get("outcomes", [])
            if len(outcomes) < 2:
                continue

            result = self._map_over_under_outcomes(outcomes)
            if result:
                # Verify we got the 2.5 line specifically
                if any(o.point == 2.5 for o in result):
                    return result

        return []

    def _map_over_under_outcomes(self, outcomes: list[dict]) -> list[MarketOutcome]:
        """Map Kambi outcome dicts to MarketOutcome for Over/Under 2.5.

        Strictly filters for the 2.5 goals line (represented as 2500 in
        Kambi's milligoals format or 2.5 as a float).

        Args:
            outcomes: List of outcome dicts from a bet offer.

        Returns:
            List of [Over, Under] MarketOutcome objects with point=2.5.
            Empty list if the 2.5 line is not found.
        """
        over_odds: float | None = None
        under_odds: float | None = None

        for outcome in outcomes:
            label = outcome.get("label", "").lower()
            outcome_type = outcome.get("type", "").lower()
            odds_value = outcome.get("odds", 0)
            line = outcome.get("line", 0)

            if odds_value is None or odds_value == 0:
                continue

            # Convert odds from milliodds
            if isinstance(odds_value, (int, float)):
                decimal_odds = odds_value / 1000.0 if odds_value > 100 else odds_value
            else:
                continue

            if decimal_odds <= 1.0:
                continue

            # Convert line from milligoals — MUST be exactly 2.5
            line_value = 0.0
            if isinstance(line, (int, float)) and line is not None:
                line_value = line / 1000.0 if line > 100 else line

            # Strict check: only accept 2.5 line
            if abs(line_value - 2.5) > 0.01:
                continue

            if "over" in label or outcome_type == "over":
                over_odds = decimal_odds
            elif "under" in label or outcome_type == "under":
                under_odds = decimal_odds

        if over_odds is None or under_odds is None:
            return []

        return [
            MarketOutcome(name="Over", odds=over_odds, point=2.5),
            MarketOutcome(name="Under", odds=under_odds, point=2.5),
        ]

    @staticmethod
    def _matches_date(match: ScrapedMatch, date: str) -> bool:
        """Check if a match's timestamp falls on the given date.

        Args:
            match: The scraped match to check.
            date: Date string in YYYY-MM-DD format.

        Returns:
            True if the match is on the specified date.
        """
        try:
            target = datetime.strptime(date, "%Y-%m-%d").date()
            return match.event_timestamp.date() == target
        except (ValueError, AttributeError):
            return True  # If date can't be parsed, include the match
