"""Tests for the headless browser scraper module."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import OddsExtractionError


class TestHeadlessScraperImportError:
    """Tests for behavior when Playwright is not installed."""

    def test_raises_import_error_when_playwright_unavailable(self):
        """HeadlessScraper raises ImportError if playwright is not installed."""
        import src.scraping.headless as headless_module

        original_flag = headless_module._PLAYWRIGHT_AVAILABLE
        try:
            headless_module._PLAYWRIGHT_AVAILABLE = False
            with pytest.raises(ImportError, match="Playwright is not installed"):
                headless_module.HeadlessScraper()
        finally:
            headless_module._PLAYWRIGHT_AVAILABLE = original_flag

    def test_import_error_message_contains_install_instructions(self):
        """ImportError message includes installation instructions."""
        import src.scraping.headless as headless_module

        original_flag = headless_module._PLAYWRIGHT_AVAILABLE
        try:
            headless_module._PLAYWRIGHT_AVAILABLE = False
            with pytest.raises(ImportError, match="pip install playwright"):
                headless_module.HeadlessScraper()
        finally:
            headless_module._PLAYWRIGHT_AVAILABLE = original_flag


class TestHeadlessScraperWithMockedPlaywright:
    """Tests for HeadlessScraper behavior with mocked Playwright."""

    def _make_scraper(self, timeout: int = 5000):
        """Create a HeadlessScraper with _PLAYWRIGHT_AVAILABLE forced True."""
        import src.scraping.headless as headless_module

        original_flag = headless_module._PLAYWRIGHT_AVAILABLE
        headless_module._PLAYWRIGHT_AVAILABLE = True
        scraper = headless_module.HeadlessScraper(timeout=timeout)
        headless_module._PLAYWRIGHT_AVAILABLE = original_flag
        return scraper

    def _setup_mock_playwright(self):
        """Create mock playwright objects."""
        mock_pw_context = MagicMock()
        mock_browser = MagicMock()
        mock_page = MagicMock()

        mock_pw_context.chromium.launch.return_value = mock_browser
        mock_browser.new_page.return_value = mock_page
        mock_page.content.return_value = "<html><body>Odds</body></html>"

        return mock_pw_context, mock_browser, mock_page

    def test_init_does_not_start_browser(self):
        """Browser is not started during __init__ (lazy initialization)."""
        scraper = self._make_scraper()
        assert scraper._browser is None
        assert scraper._playwright is None

    def test_timeout_stored_correctly(self):
        """Configured timeout is stored on the instance."""
        scraper = self._make_scraper(timeout=5000)
        assert scraper._timeout == 5000

    def test_default_timeout_is_60_seconds(self):
        """Default timeout is 60000ms."""
        import src.scraping.headless as headless_module

        original_flag = headless_module._PLAYWRIGHT_AVAILABLE
        headless_module._PLAYWRIGHT_AVAILABLE = True
        scraper = headless_module.HeadlessScraper()
        headless_module._PLAYWRIGHT_AVAILABLE = original_flag
        assert scraper._timeout == 60_000

    def test_fetch_rendered_page_starts_browser_lazily(self):
        """First call to fetch_rendered_page starts the browser."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            html = scraper.fetch_rendered_page("https://example.com", ".odds")

        mock_sync_pw.return_value.start.assert_called_once()
        mock_pw_context.chromium.launch.assert_called_once_with(headless=True)
        assert html == "<html><body>Odds</body></html>"

    def test_fetch_rendered_page_reuses_browser(self):
        """Subsequent calls reuse the same browser instance."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            scraper.fetch_rendered_page("https://example.com/1", ".a")
            scraper.fetch_rendered_page("https://example.com/2", ".b")

        # Browser launched only once
        mock_pw_context.chromium.launch.assert_called_once()
        # But new_page called twice (one per request)
        assert mock_browser.new_page.call_count == 2

    def test_fetch_rendered_page_closes_page_after_extraction(self):
        """Each page (tab) is closed after HTML is extracted."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            scraper.fetch_rendered_page("https://example.com", ".sel")

        mock_page.close.assert_called_once()

    def test_fetch_rendered_page_raises_on_timeout(self):
        """OddsExtractionError raised on Playwright timeout."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        # Simulate a playwright timeout on wait_for_selector
        # We need to create a mock timeout exception class
        timeout_exc = TimeoutError("Timeout 60000ms exceeded")
        mock_page.wait_for_selector.side_effect = timeout_exc

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        # Patch PlaywrightTimeout to be TimeoutError so it catches properly
        import src.scraping.headless as headless_module
        original_timeout = getattr(headless_module, "PlaywrightTimeout", None)
        headless_module.PlaywrightTimeout = TimeoutError

        try:
            with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
                with pytest.raises(OddsExtractionError, match="Timeout waiting for selector"):
                    scraper.fetch_rendered_page("https://example.com", ".odds-table")
        finally:
            if original_timeout is not None:
                headless_module.PlaywrightTimeout = original_timeout

        # Page should still be closed in finally block
        mock_page.close.assert_called_once()

    def test_fetch_rendered_page_raises_on_generic_error(self):
        """OddsExtractionError raised on generic page load failure."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()
        mock_page.goto.side_effect = RuntimeError("Network error")

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            with pytest.raises(OddsExtractionError, match="Failed to load page"):
                scraper.fetch_rendered_page("https://example.com", ".sel")

    def test_close_resets_state(self):
        """close() shuts down browser and playwright, resets to None."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            scraper.fetch_rendered_page("https://example.com", ".x")

        scraper.close()

        assert scraper._browser is None
        assert scraper._playwright is None
        mock_browser.close.assert_called_once()
        mock_pw_context.stop.assert_called_once()

    def test_close_without_browser_started_is_safe(self):
        """Calling close() before any fetch does not raise."""
        scraper = self._make_scraper()
        # Should not raise
        scraper.close()
        assert scraper._browser is None
        assert scraper._playwright is None

    def test_browser_can_be_restarted_after_close(self):
        """After close(), fetch_rendered_page can start a new browser."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper()

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            scraper.fetch_rendered_page("https://example.com", ".a")
            scraper.close()

            # After close, internal state is None, so next fetch starts fresh
            assert scraper._browser is None
            assert scraper._playwright is None

            # Reset mock launch call count to verify it's called again
            mock_pw_context.chromium.launch.reset_mock()

            scraper.fetch_rendered_page("https://example.com", ".b")

            # Browser was launched again
            mock_pw_context.chromium.launch.assert_called_once_with(headless=True)

    def test_fetch_passes_timeout_to_goto_and_wait(self):
        """The configured timeout is passed to page.goto and wait_for_selector."""
        mock_pw_context, mock_browser, mock_page = self._setup_mock_playwright()

        mock_sync_pw = MagicMock()
        mock_sync_pw.return_value.start.return_value = mock_pw_context

        scraper = self._make_scraper(timeout=5000)

        with patch("src.scraping.headless.sync_playwright", mock_sync_pw, create=True):
            scraper.fetch_rendered_page("https://example.com", ".sel")

        mock_page.goto.assert_called_once_with("https://example.com", timeout=5000)
        mock_page.wait_for_selector.assert_called_once_with(".sel", timeout=5000)
