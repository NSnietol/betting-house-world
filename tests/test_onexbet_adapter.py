"""Tests for the 1xBet adapter (OneXBetAdapter).

Verifies JSON parsing, team extraction, odds extraction,
date filtering, and health check behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    OddsExtractionError,
)
from src.adapters.onexbet import OneXBetAdapter, _LEAGUE_IDS


@pytest.fixture
def adapter() -> OneXBetAdapter:
    """Create an adapter instance with default resilience config."""
    return OneXBetAdapter()


class TestAdapterProperties:
    """Test adapter metadata properties."""

    def test_bookmaker_id(self, adapter: OneXBetAdapter) -> None:
        assert adapter.bookmaker_id == "onexbet"

    def test_bookmaker_name(self, adapter: OneXBetAdapter) -> None:
        assert adapter.bookmaker_name == "1xBet"

    def test_priority(self, adapter: OneXBetAdapter) -> None:
        assert adapter.priority == 1

    def test_extraction_method(self, adapter: OneXBetAdapter) -> None:
        assert adapter.extraction_method == ExtractionMethod.HTTP

    def test_base_url(self, adapter: OneXBetAdapter) -> None:
        assert adapter.base_url == "https://1xbet.com"


class TestFetch1x2:
    """Test 1X2 market fetching and parsing."""

    def _make_event(
        self,
        home: str = "Arsenal",
        away: str = "Chelsea",
        home_odds: float = 2.10,
        draw_odds: float = 3.40,
        away_odds: float = 3.50,
        timestamp: int = 1717200000,
    ) -> dict:
        """Create a mock 1xBet event with direct odds fields."""
        return {
            "O1": home,
            "O2": away,
            "SC": timestamp,
            "O1": home_odds,
            "OX": draw_odds,
            "O2": away_odds,
        }

    def _make_event_with_names(
        self,
        home: str = "Arsenal",
        away: str = "Chelsea",
        home_odds: float = 2.10,
        draw_odds: float = 3.40,
        away_odds: float = 3.50,
        timestamp: int = 1717200000,
    ) -> dict:
        """Create a mock 1xBet event with team names and odds as separate fields."""
        return {
            "Opp1": home,
            "Opp2": away,
            "SC": timestamp,
            "O1": home_odds,
            "OX": draw_odds,
            "O2": away_odds,
        }

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_basic(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test basic 1X2 extraction with direct odds fields."""
        mock_fetch.return_value = [
            self._make_event_with_names(
                "Arsenal", "Chelsea", 2.10, 3.40, 3.50
            )
        ]

        matches = adapter.fetch_1x2("soccer_epl")

        assert len(matches) == 1
        match = matches[0]
        assert match.home_team == "Arsenal"
        assert match.away_team == "Chelsea"
        assert match.market_type == "1x2"
        assert len(match.outcomes) == 3
        assert match.outcomes[0].name == "Home"
        assert match.outcomes[0].odds == pytest.approx(2.10)
        assert match.outcomes[1].name == "Draw"
        assert match.outcomes[1].odds == pytest.approx(3.40)
        assert match.outcomes[2].name == "Away"
        assert match.outcomes[2].odds == pytest.approx(3.50)

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_with_e_array(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test 1X2 extraction from 'E' array format."""
        mock_fetch.return_value = [
            {
                "Opp1": "Liverpool",
                "Opp2": "Man City",
                "SC": 1717200000,
                "E": [
                    {"T": 1, "C": 2.50},
                    {"T": 2, "C": 3.20},
                    {"T": 3, "C": 2.90},
                ],
            }
        ]

        matches = adapter.fetch_1x2("soccer_epl")

        assert len(matches) == 1
        match = matches[0]
        assert match.outcomes[0].odds == pytest.approx(2.50)
        assert match.outcomes[1].odds == pytest.approx(3.20)
        assert match.outcomes[2].odds == pytest.approx(2.90)

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_date_filter(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test that date filtering works correctly."""
        # 2024-06-01 00:00:00 UTC = 1717200000
        # 2024-06-02 12:00:00 UTC = 1717329600
        mock_fetch.return_value = [
            self._make_event_with_names(
                "Arsenal", "Chelsea", 2.10, 3.40, 3.50, timestamp=1717200000
            ),
            self._make_event_with_names(
                "Liverpool", "Everton", 1.80, 3.60, 4.50, timestamp=1717329600
            ),
        ]

        matches = adapter.fetch_1x2("soccer_epl", date="2024-06-01")

        assert len(matches) == 1
        assert matches[0].home_team == "Arsenal"

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_skips_invalid_odds(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test that events with odds <= 1.0 are skipped."""
        mock_fetch.return_value = [
            {
                "Opp1": "BadTeam",
                "Opp2": "OtherTeam",
                "SC": 1717200000,
                "O1": 0.5,  # Invalid — odds must be > 1.0
                "OX": 3.40,
                "O2": 3.50,
            }
        ]

        matches = adapter.fetch_1x2("soccer_epl")
        assert len(matches) == 0

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_skips_missing_odds(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test that events with missing odds fields are skipped."""
        mock_fetch.return_value = [
            {
                "Opp1": "Team A",
                "Opp2": "Team B",
                "SC": 1717200000,
                # No odds fields at all
            }
        ]

        matches = adapter.fetch_1x2("soccer_epl")
        assert len(matches) == 0

    def test_fetch_1x2_unsupported_sport(self, adapter: OneXBetAdapter) -> None:
        """Test that an unsupported sport key raises OddsExtractionError."""
        with pytest.raises(OddsExtractionError, match="Unsupported sport key"):
            adapter.fetch_1x2("basketball_nba")

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_empty_response(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test handling of empty event list."""
        mock_fetch.return_value = []

        matches = adapter.fetch_1x2("soccer_epl")
        assert matches == []


class TestFetchOverUnder:
    """Test Over/Under 2.5 market fetching and parsing."""

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_over_under_direct_fields(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test over/under extraction from direct TotalOver/TotalUnder fields."""
        mock_fetch.return_value = [
            {
                "Opp1": "Arsenal",
                "Opp2": "Chelsea",
                "SC": 1717200000,
                "TotalOver": 1.85,
                "TotalUnder": 2.00,
            }
        ]

        matches = adapter.fetch_over_under("soccer_epl")

        assert len(matches) == 1
        match = matches[0]
        assert match.market_type == "over_under"
        assert len(match.outcomes) == 2
        assert match.outcomes[0].name == "Over"
        assert match.outcomes[0].odds == pytest.approx(1.85)
        assert match.outcomes[0].point == 2.5
        assert match.outcomes[1].name == "Under"
        assert match.outcomes[1].odds == pytest.approx(2.00)
        assert match.outcomes[1].point == 2.5

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_over_under_e_array(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test over/under extraction from 'E' array with type 9/10."""
        mock_fetch.return_value = [
            {
                "Opp1": "Liverpool",
                "Opp2": "Man City",
                "SC": 1717200000,
                "E": [
                    {"T": 9, "C": 1.90, "P": 2.5},
                    {"T": 10, "C": 1.95, "P": 2.5},
                ],
            }
        ]

        matches = adapter.fetch_over_under("soccer_epl")

        assert len(matches) == 1
        assert matches[0].outcomes[0].odds == pytest.approx(1.90)
        assert matches[0].outcomes[1].odds == pytest.approx(1.95)

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_over_under_ignores_non_2_5_line(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test that over/under with non-2.5 point line is ignored."""
        mock_fetch.return_value = [
            {
                "Opp1": "Team A",
                "Opp2": "Team B",
                "SC": 1717200000,
                "E": [
                    {"T": 9, "C": 1.50, "P": 1.5},  # Different line
                    {"T": 10, "C": 2.50, "P": 1.5},
                ],
            }
        ]

        matches = adapter.fetch_over_under("soccer_epl")
        assert len(matches) == 0

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_over_under_date_filter(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test date filtering for over/under."""
        mock_fetch.return_value = [
            {
                "Opp1": "Arsenal",
                "Opp2": "Chelsea",
                "SC": 1717200000,  # 2024-06-01
                "TotalOver": 1.85,
                "TotalUnder": 2.00,
            },
            {
                "Opp1": "Liverpool",
                "Opp2": "Everton",
                "SC": 1717329600,  # 2024-06-02
                "TotalOver": 1.75,
                "TotalUnder": 2.10,
            },
        ]

        matches = adapter.fetch_over_under("soccer_epl", date="2024-06-02")
        assert len(matches) == 1
        assert matches[0].home_team == "Liverpool"


class TestHealthCheck:
    """Test health check behavior."""

    @patch("src.adapters.onexbet.requests.head")
    def test_health_check_reachable(
        self, mock_head: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns REACHABLE on success."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_head.return_value = mock_response

        # Bypass rate limiter for test
        adapter._scraper._rate_limiter._last_request = {}

        result = adapter.health_check()
        assert result == AdapterHealth.REACHABLE

    @patch("src.adapters.onexbet.requests.head")
    def test_health_check_rate_limited(
        self, mock_head: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns RATE_LIMITED on 429."""
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_head.return_value = mock_response

        adapter._scraper._rate_limiter._last_request = {}

        result = adapter.health_check()
        assert result == AdapterHealth.RATE_LIMITED

    @patch("src.adapters.onexbet.requests.head")
    def test_health_check_unreachable_on_error(
        self, mock_head: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns UNREACHABLE on connection error."""
        mock_head.side_effect = requests.exceptions.ConnectionError("fail")

        adapter._scraper._rate_limiter._last_request = {}

        result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE

    @patch("src.adapters.onexbet.requests.head")
    def test_health_check_unreachable_on_500(
        self, mock_head: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns UNREACHABLE on 500 status."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_head.return_value = mock_response

        adapter._scraper._rate_limiter._last_request = {}

        result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE


class TestFetchLeagueEvents:
    """Test the internal _fetch_league_events method."""

    @patch("requests.get")
    def test_fetch_returns_value_list(
        self, mock_get: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test parsing of {'Value': [...]} response format."""
        mock_response = MagicMock()
        mock_response.json.return_value = {"Value": [{"O1": "Team", "SC": 123}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        adapter._scraper._rate_limiter._last_request = {}

        events = adapter._fetch_league_events(88637)
        assert len(events) == 1

    @patch("requests.get")
    def test_fetch_returns_direct_list(
        self, mock_get: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test parsing of direct list response format."""
        mock_response = MagicMock()
        mock_response.json.return_value = [{"O1": "Team", "SC": 123}]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        adapter._scraper._rate_limiter._last_request = {}

        events = adapter._fetch_league_events(88637)
        assert len(events) == 1

    @patch("requests.get")
    def test_fetch_handles_invalid_json(
        self, mock_get: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test that invalid JSON raises OddsExtractionError."""
        mock_response = MagicMock()
        mock_response.json.side_effect = ValueError("Invalid JSON")
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        adapter._scraper._rate_limiter._last_request = {}

        with pytest.raises(OddsExtractionError, match="Invalid JSON"):
            adapter._fetch_league_events(88637)


class TestLeagueMapping:
    """Test sport key to league ID mapping."""

    def test_supported_leagues(self) -> None:
        """Verify all expected leagues are mapped."""
        assert "soccer_epl" in _LEAGUE_IDS
        assert "soccer_spain_la_liga" in _LEAGUE_IDS
        assert "soccer_germany_bundesliga" in _LEAGUE_IDS
        assert "soccer_italy_serie_a" in _LEAGUE_IDS
        assert "soccer_france_ligue_one" in _LEAGUE_IDS
        assert "soccer_uefa_champs_league" in _LEAGUE_IDS

    def test_league_id_values(self) -> None:
        """Verify league IDs match investigation findings."""
        assert _LEAGUE_IDS["soccer_epl"] == 88637
        assert _LEAGUE_IDS["soccer_spain_la_liga"] == 127733
        assert _LEAGUE_IDS["soccer_germany_bundesliga"] == 96463
        assert _LEAGUE_IDS["soccer_italy_serie_a"] == 110163
        assert _LEAGUE_IDS["soccer_france_ligue_one"] == 12821
        assert _LEAGUE_IDS["soccer_uefa_champs_league"] == 118587


class TestTeamNameExtraction:
    """Test team name extraction from various JSON formats."""

    def test_opp_fields(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from Opp1/Opp2 string fields."""
        event = {"Opp1": "Arsenal", "Opp2": "Chelsea"}
        assert adapter._extract_team_name(event, "home") == "Arsenal"
        assert adapter._extract_team_name(event, "away") == "Chelsea"

    def test_nested_opp_dict(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from nested Opp1/Opp2 dict fields."""
        event = {
            "Opp1": {"Name": "Barcelona"},
            "Opp2": {"Name": "Real Madrid"},
        }
        assert adapter._extract_team_name(event, "home") == "Barcelona"
        assert adapter._extract_team_name(event, "away") == "Real Madrid"

    def test_missing_team_raises_key_error(
        self, adapter: OneXBetAdapter
    ) -> None:
        """Test that missing team names raise KeyError."""
        with pytest.raises(KeyError):
            adapter._extract_team_name({}, "home")


class TestTimestampExtraction:
    """Test timestamp extraction from event data."""

    def test_sc_field_unix(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from SC field as unix timestamp."""
        event = {"SC": 1717200000}
        ts = adapter._extract_timestamp(event)
        assert ts == datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_s_field_unix(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from S field as unix timestamp."""
        event = {"S": 1717200000}
        ts = adapter._extract_timestamp(event)
        assert ts == datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_missing_timestamp_raises(self, adapter: OneXBetAdapter) -> None:
        """Test that missing timestamp raises KeyError."""
        with pytest.raises(KeyError):
            adapter._extract_timestamp({})


class TestGEArrayParsing:
    """Test parsing from the GE (game events) array format."""

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_1x2_from_ge_array(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test 1X2 odds extraction from GE array."""
        mock_fetch.return_value = [
            {
                "Opp1": "Team A",
                "Opp2": "Team B",
                "SC": 1717200000,
                "GE": [
                    {
                        "G": 1,
                        "E": [
                            {"T": 1, "C": 1.95},
                            {"T": 2, "C": 3.50},
                            {"T": 3, "C": 4.20},
                        ],
                    }
                ],
            }
        ]

        matches = adapter.fetch_1x2("soccer_epl")
        assert len(matches) == 1
        assert matches[0].outcomes[0].odds == pytest.approx(1.95)
        assert matches[0].outcomes[1].odds == pytest.approx(3.50)
        assert matches[0].outcomes[2].odds == pytest.approx(4.20)

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_over_under_from_ge_array(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test over/under extraction from GE array with totals group."""
        mock_fetch.return_value = [
            {
                "Opp1": "Team A",
                "Opp2": "Team B",
                "SC": 1717200000,
                "GE": [
                    {
                        "G": 17,
                        "E": [
                            {"T": 9, "C": 1.80, "P": 2.5},
                            {"T": 10, "C": 2.05, "P": 2.5},
                        ],
                    }
                ],
            }
        ]

        matches = adapter.fetch_over_under("soccer_epl")
        assert len(matches) == 1
        assert matches[0].outcomes[0].odds == pytest.approx(1.80)
        assert matches[0].outcomes[1].odds == pytest.approx(2.05)
