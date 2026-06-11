"""Adapter-based odds extraction orchestrator.

Replaces the single-API approach with multi-source extraction via
the AdapterRegistry, correlating events across adapters and enforcing
a minimum bookmaker threshold before caching and returning results.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone

from src.adapters.base import (
    ExtractionMethod,
    OddsExtractionError,
    ScrapedMatch,
)
from src.adapters.registry import AdapterRegistry
from src.cache_store import CacheStore
from src.models import AggregatedEvent

logger = logging.getLogger(__name__)

# Regex to strip common football club suffixes (case-insensitive, end of string)
_SUFFIX_RE = re.compile(r"\s+(fc|cf|sc|afc)$", re.IGNORECASE)
# Regex to strip leading "FC " prefix
_PREFIX_RE = re.compile(r"^fc\s+", re.IGNORECASE)
# Regex to collapse multiple whitespace characters into a single space
_WHITESPACE_RE = re.compile(r"\s+")

# Timestamp tolerance for event correlation: 2 hours in seconds
_TIMESTAMP_TOLERANCE_SECONDS = 7200

# Mapping from ExtractionMethod enum to cache extraction_method string
_EXTRACTION_METHOD_MAP: dict[ExtractionMethod, str] = {
    ExtractionMethod.HTTP: "scraping_http",
    ExtractionMethod.HEADLESS: "scraping_headless",
    ExtractionMethod.API: "api",
}


class OddsExtractor:
    """Orchestrates adapter-based extraction, event correlation, and caching.

    Queries all healthy adapters from the registry, correlates events across
    bookmaker sources using normalized team names and timestamp tolerance,
    enforces a minimum bookmaker threshold, and manages cache interactions.

    Attributes:
        MIN_BOOKMAKERS: Minimum number of distinct bookmaker sources required
            for an event to be included in results.
    """

    MIN_BOOKMAKERS = 3

    def __init__(
        self,
        registry: AdapterRegistry,
        cache_store: CacheStore,
        ttl_hours: int = 24,
        excluded_bookmakers: list[str] | None = None,
    ) -> None:
        """Initialize the OddsExtractor.

        Args:
            registry: AdapterRegistry instance for discovering and querying adapters.
            cache_store: CacheStore instance for caching extraction results.
            ttl_hours: Time-to-live for cache entries in hours (1-48).
            excluded_bookmakers: List of adapter bookmaker_ids to exclude from extraction.
        """
        self.registry = registry
        self.cache_store = cache_store
        self.ttl_hours = max(1, min(48, ttl_hours))
        self.excluded_bookmakers: list[str] = excluded_bookmakers or []

    def extract(
        self,
        sport: str,
        date: str | None = None,
        round_id: str | None = None,
    ) -> list[AggregatedEvent]:
        """Run full extraction across all healthy adapters.

        Extraction flow:
            1. Check cache freshness (is there cached data within TTL?)
            2. If cache hit → return cached data
            3. If cache miss or expired → query all healthy adapters
            4. For each healthy adapter: call fetch_1x2() and fetch_over_under()
            5. Handle failures gracefully (record_failure, skip adapter, continue)
            6. On success: record_success
            7. Correlate events across all adapter responses
            8. Enforce MIN_BOOKMAKERS threshold (skip events with < 3 sources)
            9. Cache the raw results
            10. If all adapters fail and stale cache exists → return stale with warning
            11. If all adapters fail and no cache → return empty list

        Args:
            sport: Sport/league key (e.g., 'soccer_epl').
            date: Optional YYYY-MM-DD date filter.
            round_id: Optional round identifier filter.

        Returns:
            List of AggregatedEvent objects meeting the minimum bookmaker threshold.
        """
        cache_key = self._build_cache_key(sport, date, round_id)
        market_type = "aggregated"
        bookmaker_keys = ("__aggregated__",)

        # Step 1-2: Check cache
        cached = self.cache_store.get(sport, market_type, cache_key, bookmaker_keys)
        stale_data: str | None = None

        if cached is not None:
            retrieved_at = datetime.fromisoformat(
                cached["retrieved_at"].replace("Z", "+00:00")
            )
            if not self.cache_store.is_expired(retrieved_at, self.ttl_hours):
                logger.debug("Cache hit for %s (key=%s)", sport, cache_key)
                return self._deserialize_events(cached["response_json"])
            else:
                stale_data = cached["response_json"]

        # Step 3: Query all healthy adapters
        healthy_adapters = self.registry.get_healthy()

        # Filter out excluded bookmakers
        if self.excluded_bookmakers:
            healthy_adapters = [
                a
                for a in healthy_adapters
                if a.bookmaker_id not in self.excluded_bookmakers
            ]

        if not healthy_adapters:
            logger.warning("No healthy adapters available for extraction.")
            return self._handle_all_failed(stale_data)

        # Step 4-6: Fetch from each adapter
        all_matches: dict[str, list[ScrapedMatch]] = {}
        all_failed = True

        for adapter in healthy_adapters:
            adapter_id = adapter.bookmaker_id
            try:
                matches_1x2 = adapter.fetch_1x2(sport, date)
                matches_ou = adapter.fetch_over_under(sport, date)
                # Enhanced: fetch multi-line O/U data
                matches_ou_total: list[ScrapedMatch] = []
                matches_ou_team: list[ScrapedMatch] = []
                if hasattr(adapter, "fetch_over_under_total"):
                    try:
                        matches_ou_total = adapter.fetch_over_under_total(sport, date)
                    except (OddsExtractionError, Exception) as exc:
                        logger.debug(
                            "Adapter '%s' failed fetching over_under_total: %s",
                            adapter_id,
                            exc,
                        )
                if hasattr(adapter, "fetch_over_under_team"):
                    try:
                        matches_ou_team = adapter.fetch_over_under_team(sport, date)
                    except (OddsExtractionError, Exception) as exc:
                        logger.debug(
                            "Adapter '%s' failed fetching over_under_team: %s",
                            adapter_id,
                            exc,
                        )
                self.registry.record_success(adapter_id)
                all_matches[adapter_id] = (
                    matches_1x2 + matches_ou + matches_ou_total + matches_ou_team
                )
                all_failed = False
                logger.debug(
                    "Adapter '%s' returned %d 1x2, %d o/u, %d o/u_total, %d o/u_team matches.",
                    adapter_id,
                    len(matches_1x2),
                    len(matches_ou),
                    len(matches_ou_total),
                    len(matches_ou_team),
                )
            except (OddsExtractionError, Exception) as exc:
                self.registry.record_failure(adapter_id)
                logger.warning(
                    "Adapter '%s' failed during extraction: %s", adapter_id, exc
                )
                continue

        # Step 10-11: Handle all adapters failing
        if all_failed:
            return self._handle_all_failed(stale_data)

        # Step 7: Correlate events
        aggregated = self._correlate_events(all_matches)

        # Step 8: Enforce minimum bookmaker threshold
        filtered = self._enforce_threshold(aggregated)

        # Step 9: Cache results
        self._cache_results(sport, cache_key, filtered, healthy_adapters)

        return filtered

    def _correlate_events(
        self,
        all_matches: dict[str, list[ScrapedMatch]],
    ) -> list[AggregatedEvent]:
        """Match events across adapters using normalized team names + ±2h timestamp.

        Creates a composite key from normalized home and away team names, then
        groups matches from different adapters that share the same key and have
        timestamps within the tolerance window.

        Args:
            all_matches: Mapping of adapter_id to list of ScrapedMatch from that adapter.

        Returns:
            List of AggregatedEvent with odds collected from all matching adapters.
        """
        # Build correlation buckets: composite_key -> list of (adapter_id, match)
        buckets: dict[str, list[tuple[str, ScrapedMatch]]] = {}

        for adapter_id, matches in all_matches.items():
            for match in matches:
                key = (
                    self.normalize_team_name(match.home_team)
                    + " vs "
                    + self.normalize_team_name(match.away_team)
                )
                if key not in buckets:
                    buckets[key] = []
                buckets[key].append((adapter_id, match))

        # Group by composite key and verify timestamp tolerance
        events: list[AggregatedEvent] = []

        for key, entries in buckets.items():
            # Group entries by timestamp clusters (within ±2h)
            clusters = self._cluster_by_timestamp(entries)

            for cluster in clusters:
                event = self._build_aggregated_event(cluster)
                if event is not None:
                    events.append(event)

        return events

    def _cluster_by_timestamp(
        self,
        entries: list[tuple[str, ScrapedMatch]],
    ) -> list[list[tuple[str, ScrapedMatch]]]:
        """Group entries into clusters where all timestamps are within ±2h of each other.

        Args:
            entries: List of (adapter_id, ScrapedMatch) tuples with the same team key.

        Returns:
            List of clusters, each cluster being a list of entries.
        """
        if not entries:
            return []

        # Sort by timestamp
        sorted_entries = sorted(entries, key=lambda e: e[1].event_timestamp)
        clusters: list[list[tuple[str, ScrapedMatch]]] = []
        current_cluster: list[tuple[str, ScrapedMatch]] = [sorted_entries[0]]

        for entry in sorted_entries[1:]:
            ref_ts = current_cluster[0][1].event_timestamp
            entry_ts = entry[1].event_timestamp
            diff = abs((entry_ts - ref_ts).total_seconds())
            if diff <= _TIMESTAMP_TOLERANCE_SECONDS:
                current_cluster.append(entry)
            else:
                clusters.append(current_cluster)
                current_cluster = [entry]

        clusters.append(current_cluster)
        return clusters

    def _build_aggregated_event(
        self,
        cluster: list[tuple[str, ScrapedMatch]],
    ) -> AggregatedEvent | None:
        """Build an AggregatedEvent from a cluster of correlated matches.

        Args:
            cluster: List of (adapter_id, ScrapedMatch) tuples for the same event.

        Returns:
            AggregatedEvent if odds data is available, None otherwise.
        """
        if not cluster:
            return None

        # Use the first entry for canonical event info
        first_match = cluster[0][1]
        home_team = first_match.home_team
        away_team = first_match.away_team
        event_timestamp = first_match.event_timestamp

        odds_1x2: dict[str, tuple[float, float, float]] = {}
        odds_ou: dict[str, tuple[float, float]] = {}
        overall_ou: dict[str, dict[float, tuple[float, float]]] = {}
        home_ou: dict[str, dict[float, tuple[float, float]]] = {}
        away_ou: dict[str, dict[float, tuple[float, float]]] = {}

        for adapter_id, match in cluster:
            if match.market_type == "1x2" and len(match.outcomes) >= 3:
                # Expect outcomes: Home, Draw, Away
                home_odds = match.outcomes[0].odds
                draw_odds = match.outcomes[1].odds
                away_odds = match.outcomes[2].odds
                odds_1x2[adapter_id] = (home_odds, draw_odds, away_odds)
            elif match.market_type == "over_under" and len(match.outcomes) >= 2:
                # Expect outcomes: Over, Under
                over_odds = match.outcomes[0].odds
                under_odds = match.outcomes[1].odds
                odds_ou[adapter_id] = (over_odds, under_odds)
            elif match.market_type == "over_under_total":
                # Multiple thresholds: outcomes come in Over/Under pairs
                lines: dict[float, tuple[float, float]] = {}
                i = 0
                while i < len(match.outcomes) - 1:
                    over_out = match.outcomes[i]
                    under_out = match.outcomes[i + 1]
                    if (
                        over_out.point is not None
                        and "over" in over_out.name.lower()
                    ):
                        lines[over_out.point] = (over_out.odds, under_out.odds)
                        i += 2
                    else:
                        i += 1
                if lines:
                    overall_ou.setdefault(adapter_id, {}).update(lines)
            elif match.market_type == "over_under_team":
                # Team-specific O/U: "Over home", "Under home", "Over away", "Under away"
                home_lines: dict[float, list[float]] = {}
                away_lines: dict[float, list[float]] = {}
                for out in match.outcomes:
                    if out.point is None:
                        continue
                    name_lower = out.name.lower()
                    if "home" in name_lower:
                        if out.point not in home_lines:
                            home_lines[out.point] = [0.0, 0.0]
                        if "over" in name_lower:
                            home_lines[out.point][0] = out.odds
                        elif "under" in name_lower:
                            home_lines[out.point][1] = out.odds
                    elif "away" in name_lower:
                        if out.point not in away_lines:
                            away_lines[out.point] = [0.0, 0.0]
                        if "over" in name_lower:
                            away_lines[out.point][0] = out.odds
                        elif "under" in name_lower:
                            away_lines[out.point][1] = out.odds
                # Convert to tuples and store
                for threshold, pair in home_lines.items():
                    if pair[0] > 0 and pair[1] > 0:
                        home_ou.setdefault(adapter_id, {})[threshold] = (
                            pair[0],
                            pair[1],
                        )
                for threshold, pair in away_lines.items():
                    if pair[0] > 0 and pair[1] > 0:
                        away_ou.setdefault(adapter_id, {})[threshold] = (
                            pair[0],
                            pair[1],
                        )

        # Count distinct sources (adapters that provide any data)
        all_sources = (
            set(odds_1x2.keys())
            | set(odds_ou.keys())
            | set(overall_ou.keys())
            | set(home_ou.keys())
            | set(away_ou.keys())
        )
        source_count = len(all_sources)

        if source_count == 0:
            return None

        return AggregatedEvent(
            home_team=home_team,
            away_team=away_team,
            event_timestamp=event_timestamp,
            bookmaker_odds_1x2=odds_1x2,
            bookmaker_odds_over_under=odds_ou,
            source_count=source_count,
            overall_ou_lines=overall_ou,
            home_team_ou_lines=home_ou,
            away_team_ou_lines=away_ou,
        )

    def _enforce_threshold(
        self,
        events: list[AggregatedEvent],
    ) -> list[AggregatedEvent]:
        """Filter out events with fewer than MIN_BOOKMAKERS sources.

        Args:
            events: List of AggregatedEvent to filter.

        Returns:
            Filtered list containing only events meeting the threshold.
        """
        filtered: list[AggregatedEvent] = []
        for event in events:
            if event.source_count >= self.MIN_BOOKMAKERS:
                filtered.append(event)
            else:
                logger.warning(
                    "Skipping '%s vs %s': only %d source(s) "
                    "(minimum %d required).",
                    event.home_team,
                    event.away_team,
                    event.source_count,
                    self.MIN_BOOKMAKERS,
                )
        return filtered

    def _handle_all_failed(self, stale_data: str | None) -> list[AggregatedEvent]:
        """Handle the case where all adapters failed.

        Args:
            stale_data: Serialized stale cache data, or None if no cache exists.

        Returns:
            Stale cached events if available, otherwise an empty list.
        """
        if stale_data is not None:
            logger.warning(
                "[STALE] All adapters failed. Returning stale cached data."
            )
            return self._deserialize_events(stale_data)

        logger.warning("All adapters failed and no cached data available.")
        return []

    def _cache_results(
        self,
        sport: str,
        cache_key: str,
        events: list[AggregatedEvent],
        adapters: list,
    ) -> None:
        """Cache the aggregated extraction results.

        Args:
            sport: Sport/league key.
            cache_key: Cache key for this extraction run.
            events: List of AggregatedEvent to cache.
            adapters: List of adapters used (for determining extraction_method).
        """
        serialized = self._serialize_events(events)

        # Determine the primary extraction method from the adapters used
        extraction_method = self._determine_extraction_method(adapters)

        self.cache_store.put(
            sport_key=sport,
            market_type="aggregated",
            event_id=cache_key,
            bookmaker_keys=("__aggregated__",),
            response_json=serialized,
            retrieved_at=datetime.now(timezone.utc),
            extraction_method=extraction_method,
        )

    def _determine_extraction_method(self, adapters: list) -> str:
        """Determine the primary extraction method from the adapters used.

        Prioritizes scraping methods over API. If any adapter uses headless,
        reports headless. If any uses HTTP scraping, reports HTTP scraping.
        Falls back to 'api'.

        Args:
            adapters: List of OddsAdapter instances.

        Returns:
            One of 'scraping_http', 'scraping_headless', or 'api'.
        """
        methods = set()
        for adapter in adapters:
            method_str = _EXTRACTION_METHOD_MAP.get(
                adapter.extraction_method, "api"
            )
            methods.add(method_str)

        if "scraping_headless" in methods:
            return "scraping_headless"
        if "scraping_http" in methods:
            return "scraping_http"
        return "api"

    @staticmethod
    def normalize_team_name(name: str) -> str:
        """Normalize a team name for correlation matching.

        Applies the following transformations in order:
            1. Strip Unicode accents using NFKD decomposition
            2. Lowercase
            3. Strip trailing suffixes: FC, CF, SC, AFC
            4. Strip leading "FC " prefix
            5. Collapse multiple whitespace to single space
            6. Strip leading/trailing whitespace

        Args:
            name: Raw team name string.

        Returns:
            Normalized team name suitable for comparison.
        """
        # Strip accents using NFKD normalization
        nfkd = unicodedata.normalize("NFKD", name)
        ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))

        # Lowercase
        result = ascii_name.lower()

        # Strip trailing suffixes (FC, CF, SC, AFC)
        result = _SUFFIX_RE.sub("", result)

        # Strip leading "FC " prefix
        result = _PREFIX_RE.sub("", result)

        # Collapse whitespace
        result = _WHITESPACE_RE.sub(" ", result)

        # Strip leading/trailing whitespace
        result = result.strip()

        return result

    @staticmethod
    def _build_cache_key(
        sport: str,
        date: str | None,
        round_id: str | None,
    ) -> str:
        """Build a composite cache key from extraction parameters.

        Args:
            sport: Sport/league key.
            date: Optional date filter.
            round_id: Optional round identifier.

        Returns:
            A string cache key.
        """
        parts = [sport]
        if date:
            parts.append(f"date_{date}")
        if round_id:
            parts.append(f"round_{round_id}")
        if not date and not round_id:
            parts.append("all")
        return "_".join(parts)

    @staticmethod
    def _serialize_events(events: list[AggregatedEvent]) -> str:
        """Serialize a list of AggregatedEvent to JSON string.

        Args:
            events: List of AggregatedEvent to serialize.

        Returns:
            JSON string representation.
        """
        data = []
        for event in events:
            # Serialize overall_ou_lines: {bm_id: {threshold: [over, under]}}
            overall_ou_ser = {}
            for bm_id, lines in event.overall_ou_lines.items():
                overall_ou_ser[bm_id] = {
                    str(k): list(v) for k, v in lines.items()
                }

            # Serialize home/away team O/U lines
            home_ou_ser = {}
            for bm_id, lines in event.home_team_ou_lines.items():
                home_ou_ser[bm_id] = {
                    str(k): list(v) for k, v in lines.items()
                }

            away_ou_ser = {}
            for bm_id, lines in event.away_team_ou_lines.items():
                away_ou_ser[bm_id] = {
                    str(k): list(v) for k, v in lines.items()
                }

            # Serialize correct_score_odds: {bm_id: {"h-a": odds}}
            correct_score_ser = {}
            for bm_id, scores in event.correct_score_odds.items():
                correct_score_ser[bm_id] = {
                    f"{h}-{a}": odds for (h, a), odds in scores.items()
                }

            data.append(
                {
                    "home_team": event.home_team,
                    "away_team": event.away_team,
                    "event_timestamp": event.event_timestamp.isoformat(),
                    "bookmaker_odds_1x2": {
                        k: list(v) for k, v in event.bookmaker_odds_1x2.items()
                    },
                    "bookmaker_odds_over_under": {
                        k: list(v)
                        for k, v in event.bookmaker_odds_over_under.items()
                    },
                    "source_count": event.source_count,
                    "overall_ou_lines": overall_ou_ser,
                    "home_team_ou_lines": home_ou_ser,
                    "away_team_ou_lines": away_ou_ser,
                    "correct_score_odds": correct_score_ser,
                }
            )
        return json.dumps(data)

    @staticmethod
    def _deserialize_events(json_str: str) -> list[AggregatedEvent]:
        """Deserialize a JSON string to a list of AggregatedEvent.

        Args:
            json_str: JSON string produced by _serialize_events.

        Returns:
            List of AggregatedEvent objects.
        """
        data = json.loads(json_str)
        events: list[AggregatedEvent] = []
        for item in data:
            # Deserialize overall_ou_lines
            overall_ou: dict[str, dict[float, tuple[float, float]]] = {}
            for bm_id, lines in item.get("overall_ou_lines", {}).items():
                overall_ou[bm_id] = {
                    float(k): tuple(v) for k, v in lines.items()
                }

            # Deserialize home/away team O/U lines
            home_ou: dict[str, dict[float, tuple[float, float]]] = {}
            for bm_id, lines in item.get("home_team_ou_lines", {}).items():
                home_ou[bm_id] = {
                    float(k): tuple(v) for k, v in lines.items()
                }

            away_ou: dict[str, dict[float, tuple[float, float]]] = {}
            for bm_id, lines in item.get("away_team_ou_lines", {}).items():
                away_ou[bm_id] = {
                    float(k): tuple(v) for k, v in lines.items()
                }

            # Deserialize correct_score_odds
            correct_score: dict[str, dict[tuple[int, int], float]] = {}
            for bm_id, scores in item.get("correct_score_odds", {}).items():
                correct_score[bm_id] = {}
                for score_str, odds in scores.items():
                    parts = score_str.split("-")
                    if len(parts) == 2:
                        correct_score[bm_id][(int(parts[0]), int(parts[1]))] = odds

            events.append(
                AggregatedEvent(
                    home_team=item["home_team"],
                    away_team=item["away_team"],
                    event_timestamp=datetime.fromisoformat(
                        item["event_timestamp"]
                    ),
                    bookmaker_odds_1x2={
                        k: tuple(v)
                        for k, v in item["bookmaker_odds_1x2"].items()
                    },
                    bookmaker_odds_over_under={
                        k: tuple(v)
                        for k, v in item["bookmaker_odds_over_under"].items()
                    },
                    source_count=item["source_count"],
                    overall_ou_lines=overall_ou,
                    home_team_ou_lines=home_ou,
                    away_team_ou_lines=away_ou,
                    correct_score_odds=correct_score,
                )
            )
        return events
