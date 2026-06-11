"""Unit tests for the adapter-based OddsExtractor orchestrator."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    MarketOutcome,
    OddsAdapter,
    OddsExtractionError,
    ScrapedMatch,
)
from src.adapters.registry import AdapterRegistry
from src.cache_store import CacheStore
from src.models import AggregatedEvent
from src.odds_extractor import OddsExtractor


# ---------------------------------------------------------------------------
# Helpers: Fake adapters for testing
# ---------------------------------------------------------------------------


class FakeAdapter(OddsAdapter):
    """A configurable fake adapter for testing."""

    def __init__(
        self,
        bm_id: str = "fake_bm",
        bm_name: str = "Fake Bookmaker",
        prio: int = 1,
        method: ExtractionMethod = ExtractionMethod.HTTP,
        matches_1x2: list[ScrapedMatch] | None = None,
        matches_ou: list[ScrapedMatch] | None = None,
        should_fail: bool = False,
    ):
        self._bookmaker_id = bm_id
        self._bookmaker_name = bm_name
        self._priority = prio
        self._extraction_method = method
        self._matches_1x2 = matches_1x2 or []
        self._matches_ou = matches_ou or []
        self._should_fail = should_fail

    @property
    def bookmaker_id(self) -> str:
        return self._bookmaker_id

    @property
    def bookmaker_name(self) -> str:
        return self._bookmaker_name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def extraction_method(self) -> ExtractionMethod:
        return self._extraction_method

    @property
    def base_url(self) -> str:
        return "https://fake.example.com"

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        if self._should_fail:
            raise OddsExtractionError("Fake failure", adapter_id=self._bookmaker_id)
        return self._matches_1x2

    def fetch_over_under(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        if self._should_fail:
            raise OddsExtractionError("Fake failure", adapter_id=self._bookmaker_id)
        return self._matches_ou

    def health_check(self) -> AdapterHealth:
        return AdapterHealth.REACHABLE


def _make_match(
    home: str = "Chelsea FC",
    away: str = "Arsenal",
    ts: datetime | None = None,
    market_type: str = "1x2",
    outcomes: list[MarketOutcome] | None = None,
) -> ScrapedMatch:
    """Helper to create a ScrapedMatch with defaults."""
    if ts is None:
        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
    if outcomes is None:
        if market_type == "1x2":
            outcomes = [
                MarketOutcome(name="Home", odds=2.10),
                MarketOutcome(name="Draw", odds=3.40),
                MarketOutcome(name="Away", odds=3.20),
            ]
        else:
            outcomes = [
                MarketOutcome(name="Over", odds=1.85, point=2.5),
                MarketOutcome(name="Under", odds=1.95, point=2.5),
            ]
    return ScrapedMatch(
        home_team=home,
        away_team=away,
        event_timestamp=ts,
        market_type=market_type,
        outcomes=outcomes,
    )


# ---------------------------------------------------------------------------
# Tests: normalize_team_name
# ---------------------------------------------------------------------------


class TestNormalizeTeamName:
    """Tests for OddsExtractor.normalize_team_name static method."""

    def test_lowercase(self):
        assert OddsExtractor.normalize_team_name("Manchester United") == "manchester united"

    def test_strip_fc_suffix(self):
        assert OddsExtractor.normalize_team_name("Chelsea FC") == "chelsea"

    def test_strip_cf_suffix(self):
        assert OddsExtractor.normalize_team_name("Valencia CF") == "valencia"

    def test_strip_sc_suffix(self):
        assert OddsExtractor.normalize_team_name("Sporting SC") == "sporting"

    def test_strip_afc_suffix(self):
        assert OddsExtractor.normalize_team_name("Bournemouth AFC") == "bournemouth"

    def test_strip_fc_prefix(self):
        assert OddsExtractor.normalize_team_name("FC Barcelona") == "barcelona"

    def test_collapse_whitespace(self):
        assert OddsExtractor.normalize_team_name("Real  Madrid") == "real madrid"

    def test_strip_accents(self):
        assert OddsExtractor.normalize_team_name("Atlético") == "atletico"

    def test_combined_normalization(self):
        assert OddsExtractor.normalize_team_name("FC  Atlético  SC") == "atletico"

    def test_idempotent(self):
        name = "Chelsea FC"
        normalized = OddsExtractor.normalize_team_name(name)
        assert OddsExtractor.normalize_team_name(normalized) == normalized

    def test_empty_string(self):
        assert OddsExtractor.normalize_team_name("") == ""

    def test_only_suffix(self):
        # Edge case: team name is just "FC"
        assert OddsExtractor.normalize_team_name("FC") == "fc"


# ---------------------------------------------------------------------------
# Tests: _correlate_events
# ---------------------------------------------------------------------------


class TestCorrelateEvents:
    """Tests for event correlation logic."""

    def test_same_event_different_adapters(self):
        """Events from different adapters with same teams correlate."""
        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        all_matches = {
            "adapter_a": [_make_match("Chelsea FC", "Arsenal", ts, "1x2")],
            "adapter_b": [_make_match("Chelsea", "Arsenal", ts, "1x2")],
        }

        events = extractor._correlate_events(all_matches)
        assert len(events) == 1
        assert events[0].source_count == 2

    def test_timestamp_within_tolerance(self):
        """Events within 2h tolerance are correlated."""
        ts1 = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        ts2 = ts1 + timedelta(hours=1, minutes=30)  # 1.5h apart
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        all_matches = {
            "adapter_a": [_make_match("Chelsea", "Arsenal", ts1, "1x2")],
            "adapter_b": [_make_match("Chelsea", "Arsenal", ts2, "1x2")],
        }

        events = extractor._correlate_events(all_matches)
        assert len(events) == 1

    def test_timestamp_outside_tolerance(self):
        """Events more than 2h apart are NOT correlated."""
        ts1 = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        ts2 = ts1 + timedelta(hours=3)  # 3h apart
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        all_matches = {
            "adapter_a": [_make_match("Chelsea", "Arsenal", ts1, "1x2")],
            "adapter_b": [_make_match("Chelsea", "Arsenal", ts2, "1x2")],
        }

        events = extractor._correlate_events(all_matches)
        assert len(events) == 2

    def test_different_teams_not_correlated(self):
        """Events with different teams are separate."""
        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        all_matches = {
            "adapter_a": [_make_match("Chelsea", "Arsenal", ts, "1x2")],
            "adapter_b": [_make_match("Liverpool", "Everton", ts, "1x2")],
        }

        events = extractor._correlate_events(all_matches)
        assert len(events) == 2

    def test_1x2_and_over_under_combined(self):
        """Both 1x2 and over_under from same adapter create one event."""
        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        all_matches = {
            "adapter_a": [
                _make_match("Chelsea", "Arsenal", ts, "1x2"),
                _make_match("Chelsea", "Arsenal", ts, "over_under"),
            ],
        }

        events = extractor._correlate_events(all_matches)
        assert len(events) == 1
        assert "adapter_a" in events[0].bookmaker_odds_1x2
        assert "adapter_a" in events[0].bookmaker_odds_over_under


# ---------------------------------------------------------------------------
# Tests: _enforce_threshold
# ---------------------------------------------------------------------------


class TestEnforceThreshold:
    """Tests for minimum bookmaker threshold enforcement."""

    def test_event_with_enough_sources_passes(self):
        """Events with >= 3 sources are kept."""
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        event = AggregatedEvent(
            home_team="Chelsea",
            away_team="Arsenal",
            event_timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
            bookmaker_odds_1x2={"a": (2.0, 3.0, 3.5), "b": (2.1, 3.1, 3.4), "c": (2.0, 3.2, 3.3)},
            bookmaker_odds_over_under={"a": (1.8, 2.0)},
            source_count=3,
        )
        result = extractor._enforce_threshold([event])
        assert len(result) == 1

    def test_event_below_threshold_filtered(self):
        """Events with < 3 sources are filtered out."""
        registry = AdapterRegistry()
        cache = MagicMock(spec=CacheStore)
        extractor = OddsExtractor(registry, cache)

        event = AggregatedEvent(
            home_team="Chelsea",
            away_team="Arsenal",
            event_timestamp=datetime(2024, 6, 1, tzinfo=timezone.utc),
            bookmaker_odds_1x2={"a": (2.0, 3.0, 3.5)},
            bookmaker_odds_over_under={},
            source_count=1,
        )
        result = extractor._enforce_threshold([event])
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Tests: extract (integration-style with fakes)
# ---------------------------------------------------------------------------


class TestExtract:
    """Tests for the full extract() method using fake adapters."""

    def test_cache_hit_returns_cached_data(self, tmp_path):
        """When cache is fresh, extract returns cached data without querying adapters."""
        db_path = str(tmp_path / "test.db")
        cache = CacheStore(db_path)
        registry = AdapterRegistry()

        extractor = OddsExtractor(registry, cache, ttl_hours=24)

        # Pre-populate cache
        events = [
            AggregatedEvent(
                home_team="Chelsea",
                away_team="Arsenal",
                event_timestamp=datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc),
                bookmaker_odds_1x2={"bm1": (2.0, 3.0, 3.5), "bm2": (2.1, 3.1, 3.4), "bm3": (1.9, 3.0, 3.6)},
                bookmaker_odds_over_under={"bm1": (1.8, 2.0)},
                source_count=3,
            )
        ]
        serialized = OddsExtractor._serialize_events(events)
        cache.put(
            sport_key="soccer_epl",
            market_type="aggregated",
            event_id="soccer_epl_date_2024-06-01",
            bookmaker_keys=("__aggregated__",),
            response_json=serialized,
            retrieved_at=datetime.now(timezone.utc),
            extraction_method="scraping_http",
        )

        result = extractor.extract("soccer_epl", date="2024-06-01")
        assert len(result) == 1
        assert result[0].home_team == "Chelsea"

    def test_adapter_failure_graceful_skip(self, tmp_path):
        """A failing adapter is skipped; successful ones still contribute."""
        db_path = str(tmp_path / "test.db")
        cache = CacheStore(db_path)
        registry = AdapterRegistry()

        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        match_1x2 = _make_match("Chelsea", "Arsenal", ts, "1x2")
        match_ou = _make_match("Chelsea", "Arsenal", ts, "over_under")

        # Register 3 working adapters + 1 failing
        for i in range(3):
            adapter = FakeAdapter(
                bm_id=f"good_{i}",
                bm_name=f"Good {i}",
                prio=i + 1,
                matches_1x2=[match_1x2],
                matches_ou=[match_ou],
            )
            registry.register(adapter)

        failing_adapter = FakeAdapter(
            bm_id="bad_one",
            bm_name="Bad One",
            prio=10,
            should_fail=True,
        )
        registry.register(failing_adapter)

        extractor = OddsExtractor(registry, cache, ttl_hours=24)
        result = extractor.extract("soccer_epl", date="2024-06-01")

        # Should have 1 event from the 3 good adapters
        assert len(result) == 1
        assert result[0].source_count == 3

    def test_all_adapters_fail_stale_cache(self, tmp_path):
        """When all adapters fail, stale cache is returned."""
        db_path = str(tmp_path / "test.db")
        cache = CacheStore(db_path)
        registry = AdapterRegistry()

        # Pre-populate expired cache
        events = [
            AggregatedEvent(
                home_team="Liverpool",
                away_team="Everton",
                event_timestamp=datetime(2024, 5, 1, 15, 0, 0, tzinfo=timezone.utc),
                bookmaker_odds_1x2={"bm1": (1.5, 4.0, 6.0), "bm2": (1.6, 3.8, 5.5), "bm3": (1.55, 3.9, 5.8)},
                bookmaker_odds_over_under={},
                source_count=3,
            )
        ]
        serialized = OddsExtractor._serialize_events(events)
        # Store with old timestamp (expired)
        cache.put(
            sport_key="soccer_epl",
            market_type="aggregated",
            event_id="soccer_epl_date_2024-06-01",
            bookmaker_keys=("__aggregated__",),
            response_json=serialized,
            retrieved_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
            extraction_method="api",
        )

        # Register only a failing adapter
        failing = FakeAdapter(bm_id="fail", should_fail=True)
        registry.register(failing)

        extractor = OddsExtractor(registry, cache, ttl_hours=1)
        result = extractor.extract("soccer_epl", date="2024-06-01")

        assert len(result) == 1
        assert result[0].home_team == "Liverpool"

    def test_all_adapters_fail_no_cache(self, tmp_path):
        """When all adapters fail and no cache, return empty list."""
        db_path = str(tmp_path / "test.db")
        cache = CacheStore(db_path)
        registry = AdapterRegistry()

        failing = FakeAdapter(bm_id="fail", should_fail=True)
        registry.register(failing)

        extractor = OddsExtractor(registry, cache, ttl_hours=24)
        result = extractor.extract("soccer_epl", date="2024-06-01")

        assert result == []

    def test_excluded_bookmakers_filtered(self, tmp_path):
        """Excluded bookmakers are not queried."""
        db_path = str(tmp_path / "test.db")
        cache = CacheStore(db_path)
        registry = AdapterRegistry()

        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        match_1x2 = _make_match("Chelsea", "Arsenal", ts, "1x2")
        match_ou = _make_match("Chelsea", "Arsenal", ts, "over_under")

        # Register 4 adapters, exclude 1
        for i in range(4):
            adapter = FakeAdapter(
                bm_id=f"bm_{i}",
                bm_name=f"BM {i}",
                prio=i + 1,
                matches_1x2=[match_1x2],
                matches_ou=[match_ou],
            )
            registry.register(adapter)

        extractor = OddsExtractor(
            registry, cache, ttl_hours=24, excluded_bookmakers=["bm_0"]
        )
        result = extractor.extract("soccer_epl", date="2024-06-01")

        assert len(result) == 1
        # bm_0 should not be in the odds
        assert "bm_0" not in result[0].bookmaker_odds_1x2

    def test_below_threshold_not_returned(self, tmp_path):
        """Events below MIN_BOOKMAKERS are not in the result."""
        db_path = str(tmp_path / "test.db")
        cache = CacheStore(db_path)
        registry = AdapterRegistry()

        ts = datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc)
        match_1x2 = _make_match("Chelsea", "Arsenal", ts, "1x2")

        # Only 2 adapters — below threshold
        for i in range(2):
            adapter = FakeAdapter(
                bm_id=f"bm_{i}",
                bm_name=f"BM {i}",
                prio=i + 1,
                matches_1x2=[match_1x2],
                matches_ou=[],
            )
            registry.register(adapter)

        extractor = OddsExtractor(registry, cache, ttl_hours=24)
        result = extractor.extract("soccer_epl", date="2024-06-01")

        assert result == []
