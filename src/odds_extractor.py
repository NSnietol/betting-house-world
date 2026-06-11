"""Odds Extractor module for fetching and caching betting odds from The Odds API v4."""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone

import requests

from src.cache_store import CacheStore

logger = logging.getLogger(__name__)


class OddsExtractionError(Exception):
    """Raised when odds extraction fails due to HTTP errors, timeouts, or API issues."""

    pass


class OddsExtractor:
    """Fetches odds from The Odds API v4 with cache-first strategy and bookmaker filtering.

    Implements a cache-first approach: checks SQLite cache before making API calls,
    stores fresh responses in cache, and falls back to stale cache on API failure.
    """

    BASE_URL = "https://api.the-odds-api.com/v4"
    TIMEOUT = 30  # seconds

    def __init__(
        self,
        api_key: str,
        cache_store: CacheStore,
        ttl_hours: int = 24,
        excluded_bookmakers: list[str] | None = None,
    ):
        """Initialize the OddsExtractor.

        Args:
            api_key: API key for The Odds API.
            cache_store: CacheStore instance for caching responses.
            ttl_hours: Time-to-live for cache entries in hours (1-48).
            excluded_bookmakers: List of bookmaker keys to exclude from processing.
        """
        self.api_key = api_key
        self.cache_store = cache_store
        self.ttl_hours = max(1, min(48, ttl_hours))
        self.excluded_bookmakers: list[str] = excluded_bookmakers or []

    def set_excluded_bookmakers(self, excluded: list[str]) -> None:
        """Update the list of excluded bookmakers.

        Args:
            excluded: List of bookmaker keys to exclude.
        """
        self.excluded_bookmakers = excluded

    def fetch_odds(
        self,
        sport: str,
        markets: list[str],
        bookmakers: list[str] | None = None,
        commence_time_from: str | None = None,
        commence_time_to: str | None = None,
    ) -> list[dict]:
        """Fetch odds from API or cache using a cache-first strategy.

        Strategy:
        1. Check cache for a valid (non-expired) entry.
        2. If cache miss or expired, fetch from The Odds API.
        3. Store fresh response in cache.
        4. On API failure with expired cache, return stale cached data.

        Args:
            sport: Sport/league key (e.g., "soccer_epl").
            markets: List of market keys to fetch (e.g., ["h2h", "totals"]).
            bookmakers: Optional list of bookmaker keys to request. If None, API default.
            commence_time_from: Optional ISO 8601 start time filter.
            commence_time_to: Optional ISO 8601 end time filter.

        Returns:
            List of event dicts, each containing bookmaker odds for requested markets.

        Raises:
            OddsExtractionError: On HTTP errors or timeouts when no cache is available.
        """
        # Build a cache key from the request parameters
        market_type = ",".join(sorted(markets))
        # Use a composite event_id based on time range or "all"
        event_id = self._build_event_id(commence_time_from, commence_time_to)
        bookmaker_keys_tuple = tuple(sorted(bookmakers)) if bookmakers else ("__all__",)

        # Step 1: Check cache
        cached = self.cache_store.get(sport, market_type, event_id, bookmaker_keys_tuple)
        stale_data = None

        if cached is not None:
            retrieved_at = datetime.fromisoformat(
                cached["retrieved_at"].replace("Z", "+00:00")
            )
            if not self.cache_store.is_expired(retrieved_at, self.ttl_hours):
                # Cache hit - return cached data without API call
                events = json.loads(cached["response_json"])
                return self._apply_exclusions_and_filter(events)
            else:
                # Cache expired - keep as stale fallback
                stale_data = cached["response_json"]

        # Step 2: Fetch from API
        try:
            events = self._fetch_from_api(
                sport, markets, bookmakers, commence_time_from, commence_time_to
            )
        except OddsExtractionError:
            # Step 4: On API failure, fall back to stale cache if available
            if stale_data is not None:
                logger.warning(
                    "[STALE] Returning stale cached data for %s due to API failure.",
                    sport,
                )
                print(
                    f"[STALE] Warning: Returning stale cached data for {sport}. "
                    f"Data may be outdated.",
                    file=sys.stderr,
                )
                events = json.loads(stale_data)
                return self._apply_exclusions_and_filter(events)
            raise

        # Step 3: Store fresh response in cache
        self.cache_store.put(
            sport_key=sport,
            market_type=market_type,
            event_id=event_id,
            bookmaker_keys=bookmaker_keys_tuple,
            response_json=json.dumps(events),
            retrieved_at=datetime.now(timezone.utc),
        )

        return self._apply_exclusions_and_filter(events)

    def _fetch_from_api(
        self,
        sport: str,
        markets: list[str],
        bookmakers: list[str] | None,
        commence_time_from: str | None,
        commence_time_to: str | None,
    ) -> list[dict]:
        """Make the actual HTTP GET request to The Odds API.

        Args:
            sport: Sport/league key.
            markets: List of market keys.
            bookmakers: Optional bookmaker filter list.
            commence_time_from: Optional start time filter.
            commence_time_to: Optional end time filter.

        Returns:
            List of event dicts from the API response.

        Raises:
            OddsExtractionError: On timeout, HTTP 4xx/5xx, or network errors.
        """
        params = self._build_request_params(
            sport, markets, bookmakers, commence_time_from, commence_time_to
        )
        url = f"{self.BASE_URL}/sports/{sport}/odds"

        try:
            response = requests.get(url, params=params, timeout=self.TIMEOUT)
        except requests.Timeout:
            msg = f"TIMEOUT: The Odds API did not respond within {self.TIMEOUT}s for {sport}"
            logger.error(msg)
            raise OddsExtractionError(msg)
        except requests.RequestException as e:
            msg = f"Network error fetching odds for {sport}: {e}"
            logger.error(msg)
            raise OddsExtractionError(msg)

        if response.status_code >= 400:
            msg = (
                f"HTTP {response.status_code} error fetching odds for {sport}: "
                f"{response.text[:200]}"
            )
            logger.error(msg)
            raise OddsExtractionError(msg)

        return response.json()

    def _build_request_params(
        self,
        sport: str,
        markets: list[str],
        bookmakers: list[str] | None,
        commence_time_from: str | None,
        commence_time_to: str | None,
    ) -> dict:
        """Build query parameters for The Odds API v4 GET /odds endpoint.

        Args:
            sport: Sport/league key (used in URL path, not as a param).
            markets: List of market keys (e.g., ["h2h", "totals"]).
            bookmakers: Optional list of bookmaker keys to filter.
            commence_time_from: Optional ISO 8601 start time.
            commence_time_to: Optional ISO 8601 end time.

        Returns:
            Dict of query parameters for the API request.
        """
        params: dict = {
            "apiKey": self.api_key,
            "markets": ",".join(markets),
            "oddsFormat": "decimal",
        }

        if bookmakers:
            params["bookmakers"] = ",".join(bookmakers)

        if commence_time_from:
            params["commenceTimeFrom"] = commence_time_from

        if commence_time_to:
            params["commenceTimeTo"] = commence_time_to

        return params

    def _filter_events(
        self, events: list[dict], min_bookmakers: int = 3
    ) -> list[dict]:
        """Filter events ensuring each has both h2h and totals markets from min bookmakers.

        An event passes the filter only if at least `min_bookmakers` each provide
        both an 'h2h' market and a 'totals' market.

        Args:
            events: List of event dicts from the API response.
            min_bookmakers: Minimum number of bookmakers required with both markets.

        Returns:
            Filtered list of events meeting the criteria.
        """
        filtered = []
        for event in events:
            home = event.get("home_team", "Unknown")
            away = event.get("away_team", "Unknown")
            match_id = f"{home} vs {away}"

            bookmakers_data = event.get("bookmakers", [])
            qualifying_count = 0

            for bm in bookmakers_data:
                market_keys = {m.get("key") for m in bm.get("markets", [])}
                if "h2h" in market_keys and "totals" in market_keys:
                    qualifying_count += 1

            if qualifying_count >= min_bookmakers:
                filtered.append(event)
            else:
                logger.warning(
                    "Skipping %s: only %d bookmaker(s) with both h2h and totals "
                    "(minimum %d required).",
                    match_id,
                    qualifying_count,
                    min_bookmakers,
                )
                print(
                    f"Warning: Skipping {match_id} — only {qualifying_count} "
                    f"bookmaker(s) with both h2h and totals markets "
                    f"(minimum {min_bookmakers} required).",
                    file=sys.stderr,
                )

        return filtered

    def _apply_exclusions_and_filter(self, events: list[dict]) -> list[dict]:
        """Apply bookmaker exclusions and then filter events.

        Removes excluded bookmakers from each event's bookmaker list,
        then applies the standard filter requiring min 3 bookmakers with
        both h2h and totals markets.

        Args:
            events: List of event dicts.

        Returns:
            Filtered list of events after exclusions.
        """
        if self.excluded_bookmakers:
            for event in events:
                event["bookmakers"] = [
                    bm
                    for bm in event.get("bookmakers", [])
                    if bm.get("key") not in self.excluded_bookmakers
                ]

        return self._filter_events(events)

    def _build_event_id(
        self, commence_time_from: str | None, commence_time_to: str | None
    ) -> str:
        """Build a composite event ID for cache key purposes.

        Args:
            commence_time_from: Optional start time filter.
            commence_time_to: Optional end time filter.

        Returns:
            A string representing the time-range key for caching.
        """
        if commence_time_from and commence_time_to:
            return f"{commence_time_from}_{commence_time_to}"
        elif commence_time_from:
            return f"from_{commence_time_from}"
        elif commence_time_to:
            return f"to_{commence_time_to}"
        return "all_events"
