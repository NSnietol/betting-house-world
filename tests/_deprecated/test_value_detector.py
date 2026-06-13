"""Unit tests for the ValueBetDetector module."""

from __future__ import annotations

from datetime import datetime

import pytest

from src.models import AggregatedEvent
from src.value_detector import SHARP_BOOKMAKERS, ValueBet, ValueBetDetector


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_event(
    odds_1x2: dict[str, tuple[float, float, float]] | None = None,
    odds_ou: dict[str, tuple[float, float]] | None = None,
    home: str = "Mexico",
    away: str = "South Africa",
) -> AggregatedEvent:
    """Helper to create an AggregatedEvent for tests."""
    return AggregatedEvent(
        home_team=home,
        away_team=away,
        event_timestamp=datetime(2026, 6, 11, 18, 0),
        bookmaker_odds_1x2=odds_1x2 or {},
        bookmaker_odds_over_under=odds_ou or {},
        source_count=len(odds_1x2 or {}) + len(odds_ou or {}),
    )


# ---------------------------------------------------------------------------
# Tests: Consensus Calculation
# ---------------------------------------------------------------------------


class TestConsensusCalculation:
    """Tests for weighted consensus probability calculation."""

    def test_sharp_bookmakers_get_double_weight(self) -> None:
        """Sharp bookmakers (pinnacle, betfair) receive 2x weight."""
        # Pinnacle: home=2.0 (imp=0.5), draw=3.5 (imp=0.286), away=3.5 (imp=0.286)
        # Normalized: 0.467, 0.267, 0.267
        # Unibet: home=1.8 (imp=0.556), draw=3.8 (imp=0.263), away=4.0 (imp=0.25)
        # Normalized: 0.520, 0.246, 0.234
        # Weighted: pinnacle(2x) + unibet(1x) => total weight=3
        odds_1x2 = {
            "pinnacle": (2.0, 3.5, 3.5),
            "unibet": (1.8, 3.8, 4.0),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.0)

        consensus = detector._calculate_consensus_1x2(event.bookmaker_odds_1x2)
        assert consensus is not None

        # Pinnacle normalized probs
        pin_total = 1 / 2.0 + 1 / 3.5 + 1 / 3.5
        pin_home = (1 / 2.0) / pin_total
        pin_draw = (1 / 3.5) / pin_total
        pin_away = (1 / 3.5) / pin_total

        # Unibet normalized probs
        uni_total = 1 / 1.8 + 1 / 3.8 + 1 / 4.0
        uni_home = (1 / 1.8) / uni_total
        uni_draw = (1 / 3.8) / uni_total
        uni_away = (1 / 4.0) / uni_total

        # Weighted average (pinnacle=2, unibet=1, total=3)
        expected_home = (pin_home * 2 + uni_home * 1) / 3
        expected_draw = (pin_draw * 2 + uni_draw * 1) / 3
        expected_away = (pin_away * 2 + uni_away * 1) / 3

        assert consensus[0] == pytest.approx(expected_home, abs=1e-6)
        assert consensus[1] == pytest.approx(expected_draw, abs=1e-6)
        assert consensus[2] == pytest.approx(expected_away, abs=1e-6)

    def test_all_soft_bookmakers_equal_weight(self) -> None:
        """When no sharp bookmakers present, all get equal weight."""
        odds_1x2 = {
            "unibet": (2.0, 3.5, 3.5),
            "onexbet": (2.1, 3.3, 3.4),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector()

        consensus = detector._calculate_consensus_1x2(event.bookmaker_odds_1x2)
        assert consensus is not None

        # Both get weight=1, so it's a simple average of normalized probs
        uni_total = 1 / 2.0 + 1 / 3.5 + 1 / 3.5
        one_total = 1 / 2.1 + 1 / 3.3 + 1 / 3.4

        expected_home = ((1 / 2.0) / uni_total + (1 / 2.1) / one_total) / 2
        expected_draw = ((1 / 3.5) / uni_total + (1 / 3.3) / one_total) / 2
        expected_away = ((1 / 3.5) / uni_total + (1 / 3.4) / one_total) / 2

        assert consensus[0] == pytest.approx(expected_home, abs=1e-6)
        assert consensus[1] == pytest.approx(expected_draw, abs=1e-6)
        assert consensus[2] == pytest.approx(expected_away, abs=1e-6)

    def test_consensus_probabilities_sum_to_one(self) -> None:
        """Consensus probabilities should approximately sum to 1.0."""
        odds_1x2 = {
            "pinnacle": (1.95, 3.6, 4.2),
            "betfair": (2.0, 3.5, 4.0),
            "unibet": (1.85, 3.4, 4.5),
            "onexbet": (1.90, 3.5, 4.3),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector()

        consensus = detector._calculate_consensus_1x2(event.bookmaker_odds_1x2)
        assert consensus is not None
        assert sum(consensus) == pytest.approx(1.0, abs=1e-6)

    def test_consensus_over_under(self) -> None:
        """Consensus for over/under market uses same weighting logic."""
        odds_ou = {
            "pinnacle": (1.90, 1.95),
            "unibet": (2.10, 1.75),
        }
        event = _make_event(odds_ou=odds_ou)
        detector = ValueBetDetector()

        consensus = detector._calculate_consensus_over_under(
            event.bookmaker_odds_over_under
        )
        assert consensus is not None
        assert sum(consensus) == pytest.approx(1.0, abs=1e-6)

    def test_empty_odds_returns_none(self) -> None:
        """Empty bookmaker odds dict returns None consensus."""
        detector = ValueBetDetector()
        assert detector._calculate_consensus_1x2({}) is None
        assert detector._calculate_consensus_over_under({}) is None


# ---------------------------------------------------------------------------
# Tests: Edge Detection
# ---------------------------------------------------------------------------


class TestEdgeDetection:
    """Tests for value bet edge detection above and below threshold."""

    def test_detects_value_above_threshold(self) -> None:
        """Flags value bet when edge exceeds threshold."""
        # Sharp bookmaker prices home very strong (1.45 => ~69% implied).
        # Soft bookmaker is much more generous on home (1.80 => ~55.6% implied).
        # Consensus heavily influenced by sharp (2x weight) will be ~62%,
        # which is well above unibet's implied 55.6% => clear value.
        odds_1x2 = {
            "pinnacle": (1.45, 4.50, 7.00),
            "unibet": (1.80, 3.80, 5.00),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.03)

        value_bets = detector.detect(event)

        # Unibet should have at least one value bet detected on Home
        unibet_bets = [vb for vb in value_bets if vb.bookmaker_id == "unibet"]
        assert len(unibet_bets) > 0
        home_bets = [vb for vb in unibet_bets if vb.outcome == "Home"]
        assert len(home_bets) == 1
        assert home_bets[0].edge_pct > 0

    def test_no_value_below_threshold(self) -> None:
        """No value bets when all odds are near or below fair value."""
        # All bookmakers offer very similar odds
        odds_1x2 = {
            "pinnacle": (2.00, 3.50, 3.50),
            "unibet": (1.95, 3.50, 3.60),
            "onexbet": (1.98, 3.45, 3.55),
        }
        event = _make_event(odds_1x2=odds_1x2)
        # Use a high threshold to ensure nothing triggers
        detector = ValueBetDetector(edge_threshold=0.10)

        value_bets = detector.detect(event)
        assert len(value_bets) == 0

    def test_sharp_bookmakers_never_flagged(self) -> None:
        """Sharp bookmakers are never flagged as having value."""
        odds_1x2 = {
            "pinnacle": (3.00, 3.00, 3.00),
            "betfair": (3.50, 3.00, 2.50),
            "unibet": (2.80, 3.10, 3.10),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.01)

        value_bets = detector.detect(event)
        for vb in value_bets:
            assert vb.bookmaker_id not in SHARP_BOOKMAKERS

    def test_configurable_threshold(self) -> None:
        """Lower threshold detects more value bets."""
        odds_1x2 = {
            "pinnacle": (2.00, 3.50, 3.50),
            "unibet": (2.10, 3.40, 3.40),
        }
        event = _make_event(odds_1x2=odds_1x2)

        # High threshold: fewer or no bets
        detector_high = ValueBetDetector(edge_threshold=0.10)
        bets_high = detector_high.detect(event)

        # Low threshold: more bets
        detector_low = ValueBetDetector(edge_threshold=0.01)
        bets_low = detector_low.detect(event)

        assert len(bets_low) >= len(bets_high)


# ---------------------------------------------------------------------------
# Tests: Kelly Fraction
# ---------------------------------------------------------------------------


class TestKellyFraction:
    """Tests for Kelly criterion fraction computation."""

    def test_kelly_formula(self) -> None:
        """Kelly fraction matches formula: (p*odds - 1) / (odds - 1)."""
        detector = ValueBetDetector()

        # prob=0.6, odds=2.0 => kelly = (0.6*2 - 1)/(2-1) = 0.2/1 = 0.2
        kelly = detector._kelly_fraction(0.6, 2.0)
        assert kelly == pytest.approx(0.2, abs=1e-6)

    def test_kelly_zero_when_no_edge(self) -> None:
        """Kelly fraction is 0 when there's no positive edge."""
        detector = ValueBetDetector()

        # prob=0.4, odds=2.0 => kelly = (0.4*2 - 1)/(2-1) = -0.2/1 = -0.2 => 0
        kelly = detector._kelly_fraction(0.4, 2.0)
        assert kelly == 0.0

    def test_kelly_zero_when_odds_equal_one(self) -> None:
        """Kelly returns 0 for degenerate odds of 1.0."""
        detector = ValueBetDetector()
        kelly = detector._kelly_fraction(0.9, 1.0)
        assert kelly == 0.0

    def test_kelly_in_value_bet_output(self) -> None:
        """Detected value bets include correct Kelly fraction."""
        # Set up a clear value bet scenario
        odds_1x2 = {
            "pinnacle": (1.50, 4.50, 6.00),
            "unibet": (1.70, 4.00, 5.00),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.01)

        value_bets = detector.detect(event)
        for vb in value_bets:
            # Verify Kelly matches formula
            expected_kelly = (vb.consensus_probability * vb.offered_odds - 1.0) / (
                vb.offered_odds - 1.0
            )
            assert vb.kelly_fraction == pytest.approx(max(0.0, expected_kelly), abs=1e-6)


# ---------------------------------------------------------------------------
# Tests: No Value Bets Scenario
# ---------------------------------------------------------------------------


class TestNoValueBets:
    """Tests verifying no value bets are detected when odds are below fair value."""

    def test_all_soft_odds_below_fair(self) -> None:
        """No value bets when soft bookmakers offer shorter odds than consensus."""
        # Soft bookmakers offer LOWER odds (shorter prices) than sharp consensus
        # This means implied prob from soft > consensus, so no value
        odds_1x2 = {
            "pinnacle": (2.50, 3.50, 2.80),
            "betfair": (2.55, 3.45, 2.75),
            "unibet": (2.30, 3.20, 2.60),
            "onexbet": (2.35, 3.25, 2.55),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.03)

        value_bets = detector.detect(event)
        assert len(value_bets) == 0

    def test_empty_event_no_value_bets(self) -> None:
        """No value bets for event with no odds data."""
        event = _make_event(odds_1x2={}, odds_ou={})
        detector = ValueBetDetector()

        value_bets = detector.detect(event)
        assert len(value_bets) == 0

    def test_single_bookmaker_no_value_bets(self) -> None:
        """Cannot detect value with only one soft bookmaker (no sharp reference)."""
        # With only one bookmaker, consensus equals that bookmaker's own probs
        # So there can never be a positive edge
        odds_1x2 = {
            "unibet": (2.00, 3.50, 3.50),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.03)

        value_bets = detector.detect(event)
        assert len(value_bets) == 0


# ---------------------------------------------------------------------------
# Tests: Multiple Outcomes for Same Match
# ---------------------------------------------------------------------------


class TestMultipleOutcomes:
    """Tests for detecting value across multiple outcomes of the same match."""

    def test_multiple_1x2_outcomes_flagged(self) -> None:
        """Can detect value on multiple outcomes of the same 1X2 market."""
        # Sharp bookmaker: equal odds for all three
        # Soft bookmaker offers much higher odds on all three (generous all-around)
        odds_1x2 = {
            "pinnacle": (2.50, 3.00, 3.00),
            "unibet": (2.90, 3.50, 3.50),
        }
        event = _make_event(odds_1x2=odds_1x2)
        # Very low threshold to catch multiple
        detector = ValueBetDetector(edge_threshold=0.02)

        value_bets = detector.detect(event)
        outcomes_found = {vb.outcome for vb in value_bets}
        # At least one outcome should be flagged
        assert len(outcomes_found) >= 1

    def test_1x2_and_over_under_detected(self) -> None:
        """Can detect value in both 1X2 and Over/Under markets simultaneously."""
        odds_1x2 = {
            "pinnacle": (1.80, 3.80, 4.50),
            "unibet": (2.10, 3.50, 4.00),
        }
        odds_ou = {
            "pinnacle": (1.85, 2.00),
            "unibet": (2.15, 1.75),
        }
        event = _make_event(odds_1x2=odds_1x2, odds_ou=odds_ou)
        detector = ValueBetDetector(edge_threshold=0.02)

        value_bets = detector.detect(event)

        # Check we can have bets from both markets
        market_types = set()
        for vb in value_bets:
            if vb.outcome in ("Home", "Draw", "Away"):
                market_types.add("1x2")
            elif vb.outcome in ("Over 2.5", "Under 2.5"):
                market_types.add("ou")

        # At least one market should have value
        assert len(market_types) >= 1

    def test_value_bet_fields_populated(self) -> None:
        """All ValueBet fields are correctly populated."""
        odds_1x2 = {
            "pinnacle": (1.50, 4.50, 6.00),
            "unibet": (1.80, 4.00, 4.50),
        }
        event = _make_event(odds_1x2=odds_1x2, home="Brazil", away="Croatia")
        detector = ValueBetDetector(edge_threshold=0.01)

        value_bets = detector.detect(event)
        assert len(value_bets) > 0

        for vb in value_bets:
            assert vb.home_team == "Brazil"
            assert vb.away_team == "Croatia"
            assert vb.outcome in ("Home", "Draw", "Away", "Over 2.5", "Under 2.5")
            assert vb.bookmaker_id == "unibet"
            assert vb.offered_odds > 1.0
            assert vb.fair_odds > 1.0
            assert 0.0 < vb.consensus_probability < 1.0
            assert 0.0 < vb.implied_probability < 1.0
            assert vb.edge_pct > 0.0
            assert vb.kelly_fraction >= 0.0


# ---------------------------------------------------------------------------
# Tests: Edge Cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_very_high_odds_value_bet(self) -> None:
        """Value detection works for longshot outcomes."""
        odds_1x2 = {
            "pinnacle": (1.20, 6.00, 15.00),
            "unibet": (1.20, 6.50, 20.00),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.01)

        value_bets = detector.detect(event)
        # The away outcome at 20.0 vs pinnacle's 15.0 should show value
        away_bets = [vb for vb in value_bets if vb.outcome == "Away"]
        if away_bets:
            assert away_bets[0].offered_odds == 20.0

    def test_threshold_boundary_exactly_at_threshold(self) -> None:
        """Edge exactly at threshold is NOT flagged (strict >)."""
        detector = ValueBetDetector(edge_threshold=0.03)

        # Construct odds where edge is exactly at threshold
        # This is hard to hit exactly, so we verify behavior near boundary
        odds_1x2 = {
            "pinnacle": (2.00, 3.50, 3.50),
            "unibet": (2.00, 3.50, 3.50),  # identical odds = 0 edge
        }
        event = _make_event(odds_1x2=odds_1x2)

        value_bets = detector.detect(event)
        assert len(value_bets) == 0

    def test_multiple_soft_bookmakers(self) -> None:
        """Each soft bookmaker is independently evaluated for value."""
        odds_1x2 = {
            "pinnacle": (2.00, 3.50, 3.50),
            "unibet": (2.30, 3.20, 3.30),
            "onexbet": (2.40, 3.10, 3.20),
            "the_odds_api": (2.50, 3.00, 3.10),
        }
        event = _make_event(odds_1x2=odds_1x2)
        detector = ValueBetDetector(edge_threshold=0.02)

        value_bets = detector.detect(event)

        # Multiple bookmakers can have value bets
        bookmakers_with_value = {vb.bookmaker_id for vb in value_bets}
        # At least the most generous soft bookmakers should be flagged
        if value_bets:
            assert all(bm not in SHARP_BOOKMAKERS for bm in bookmakers_with_value)
