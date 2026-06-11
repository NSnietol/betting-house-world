"""Unit tests for the Unibet/Kambi adapter."""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.adapters.base import (
    AdapterHealth,
    ExtractionMethod,
    MarketOutcome,
    OddsExtractionError,
    ScrapedMatch,
)
from src.adapters.unibet import UnibetAdapter, _LEAGUE_PATHS


@pytest.fixture
def adapter() -> UnibetAdapter:
    """Create a fresh UnibetAdapter instance."""
    return UnibetAdapter()


class TestUnibetAdapterProperties:
    """Test basic adapter properties."""

    def test_bookmaker_id(self, adapter: UnibetAdapter) -> None:
        assert adapter.bookmaker_id == "unibet"

    def test_bookmaker_name(self, adapter: UnibetAdapter) -> None:
        assert adapter.bookmaker_name == "Unibet (Kambi)"

    def test_priority(self, adapter: UnibetAdapter) -> None:
        assert adapter.priority == 2

    def test_extraction_method(self, adapter: UnibetAdapter) -> None:
        assert adapter.extraction_method == ExtractionMethod.HTTP

    def test_base_url(self, adapter: UnibetAdapter) -> None:
        assert "kambicdn.com" in adapter.base_url


class TestLeaguePathMapping:
    """Test sport key to Kambi league path mapping."""

    def test_supported_sport_keys(self, adapter: UnibetAdapter) -> None:
        assert adapter._get_league_path("soccer_epl") == "football/england/premier_league"
        assert adapter._get_league_path("soccer_spain_la_liga") == "football/spain/la_liga"
        assert adapter._get_league_path("soccer_germany_bundesliga") == "football/germany/bundesliga"
        assert adapter._get_league_path("soccer_italy_serie_a") == "football/italy/serie_a"
        assert adapter._get_league_path("soccer_france_ligue_one") == "football/france/ligue_1"
        assert adapter._get_league_path("soccer_uefa_champs_league") == "football/champions_league"

    def test_unsupported_sport_raises_error(self, adapter: UnibetAdapter) -> None:
        with pytest.raises(OddsExtractionError, match="Unsupported sport key"):
            adapter._get_league_path("soccer_unknown_league")


class TestExtractTeams:
    """Test team name extraction from different event data formats."""

    def test_home_away_name_fields(self, adapter: UnibetAdapter) -> None:
        event_data = {"homeName": "Arsenal", "awayName": "Chelsea"}
        home, away = adapter._extract_teams(event_data)
        assert home == "Arsenal"
        assert away == "Chelsea"

    def test_name_with_vs_separator(self, adapter: UnibetAdapter) -> None:
        event_data = {"name": "Manchester United vs Liverpool"}
        home, away = adapter._extract_teams(event_data)
        assert home == "Manchester United"
        assert away == "Liverpool"

    def test_name_with_dash_separator(self, adapter: UnibetAdapter) -> None:
        event_data = {"name": "Barcelona - Real Madrid"}
        home, away = adapter._extract_teams(event_data)
        assert home == "Barcelona"
        assert away == "Real Madrid"

    def test_participants_array(self, adapter: UnibetAdapter) -> None:
        event_data = {
            "participants": [
                {"name": "Bayern Munich"},
                {"name": "Borussia Dortmund"},
            ]
        }
        home, away = adapter._extract_teams(event_data)
        assert home == "Bayern Munich"
        assert away == "Borussia Dortmund"

    def test_missing_teams_raises_key_error(self, adapter: UnibetAdapter) -> None:
        event_data = {"id": "12345"}
        with pytest.raises(KeyError):
            adapter._extract_teams(event_data)


class TestExtractTimestamp:
    """Test event timestamp extraction."""

    def test_iso_format_with_z(self, adapter: UnibetAdapter) -> None:
        event_data = {"start": "2024-06-01T15:00:00Z"}
        ts = adapter._extract_timestamp(event_data)
        assert ts.year == 2024
        assert ts.month == 6
        assert ts.day == 1
        assert ts.hour == 15

    def test_epoch_milliseconds(self, adapter: UnibetAdapter) -> None:
        # 2024-06-01 15:00:00 UTC in milliseconds
        epoch_ms = 1717254000000
        event_data = {"start": epoch_ms}
        ts = adapter._extract_timestamp(event_data)
        assert ts.year == 2024
        assert ts.month == 6
        assert ts.day == 1

    def test_fallback_to_now(self, adapter: UnibetAdapter) -> None:
        event_data = {}
        ts = adapter._extract_timestamp(event_data)
        # Should be close to current time
        assert ts.tzinfo is not None


class TestParse1x2Outcomes:
    """Test 1X2 outcome parsing from Kambi bet offers."""

    def test_standard_1x2_with_milliodds(self, adapter: UnibetAdapter) -> None:
        bet_offers = [
            {
                "criterion": {"label": "Full Time"},
                "betOfferType": {"name": "Match"},
                "outcomes": [
                    {"label": "1", "type": "home", "odds": 2450},
                    {"label": "X", "type": "draw", "odds": 3200},
                    {"label": "2", "type": "away", "odds": 2850},
                ],
            }
        ]
        outcomes = adapter._find_1x2_outcomes(bet_offers)
        assert len(outcomes) == 3
        assert outcomes[0].name == "Home"
        assert outcomes[0].odds == pytest.approx(2.45)
        assert outcomes[1].name == "Draw"
        assert outcomes[1].odds == pytest.approx(3.20)
        assert outcomes[2].name == "Away"
        assert outcomes[2].odds == pytest.approx(2.85)

    def test_decimal_odds_format(self, adapter: UnibetAdapter) -> None:
        bet_offers = [
            {
                "criterion": {"label": "Match Winner"},
                "betOfferType": {"name": "1X2"},
                "outcomes": [
                    {"label": "Home", "type": "home", "odds": 1.85},
                    {"label": "Draw", "type": "draw", "odds": 3.50},
                    {"label": "Away", "type": "away", "odds": 4.20},
                ],
            }
        ]
        outcomes = adapter._find_1x2_outcomes(bet_offers)
        assert len(outcomes) == 3
        assert outcomes[0].odds == pytest.approx(1.85)
        assert outcomes[1].odds == pytest.approx(3.50)
        assert outcomes[2].odds == pytest.approx(4.20)

    def test_no_matching_market_returns_empty(self, adapter: UnibetAdapter) -> None:
        bet_offers = [
            {
                "criterion": {"label": "Both Teams to Score"},
                "betOfferType": {"name": "BTTS"},
                "outcomes": [
                    {"label": "Yes", "odds": 1800},
                    {"label": "No", "odds": 1950},
                ],
            }
        ]
        outcomes = adapter._find_1x2_outcomes(bet_offers)
        assert outcomes == []

    def test_empty_bet_offers_returns_empty(self, adapter: UnibetAdapter) -> None:
        outcomes = adapter._find_1x2_outcomes([])
        assert outcomes == []

    def test_positional_fallback_mapping(self, adapter: UnibetAdapter) -> None:
        """When outcome types/labels don't match, use positional mapping."""
        bet_offers = [
            {
                "criterion": {"label": "Full Time"},
                "betOfferType": {"name": "Match"},
                "outcomes": [
                    {"label": "unknown1", "odds": 2100},
                    {"label": "unknown2", "odds": 3400},
                    {"label": "unknown3", "odds": 3100},
                ],
            }
        ]
        outcomes = adapter._find_1x2_outcomes(bet_offers)
        assert len(outcomes) == 3
        assert outcomes[0].odds == pytest.approx(2.10)
        assert outcomes[1].odds == pytest.approx(3.40)
        assert outcomes[2].odds == pytest.approx(3.10)


class TestParseOverUnderOutcomes:
    """Test Over/Under outcome parsing from Kambi bet offers."""

    def test_standard_over_under_with_milliodds(self, adapter: UnibetAdapter) -> None:
        bet_offers = [
            {
                "criterion": {"label": "Total Goals - Over/Under"},
                "betOfferType": {"name": "Over/Under"},
                "outcomes": [
                    {"label": "Over", "type": "over", "odds": 1900, "line": 2500},
                    {"label": "Under", "type": "under", "odds": 1850, "line": 2500},
                ],
            }
        ]
        outcomes = adapter._find_over_under_outcomes(bet_offers)
        assert len(outcomes) == 2
        assert outcomes[0].name == "Over"
        assert outcomes[0].odds == pytest.approx(1.90)
        assert outcomes[0].point == 2.5
        assert outcomes[1].name == "Under"
        assert outcomes[1].odds == pytest.approx(1.85)
        assert outcomes[1].point == 2.5

    def test_decimal_odds_format(self, adapter: UnibetAdapter) -> None:
        bet_offers = [
            {
                "criterion": {"label": "Over/Under"},
                "betOfferType": {"name": "Total"},
                "outcomes": [
                    {"label": "Over", "type": "over", "odds": 2.10, "line": 2.5},
                    {"label": "Under", "type": "under", "odds": 1.75, "line": 2.5},
                ],
            }
        ]
        outcomes = adapter._find_over_under_outcomes(bet_offers)
        assert len(outcomes) == 2
        assert outcomes[0].odds == pytest.approx(2.10)
        assert outcomes[1].odds == pytest.approx(1.75)

    def test_no_matching_market_returns_empty(self, adapter: UnibetAdapter) -> None:
        bet_offers = [
            {
                "criterion": {"label": "Correct Score"},
                "betOfferType": {"name": "Score"},
                "outcomes": [
                    {"label": "1-0", "odds": 6500},
                    {"label": "2-1", "odds": 7200},
                ],
            }
        ]
        outcomes = adapter._find_over_under_outcomes(bet_offers)
        assert outcomes == []

    def test_empty_bet_offers_returns_empty(self, adapter: UnibetAdapter) -> None:
        outcomes = adapter._find_over_under_outcomes([])
        assert outcomes == []


class TestFetch1x2:
    """Test fetch_1x2 with mocked HTTP responses."""

    def test_successful_fetch_returns_matches(self, adapter: UnibetAdapter) -> None:
        mock_response = {
            "events": [
                {
                    "id": 1001,
                    "homeName": "Arsenal",
                    "awayName": "Chelsea",
                    "start": "2024-06-01T15:00:00Z",
                },
            ],
            "betOffers": [
                {
                    "eventId": 1001,
                    "criterion": {"label": "Full Time"},
                    "betOfferType": {"name": "Match"},
                    "outcomes": [
                        {"label": "1", "type": "home", "odds": 2100},
                        {"label": "X", "type": "draw", "odds": 3400},
                        {"label": "2", "type": "away", "odds": 3100},
                    ],
                }
            ],
        }

        with patch.object(adapter, "_fetch_json", return_value=mock_response):
            matches = adapter.fetch_1x2("soccer_epl")

        assert len(matches) == 1
        assert matches[0].home_team == "Arsenal"
        assert matches[0].away_team == "Chelsea"
        assert matches[0].market_type == "1x2"
        assert len(matches[0].outcomes) == 3

    def test_date_filter(self, adapter: UnibetAdapter) -> None:
        mock_response = {
            "events": [
                {
                    "id": 1001,
                    "homeName": "Arsenal",
                    "awayName": "Chelsea",
                    "start": "2024-06-01T15:00:00Z",
                },
            ],
            "betOffers": [
                {
                    "eventId": 1001,
                    "criterion": {"label": "Full Time"},
                    "betOfferType": {"name": "Match"},
                    "outcomes": [
                        {"label": "1", "type": "home", "odds": 2100},
                        {"label": "X", "type": "draw", "odds": 3400},
                        {"label": "2", "type": "away", "odds": 3100},
                    ],
                }
            ],
        }

        with patch.object(adapter, "_fetch_json", return_value=mock_response):
            # Match on correct date
            matches = adapter.fetch_1x2("soccer_epl", date="2024-06-01")
            assert len(matches) == 1

            # No match on wrong date
            matches = adapter.fetch_1x2("soccer_epl", date="2024-06-02")
            assert len(matches) == 0

    def test_empty_response_returns_empty_list(self, adapter: UnibetAdapter) -> None:
        with patch.object(adapter, "_fetch_json", return_value={"events": [], "betOffers": []}):
            matches = adapter.fetch_1x2("soccer_epl")
            assert matches == []

    def test_unsupported_sport_raises_error(self, adapter: UnibetAdapter) -> None:
        with pytest.raises(OddsExtractionError, match="Unsupported sport key"):
            adapter.fetch_1x2("basketball_nba")


class TestFetchOverUnder:
    """Test fetch_over_under with mocked HTTP responses."""

    def test_successful_fetch_returns_matches(self, adapter: UnibetAdapter) -> None:
        mock_response = {
            "events": [
                {
                    "id": 2001,
                    "homeName": "Liverpool",
                    "awayName": "Man City",
                    "start": "2024-06-01T17:30:00Z",
                },
            ],
            "betOffers": [
                {
                    "eventId": 2001,
                    "criterion": {"label": "Total Goals - Over/Under"},
                    "betOfferType": {"name": "Over/Under"},
                    "outcomes": [
                        {"label": "Over", "type": "over", "odds": 1950, "line": 2500},
                        {"label": "Under", "type": "under", "odds": 1850, "line": 2500},
                    ],
                }
            ],
        }

        with patch.object(adapter, "_fetch_json", return_value=mock_response):
            matches = adapter.fetch_over_under("soccer_epl")

        assert len(matches) == 1
        assert matches[0].home_team == "Liverpool"
        assert matches[0].away_team == "Man City"
        assert matches[0].market_type == "over_under"
        assert len(matches[0].outcomes) == 2
        assert matches[0].outcomes[0].point == 2.5


class TestHealthCheck:
    """Test health_check method."""

    def test_healthy_when_api_responds(self, adapter: UnibetAdapter) -> None:
        with patch.object(adapter, "_fetch_json", return_value={"events": []}):
            assert adapter.health_check() == AdapterHealth.REACHABLE

    def test_unreachable_when_api_fails(self, adapter: UnibetAdapter) -> None:
        with patch.object(
            adapter,
            "_fetch_json",
            side_effect=OddsExtractionError("Connection failed"),
        ):
            assert adapter.health_check() == AdapterHealth.UNREACHABLE


class TestExtractEvents:
    """Test _extract_events from various response formats."""

    def test_events_at_top_level(self, adapter: UnibetAdapter) -> None:
        data = {"events": [{"id": 1}, {"id": 2}]}
        events = adapter._extract_events(data)
        assert len(events) == 2

    def test_result_list_fallback(self, adapter: UnibetAdapter) -> None:
        data = {"result": [{"id": 1}]}
        events = adapter._extract_events(data)
        assert len(events) == 1

    def test_empty_response(self, adapter: UnibetAdapter) -> None:
        events = adapter._extract_events({})
        assert events == []

    def test_non_dict_input(self, adapter: UnibetAdapter) -> None:
        events = adapter._extract_events([])  # type: ignore
        assert events == []


class TestMatchesDate:
    """Test the static _matches_date helper."""

    def test_matching_date(self) -> None:
        match = ScrapedMatch(
            home_team="A",
            away_team="B",
            event_timestamp=datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc),
            market_type="1x2",
        )
        assert UnibetAdapter._matches_date(match, "2024-06-01") is True

    def test_non_matching_date(self) -> None:
        match = ScrapedMatch(
            home_team="A",
            away_team="B",
            event_timestamp=datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc),
            market_type="1x2",
        )
        assert UnibetAdapter._matches_date(match, "2024-06-02") is False

    def test_invalid_date_includes_match(self) -> None:
        match = ScrapedMatch(
            home_team="A",
            away_team="B",
            event_timestamp=datetime(2024, 6, 1, 15, 0, 0, tzinfo=timezone.utc),
            market_type="1x2",
        )
        assert UnibetAdapter._matches_date(match, "invalid-date") is True
