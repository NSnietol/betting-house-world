"""Unit tests for the Betfair bookmaker adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    OddsExtractionError,
)
from src.adapters.betfair import BetfairAdapter, _SPORT_URL_MAP


@pytest.fixture
def adapter() -> BetfairAdapter:
    """Create a BetfairAdapter with headless disabled for unit testing."""
    with patch("src.adapters.betfair.BetfairAdapter._init_headless", return_value=False):
        a = BetfairAdapter()
        a._headless_available = False
        a._headless_scraper = None
        return a


class TestBetfairAdapterProperties:
    """Tests for adapter metadata properties."""

    def test_bookmaker_id(self, adapter: BetfairAdapter) -> None:
        assert adapter.bookmaker_id == "betfair"

    def test_bookmaker_name(self, adapter: BetfairAdapter) -> None:
        assert adapter.bookmaker_name == "Betfair"

    def test_priority(self, adapter: BetfairAdapter) -> None:
        assert adapter.priority == 3

    def test_extraction_method(self, adapter: BetfairAdapter) -> None:
        assert adapter.extraction_method == ExtractionMethod.HEADLESS

    def test_base_url(self, adapter: BetfairAdapter) -> None:
        assert adapter.base_url == "https://www.betfair.com"


class TestBetfairAdapterBuildUrl:
    """Tests for URL construction."""

    def test_known_sport_key(self, adapter: BetfairAdapter) -> None:
        url = adapter._build_url("soccer_epl")
        assert url == "https://www.betfair.com/sport/football/english-premier-league"

    def test_la_liga_key(self, adapter: BetfairAdapter) -> None:
        url = adapter._build_url("soccer_spain_la_liga")
        assert url == "https://www.betfair.com/sport/football/spanish-la-liga"

    def test_unknown_sport_defaults_to_football(self, adapter: BetfairAdapter) -> None:
        url = adapter._build_url("soccer_unknown_league")
        assert url == "https://www.betfair.com/sport/football"


class TestBetfairParseEventName:
    """Tests for event name parsing."""

    def test_parse_with_v_separator(self) -> None:
        result = BetfairAdapter._parse_event_name("Arsenal v Chelsea")
        assert result == ("Arsenal", "Chelsea")

    def test_parse_with_vs_separator(self) -> None:
        result = BetfairAdapter._parse_event_name("Arsenal vs Chelsea")
        assert result == ("Arsenal", "Chelsea")

    def test_parse_with_dash_separator(self) -> None:
        result = BetfairAdapter._parse_event_name("Arsenal - Chelsea")
        assert result == ("Arsenal", "Chelsea")

    def test_parse_no_separator_returns_none(self) -> None:
        result = BetfairAdapter._parse_event_name("ArsenalChelsea")
        assert result is None

    def test_parse_empty_string_returns_none(self) -> None:
        result = BetfairAdapter._parse_event_name("")
        assert result is None

    def test_parse_strips_whitespace(self) -> None:
        result = BetfairAdapter._parse_event_name("  Arsenal  v  Chelsea  ")
        assert result == ("Arsenal", "Chelsea")


class TestBetfairParseOddsText:
    """Tests for odds text parsing."""

    def test_valid_decimal_odds(self) -> None:
        assert BetfairAdapter._parse_odds_text("2.50") == 2.50

    def test_valid_decimal_odds_high(self) -> None:
        assert BetfairAdapter._parse_odds_text("10.00") == 10.0

    def test_odds_equal_one_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 1.0"):
            BetfairAdapter._parse_odds_text("1.00")

    def test_odds_below_one_raises(self) -> None:
        with pytest.raises(ValueError, match="must be > 1.0"):
            BetfairAdapter._parse_odds_text("0.50")

    def test_invalid_text_raises(self) -> None:
        with pytest.raises(ValueError):
            BetfairAdapter._parse_odds_text("abc")


class TestBetfairParse1x2FromSoup:
    """Tests for 1X2 parsing from HTML."""

    def test_parses_valid_1x2_html(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">2.10</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.40</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.80</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_1x2_from_soup(soup)

        assert len(matches) == 1
        match = matches[0]
        assert match.home_team == "Arsenal"
        assert match.away_team == "Chelsea"
        assert match.market_type == "1x2"
        assert len(match.outcomes) == 3
        assert match.outcomes[0].name == "Home"
        assert match.outcomes[0].odds == pytest.approx(2.10)
        assert match.outcomes[1].name == "Draw"
        assert match.outcomes[1].odds == pytest.approx(3.40)
        assert match.outcomes[2].name == "Away"
        assert match.outcomes[2].odds == pytest.approx(3.80)

    def test_multiple_events(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <span class="event-name">Liverpool v Man City</span>
            <button class="bet-button-price"><span class="bet-button-price">2.10</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.40</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.80</span></button>
            <button class="bet-button-price"><span class="bet-button-price">2.80</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.20</span></button>
            <button class="bet-button-price"><span class="bet-button-price">2.60</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_1x2_from_soup(soup)

        assert len(matches) == 2
        assert matches[0].home_team == "Arsenal"
        assert matches[1].home_team == "Liverpool"

    def test_empty_html_returns_empty(self, adapter: BetfairAdapter) -> None:
        html = "<html><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_1x2_from_soup(soup)
        assert matches == []

    def test_missing_odds_skips_event(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">2.10</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_1x2_from_soup(soup)
        assert matches == []

    def test_invalid_odds_text_skips_event(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">abc</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.40</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.80</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_1x2_from_soup(soup)
        assert matches == []


class TestBetfairParseOverUnderFromSoup:
    """Tests for Over/Under parsing from HTML."""

    def test_parses_valid_over_under_html(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">1.85</span></button>
            <button class="bet-button-price"><span class="bet-button-price">2.05</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_over_under_from_soup(soup)

        assert len(matches) == 1
        match = matches[0]
        assert match.home_team == "Arsenal"
        assert match.away_team == "Chelsea"
        assert match.market_type == "over_under"
        assert len(match.outcomes) == 2
        assert match.outcomes[0].name == "Over"
        assert match.outcomes[0].odds == pytest.approx(1.85)
        assert match.outcomes[0].point == 2.5
        assert match.outcomes[1].name == "Under"
        assert match.outcomes[1].odds == pytest.approx(2.05)
        assert match.outcomes[1].point == 2.5

    def test_empty_html_returns_empty(self, adapter: BetfairAdapter) -> None:
        html = "<html><body></body></html>"
        soup = BeautifulSoup(html, "lxml")
        matches = adapter._parse_over_under_from_soup(soup)
        assert matches == []


class TestBetfairHealthCheck:
    """Tests for the health check method."""

    def test_reachable_on_success(self, adapter: BetfairAdapter) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        with patch("src.adapters.betfair.requests.head", return_value=mock_response):
            result = adapter.health_check()
        assert result == AdapterHealth.REACHABLE

    def test_reachable_on_redirect(self, adapter: BetfairAdapter) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 302
        with patch("src.adapters.betfair.requests.head", return_value=mock_response):
            result = adapter.health_check()
        assert result == AdapterHealth.REACHABLE

    def test_rate_limited_on_429(self, adapter: BetfairAdapter) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 429
        with patch("src.adapters.betfair.requests.head", return_value=mock_response):
            result = adapter.health_check()
        assert result == AdapterHealth.RATE_LIMITED

    def test_unreachable_on_server_error(self, adapter: BetfairAdapter) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 500
        with patch("src.adapters.betfair.requests.head", return_value=mock_response):
            result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE

    def test_unreachable_on_exception(self, adapter: BetfairAdapter) -> None:
        with patch(
            "src.adapters.betfair.requests.head",
            side_effect=Exception("Connection failed"),
        ):
            result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE


class TestBetfairFetch1x2:
    """Tests for the fetch_1x2 method with mocked scraper."""

    def test_returns_empty_when_http_fails_and_no_headless(
        self, adapter: BetfairAdapter
    ) -> None:
        with patch.object(
            adapter._http_scraper,
            "fetch_page",
            side_effect=OddsExtractionError("timeout"),
        ):
            result = adapter.fetch_1x2("soccer_epl")
        assert result == []

    def test_returns_matches_from_http(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">2.10</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.40</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.80</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        with patch.object(adapter._http_scraper, "fetch_page", return_value=soup):
            result = adapter.fetch_1x2("soccer_epl")
        assert len(result) == 1
        assert result[0].home_team == "Arsenal"


class TestBetfairFetchOverUnder:
    """Tests for the fetch_over_under method with mocked scraper."""

    def test_returns_empty_when_http_fails_and_no_headless(
        self, adapter: BetfairAdapter
    ) -> None:
        with patch.object(
            adapter._http_scraper,
            "fetch_page",
            side_effect=OddsExtractionError("timeout"),
        ):
            result = adapter.fetch_over_under("soccer_epl")
        assert result == []

    def test_returns_matches_from_http(self, adapter: BetfairAdapter) -> None:
        html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">1.85</span></button>
            <button class="bet-button-price"><span class="bet-button-price">2.05</span></button>
        </body></html>
        """
        soup = BeautifulSoup(html, "lxml")
        with patch.object(adapter._http_scraper, "fetch_page", return_value=soup):
            result = adapter.fetch_over_under("soccer_epl")
        assert len(result) == 1
        assert result[0].market_type == "over_under"


class TestBetfairHeadlessFallback:
    """Tests for headless browser fallback behavior."""

    def test_falls_back_to_headless_when_http_returns_empty(self) -> None:
        with patch("src.adapters.betfair.BetfairAdapter._init_headless", return_value=True):
            adapter = BetfairAdapter()
            adapter._headless_available = True

        mock_headless = MagicMock()
        rendered_html = """
        <html><body>
            <span class="event-name">Arsenal v Chelsea</span>
            <button class="bet-button-price"><span class="bet-button-price">2.10</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.40</span></button>
            <button class="bet-button-price"><span class="bet-button-price">3.80</span></button>
        </body></html>
        """
        mock_headless.fetch_rendered_page.return_value = rendered_html
        adapter._headless_scraper = mock_headless

        # HTTP returns empty page (no odds elements)
        empty_soup = BeautifulSoup("<html><body></body></html>", "lxml")
        with patch.object(adapter._http_scraper, "fetch_page", return_value=empty_soup):
            result = adapter.fetch_1x2("soccer_epl")

        assert len(result) == 1
        assert result[0].home_team == "Arsenal"
        mock_headless.fetch_rendered_page.assert_called_once()

    def test_graceful_when_headless_not_available(self) -> None:
        with patch("src.adapters.betfair.BetfairAdapter._init_headless", return_value=False):
            adapter = BetfairAdapter()
            adapter._headless_available = False
            adapter._headless_scraper = None

        empty_soup = BeautifulSoup("<html><body></body></html>", "lxml")
        with patch.object(adapter._http_scraper, "fetch_page", return_value=empty_soup):
            result = adapter.fetch_1x2("soccer_epl")

        assert result == []
