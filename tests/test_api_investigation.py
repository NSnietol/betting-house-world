"""API Investigation Tests — The Odds API v4 connectivity and response validation.

These tests verify that:
1. The Odds API v4 is reachable and returns the expected response structure
2. The API key (from ODDS_API_KEY env var) is valid
3. Available sports/leagues can be listed
4. Odds endpoints return data in the expected format (h2h + totals markets)

USAGE:
    Set ODDS_API_KEY environment variable, then run:
        uv run pytest tests/test_api_investigation.py -v

    These tests hit the live API — they consume API credits.
    Skip them in CI with: pytest -m "not live_api"
"""

from __future__ import annotations

import os

import pytest
import requests

# Skip entire module if no API key is set
API_KEY = os.environ.get("ODDS_API_KEY")
pytestmark = pytest.mark.skipif(
    not API_KEY,
    reason="ODDS_API_KEY environment variable not set — skipping live API tests",
)

BASE_URL = "https://api.the-odds-api.com/v4"
TIMEOUT = 30


class TestApiConnectivity:
    """Verify basic connectivity and auth with The Odds API v4."""

    def test_api_is_reachable(self):
        """The Odds API base endpoint responds within timeout."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        assert response.status_code == 200, (
            f"Expected 200, got {response.status_code}: {response.text[:200]}"
        )

    def test_invalid_key_returns_401(self):
        """An invalid API key should return 401 Unauthorized."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": "invalid_key_12345"},
            timeout=TIMEOUT,
        )
        assert response.status_code == 401

    def test_remaining_credits_header(self):
        """Response includes x-requests-remaining header for credit tracking."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        assert response.status_code == 200
        # The Odds API returns remaining request quota in headers
        remaining = response.headers.get("x-requests-remaining")
        assert remaining is not None, (
            "Expected 'x-requests-remaining' header from The Odds API"
        )
        print(f"\n[INFO] API credits remaining: {remaining}")


class TestSportsDiscovery:
    """Investigate available sports and leagues from The Odds API."""

    def test_list_sports_returns_array(self):
        """GET /sports returns a JSON array of sport objects."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        data = response.json()
        assert isinstance(data, list)
        assert len(data) > 0, "No sports returned from API"

    def test_sport_object_structure(self):
        """Each sport object has required fields: key, group, title, active."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        data = response.json()
        sport = data[0]
        assert "key" in sport
        assert "group" in sport
        assert "title" in sport
        assert "active" in sport

    def test_soccer_leagues_exist(self):
        """At least one soccer league is available in the API."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        data = response.json()
        soccer_sports = [s for s in data if "soccer" in s.get("group", "").lower()]
        assert len(soccer_sports) > 0, "No soccer leagues found in API"
        print(f"\n[INFO] Available soccer leagues ({len(soccer_sports)}):")
        for s in soccer_sports[:10]:
            print(f"  - {s['key']}: {s['title']} (active={s['active']})")


class TestOddsEndpoint:
    """Investigate the odds endpoint structure and response format."""

    @pytest.fixture
    def active_soccer_sport(self):
        """Find an active soccer sport key for testing."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        data = response.json()
        active_soccer = [
            s for s in data
            if "soccer" in s.get("group", "").lower() and s.get("active")
        ]
        if not active_soccer:
            pytest.skip("No active soccer leagues available right now")
        return active_soccer[0]["key"]

    def test_odds_endpoint_returns_events(self, active_soccer_sport):
        """GET /sports/{sport}/odds returns event data."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/odds",
            params={
                "apiKey": API_KEY,
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
            },
            timeout=TIMEOUT,
        )
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"\n[INFO] {active_soccer_sport}: {len(data)} events with odds")

    def test_event_structure(self, active_soccer_sport):
        """Each event has: id, sport_key, commence_time, home_team, away_team, bookmakers."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/odds",
            params={
                "apiKey": API_KEY,
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
            },
            timeout=TIMEOUT,
        )
        data = response.json()
        if not data:
            pytest.skip("No events available for this sport right now")

        event = data[0]
        assert "id" in event
        assert "sport_key" in event
        assert "commence_time" in event
        assert "home_team" in event
        assert "away_team" in event
        assert "bookmakers" in event
        print(f"\n[INFO] Sample event: {event['home_team']} vs {event['away_team']}")
        print(f"       commence_time: {event['commence_time']}")
        print(f"       bookmakers: {len(event['bookmakers'])}")

    def test_bookmaker_structure(self, active_soccer_sport):
        """Each bookmaker has: key, title, markets with outcomes."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/odds",
            params={
                "apiKey": API_KEY,
                "markets": "h2h,totals",
                "oddsFormat": "decimal",
            },
            timeout=TIMEOUT,
        )
        data = response.json()
        if not data:
            pytest.skip("No events available")

        event = data[0]
        if not event.get("bookmakers"):
            pytest.skip("No bookmakers in first event")

        bm = event["bookmakers"][0]
        assert "key" in bm
        assert "title" in bm
        assert "markets" in bm
        assert len(bm["markets"]) > 0

        market = bm["markets"][0]
        assert "key" in market
        assert "outcomes" in market
        assert len(market["outcomes"]) > 0

        outcome = market["outcomes"][0]
        assert "name" in outcome
        assert "price" in outcome
        assert isinstance(outcome["price"], (int, float))

        print(f"\n[INFO] Bookmaker: {bm['key']} ({bm['title']})")
        print(f"       Markets: {[m['key'] for m in bm['markets']]}")

    def test_h2h_market_has_three_outcomes(self, active_soccer_sport):
        """The h2h (1X2) market should have exactly 3 outcomes for soccer."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/odds",
            params={
                "apiKey": API_KEY,
                "markets": "h2h",
                "oddsFormat": "decimal",
            },
            timeout=TIMEOUT,
        )
        data = response.json()
        if not data:
            pytest.skip("No events available")

        event = data[0]
        for bm in event.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market["key"] == "h2h":
                    assert len(market["outcomes"]) == 3, (
                        f"Expected 3 outcomes for h2h, got {len(market['outcomes'])}"
                    )
                    names = {o["name"] for o in market["outcomes"]}
                    assert event["home_team"] in names
                    assert event["away_team"] in names
                    assert "Draw" in names
                    return

        pytest.skip("No h2h market found in bookmakers")

    def test_totals_market_has_over_under(self, active_soccer_sport):
        """The totals market should have Over/Under outcomes with point=2.5."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/odds",
            params={
                "apiKey": API_KEY,
                "markets": "totals",
                "oddsFormat": "decimal",
            },
            timeout=TIMEOUT,
        )
        data = response.json()
        if not data:
            pytest.skip("No events available")

        event = data[0]
        for bm in event.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market["key"] == "totals":
                    outcomes = market["outcomes"]
                    names = {o["name"] for o in outcomes}
                    assert "Over" in names or "Under" in names, (
                        f"Expected Over/Under in totals, got {names}"
                    )
                    # Check for point field (e.g., 2.5)
                    for o in outcomes:
                        if o["name"] == "Over":
                            assert "point" in o, "Over outcome missing 'point' field"
                            print(f"\n[INFO] Totals market point: {o['point']}")
                            return

        pytest.skip("No totals market found in bookmakers")


class TestScoresEndpoint:
    """Investigate the scores endpoint for retro-feedback functionality."""

    @pytest.fixture
    def active_soccer_sport(self):
        """Find an active soccer sport key."""
        response = requests.get(
            f"{BASE_URL}/sports",
            params={"apiKey": API_KEY},
            timeout=TIMEOUT,
        )
        data = response.json()
        active_soccer = [
            s for s in data
            if "soccer" in s.get("group", "").lower() and s.get("active")
        ]
        if not active_soccer:
            pytest.skip("No active soccer leagues available")
        return active_soccer[0]["key"]

    def test_scores_endpoint_exists(self, active_soccer_sport):
        """GET /sports/{sport}/scores returns completed match scores."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/scores",
            params={
                "apiKey": API_KEY,
                "daysFrom": 3,
            },
            timeout=TIMEOUT,
        )
        # Scores endpoint should be 200 (may return empty list if no recent games)
        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        print(f"\n[INFO] Scores endpoint: {len(data)} results in last 3 days")

    def test_score_object_structure(self, active_soccer_sport):
        """Score objects have expected fields for retro-feedback."""
        response = requests.get(
            f"{BASE_URL}/sports/{active_soccer_sport}/scores",
            params={
                "apiKey": API_KEY,
                "daysFrom": 7,
            },
            timeout=TIMEOUT,
        )
        data = response.json()
        completed = [e for e in data if e.get("completed")]
        if not completed:
            pytest.skip("No completed matches in last 7 days")

        event = completed[0]
        assert "id" in event
        assert "home_team" in event
        assert "away_team" in event
        assert "scores" in event
        assert event["completed"] is True

        print(f"\n[INFO] Completed match: {event['home_team']} vs {event['away_team']}")
        print(f"       Scores: {event['scores']}")
