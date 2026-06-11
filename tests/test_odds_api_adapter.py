"""Tests for the Odds API adapter."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    OddsExtractionError,
)
from src.adapters.odds_api import OddsAPIAdapter


class TestOddsAPIAdapterProperties:
    """Test adapter metadata properties."""

    def test_bookmaker_id(self) -> None:
        adapter = OddsAPIAdapter()
        assert adapter.bookmaker_id == "the_odds_api"

    def test_bookmaker_name(self) -> None:
        adapter = OddsAPIAdapter()
        assert adapter.bookmaker_name == "The Odds API"

    def test_priority_is_10(self) -> None:
        adapter = OddsAPIAdapter()
        assert adapter.priority == 10

    def test_extraction_method_is_api(self) -> None:
        adapter = OddsAPIAdapter()
        assert adapter.extraction_method == ExtractionMethod.API

    def test_base_url(self) -> None:
        adapter = OddsAPIAdapter()
        assert adapter.base_url == "https://api.the-odds-api.com"


class TestOddsAPIAdapterHealthCheck:
    """Test health_check behavior."""

    def test_health_unreachable_without_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            # Ensure ODDS_API_KEY is not set
            os.environ.pop("ODDS_API_KEY", None)
            adapter = OddsAPIAdapter()
        assert adapter.health_check() == AdapterHealth.UNREACHABLE

    @patch("src.adapters.odds_api.requests.get")
    def test_health_reachable_with_valid_key(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(status_code=200)
        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            result = adapter.health_check()
        assert result == AdapterHealth.REACHABLE

    @patch("src.adapters.odds_api.requests.get")
    def test_health_rate_limited_on_429(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(status_code=429)
        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            result = adapter.health_check()
        assert result == AdapterHealth.RATE_LIMITED

    @patch("src.adapters.odds_api.requests.get")
    def test_health_unreachable_on_401(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(status_code=401)
        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE

    @patch("src.adapters.odds_api.requests.get")
    def test_health_unreachable_on_network_error(self, mock_get: MagicMock) -> None:
        import requests as req

        mock_get.side_effect = req.ConnectionError("Connection refused")
        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE


class TestOddsAPIAdapterFetch1x2:
    """Test fetch_1x2 method."""

    def test_raises_without_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ODDS_API_KEY", None)
            adapter = OddsAPIAdapter()
        with pytest.raises(OddsExtractionError, match="ODDS_API_KEY"):
            adapter.fetch_1x2("soccer_epl")

    @patch("src.adapters.odds_api.requests.get")
    def test_parses_h2h_response(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = [
            {
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2024-06-01T15:00:00Z",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Arsenal", "price": 2.10},
                                    {"name": "Chelsea", "price": 3.40},
                                    {"name": "Draw", "price": 3.20},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            matches = adapter.fetch_1x2("soccer_epl")

        assert len(matches) == 1
        match = matches[0]
        assert match.home_team == "Arsenal"
        assert match.away_team == "Chelsea"
        assert match.market_type == "1x2"
        assert len(match.outcomes) == 3
        assert match.outcomes[0].name == "Arsenal"
        assert match.outcomes[0].odds == 2.10

    @patch("src.adapters.odds_api.requests.get")
    def test_filters_by_date(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = [
            {
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2024-06-01T15:00:00Z",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Arsenal", "price": 2.10},
                                    {"name": "Chelsea", "price": 3.40},
                                    {"name": "Draw", "price": 3.20},
                                ],
                            }
                        ],
                    }
                ],
            },
            {
                "home_team": "Liverpool",
                "away_team": "Man City",
                "commence_time": "2024-06-02T15:00:00Z",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Liverpool", "price": 2.50},
                                    {"name": "Man City", "price": 2.80},
                                    {"name": "Draw", "price": 3.10},
                                ],
                            }
                        ],
                    }
                ],
            },
        ]
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            matches = adapter.fetch_1x2("soccer_epl", date="2024-06-01")

        assert len(matches) == 1
        assert matches[0].home_team == "Arsenal"

    @patch("src.adapters.odds_api.requests.get")
    def test_raises_on_401(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock(status_code=401, text="Unauthorized")
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"ODDS_API_KEY": "bad_key"}):
            adapter = OddsAPIAdapter()
            with pytest.raises(OddsExtractionError, match="401"):
                adapter.fetch_1x2("soccer_epl")

    @patch("src.adapters.odds_api.requests.get")
    def test_raises_on_429(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock(status_code=429, text="Rate limited")
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            with pytest.raises(OddsExtractionError, match="429"):
                adapter.fetch_1x2("soccer_epl")

    @patch("src.adapters.odds_api.requests.get")
    def test_raises_on_timeout(self, mock_get: MagicMock) -> None:
        import requests as req

        mock_get.side_effect = req.Timeout("Request timed out")

        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            with pytest.raises(OddsExtractionError, match="TIMEOUT"):
                adapter.fetch_1x2("soccer_epl")


class TestOddsAPIAdapterFetchOverUnder:
    """Test fetch_over_under method."""

    def test_raises_without_api_key(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ODDS_API_KEY", None)
            adapter = OddsAPIAdapter()
        with pytest.raises(OddsExtractionError, match="ODDS_API_KEY"):
            adapter.fetch_over_under("soccer_epl")

    @patch("src.adapters.odds_api.requests.get")
    def test_parses_totals_response(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock(status_code=200)
        mock_response.json.return_value = [
            {
                "home_team": "Arsenal",
                "away_team": "Chelsea",
                "commence_time": "2024-06-01T15:00:00Z",
                "bookmakers": [
                    {
                        "key": "pinnacle",
                        "markets": [
                            {
                                "key": "totals",
                                "outcomes": [
                                    {"name": "Over", "price": 1.85, "point": 2.5},
                                    {"name": "Under", "price": 2.00, "point": 2.5},
                                ],
                            }
                        ],
                    }
                ],
            }
        ]
        mock_get.return_value = mock_response

        with patch.dict(os.environ, {"ODDS_API_KEY": "test_key"}):
            adapter = OddsAPIAdapter()
            matches = adapter.fetch_over_under("soccer_epl")

        assert len(matches) == 1
        match = matches[0]
        assert match.home_team == "Arsenal"
        assert match.away_team == "Chelsea"
        assert match.market_type == "over_under"
        assert len(match.outcomes) == 2
        assert match.outcomes[0].name == "Over"
        assert match.outcomes[0].odds == 1.85
        assert match.outcomes[0].point == 2.5
        assert match.outcomes[1].name == "Under"
        assert match.outcomes[1].odds == 2.00
        assert match.outcomes[1].point == 2.5


class TestOddsAPIAdapterRegistryIntegration:
    """Test that the adapter integrates with the registry."""

    def test_auto_discovered_by_registry(self) -> None:
        from src.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.discover()

        adapter_ids = [a.bookmaker_id for a in registry.get_all()]
        assert "the_odds_api" in adapter_ids

    def test_lower_priority_than_scraping_adapters(self) -> None:
        from src.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        registry.discover()

        all_adapters = registry.get_all()
        odds_api = next(a for a in all_adapters if a.bookmaker_id == "the_odds_api")
        other_adapters = [a for a in all_adapters if a.bookmaker_id != "the_odds_api"]

        for other in other_adapters:
            assert odds_api.priority > other.priority
