"""HTTP-based extraction using requests + BeautifulSoup.

Provides the HTTPScraper class for fetching web pages with resilience
(rate limiting, UA rotation, retries) and extracting odds data using
CSS selectors or XPath expressions.
"""
from __future__ import annotations

import re

import requests
from bs4 import BeautifulSoup
from lxml import etree

from src.adapters.base import OddsExtractionError
from src.scraping.resilience import (
    RateLimiter,
    ResilienceConfig,
    RetryHandler,
    UserAgentRotator,
)


class HTTPScraper:
    """HTTP-based extraction using requests + BeautifulSoup.

    Combines rate limiting, user-agent rotation, and retry logic to
    fetch bookmaker pages reliably, then provides CSS and XPath
    extraction utilities plus odds format parsing.
    """

    def __init__(self, resilience: ResilienceConfig) -> None:
        """Initialize with resilience configuration.

        Args:
            resilience: Configuration for rate limiting, retries, UA rotation,
                and optional proxy.
        """
        self._config = resilience
        self._rate_limiter = RateLimiter(resilience.rate_limiter)
        self._ua_rotator = UserAgentRotator()
        self._retry_handler = RetryHandler(
            max_retries=resilience.max_retries,
            backoff_base=resilience.backoff_base,
        )

    def fetch_page(self, url: str, domain: str) -> BeautifulSoup:
        """Fetch a URL respecting rate limits and UA rotation.

        Waits for the domain rate limiter, sets a rotated User-Agent header,
        and uses the retry handler to wrap the HTTP GET request. On success,
        returns a parsed BeautifulSoup document.

        Args:
            url: Full URL to fetch.
            domain: Domain key for rate limiting (e.g., 'pinnacle.com').

        Returns:
            Parsed BeautifulSoup document using the lxml parser.

        Raises:
            OddsExtractionError: On timeout, HTTP error after retries.
        """
        self._rate_limiter.wait(domain)

        def _do_request() -> requests.Response:
            headers = {"User-Agent": self._ua_rotator.next()}
            proxies = None
            if self._config.proxy:
                proxies = {"http": self._config.proxy, "https": self._config.proxy}

            response = requests.get(
                url,
                headers=headers,
                timeout=30,
                proxies=proxies,
            )
            response.raise_for_status()
            return response

        try:
            response = self._retry_handler.execute(_do_request)
        except OddsExtractionError:
            raise
        except requests.exceptions.HTTPError as exc:
            status_code = exc.response.status_code if exc.response is not None else None
            raise OddsExtractionError(
                message=f"HTTP error fetching {url}: {exc}",
                status_code=status_code,
            ) from exc
        except (requests.exceptions.Timeout, requests.exceptions.ConnectTimeout) as exc:
            raise OddsExtractionError(
                message=f"Timeout fetching {url}: {exc}",
                status_code=None,
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise OddsExtractionError(
                message=f"Request failed for {url}: {exc}",
                status_code=None,
            ) from exc

        return BeautifulSoup(response.text, "lxml")

    def extract_by_css(
        self, soup: BeautifulSoup, selectors: dict[str, str]
    ) -> list[dict]:
        """Extract elements using CSS selectors.

        For each selector, finds all matching elements. Results are grouped
        by index position: the first match of each selector forms one row,
        the second match of each forms the next row, etc.

        Args:
            soup: Parsed HTML document.
            selectors: Mapping of field names to CSS selectors.

        Returns:
            List of dicts with field names as keys and element text as values.
            Each dict represents one row (grouped by index position).
        """
        field_results: dict[str, list[str]] = {}

        for field_name, selector in selectors.items():
            elements = soup.select(selector)
            field_results[field_name] = [el.get_text(strip=True) for el in elements]

        if not field_results:
            return []

        # Determine the number of rows from the maximum matches found
        max_rows = max(len(vals) for vals in field_results.values()) if field_results else 0

        rows: list[dict] = []
        for i in range(max_rows):
            row: dict[str, str] = {}
            for field_name, values in field_results.items():
                if i < len(values):
                    row[field_name] = values[i]
            rows.append(row)

        return rows

    def extract_by_xpath(
        self, soup: BeautifulSoup, expressions: dict[str, str]
    ) -> list[dict]:
        """Extract elements using XPath expressions via lxml.

        Converts the BeautifulSoup soup to an lxml etree for XPath support,
        then evaluates each expression to extract text values. Results are
        grouped by index position similar to extract_by_css.

        Args:
            soup: Parsed HTML document.
            expressions: Mapping of field names to XPath expressions.

        Returns:
            List of dicts with field names as keys and element text as values.
        """
        # Convert BeautifulSoup to lxml etree
        html_string = str(soup)
        tree = etree.HTML(html_string)

        field_results: dict[str, list[str]] = {}

        for field_name, xpath_expr in expressions.items():
            elements = tree.xpath(xpath_expr)
            values: list[str] = []
            for el in elements:
                if isinstance(el, str):
                    values.append(el.strip())
                elif hasattr(el, "text") and el.text:
                    # Get full text content including children
                    text = "".join(el.itertext()).strip()
                    values.append(text)
                else:
                    # Element with no direct text — get all nested text
                    text = "".join(el.itertext()).strip() if hasattr(el, "itertext") else ""
                    values.append(text)
            field_results[field_name] = values

        if not field_results:
            return []

        max_rows = max(len(vals) for vals in field_results.values()) if field_results else 0

        rows: list[dict] = []
        for i in range(max_rows):
            row: dict[str, str] = {}
            for field_name, values in field_results.items():
                if i < len(values):
                    row[field_name] = values[i]
            rows.append(row)

        return rows

    @staticmethod
    def parse_odds_value(raw: str) -> float:
        """Parse a raw odds string into decimal format.

        Handles three formats:
        - Decimal: '2.50' → 2.50
        - Fractional: '3/2' → (3/2) + 1 = 2.50
        - American positive: '+150' → (150/100) + 1 = 2.50
        - American negative: '-200' → (100/200) + 1 = 1.50

        Args:
            raw: Raw odds text from the page.

        Returns:
            Decimal odds as float.

        Raises:
            ValueError: If the format is unrecognizable.
        """
        text = raw.strip()

        if not text:
            raise ValueError(f"Empty odds value: '{raw}'")

        # American positive: +150
        if text.startswith("+"):
            try:
                value = float(text[1:])
                return (value / 100.0) + 1.0
            except ValueError:
                raise ValueError(f"Invalid American positive odds: '{raw}'")

        # American negative: -200
        if text.startswith("-"):
            try:
                value = float(text[1:])
                if value == 0:
                    raise ValueError(f"Invalid American negative odds (zero): '{raw}'")
                return (100.0 / value) + 1.0
            except ValueError as exc:
                if "Invalid American" in str(exc):
                    raise
                raise ValueError(f"Invalid American negative odds: '{raw}'")

        # Fractional: 3/2
        if "/" in text:
            parts = text.split("/")
            if len(parts) != 2:
                raise ValueError(f"Invalid fractional odds: '{raw}'")
            try:
                numerator = float(parts[0])
                denominator = float(parts[1])
                if denominator == 0:
                    raise ValueError(f"Invalid fractional odds (zero denominator): '{raw}'")
                return (numerator / denominator) + 1.0
            except ValueError as exc:
                if "Invalid fractional" in str(exc):
                    raise
                raise ValueError(f"Invalid fractional odds: '{raw}'")

        # Decimal: 2.50
        try:
            value = float(text)
            return value
        except ValueError:
            raise ValueError(f"Unrecognized odds format: '{raw}'")
