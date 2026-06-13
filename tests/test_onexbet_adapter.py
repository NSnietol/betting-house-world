"""Tests for the 1xBet adapter (OneXBetAdapter).

Verifies JSON parsing, team extraction, odds extraction,
date filtering, and health check behavior.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from playwright.sync_api import TimeoutError as PlaywrightTimeout

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
        assert adapter.extraction_method == ExtractionMethod.HEADLESS

    def test_base_url(self, adapter: OneXBetAdapter) -> None:
        assert adapter.base_url == "https://1xbet.com"


class TestFetch1x2:
    """Test 1X2 market fetching and parsing."""

    def _make_event_with_e_array(
        self,
        home: str = "Arsenal",
        away: str = "Chelsea",
        home_odds: float = 2.10,
        draw_odds: float = 3.40,
        away_odds: float = 3.50,
        timestamp: int = 1717200000,
    ) -> dict:
        """Create a mock 1xBet event with E array format (new API)."""
        return {
            "O1": home,
            "O2": away,
            "S": timestamp,
            "E": [
                {"T": 1, "C": home_odds},
                {"T": 2, "C": draw_odds},
                {"T": 3, "C": away_odds},
            ],
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
        """Create a mock 1xBet event with Opp fields and E array."""
        return {
            "Opp1": home,
            "Opp2": away,
            "S": timestamp,
            "E": [
                {"T": 1, "C": home_odds},
                {"T": 2, "C": draw_odds},
                {"T": 3, "C": away_odds},
            ],
        }

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_fetch_1x2_basic(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test basic 1X2 extraction with E array format."""
        mock_fetch.return_value = [
            self._make_event_with_e_array(
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
                "S": 1717200000,
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
            self._make_event_with_e_array(
                "Arsenal", "Chelsea", 2.10, 3.40, 3.50, timestamp=1717200000
            ),
            self._make_event_with_e_array(
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
                "S": 1717200000,
                "E": [
                    {"T": 1, "C": 0.5},  # Invalid — odds must be > 1.0
                    {"T": 2, "C": 3.40},
                    {"T": 3, "C": 3.50},
                ],
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
                "S": 1717200000,
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
                "S": 1717200000,
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
                "S": 1717200000,
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
                "S": 1717200000,
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
                "S": 1717200000,  # 2024-06-01
                "TotalOver": 1.85,
                "TotalUnder": 2.00,
            },
            {
                "Opp1": "Liverpool",
                "Opp2": "Everton",
                "S": 1717329600,  # 2024-06-02
                "TotalOver": 1.75,
                "TotalUnder": 2.10,
            },
        ]

        matches = adapter.fetch_over_under("soccer_epl", date="2024-06-02")
        assert len(matches) == 1
        assert matches[0].home_team == "Liverpool"


class TestHealthCheck:
    """Test health check behavior."""

    @patch("src.adapters.onexbet.sync_playwright")
    def test_health_check_reachable(
        self, mock_pw: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns REACHABLE on success."""
        # Set up mock chain
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 200

        mock_page.goto.return_value = mock_response
        mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_pw.return_value.start.return_value = mock_pw_instance

        result = adapter.health_check()
        assert result == AdapterHealth.REACHABLE

    @patch("src.adapters.onexbet.sync_playwright")
    def test_health_check_unreachable_on_timeout(
        self, mock_pw: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns UNREACHABLE on timeout."""
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_page.goto.side_effect = PlaywrightTimeout("Timeout")

        mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_pw.return_value.start.return_value = mock_pw_instance

        result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE

    @patch("src.adapters.onexbet.sync_playwright")
    def test_health_check_unreachable_on_error(
        self, mock_pw: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns UNREACHABLE on connection error."""
        mock_pw.return_value.start.side_effect = Exception("Connection failed")

        result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE

    @patch("src.adapters.onexbet.sync_playwright")
    def test_health_check_unreachable_on_500(
        self, mock_pw: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test health check returns UNREACHABLE on 500 status."""
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_response = MagicMock()
        mock_response.status = 500

        mock_page.goto.return_value = mock_response
        mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_pw.return_value.start.return_value = mock_pw_instance

        result = adapter.health_check()
        assert result == AdapterHealth.UNREACHABLE


class TestFetchLeagueEvents:
    """Test the internal _fetch_league_events method via Playwright mock."""

    @patch("src.adapters.onexbet.sync_playwright")
    def test_fetch_captures_api_response(
        self, mock_pw: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test that _fetch_league_events captures API intercept data."""
        mock_browser = MagicMock()
        mock_page = MagicMock()
        mock_page.goto.return_value = MagicMock(status=200)
        mock_page.wait_for_selector.return_value = None
        mock_page.wait_for_timeout.return_value = None

        # Simulate the response interception callback
        response_callbacks = []

        def mock_on(event_name, callback):
            if event_name == "response":
                response_callbacks.append(callback)

        mock_page.on = mock_on
        mock_browser.new_page.return_value = mock_page
        mock_pw_instance = MagicMock()
        mock_pw_instance.chromium.launch.return_value = mock_browser
        mock_pw.return_value.start.return_value = mock_pw_instance

        # Trigger the fetch — this sets up the response handler
        # We need to simulate the response callback being triggered
        import json

        api_response_data = {
            "Value": [
                {"O1": "Qatar", "O2": "Switzerland", "S": 1717200000,
                 "N": 88637, "E": [{"T": 1, "C": 16.0}, {"T": 2, "C": 7.3}, {"T": 3, "C": 1.21}]},
            ]
        }

        # Override goto to trigger the callback
        def simulate_navigation(url, **kwargs):
            # Trigger the captured response callback
            if response_callbacks:
                mock_response = MagicMock()
                mock_response.url = "https://col-1xbet.com/service-api/LineFeed/Get1x2_VZip?sports=1"
                mock_response.status = 200
                mock_response.text.return_value = json.dumps(api_response_data)
                response_callbacks[0](mock_response)
            return MagicMock(status=200)

        mock_page.goto = simulate_navigation

        events = adapter._fetch_league_events(88637)
        assert len(events) == 1
        assert events[0]["O1"] == "Qatar"


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
        assert "soccer_world_cup" in _LEAGUE_IDS

    def test_league_id_values(self) -> None:
        """Verify league IDs match investigation findings."""
        assert _LEAGUE_IDS["soccer_epl"] == 88637
        assert _LEAGUE_IDS["soccer_spain_la_liga"] == 127733
        assert _LEAGUE_IDS["soccer_germany_bundesliga"] == 96463
        assert _LEAGUE_IDS["soccer_italy_serie_a"] == 110163
        assert _LEAGUE_IDS["soccer_france_ligue_one"] == 12821
        assert _LEAGUE_IDS["soccer_uefa_champs_league"] == 118587
        assert _LEAGUE_IDS["soccer_world_cup"] == 2708736


class TestTeamNameExtraction:
    """Test team name extraction from various JSON formats."""

    def test_o1_o2_fields(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from O1/O2 string fields."""
        event = {"O1": "Arsenal", "O2": "Chelsea"}
        assert adapter._extract_team_name(event, "home") == "Arsenal"
        assert adapter._extract_team_name(event, "away") == "Chelsea"

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

    def test_s_field_unix(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from S field as unix timestamp (new API)."""
        event = {"S": 1717200000}
        ts = adapter._extract_timestamp(event)
        assert ts == datetime(2024, 6, 1, 0, 0, 0, tzinfo=timezone.utc)

    def test_sc_field_unix(self, adapter: OneXBetAdapter) -> None:
        """Test extraction from SC field as unix timestamp (legacy)."""
        event = {"SC": 1717200000}
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
                "S": 1717200000,
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
                "S": 1717200000,
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


class TestNewAPIFormat:
    """Test parsing from the new LineFeed Get1x2_VZip response format."""

    @patch.object(OneXBetAdapter, "_fetch_league_events")
    def test_real_world_event_format(
        self, mock_fetch: MagicMock, adapter: OneXBetAdapter
    ) -> None:
        """Test parsing the actual API response format observed from 1xBet."""
        # This matches the real structure captured from the API
        mock_fetch.return_value = [
            {
                "O1": "Qatar",
                "O2": "Switzerland",
                "S": 1781377200,
                "N": 167688,
                "LI": 2708736,
                "CI": 290917079,
                "E": [
                    {"T": 6, "C": 1.032, "G": 8},
                    {"T": 5, "C": 1.118, "G": 8},
                    {"T": 3, "C": 1.21, "G": 1},
                    {"T": 9, "P": 2.5, "C": 1.636, "G": 17, "CE": 1},
                    {"T": 10, "P": 2.5, "C": 2.294, "G": 17, "CE": 1},
                    {"T": 2, "C": 7.3, "G": 1},
                    {"T": 1, "C": 16.0, "G": 1},
                ],
                "WP": {"P1": 0.06, "P2": 0.81, "PX": 0.13},
            }
        ]

        # Test 1X2
        matches_1x2 = adapter.fetch_1x2("soccer_world_cup")
        assert len(matches_1x2) == 1
        m = matches_1x2[0]
        assert m.home_team == "Qatar"
        assert m.away_team == "Switzerland"
        assert m.outcomes[0].odds == pytest.approx(16.0)  # Home
        assert m.outcomes[1].odds == pytest.approx(7.3)   # Draw
        assert m.outcomes[2].odds == pytest.approx(1.21)  # Away

        # Test Over/Under
        matches_ou = adapter.fetch_over_under("soccer_world_cup")
        assert len(matches_ou) == 1
        m_ou = matches_ou[0]
        assert m_ou.outcomes[0].name == "Over"
        assert m_ou.outcomes[0].odds == pytest.approx(1.636)
        assert m_ou.outcomes[0].point == 2.5
        assert m_ou.outcomes[1].name == "Under"
        assert m_ou.outcomes[1].odds == pytest.approx(2.294)
        assert m_ou.outcomes[1].point == 2.5
