"""Adapter registry with auto-discovery for bookmaker odds adapters."""
from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path

from src.adapters.base import AdapterDiscoveryError, AdapterHealth, OddsAdapter

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Auto-discovers and manages OddsAdapter implementations.

    The registry scans the adapters package at startup to find all concrete
    implementations of OddsAdapter, instantiates them, and provides methods
    to query adapters by health status and priority.

    Attributes:
        _adapters: Mapping of bookmaker_id to adapter instance.
        _consecutive_failures: Mapping of bookmaker_id to consecutive failure count.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, OddsAdapter] = {}
        self._consecutive_failures: dict[str, int] = {}

    def discover(self) -> None:
        """Scan the adapters package and instantiate all OddsAdapter subclasses.

        Iterates over all modules in the ``src/adapters/`` package, imports each
        module, and finds classes that inherit from OddsAdapter (excluding the
        ABC itself). Each discovered concrete class is instantiated and registered
        by its ``bookmaker_id``.

        Raises:
            AdapterDiscoveryError: If the adapters package directory cannot be found.
        """
        adapters_package_path = Path(__file__).parent
        if not adapters_package_path.is_dir():
            raise AdapterDiscoveryError(
                f"Adapters directory not found: {adapters_package_path}"
            )

        package_name = "src.adapters"

        for module_info in pkgutil.iter_modules([str(adapters_package_path)]):
            # Skip the base module and __init__
            if module_info.name in ("base", "registry", "__init__"):
                continue

            module_name = f"{package_name}.{module_info.name}"
            try:
                module = importlib.import_module(module_name)
            except Exception as exc:
                logger.warning(
                    "Failed to import adapter module '%s': %s", module_name, exc
                )
                continue

            for _name, obj in inspect.getmembers(module, inspect.isclass):
                if (
                    issubclass(obj, OddsAdapter)
                    and obj is not OddsAdapter
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()
                        self._adapters[instance.bookmaker_id] = instance
                        self._consecutive_failures[instance.bookmaker_id] = 0
                        logger.info(
                            "Discovered adapter: %s (%s)",
                            instance.bookmaker_name,
                            instance.bookmaker_id,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Failed to instantiate adapter class '%s': %s",
                            _name,
                            exc,
                        )

    def register(self, adapter: OddsAdapter) -> None:
        """Manually register an adapter instance.

        Args:
            adapter: An OddsAdapter instance to register.
        """
        self._adapters[adapter.bookmaker_id] = adapter
        self._consecutive_failures.setdefault(adapter.bookmaker_id, 0)

    def get_all(self) -> list[OddsAdapter]:
        """Return all registered adapters sorted by priority (ascending).

        Returns:
            List of all adapters ordered from highest priority (lowest number)
            to lowest priority (highest number).
        """
        return sorted(self._adapters.values(), key=lambda a: a.priority)

    def get_healthy(self) -> list[OddsAdapter]:
        """Return only adapters with health == REACHABLE, sorted by priority.

        An adapter is considered healthy if its ``health_check()`` returns
        REACHABLE and it has not been marked as DEGRADED by the registry
        (i.e., fewer than 3 consecutive failures).

        Returns:
            List of healthy adapters sorted by priority (ascending).
        """
        healthy: list[OddsAdapter] = []
        for adapter in self._adapters.values():
            # Skip adapters marked as degraded by the registry
            if self._consecutive_failures.get(adapter.bookmaker_id, 0) >= 3:
                continue
            if adapter.health_check() == AdapterHealth.REACHABLE:
                healthy.append(adapter)
        return sorted(healthy, key=lambda a: a.priority)

    def list_with_status(self) -> list[dict[str, str]]:
        """Return adapter info dicts with id, name, and health status.

        For each adapter, the health status reflects both the adapter's own
        health_check() and the registry's degradation tracking. If the registry
        has recorded 3 or more consecutive failures, the status is reported as
        DEGRADED regardless of the adapter's own health_check result.

        Returns:
            List of dicts with keys: 'id', 'name', 'health'.
        """
        result: list[dict[str, str]] = []
        for adapter in sorted(self._adapters.values(), key=lambda a: a.priority):
            if self._consecutive_failures.get(adapter.bookmaker_id, 0) >= 3:
                health = AdapterHealth.DEGRADED.value
            else:
                health = adapter.health_check().value
            result.append(
                {
                    "id": adapter.bookmaker_id,
                    "name": adapter.bookmaker_name,
                    "health": health,
                }
            )
        return result

    def record_failure(self, adapter_id: str) -> None:
        """Increment consecutive failure count. Mark DEGRADED at 3.

        After exactly 3 consecutive failures without an intervening success,
        the adapter is considered degraded and will be excluded from healthy
        adapter queries.

        Args:
            adapter_id: The bookmaker_id of the adapter that failed.
        """
        if adapter_id not in self._adapters:
            logger.warning("Cannot record failure for unknown adapter: %s", adapter_id)
            return

        self._consecutive_failures[adapter_id] = (
            self._consecutive_failures.get(adapter_id, 0) + 1
        )
        count = self._consecutive_failures[adapter_id]
        if count >= 3:
            logger.warning(
                "Adapter '%s' marked as DEGRADED after %d consecutive failures. "
                "Investigation recommended.",
                adapter_id,
                count,
            )

    def record_success(self, adapter_id: str) -> None:
        """Reset consecutive failure count for the adapter.

        Args:
            adapter_id: The bookmaker_id of the adapter that succeeded.
        """
        if adapter_id not in self._adapters:
            logger.warning("Cannot record success for unknown adapter: %s", adapter_id)
            return

        self._consecutive_failures[adapter_id] = 0
