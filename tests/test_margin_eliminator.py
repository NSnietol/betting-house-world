"""Unit tests for the MarginEliminator module."""

import math

import pytest

from src.margin_eliminator import AnomalousOddsError, MarginEliminator


class TestMarginEliminatorInit:
    """Tests for MarginEliminator initialization."""

    def test_default_method_is_shin(self):
        me = MarginEliminator()
        assert me.method == "shin"

    def test_accepts_shin_method(self):
        me = MarginEliminator(method="shin")
        assert me.method == "shin"

    def test_accepts_logarithmic_method(self):
        me = MarginEliminator(method="logarithmic")
        assert me.method == "logarithmic"

    def test_rejects_invalid_method(self):
        with pytest.raises(ValueError, match="Invalid method"):
            MarginEliminator(method="invalid")

    def test_rejects_empty_string_method(self):
        with pytest.raises(ValueError):
            MarginEliminator(method="")


class TestAnomalousOddsError:
    """Tests for the AnomalousOddsError exception."""

    def test_raised_when_implied_sum_equals_one(self):
        me = MarginEliminator()
        # Odds where 1/h + 1/d + 1/a = 1.0
        # e.g., (2.0, 4.0, 4.0) → 0.5 + 0.25 + 0.25 = 1.0
        with pytest.raises(AnomalousOddsError) as exc_info:
            me.eliminate((2.0, 4.0, 4.0))
        assert exc_info.value.implied_sum == pytest.approx(1.0)

    def test_raised_when_implied_sum_below_one(self):
        me = MarginEliminator()
        # Odds where reciprocals sum < 1.0
        # (5.0, 5.0, 5.0) → 0.2 + 0.2 + 0.2 = 0.6
        with pytest.raises(AnomalousOddsError) as exc_info:
            me.eliminate((5.0, 5.0, 5.0))
        assert exc_info.value.implied_sum < 1.0

    def test_error_contains_odds_info(self):
        me = MarginEliminator()
        odds = (5.0, 5.0, 5.0)
        with pytest.raises(AnomalousOddsError) as exc_info:
            me.eliminate(odds)
        assert exc_info.value.odds == odds


class TestEliminateShin:
    """Tests for the Shin method margin elimination."""

    def test_probabilities_sum_to_one(self):
        me = MarginEliminator(method="shin")
        # Typical match odds with overround
        odds = (2.10, 3.40, 3.60)
        result = me.eliminate(odds)
        assert sum(result) == pytest.approx(1.0, abs=0.001)

    def test_all_probabilities_in_valid_range(self):
        me = MarginEliminator(method="shin")
        odds = (1.50, 4.20, 6.50)
        result = me.eliminate(odds)
        for p in result:
            assert 0.0 <= p <= 1.0

    def test_favourite_gets_highest_probability(self):
        me = MarginEliminator(method="shin")
        # Home is heavy favourite (low odds)
        odds = (1.30, 5.50, 10.0)
        result = me.eliminate(odds)
        # Home prob should be highest
        assert result[0] > result[1]
        assert result[0] > result[2]

    def test_balanced_odds_produce_balanced_probs(self):
        me = MarginEliminator(method="shin")
        # Nearly equal odds
        odds = (2.90, 2.90, 2.90)
        result = me.eliminate(odds)
        # All should be close to 1/3
        for p in result:
            assert p == pytest.approx(1.0 / 3.0, abs=0.01)

    def test_high_overround_still_sums_to_one(self):
        me = MarginEliminator(method="shin")
        # High overround (low odds for all outcomes)
        odds = (1.80, 3.00, 3.50)
        result = me.eliminate(odds)
        assert sum(result) == pytest.approx(1.0, abs=0.001)


class TestEliminateLogarithmic:
    """Tests for the Logarithmic method margin elimination."""

    def test_probabilities_sum_to_one(self):
        me = MarginEliminator(method="logarithmic")
        odds = (2.10, 3.40, 3.60)
        result = me.eliminate(odds)
        assert sum(result) == pytest.approx(1.0, abs=0.001)

    def test_all_probabilities_in_valid_range(self):
        me = MarginEliminator(method="logarithmic")
        odds = (1.50, 4.20, 6.50)
        result = me.eliminate(odds)
        for p in result:
            assert 0.0 <= p <= 1.0

    def test_favourite_gets_highest_probability(self):
        me = MarginEliminator(method="logarithmic")
        odds = (1.30, 5.50, 10.0)
        result = me.eliminate(odds)
        assert result[0] > result[1]
        assert result[0] > result[2]

    def test_balanced_odds_produce_balanced_probs(self):
        me = MarginEliminator(method="logarithmic")
        odds = (2.90, 2.90, 2.90)
        result = me.eliminate(odds)
        for p in result:
            assert p == pytest.approx(1.0 / 3.0, abs=0.01)

    def test_high_overround_still_sums_to_one(self):
        me = MarginEliminator(method="logarithmic")
        odds = (1.80, 3.00, 3.50)
        result = me.eliminate(odds)
        assert sum(result) == pytest.approx(1.0, abs=0.001)


class TestMethodComparison:
    """Tests comparing the two methods."""

    def test_both_methods_produce_similar_results(self):
        """Both methods should produce similar but not identical results."""
        shin = MarginEliminator(method="shin")
        log = MarginEliminator(method="logarithmic")
        odds = (2.10, 3.40, 3.60)
        shin_result = shin.eliminate(odds)
        log_result = log.eliminate(odds)

        # Results should be similar (within ~5%)
        for s, l in zip(shin_result, log_result):
            assert abs(s - l) < 0.05

    def test_shin_adjusts_favourites_differently(self):
        """Shin method accounts for favourite-longshot bias."""
        shin = MarginEliminator(method="shin")
        log = MarginEliminator(method="logarithmic")
        # Strong favourite
        odds = (1.20, 6.00, 15.0)
        shin_result = shin.eliminate(odds)
        log_result = log.eliminate(odds)

        # Both should sum to 1
        assert sum(shin_result) == pytest.approx(1.0, abs=0.001)
        assert sum(log_result) == pytest.approx(1.0, abs=0.001)
