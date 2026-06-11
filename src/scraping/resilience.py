"""Resilience primitives for the scraping layer.

Provides rate limiting, user-agent rotation, and retry logic with
exponential backoff to handle transient failures gracefully.
"""
from __future__ import annotations

import os
import random
import time
from dataclasses import dataclass, field

import requests

from src.adapters.base import OddsExtractionError


@dataclass
class RateLimiterConfig:
    """Per-domain rate limiting configuration.

    Attributes:
        min_delay: Minimum seconds between requests to the same domain.
        max_delay: Upper bound on delay (caps jitter-inflated values).
        jitter_pct: Fraction of min_delay used as ±randomization (0.30 = ±30%).
    """

    min_delay: float = 3.0
    max_delay: float = 30.0
    jitter_pct: float = 0.30


@dataclass
class ResilienceConfig:
    """Full resilience configuration for the scraping layer.

    Attributes:
        rate_limiter: Per-domain rate limiting settings.
        max_retries: Maximum number of retry attempts for transient errors.
        backoff_base: Base for exponential backoff calculation (2^i seconds).
        ua_pool_size: Number of user-agent strings in the rotation pool.
        proxy: Optional proxy URL (read from SCRAPER_PROXY env var if not set).
    """

    rate_limiter: RateLimiterConfig = field(default_factory=RateLimiterConfig)
    max_retries: int = 3
    backoff_base: float = 2.0
    ua_pool_size: int = 10
    proxy: str | None = None

    def __post_init__(self) -> None:
        """Read proxy from environment if not explicitly provided."""
        if self.proxy is None:
            self.proxy = os.environ.get("SCRAPER_PROXY") or None


class RateLimiter:
    """Token-bucket style per-domain rate limiter with jitter.

    Tracks the last request time for each domain and sleeps as needed
    before allowing the next request, adding ±30% jitter to avoid
    predictable request patterns.
    """

    def __init__(self, config: RateLimiterConfig) -> None:
        """Initialize the rate limiter.

        Args:
            config: Rate limiting configuration with delay and jitter settings.
        """
        self._config = config
        self._last_request: dict[str, float] = {}

    def wait(self, domain: str) -> None:
        """Block until the domain's rate limit window has passed.

        If a previous request was made to this domain, computes the required
        delay (with jitter) and sleeps for the remaining time. First requests
        to a domain pass through immediately.

        Args:
            domain: The domain key to rate limit (e.g., 'pinnacle.com').
        """
        now = time.monotonic()
        last = self._last_request.get(domain)

        if last is not None:
            delay = self._compute_delay()
            elapsed = now - last
            remaining = delay - elapsed
            if remaining > 0:
                time.sleep(remaining)

        self._last_request[domain] = time.monotonic()

    def _compute_delay(self) -> float:
        """Compute delay with jitter applied.

        Returns:
            Delay in seconds, guaranteed to be at least 0.1 and at most
            the configured max_delay.
        """
        base = self._config.min_delay
        jitter = base * self._config.jitter_pct * random.uniform(-1, 1)
        delay = base + jitter
        return max(0.1, min(delay, self._config.max_delay))


class UserAgentRotator:
    """Cycles through realistic browser user-agent strings.

    Maintains a pool of 10+ modern browser UA strings (Chrome, Firefox,
    Safari, Edge) and rotates through them sequentially to reduce
    detection probability.
    """

    _AGENTS: list[str] = [
        # Chrome on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # Chrome on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        # Chrome on Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
        # Firefox on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0",
        # Firefox on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) "
        "Gecko/20100101 Firefox/125.0",
        # Firefox on Linux
        "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) "
        "Gecko/20100101 Firefox/124.0",
        # Safari on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.4 Safari/605.1.15",
        # Edge on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0",
        # Chrome on Android
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Mobile Safari/537.36",
        # Safari on iOS
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 "
        "Mobile/15E148 Safari/604.1",
        # Edge on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0",
        # Chrome on Windows (older version)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    ]

    def __init__(self) -> None:
        """Initialize the rotator with index at start of pool."""
        self._index = 0

    def next(self) -> str:
        """Return the next user-agent string in rotation.

        Cycles sequentially through the pool, wrapping around to the
        beginning after reaching the end.

        Returns:
            A realistic browser user-agent string.
        """
        ua = self._AGENTS[self._index]
        self._index = (self._index + 1) % len(self._AGENTS)
        return ua

    @property
    def pool_size(self) -> int:
        """Return the number of user-agent strings in the pool."""
        return len(self._AGENTS)


class RetryHandler:
    """Exponential backoff retry logic for transient failures.

    Retries callable operations that fail with transient HTTP errors
    (5xx, connection timeout, connection reset) up to a configurable
    maximum number of attempts. Respects Retry-After headers on 429
    responses.
    """

    def __init__(self, max_retries: int = 3, backoff_base: float = 2.0) -> None:
        """Initialize the retry handler.

        Args:
            max_retries: Maximum number of retry attempts (default: 3).
            backoff_base: Base for exponential backoff (default: 2.0).
                Delays are backoff_base^attempt seconds (2, 4, 8).
        """
        self._max_retries = max_retries
        self._backoff_base = backoff_base

    def execute(self, fn, *args, **kwargs):
        """Execute fn with retry on transient errors.

        Calls fn(*args, **kwargs). On transient failures (HTTP 5xx,
        connection timeout, connection reset), retries up to max_retries
        times with exponential backoff. On HTTP 429, waits for the
        Retry-After duration (or 60 seconds default) before retrying.

        Args:
            fn: Callable to execute.
            *args: Positional arguments passed to fn.
            **kwargs: Keyword arguments passed to fn.

        Returns:
            The result of fn on success.

        Raises:
            OddsExtractionError: After all retries are exhausted.
        """
        last_exception: Exception | None = None

        for attempt in range(1, self._max_retries + 1):
            try:
                return fn(*args, **kwargs)
            except requests.exceptions.HTTPError as exc:
                response = exc.response
                if response is not None and response.status_code == 429:
                    wait_time = self._get_retry_after(response)
                    time.sleep(wait_time)
                    last_exception = exc
                    continue
                elif response is not None and response.status_code >= 500:
                    last_exception = exc
                    self._backoff_sleep(attempt)
                    continue
                else:
                    # Non-transient HTTP errors (4xx except 429) — don't retry
                    raise OddsExtractionError(
                        message=f"HTTP error: {exc}",
                        status_code=response.status_code if response is not None else None,
                    ) from exc
            except (
                requests.exceptions.ConnectTimeout,
                requests.exceptions.ReadTimeout,
                requests.exceptions.Timeout,
            ) as exc:
                last_exception = exc
                self._backoff_sleep(attempt)
                continue
            except (
                requests.exceptions.ConnectionError,
                ConnectionResetError,
            ) as exc:
                last_exception = exc
                self._backoff_sleep(attempt)
                continue

        # All retries exhausted
        raise OddsExtractionError(
            message=f"All {self._max_retries} retries exhausted. Last error: {last_exception}",
            status_code=None,
        )

    def _backoff_sleep(self, attempt: int) -> None:
        """Sleep with exponential backoff and ±30% jitter.

        Args:
            attempt: The current attempt number (1-indexed).
        """
        base_delay = self._backoff_base**attempt
        jitter = base_delay * 0.30 * random.uniform(-1, 1)
        delay = max(0.1, base_delay + jitter)
        time.sleep(delay)

    @staticmethod
    def _get_retry_after(response: requests.Response) -> float:
        """Extract Retry-After value from response headers.

        Args:
            response: The HTTP response with a 429 status.

        Returns:
            Number of seconds to wait. Defaults to 60 if header is
            missing or unparseable.
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after is None:
            return 60.0
        try:
            return float(retry_after)
        except (ValueError, TypeError):
            return 60.0
