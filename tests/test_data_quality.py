"""Unit tests for DataQualityAnalyzer."""

import math

import pytest

from src.data_quality import DataQualityAnalyzer


@pytest.fixture
def analyzer():
    return DataQualityAnalyzer()


class TestAnalyze:
    """Tests for the analyze() method."""

    def test_no_flags_triggered(self, analyzer):
        """When all metrics are within thresholds, flags should be zero."""
        # Odds with low overround (~3%) and consistent across bookmakers
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (2.00, 3.40, 3.50)},
                {"decimal_odds": (2.01, 3.39, 3.49)},
                {"decimal_odds": (2.02, 3.38, 3.48)},
            ]
        }
        result = analyzer.analyze(match_odds)
        assert result["variance_flag"] == 0.00
        assert result["high_margin"] == 0.00
        assert result["low_coverage"] == 0

    def test_variance_flag_triggered(self, analyzer):
        """When std dev exceeds 0.03, variance flag should be set to max std dev."""
        # Odds with high variance in home probability
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (1.50, 4.00, 6.00)},  # home implied ~0.667
                {"decimal_odds": (2.50, 3.50, 3.00)},  # home implied ~0.400
                {"decimal_odds": (2.00, 3.80, 4.00)},  # home implied ~0.500
            ]
        }
        result = analyzer.analyze(match_odds)
        assert result["variance_flag"] > 0.03

    def test_high_margin_flag_triggered(self, analyzer):
        """When any bookmaker overround exceeds 0.10, high margin flag is set."""
        # Odds with very high overround (low decimal odds)
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (1.50, 3.00, 4.00)},  # overround = 1/1.5 + 1/3 + 1/4 - 1 = 0.667+0.333+0.25-1 = 0.25
                {"decimal_odds": (2.00, 3.40, 3.50)},
                {"decimal_odds": (2.00, 3.40, 3.50)},
            ]
        }
        result = analyzer.analyze(match_odds)
        assert result["high_margin"] > 0.10

    def test_low_coverage_flag_triggered(self, analyzer):
        """When fewer than 3 bookmakers, low coverage flag shows count."""
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (2.00, 3.40, 3.50)},
                {"decimal_odds": (2.01, 3.39, 3.49)},
            ]
        }
        result = analyzer.analyze(match_odds)
        assert result["low_coverage"] == 2

    def test_low_coverage_exactly_three_bookmakers(self, analyzer):
        """With exactly 3 bookmakers, low coverage flag should be 0."""
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (2.00, 3.40, 3.50)},
                {"decimal_odds": (2.01, 3.39, 3.49)},
                {"decimal_odds": (2.02, 3.38, 3.48)},
            ]
        }
        result = analyzer.analyze(match_odds)
        assert result["low_coverage"] == 0

    def test_empty_bookmaker_list(self, analyzer):
        """With no bookmakers, low coverage is flagged and other flags are zero."""
        match_odds = {"bookmaker_odds": []}
        result = analyzer.analyze(match_odds)
        assert result["variance_flag"] == 0.00
        assert result["high_margin"] == 0.00
        assert result["low_coverage"] == 0

    def test_single_bookmaker(self, analyzer):
        """With one bookmaker, variance is 0 (can't compute std dev)."""
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (1.50, 3.00, 4.00)},
            ]
        }
        result = analyzer.analyze(match_odds)
        assert result["variance_flag"] == 0.00
        assert result["low_coverage"] == 1

    def test_all_flags_triggered(self, analyzer):
        """Test scenario where all three flags are triggered."""
        match_odds = {
            "bookmaker_odds": [
                {"decimal_odds": (1.30, 4.00, 8.00)},  # high overround, skewed
                {"decimal_odds": (2.50, 3.00, 3.00)},  # different profile
            ]
        }
        result = analyzer.analyze(match_odds)
        # Should have variance flag (odds are very different)
        assert result["variance_flag"] > 0.0
        # Should have high margin (1/1.30 + 1/4 + 1/8 = 0.769+0.25+0.125 - 1 = 0.144)
        assert result["high_margin"] > 0.10
        # Only 2 bookmakers
        assert result["low_coverage"] == 2


class TestComputeStdDev:
    """Tests for the _compute_std_dev() method."""

    def test_identical_probs(self, analyzer):
        """When all bookmakers agree, std dev should be zero."""
        probs = [(0.5, 0.3, 0.2), (0.5, 0.3, 0.2), (0.5, 0.3, 0.2)]
        assert analyzer._compute_std_dev(probs) < 1e-10

    def test_known_values(self, analyzer):
        """Test with known values to verify computation."""
        # Home: [0.4, 0.6] → mean=0.5, var=0.01, std=0.1
        # Draw: [0.3, 0.3] → mean=0.3, var=0.0, std=0.0
        # Away: [0.3, 0.1] → mean=0.2, var=0.01, std=0.1
        probs = [(0.4, 0.3, 0.3), (0.6, 0.3, 0.1)]
        result = analyzer._compute_std_dev(probs)
        assert abs(result - 0.1) < 1e-10

    def test_single_bookmaker_returns_zero(self, analyzer):
        """With only one bookmaker, std dev is zero."""
        probs = [(0.5, 0.3, 0.2)]
        assert analyzer._compute_std_dev(probs) == 0.0

    def test_empty_list_returns_zero(self, analyzer):
        """Empty list should return zero."""
        assert analyzer._compute_std_dev([]) == 0.0


class TestComputeOverround:
    """Tests for the _compute_overround() method."""

    def test_fair_odds(self, analyzer):
        """Fair odds (no overround) should give 0.0."""
        # 1/2 + 1/3.33 + 1/5 should be close to 1.0
        # Let's use exact: odds that sum reciprocals to exactly 1
        # home=2.0, draw=inf, away=inf → not practical
        # Use 2.0, 3.0, 6.0: 0.5 + 0.333 + 0.167 = 1.0
        result = analyzer._compute_overround((2.0, 3.0, 6.0))
        assert abs(result - 0.0) < 1e-10

    def test_typical_overround(self, analyzer):
        """Typical bookmaker odds have positive overround."""
        # 1/1.9 + 1/3.4 + 1/4.0 = 0.5263 + 0.2941 + 0.25 = 1.0704 → overround = 0.0704
        result = analyzer._compute_overround((1.9, 3.4, 4.0))
        expected = (1/1.9) + (1/3.4) + (1/4.0) - 1.0
        assert abs(result - expected) < 1e-10

    def test_high_overround(self, analyzer):
        """Very low odds should produce high overround."""
        # 1/1.5 + 1/3.0 + 1/4.0 = 0.667 + 0.333 + 0.25 - 1 = 0.25
        result = analyzer._compute_overround((1.5, 3.0, 4.0))
        expected = 0.25
        assert abs(result - expected) < 1e-3
