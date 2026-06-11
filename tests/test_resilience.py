"""Unit tests for the scraping resilience module."""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.adapters.base import OddsExtractionError
from src.scraping.resilience import (
    RateLimiter,
    RateLimiterConfig,
    ResilienceConfig,
    RetryHandler,
    UserAgentRotator,
)


class TestRateLimiterConfig:
    """Tests for RateLimiterConfig dataclass."""

    def test_default_values(self) -> None:
        config = RateLimiterConfig()
        assert config.min_delay == 3.0
        assert config.max_delay == 30.0
        assert config.jitter_pct == 0.30

    def test_custom_values(self) -> None:
        config = RateLimiterConfig(min_delay=5.0, max_delay=60.0, jitter_pct=0.10)
        assert config.min_delay == 5.0
        assert config.max_delay == 60.0
        assert config.jitter_pct == 0.10


class TestResilienceConfig:
    """Tests for ResilienceConfig dataclass."""

    def test_default_values(self) -> None:
        config = ResilienceConfig()
        assert config.max_retries == 3
        assert config.backoff_base == 2.0
        assert config.ua_pool_size == 10
        assert isinstance(config.rate_limiter, RateLimiterConfig)

    def test_proxy_from_env_variable(self) -> None:
        with patch.dict(os.environ, {"SCRAPER_PROXY": "http://proxy:8080"}):
            config = ResilienceConfig()
            assert config.proxy == "http://proxy:8080"

    def test_proxy_none_when_env_not_set(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = ResilienceConfig()
            assert config.proxy is None

    def test_explicit_proxy_not_overridden_by_env(self) -> None:
        with patch.dict(os.environ, {"SCRAPER_PROXY": "http://env:8080"}):
            config = ResilienceConfig(proxy="http://explicit:9090")
            assert config.proxy == "http://explicit:9090"

    def test_empty_env_proxy_treated_as_none(self) -> None:
        with patch.dict(os.environ, {"SCRAPER_PROXY": ""}):
            config = ResilienceConfig()
            assert config.proxy is None


class TestRateLimiter:
    """Tests for RateLimiter class."""

    def test_first_request_no_wait(self) -> None:
        config = RateLimiterConfig(min_delay=3.0)
        limiter = RateLimiter(config)
        start = time.monotonic()
        limiter.wait("example.com")
        elapsed = time.monotonic() - start
        assert elapsed < 0.1  # should be near-instant

    def test_compute_delay_within_jitter_bounds(self) -> None:
        config = RateLimiterConfig(min_delay=3.0, max_delay=30.0, jitter_pct=0.30)
        limiter = RateLimiter(config)
        for _ in range(200):
            delay = limiter._compute_delay()
            # min_delay * (1 - jitter) = 3.0 * 0.7 = 2.1
            # min_delay * (1 + jitter) = 3.0 * 1.3 = 3.9
            assert 2.1 <= delay <= 3.9, f"Delay {delay} outside expected range"

    def test_compute_delay_respects_max_delay(self) -> None:
        config = RateLimiterConfig(min_delay=28.0, max_delay=30.0, jitter_pct=0.30)
        limiter = RateLimiter(config)
        for _ in range(100):
            delay = limiter._compute_delay()
            assert delay <= 30.0

    def test_compute_delay_minimum_floor(self) -> None:
        config = RateLimiterConfig(min_delay=0.05, max_delay=30.0, jitter_pct=0.90)
        limiter = RateLimiter(config)
        for _ in range(100):
            delay = limiter._compute_delay()
            assert delay >= 0.1

    def test_different_domains_tracked_independently(self) -> None:
        config = RateLimiterConfig(min_delay=0.05, jitter_pct=0.0)
        limiter = RateLimiter(config)
        limiter.wait("domain-a.com")
        limiter.wait("domain-b.com")  # Should not wait for domain-a's cooldown

    @patch("src.scraping.resilience.time.sleep")
    def test_second_request_sleeps_remaining_time(self, mock_sleep: MagicMock) -> None:
        config = RateLimiterConfig(min_delay=3.0, jitter_pct=0.0)
        limiter = RateLimiter(config)
        limiter.wait("test.com")
        # Simulate immediate second call
        limiter.wait("test.com")
        # Should have slept since 0% jitter means exactly 3.0 delay
        mock_sleep.assert_called()


class TestUserAgentRotator:
    """Tests for UserAgentRotator class."""

    def test_pool_has_at_least_10_agents(self) -> None:
        rotator = UserAgentRotator()
        assert rotator.pool_size >= 10

    def test_sequential_cycling(self) -> None:
        rotator = UserAgentRotator()
        agents = [rotator.next() for _ in range(rotator.pool_size)]
        assert agents == UserAgentRotator._AGENTS

    def test_wraps_around_after_full_cycle(self) -> None:
        rotator = UserAgentRotator()
        # Exhaust one full cycle
        for _ in range(rotator.pool_size):
            rotator.next()
        # Next should be the first agent again
        assert rotator.next() == UserAgentRotator._AGENTS[0]

    def test_all_agents_are_unique(self) -> None:
        assert len(set(UserAgentRotator._AGENTS)) == len(UserAgentRotator._AGENTS)

    def test_all_agents_contain_mozilla(self) -> None:
        for ua in UserAgentRotator._AGENTS:
            assert "Mozilla/5.0" in ua

    def test_covers_multiple_browsers(self) -> None:
        agents_str = " ".join(UserAgentRotator._AGENTS)
        assert "Chrome" in agents_str
        assert "Firefox" in agents_str
        assert "Safari" in agents_str
        assert "Edg" in agents_str


class TestRetryHandler:
    """Tests for RetryHandler class."""

    def test_success_on_first_try(self) -> None:
        handler = RetryHandler(max_retries=3)
        result = handler.execute(lambda: 42)
        assert result == 42

    def test_retries_on_connection_reset(self) -> None:
        call_count = [0]

        def flaky():
            call_count[0] += 1
            if call_count[0] < 3:
                raise ConnectionResetError("reset")
            return "ok"

        handler = RetryHandler(max_retries=3, backoff_base=1.01)
        result = handler.execute(flaky)
        assert result == "ok"
        assert call_count[0] == 3

    def test_raises_after_max_retries_exhausted(self) -> None:
        handler = RetryHandler(max_retries=3, backoff_base=1.01)

        def always_fails():
            raise ConnectionResetError("always fails")

        with pytest.raises(OddsExtractionError, match="retries exhausted"):
            handler.execute(always_fails)

    def test_retries_on_http_5xx(self) -> None:
        call_count = [0]

        def server_error():
            call_count[0] += 1
            if call_count[0] < 2:
                response = MagicMock()
                response.status_code = 503
                exc = requests.exceptions.HTTPError(response=response)
                exc.response = response
                raise exc
            return "recovered"

        handler = RetryHandler(max_retries=3, backoff_base=1.01)
        result = handler.execute(server_error)
        assert result == "recovered"
        assert call_count[0] == 2

    def test_no_retry_on_http_4xx(self) -> None:
        call_count = [0]

        def client_error():
            call_count[0] += 1
            response = MagicMock()
            response.status_code = 404
            exc = requests.exceptions.HTTPError(response=response)
            exc.response = response
            raise exc

        handler = RetryHandler(max_retries=3)
        with pytest.raises(OddsExtractionError):
            handler.execute(client_error)
        assert call_count[0] == 1  # No retry for 4xx

    def test_respects_retry_after_header_on_429(self) -> None:
        call_count = [0]

        def rate_limited():
            call_count[0] += 1
            if call_count[0] == 1:
                response = MagicMock()
                response.status_code = 429
                response.headers = {"Retry-After": "0.1"}
                exc = requests.exceptions.HTTPError(response=response)
                exc.response = response
                raise exc
            return "success"

        handler = RetryHandler(max_retries=3, backoff_base=1.01)
        result = handler.execute(rate_limited)
        assert result == "success"
        assert call_count[0] == 2

    def test_retries_on_timeout(self) -> None:
        call_count = [0]

        def timeout_fn():
            call_count[0] += 1
            if call_count[0] < 2:
                raise requests.exceptions.ConnectTimeout("timed out")
            return "done"

        handler = RetryHandler(max_retries=3, backoff_base=1.01)
        result = handler.execute(timeout_fn)
        assert result == "done"
        assert call_count[0] == 2

    def test_backoff_sleep_called_with_increasing_delays(self) -> None:
        delays = []

        def always_resets():
            raise ConnectionResetError("reset")

        handler = RetryHandler(max_retries=3, backoff_base=2.0)

        with patch("src.scraping.resilience.time.sleep") as mock_sleep:
            mock_sleep.side_effect = lambda d: delays.append(d)
            with pytest.raises(OddsExtractionError):
                handler.execute(always_resets)

        assert len(delays) == 3
        # backoff_base^1 = 2, ^2 = 4, ^3 = 8 (±30% jitter)
        assert 1.4 <= delays[0] <= 2.6  # 2 ± 30%
        assert 2.8 <= delays[1] <= 5.2  # 4 ± 30%
        assert 5.6 <= delays[2] <= 10.4  # 8 ± 30%

    def test_429_default_wait_60s_when_no_header(self) -> None:
        response = MagicMock()
        response.status_code = 429
        response.headers = {}

        wait = RetryHandler._get_retry_after(response)
        assert wait == 60.0

    def test_429_parses_numeric_retry_after(self) -> None:
        response = MagicMock()
        response.headers = {"Retry-After": "120"}

        wait = RetryHandler._get_retry_after(response)
        assert wait == 120.0
