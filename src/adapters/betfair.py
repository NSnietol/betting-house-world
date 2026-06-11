"""Betfair bookmaker adapter for odds extraction.

Implements the OddsAdapter interface for Betfair's sportsbook/exchange.
Uses HTTP-based extraction as the primary method with a headless browser
fallback for JavaScript-rendered pages. Betfair is classified as Tier 2
(headless/API) but this adapter degrades gracefully when Playwright is
not installed.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

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

# Sport key to Betfair sportsbook URL path mapping
_SPORT_URL_MAP: dict[str, str] = {
    "soccer_epl": "/sport/football/english-premier-league",
    "soccer_spain_la_liga": "/sport/football/spanish-la-liga",
    "soccer_germany_bundesliga": "/sport/football/german-bundesliga",
    "soccer_italy_serie_a": "/sport/football/italian-serie-a",
    "soccer_france_ligue_one": "/sport/football/french-ligue-1",
    "soccer_uefa_champs_league": "/sport/football/champions-league",
}

# CSS selectors for Betfair sportsbook pages
_SELECTORS = {
    "runner_line": "div.runner-line",
    "odds_price": "button.bet-button-price span.bet-button-price",
    "event_name": "span.event-name",
    "market_name": "h3.market-name",
}

# Wait selector for headless rendering
_HEADLESS_WAIT_SELECTOR = "div.runner-line"


class BetfairAdapter(OddsAdapter):
    """Betfair sportsbook/exchange odds extraction adapter.

    Attempts HTTP-based extraction from Betfair's publicly accessible
    sportsbook pages. Falls back to headless browser rendering if HTTP
    extraction yields no results and Playwright is available.

    The adapter handles the case where Playwright is not installed by
    logging a warning and returning empty results for pages requiring
    JavaScript rendering.
    """

    def __init__(self) -> None:
        """Initialize the Betfair adapter with HTTP scraper and optional headless."""
        resilience = ResilienceConfig(
            rate_limiter=RateLimiterConfig(
                min_delay=3.0,
                max_delay=30.0,
                jitter_pct=0.30,
            ),
            max_retries=3,
            backoff_base=2.0,
        )
        self._http_scraper = HTTPScraper(resilience)
        self._headless_scraper = None
        self._headless_available = self._init_headless()

    def _init_headless(self) -> bool:
        """Attempt to initialize the headless scraper.

        Returns:
            True if Playwright is available and HeadlessScraper initialized,
            False otherwise.
        """
        try:
            from src.scraping.headless import HeadlessScraper

            self._headless_scraper = HeadlessScraper(timeout=60_000)
            return True
        except ImportError:
            logger.warning(
                "Playwright not installed. Betfair adapter will operate in "
                "HTTP-only mode with reduced extraction capability."
            )
            return False

    @property
    def bookmaker_id(self) -> str:
        """Unique identifier for Betfair."""
        return "betfair"

    @property
    def bookmaker_name(self) -> str:
        """Human-readable name for Betfair."""
        return "Betfair"

    @property
    def priority(self) -> int:
        """Priority level for Betfair (Tier 2, third in recommended order)."""
        return 3

    @property
    def extraction_method(self) -> ExtractionMethod:
        """Betfair requires headless browser for full extraction."""
        return ExtractionMethod.HEADLESS

    @property
    def base_url(self) -> str:
        """Root URL for the Betfair site."""
        return "https://www.betfair.com"

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch 1X2 (match winner) odds from Betfair sportsbook.

        Attempts HTTP extraction first, then falls back to headless browser
        if no results are obtained and Playwright is available.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='1x2'.

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        url = self._build_url(sport)

        # Attempt HTTP extraction first
        matches = self._extract_1x2_http(url)
        if matches:
            return matches

        # Fall back to headless if available
        if self._headless_available and self._headless_scraper is not None:
            matches = self._extract_1x2_headless(url)
            if matches:
                return matches

        logger.info(
            "Betfair: No 1X2 odds extracted for sport='%s'. "
            "Page may require JavaScript rendering.",
            sport,
        )
        return []

    def fetch_over_under(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        """Fetch Over/Under 2.5 goals odds from Betfair sportsbook.

        Attempts HTTP extraction first, then falls back to headless browser
        if no results are obtained and Playwright is available.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter.

        Returns:
            List of ScrapedMatch with market_type='over_under'.

        Raises:
            OddsExtractionError: On unrecoverable extraction failure.
        """
        url = self._build_url(sport)

        # Attempt HTTP extraction first
        matches = self._extract_over_under_http(url)
        if matches:
            return matches

        # Fall back to headless if available
        if self._headless_available and self._headless_scraper is not None:
            matches = self._extract_over_under_headless(url)
            if matches:
                return matches

        logger.info(
            "Betfair: No Over/Under odds extracted for sport='%s'. "
            "Page may require JavaScript rendering.",
            sport,
        )
        return []

    def health_check(self) -> AdapterHealth:
        """Check Betfair sportsbook reachability with a lightweight HEAD request.

        Returns:
            AdapterHealth.REACHABLE if the site responds with 2xx/3xx,
            AdapterHealth.RATE_LIMITED if 429,
            AdapterHealth.UNREACHABLE otherwise.
        """
        try:
            response = requests.head(
                self.base_url,
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"},
                allow_redirects=True,
            )
            if response.status_code == 429:
                return AdapterHealth.RATE_LIMITED
            if response.status_code < 400:
                return AdapterHealth.REACHABLE
            return AdapterHealth.UNREACHABLE
        except (requests.exceptions.RequestException, Exception):
            return AdapterHealth.UNREACHABLE

    def _build_url(self, sport: str) -> str:
        """Build the Betfair sportsbook URL for a given sport key.

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').

        Returns:
            Full URL to the Betfair sportsbook page for that league.
        """
        path = _SPORT_URL_MAP.get(sport, "/sport/football")
        return f"{self.base_url}{path}"

    def _extract_1x2_http(self, url: str) -> list[ScrapedMatch]:
        """Attempt to extract 1X2 odds via HTTP scraping.

        Args:
            url: Full URL to the Betfair sportsbook page.

        Returns:
            List of ScrapedMatch objects, may be empty if extraction fails.
        """
        try:
            soup = self._http_scraper.fetch_page(url, domain="betfair.com")
            return self._parse_1x2_from_soup(soup)
        except OddsExtractionError as exc:
            logger.debug("Betfair HTTP extraction failed for 1X2: %s", exc)
            return []

    def _extract_1x2_headless(self, url: str) -> list[ScrapedMatch]:
        """Attempt to extract 1X2 odds via headless browser.

        Args:
            url: Full URL to the Betfair sportsbook page.

        Returns:
            List of ScrapedMatch objects, may be empty if extraction fails.
        """
        try:
            html = self._headless_scraper.fetch_rendered_page(
                url, wait_selector=_HEADLESS_WAIT_SELECTOR
            )
            soup = BeautifulSoup(html, "lxml")
            return self._parse_1x2_from_soup(soup)
        except OddsExtractionError as exc:
            logger.debug("Betfair headless extraction failed for 1X2: %s", exc)
            return []
        except Exception as exc:
            logger.debug("Betfair headless unexpected error for 1X2: %s", exc)
            return []

    def _extract_over_under_http(self, url: str) -> list[ScrapedMatch]:
        """Attempt to extract Over/Under odds via HTTP scraping.

        Args:
            url: Full URL to the Betfair sportsbook page.

        Returns:
            List of ScrapedMatch objects, may be empty if extraction fails.
        """
        try:
            soup = self._http_scraper.fetch_page(url, domain="betfair.com")
            return self._parse_over_under_from_soup(soup)
        except OddsExtractionError as exc:
            logger.debug("Betfair HTTP extraction failed for O/U: %s", exc)
            return []

    def _extract_over_under_headless(self, url: str) -> list[ScrapedMatch]:
        """Attempt to extract Over/Under odds via headless browser.

        Args:
            url: Full URL to the Betfair sportsbook page.

        Returns:
            List of ScrapedMatch objects, may be empty if extraction fails.
        """
        try:
            html = self._headless_scraper.fetch_rendered_page(
                url, wait_selector=_HEADLESS_WAIT_SELECTOR
            )
            soup = BeautifulSoup(html, "lxml")
            return self._parse_over_under_from_soup(soup)
        except OddsExtractionError as exc:
            logger.debug("Betfair headless extraction failed for O/U: %s", exc)
            return []
        except Exception as exc:
            logger.debug("Betfair headless unexpected error for O/U: %s", exc)
            return []

    def _parse_1x2_from_soup(self, soup: BeautifulSoup) -> list[ScrapedMatch]:
        """Parse 1X2 match winner odds from a BeautifulSoup document.

        Looks for event names and associated odds prices using Betfair's
        sportsbook CSS selectors. Groups odds into Home/Draw/Away outcomes.

        Args:
            soup: Parsed HTML document from Betfair sportsbook.

        Returns:
            List of ScrapedMatch with market_type='1x2'.
        """
        matches: list[ScrapedMatch] = []

        # Find event names
        event_elements = soup.select(_SELECTORS["event_name"])
        # Find all odds prices
        odds_elements = soup.select(_SELECTORS["odds_price"])

        if not event_elements or not odds_elements:
            return []

        # Betfair 1X2 market: 3 odds per event (Home, Draw, Away)
        odds_per_event = 3

        for i, event_el in enumerate(event_elements):
            event_text = event_el.get_text(strip=True)
            teams = self._parse_event_name(event_text)
            if not teams:
                continue

            home_team, away_team = teams

            # Get the 3 odds for this event
            start_idx = i * odds_per_event
            end_idx = start_idx + odds_per_event
            event_odds = odds_elements[start_idx:end_idx]

            if len(event_odds) < odds_per_event:
                continue

            try:
                home_odds = self._parse_odds_text(event_odds[0].get_text(strip=True))
                draw_odds = self._parse_odds_text(event_odds[1].get_text(strip=True))
                away_odds = self._parse_odds_text(event_odds[2].get_text(strip=True))
            except (ValueError, IndexError):
                continue

            match = ScrapedMatch(
                home_team=home_team,
                away_team=away_team,
                event_timestamp=datetime.now(timezone.utc),
                market_type="1x2",
                outcomes=[
                    MarketOutcome(name="Home", odds=home_odds),
                    MarketOutcome(name="Draw", odds=draw_odds),
                    MarketOutcome(name="Away", odds=away_odds),
                ],
            )
            matches.append(match)

        return matches

    def _parse_over_under_from_soup(self, soup: BeautifulSoup) -> list[ScrapedMatch]:
        """Parse Over/Under 2.5 goals odds from a BeautifulSoup document.

        Looks for event names and associated over/under odds prices using
        Betfair's sportsbook CSS selectors.

        Args:
            soup: Parsed HTML document from Betfair sportsbook.

        Returns:
            List of ScrapedMatch with market_type='over_under'.
        """
        matches: list[ScrapedMatch] = []

        # Find event names
        event_elements = soup.select(_SELECTORS["event_name"])
        # Find all odds prices
        odds_elements = soup.select(_SELECTORS["odds_price"])

        if not event_elements or not odds_elements:
            return []

        # Over/Under market: 2 odds per event (Over, Under)
        odds_per_event = 2

        for i, event_el in enumerate(event_elements):
            event_text = event_el.get_text(strip=True)
            teams = self._parse_event_name(event_text)
            if not teams:
                continue

            home_team, away_team = teams

            # Get the 2 odds for this event
            start_idx = i * odds_per_event
            end_idx = start_idx + odds_per_event
            event_odds = odds_elements[start_idx:end_idx]

            if len(event_odds) < odds_per_event:
                continue

            try:
                over_odds = self._parse_odds_text(event_odds[0].get_text(strip=True))
                under_odds = self._parse_odds_text(event_odds[1].get_text(strip=True))
            except (ValueError, IndexError):
                continue

            match = ScrapedMatch(
                home_team=home_team,
                away_team=away_team,
                event_timestamp=datetime.now(timezone.utc),
                market_type="over_under",
                outcomes=[
                    MarketOutcome(name="Over", odds=over_odds, point=2.5),
                    MarketOutcome(name="Under", odds=under_odds, point=2.5),
                ],
            )
            matches.append(match)

        return matches

    @staticmethod
    def _parse_event_name(text: str) -> tuple[str, str] | None:
        """Parse an event name into home and away teams.

        Supports common separators: ' v ', ' vs ', ' - '.

        Args:
            text: Event name text (e.g., 'Arsenal v Chelsea').

        Returns:
            Tuple of (home_team, away_team), or None if parsing fails.
        """
        for separator in (" v ", " vs ", " - "):
            if separator in text:
                parts = text.split(separator, maxsplit=1)
                if len(parts) == 2:
                    home = parts[0].strip()
                    away = parts[1].strip()
                    if home and away:
                        return (home, away)
        return None

    @staticmethod
    def _parse_odds_text(text: str) -> float:
        """Parse odds text into a decimal float value.

        Uses HTTPScraper.parse_odds_value for format handling but adds
        Betfair-specific validation (odds must be > 1.0 for valid markets).

        Args:
            text: Raw odds text from the page.

        Returns:
            Decimal odds as float.

        Raises:
            ValueError: If the text cannot be parsed or odds <= 1.0.
        """
        odds = HTTPScraper.parse_odds_value(text)
        if odds <= 1.0:
            raise ValueError(f"Invalid odds value (must be > 1.0): {odds}")
        return odds
