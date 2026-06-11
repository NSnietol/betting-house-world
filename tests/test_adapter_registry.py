"""Unit tests for the AdapterRegistry class."""
from __future__ import annotations

import pytest

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    OddsAdapter,
    ScrapedMatch,
)
from src.adapters.registry import AdapterRegistry


class FakeAdapter(OddsAdapter):
    """A concrete adapter for testing purposes."""

    def __init__(
        self,
        bm_id: str = "fake",
        bm_name: str = "Fake Bookmaker",
        prio: int = 1,
        health: AdapterHealth = AdapterHealth.REACHABLE,
    ) -> None:
        self._id = bm_id
        self._name = bm_name
        self._priority = prio
        self._health = health

    @property
    def bookmaker_id(self) -> str:
        return self._id

    @property
    def bookmaker_name(self) -> str:
        return self._name

    @property
    def priority(self) -> int:
        return self._priority

    @property
    def extraction_method(self) -> ExtractionMethod:
        return ExtractionMethod.HTTP

    @property
    def base_url(self) -> str:
        return "https://fake.example.com"

    def fetch_1x2(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        return []

    def fetch_over_under(self, sport: str, date: str | None = None) -> list[ScrapedMatch]:
        return []

    def health_check(self) -> AdapterHealth:
        return self._health


class TestAdapterRegistryGetAll:
    """Tests for get_all method."""

    def test_empty_registry_returns_empty_list(self) -> None:
        registry = AdapterRegistry()
        assert registry.get_all() == []

    def test_single_adapter(self) -> None:
        registry = AdapterRegistry()
        adapter = FakeAdapter(bm_id="pinnacle", prio=1)
        registry.register(adapter)
        result = registry.get_all()
        assert len(result) == 1
        assert result[0].bookmaker_id == "pinnacle"

    def test_multiple_adapters_sorted_by_priority(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="low_prio", prio=10))
        registry.register(FakeAdapter(bm_id="high_prio", prio=1))
        registry.register(FakeAdapter(bm_id="mid_prio", prio=5))

        result = registry.get_all()
        assert [a.bookmaker_id for a in result] == [
            "high_prio",
            "mid_prio",
            "low_prio",
        ]


class TestAdapterRegistryGetHealthy:
    """Tests for get_healthy method."""

    def test_only_reachable_adapters_returned(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="healthy", health=AdapterHealth.REACHABLE))
        registry.register(
            FakeAdapter(bm_id="down", health=AdapterHealth.UNREACHABLE, prio=2)
        )

        result = registry.get_healthy()
        assert len(result) == 1
        assert result[0].bookmaker_id == "healthy"

    def test_degraded_adapter_excluded(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="degraded", health=AdapterHealth.REACHABLE))
        # Simulate 3 consecutive failures
        registry.record_failure("degraded")
        registry.record_failure("degraded")
        registry.record_failure("degraded")

        result = registry.get_healthy()
        assert len(result) == 0

    def test_healthy_sorted_by_priority(self) -> None:
        registry = AdapterRegistry()
        registry.register(
            FakeAdapter(bm_id="b", prio=5, health=AdapterHealth.REACHABLE)
        )
        registry.register(
            FakeAdapter(bm_id="a", prio=1, health=AdapterHealth.REACHABLE)
        )

        result = registry.get_healthy()
        assert [a.bookmaker_id for a in result] == ["a", "b"]


class TestAdapterRegistryListWithStatus:
    """Tests for list_with_status method."""

    def test_returns_correct_structure(self) -> None:
        registry = AdapterRegistry()
        registry.register(
            FakeAdapter(bm_id="test", bm_name="Test Bookie", health=AdapterHealth.REACHABLE)
        )

        result = registry.list_with_status()
        assert len(result) == 1
        assert result[0] == {
            "id": "test",
            "name": "Test Bookie",
            "health": "reachable",
        }

    def test_degraded_status_overrides_adapter_health(self) -> None:
        registry = AdapterRegistry()
        registry.register(
            FakeAdapter(bm_id="failing", bm_name="Failing", health=AdapterHealth.REACHABLE)
        )
        registry.record_failure("failing")
        registry.record_failure("failing")
        registry.record_failure("failing")

        result = registry.list_with_status()
        assert result[0]["health"] == "degraded"


class TestAdapterRegistryFailureTracking:
    """Tests for record_failure and record_success methods."""

    def test_record_failure_increments_count(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="test"))
        registry.record_failure("test")
        registry.record_failure("test")
        # Should still be healthy (< 3 failures)
        assert registry.get_healthy() == [registry._adapters["test"]]

    def test_three_failures_triggers_degradation(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="test", health=AdapterHealth.REACHABLE))
        registry.record_failure("test")
        registry.record_failure("test")
        registry.record_failure("test")
        # Should now be degraded
        assert registry.get_healthy() == []

    def test_record_success_resets_failure_count(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="test", health=AdapterHealth.REACHABLE))
        registry.record_failure("test")
        registry.record_failure("test")
        registry.record_success("test")
        # After reset, 3 more failures needed for degradation
        registry.record_failure("test")
        registry.record_failure("test")
        assert len(registry.get_healthy()) == 1

    def test_record_failure_unknown_adapter_no_crash(self) -> None:
        registry = AdapterRegistry()
        # Should not raise
        registry.record_failure("nonexistent")

    def test_record_success_unknown_adapter_no_crash(self) -> None:
        registry = AdapterRegistry()
        # Should not raise
        registry.record_success("nonexistent")

    def test_success_after_degradation_restores_health(self) -> None:
        registry = AdapterRegistry()
        registry.register(FakeAdapter(bm_id="test", health=AdapterHealth.REACHABLE))
        registry.record_failure("test")
        registry.record_failure("test")
        registry.record_failure("test")
        assert registry.get_healthy() == []

        registry.record_success("test")
        assert len(registry.get_healthy()) == 1


class TestAdapterRegistryDiscover:
    """Tests for the discover method."""

    def test_discover_does_not_crash_on_empty_package(self) -> None:
        """Discover should work even if no concrete adapters exist yet."""
        registry = AdapterRegistry()
        registry.discover()
        # base.py and registry.py are skipped, __init__.py is skipped
        # No other adapter files exist, so nothing should be registered
        # (or concrete adapters if any exist in the package)
        # Main assertion: no exception raised
        assert isinstance(registry.get_all(), list)
