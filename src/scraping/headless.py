"""Headless browser scraper using Playwright (optional dependency)."""
from __future__ import annotations

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
    _PLAYWRIGHT_AVAILABLE = True
except ImportError:
    _PLAYWRIGHT_AVAILABLE = False

from src.adapters.base import OddsExtractionError


class HeadlessScraper:
    """Playwright-based headless browser extraction (optional dependency).

    Provides a shared browser instance for rendering JavaScript-heavy
    bookmaker pages. Playwright is an optional dependency — if not
    installed, instantiation raises ImportError with a clear message.

    Example:
        scraper = HeadlessScraper(timeout=30_000)
        html = scraper.fetch_rendered_page(
            "https://example.com/odds",
            wait_selector=".odds-table"
        )
        scraper.close()
    """

    def __init__(self, timeout: int = 60_000) -> None:
        """Initialize the headless scraper.

        Args:
            timeout: Default page load and selector wait timeout in
                milliseconds. Defaults to 60000 (60 seconds).

        Raises:
            ImportError: If the playwright package is not installed.
        """
        if not _PLAYWRIGHT_AVAILABLE:
            raise ImportError(
                "Playwright is not installed. Install it with "
                "'pip install playwright' and run 'playwright install chromium' "
                "to enable headless browser scraping."
            )
        self._timeout = timeout
        self._playwright = None
        self._browser = None

    def fetch_rendered_page(self, url: str, wait_selector: str) -> str:
        """Load a page in headless Chromium, wait for a selector, return HTML.

        Lazily starts a shared browser instance on the first call. Each
        invocation opens a new page (tab) and closes it after extracting
        the rendered HTML content.

        Args:
            url: Page URL to load.
            wait_selector: CSS selector to wait for before extracting.

        Returns:
            Rendered HTML string of the full page.

        Raises:
            OddsExtractionError: On timeout or page load failure.
        """
        self._ensure_browser()
        page = None
        try:
            page = self._browser.new_page()
            page.goto(url, timeout=self._timeout)
            page.wait_for_selector(wait_selector, timeout=self._timeout)
            html = page.content()
            return html
        except PlaywrightTimeout as exc:
            raise OddsExtractionError(
                f"Timeout waiting for selector '{wait_selector}' on {url}",
            ) from exc
        except Exception as exc:
            raise OddsExtractionError(
                f"Failed to load page {url}: {exc}",
            ) from exc
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def close(self) -> None:
        """Close the shared browser instance and Playwright context.

        Resets internal state so a new browser can be started on the
        next call to fetch_rendered_page if needed.
        """
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:
                pass
            self._playwright = None

    def _ensure_browser(self) -> None:
        """Lazily start the Playwright browser if not already running."""
        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(headless=True)
