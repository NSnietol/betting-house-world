"""Unit tests for the HTTPScraper module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from bs4 import BeautifulSoup

from src.adapters.base import OddsExtractionError
from src.scraping.http_scraper import HTTPScraper
from src.scraping.resilience import RateLimiterConfig, ResilienceConfig


@pytest.fixture
def scraper() -> HTTPScraper:
    """Create an HTTPScraper with minimal delay for testing."""
    config = ResilienceConfig(
        rate_limiter=RateLimiterConfig(min_delay=0.01, jitter_pct=0.0),
        max_retries=1,
        backoff_base=1.0,
    )
    return HTTPScraper(config)


class TestParseOddsValue:
    """Tests for HTTPScraper.parse_odds_value."""

    def test_decimal_simple(self) -> None:
        assert HTTPScraper.parse_odds_value("2.50") == 2.50

    def test_decimal_integer(self) -> None:
        assert HTTPScraper.parse_odds_value("3") == 3.0

    def test_decimal_with_whitespace(self) -> None:
        assert HTTPScraper.parse_odds_value("  1.85  ") == 1.85

    def test_fractional_simple(self) -> None:
        result = HTTPScraper.parse_odds_value("3/2")
        assert result == pytest.approx(2.50)

    def test_fractional_evens(self) -> None:
        result = HTTPScraper.parse_odds_value("1/1")
        assert result == pytest.approx(2.0)

    def test_fractional_long_odds(self) -> None:
        result = HTTPScraper.parse_odds_value("10/1")
        assert result == pytest.approx(11.0)

    def test_american_positive(self) -> None:
        result = HTTPScraper.parse_odds_value("+150")
        assert result == pytest.approx(2.50)

    def test_american_positive_100(self) -> None:
        result = HTTPScraper.parse_odds_value("+100")
        assert result == pytest.approx(2.0)

    def test_american_negative(self) -> None:
        result = HTTPScraper.parse_odds_value("-200")
        assert result == pytest.approx(1.50)

    def test_american_negative_100(self) -> None:
        result = HTTPScraper.parse_odds_value("-100")
        assert result == pytest.approx(2.0)

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty odds value"):
            HTTPScraper.parse_odds_value("")

    def test_whitespace_only_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty odds value"):
            HTTPScraper.parse_odds_value("   ")

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unrecognized odds format"):
            HTTPScraper.parse_odds_value("abc")

    def test_fractional_zero_denominator_raises(self) -> None:
        with pytest.raises(ValueError, match="zero denominator"):
            HTTPScraper.parse_odds_value("3/0")

    def test_american_negative_zero_raises(self) -> None:
        with pytest.raises(ValueError, match="zero"):
            HTTPScraper.parse_odds_value("-0")


class TestExtractByCSS:
    """Tests for HTTPScraper.extract_by_css."""

    def test_simple_extraction(self, scraper: HTTPScraper) -> None:
        html = """
        <html>
        <body>
            <span class="home-odds">1.85</span>
            <span class="draw-odds">3.40</span>
            <span class="away-odds">4.50</span>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        selectors = {
            "home": "span.home-odds",
            "draw": "span.draw-odds",
            "away": "span.away-odds",
        }
        result = scraper.extract_by_css(soup, selectors)
        assert len(result) == 1
        assert result[0]["home"] == "1.85"
        assert result[0]["draw"] == "3.40"
        assert result[0]["away"] == "4.50"

    def test_multiple_rows(self, scraper: HTTPScraper) -> None:
        html = """
        <html>
        <body>
            <span class="odds">1.85</span>
            <span class="odds">3.40</span>
            <span class="odds">4.50</span>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        selectors = {"value": "span.odds"}
        result = scraper.extract_by_css(soup, selectors)
        assert len(result) == 3
        assert result[0]["value"] == "1.85"
        assert result[1]["value"] == "3.40"
        assert result[2]["value"] == "4.50"

    def test_no_matches_returns_empty(self, scraper: HTTPScraper) -> None:
        html = "<html><body><p>No odds here</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        selectors = {"value": "span.odds"}
        result = scraper.extract_by_css(soup, selectors)
        assert result == []

    def test_empty_selectors_returns_empty(self, scraper: HTTPScraper) -> None:
        html = "<html><body><span>1.50</span></body></html>"
        soup = BeautifulSoup(html, "lxml")
        result = scraper.extract_by_css(soup, {})
        assert result == []

    def test_uneven_matches_grouped(self, scraper: HTTPScraper) -> None:
        html = """
        <html>
        <body>
            <span class="team">Team A</span>
            <span class="team">Team B</span>
            <span class="odds">1.85</span>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        selectors = {"team": "span.team", "odds": "span.odds"}
        result = scraper.extract_by_css(soup, selectors)
        assert len(result) == 2
        assert result[0]["team"] == "Team A"
        assert result[0]["odds"] == "1.85"
        assert result[1]["team"] == "Team B"
        assert "odds" not in result[1]


class TestExtractByXPath:
    """Tests for HTTPScraper.extract_by_xpath."""

    def test_simple_xpath_extraction(self, scraper: HTTPScraper) -> None:
        html = """
        <html>
        <body>
            <div class="match">
                <span class="home">1.85</span>
                <span class="draw">3.40</span>
                <span class="away">4.50</span>
            </div>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        expressions = {
            "home": "//span[@class='home']",
            "draw": "//span[@class='draw']",
            "away": "//span[@class='away']",
        }
        result = scraper.extract_by_xpath(soup, expressions)
        assert len(result) == 1
        assert result[0]["home"] == "1.85"
        assert result[0]["draw"] == "3.40"
        assert result[0]["away"] == "4.50"

    def test_xpath_text_extraction(self, scraper: HTTPScraper) -> None:
        html = """
        <html>
        <body>
            <div class="odds-container">
                <span>2.10</span>
                <span>3.20</span>
            </div>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        expressions = {"value": "//div[@class='odds-container']/span"}
        result = scraper.extract_by_xpath(soup, expressions)
        assert len(result) == 2
        assert result[0]["value"] == "2.10"
        assert result[1]["value"] == "3.20"

    def test_xpath_no_matches(self, scraper: HTTPScraper) -> None:
        html = "<html><body><p>Nothing</p></body></html>"
        soup = BeautifulSoup(html, "lxml")
        expressions = {"value": "//span[@class='missing']"}
        result = scraper.extract_by_xpath(soup, expressions)
        assert result == []

    def test_xpath_empty_expressions(self, scraper: HTTPScraper) -> None:
        html = "<html><body><span>1.50</span></body></html>"
        soup = BeautifulSoup(html, "lxml")
        result = scraper.extract_by_xpath(soup, {})
        assert result == []

    def test_xpath_text_node(self, scraper: HTTPScraper) -> None:
        html = """
        <html>
        <body>
            <span class="odds">2.50</span>
        </body>
        </html>
        """
        soup = BeautifulSoup(html, "lxml")
        expressions = {"value": "//span[@class='odds']/text()"}
        result = scraper.extract_by_xpath(soup, expressions)
        assert len(result) == 1
        assert result[0]["value"] == "2.50"


class TestFetchPage:
    """Tests for HTTPScraper.fetch_page (mocked HTTP)."""

    @patch("src.scraping.http_scraper.requests.get")
    def test_successful_fetch(self, mock_get: MagicMock, scraper: HTTPScraper) -> None:
        mock_response = MagicMock()
        mock_response.text = "<html><body><p>Hello</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = scraper.fetch_page("https://example.com/odds", "example.com")
        assert isinstance(result, BeautifulSoup)
        assert result.find("p").get_text() == "Hello"

    @patch("src.scraping.http_scraper.requests.get")
    def test_http_error_raises_extraction_error(
        self, mock_get: MagicMock, scraper: HTTPScraper
    ) -> None:
        import requests as req

        mock_response = MagicMock()
        mock_response.status_code = 403
        http_error = req.exceptions.HTTPError(response=mock_response)
        mock_response.raise_for_status.side_effect = http_error
        mock_get.return_value = mock_response

        with pytest.raises(OddsExtractionError) as exc_info:
            scraper.fetch_page("https://example.com/odds", "example.com")
        assert exc_info.value.status_code == 403

    @patch("src.scraping.http_scraper.requests.get")
    def test_timeout_raises_extraction_error(
        self, mock_get: MagicMock, scraper: HTTPScraper
    ) -> None:
        import requests as req

        mock_get.side_effect = req.exceptions.Timeout("Connection timed out")

        with pytest.raises(OddsExtractionError) as exc_info:
            scraper.fetch_page("https://example.com/odds", "example.com")
        assert exc_info.value.status_code is None

    @patch("src.scraping.http_scraper.requests.get")
    def test_uses_30s_timeout(self, mock_get: MagicMock, scraper: HTTPScraper) -> None:
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        scraper.fetch_page("https://example.com", "example.com")

        _, kwargs = mock_get.call_args
        assert kwargs["timeout"] == 30

    @patch("src.scraping.http_scraper.requests.get")
    def test_sets_user_agent_header(
        self, mock_get: MagicMock, scraper: HTTPScraper
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "<html></html>"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        scraper.fetch_page("https://example.com", "example.com")

        _, kwargs = mock_get.call_args
        assert "User-Agent" in kwargs["headers"]
        assert "Mozilla" in kwargs["headers"]["User-Agent"]
