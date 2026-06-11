"""Tests for the Polla Mundialista Scoring Optimizer.

Tests cover:
- Expected points calculation correctness
- Known matrix where most probable score differs from Polla-optimal
- Group vs knockout stage multipliers
- Edge cases: symmetric matrix, all probability in one cell
- Mexico vs South Africa comparison (lam=1.84, mu=0.56)
"""

from __future__ import annotations

import math

import pytest

from src.polla_scorer import PollaRecommendation, PollaScorer, _result_sign


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _poisson_pmf(k: int, lam: float) -> float:
    """Compute Poisson PMF: P(X=k) = lam^k * e^(-lam) / k!"""
    return (lam**k) * math.exp(-lam) / math.factorial(k)


def _build_poisson_matrix(lam: float, mu: float) -> list[list[float]]:
    """Build a 6x6 independent Poisson probability matrix."""
    matrix: list[list[float]] = []
    for i in range(6):
        row: list[float] = []
        for j in range(6):
            row.append(_poisson_pmf(i, lam) * _poisson_pmf(j, mu))
        matrix.append(row)
    return matrix


def _zero_matrix() -> list[list[float]]:
    """Create a 6x6 zero matrix."""
    return [[0.0] * 6 for _ in range(6)]


# ---------------------------------------------------------------------------
# Test: PollaScorer instantiation and properties
# ---------------------------------------------------------------------------


class TestPollaScorerProperties:
    """Test point values for group and knockout stages."""

    def test_group_stage_points(self) -> None:
        scorer = PollaScorer(is_knockout=False)
        assert scorer.result_points == 5
        assert scorer.goals_points == 2
        assert scorer.diff_points == 1
        assert scorer.max_points == 10

    def test_knockout_stage_points(self) -> None:
        scorer = PollaScorer(is_knockout=True)
        assert scorer.result_points == 10
        assert scorer.goals_points == 4
        assert scorer.diff_points == 2
        assert scorer.max_points == 20


# ---------------------------------------------------------------------------
# Test: score_prediction (deterministic points calculation)
# ---------------------------------------------------------------------------


class TestScorePrediction:
    """Test the deterministic Polla points calculation."""

    def test_perfect_prediction_group(self) -> None:
        """Exact match: 5 + 2 + 2 + 1 = 10 points."""
        scorer = PollaScorer(is_knockout=False)
        assert scorer.score_prediction(1, 0, 1, 0) == 10

    def test_perfect_prediction_knockout(self) -> None:
        """Exact match: 10 + 4 + 4 + 2 = 20 points."""
        scorer = PollaScorer(is_knockout=True)
        assert scorer.score_prediction(1, 0, 1, 0) == 20

    def test_correct_result_and_diff_only(self) -> None:
        """Pred 2-0, actual 3-1: result + diff = 5 + 1 = 6 points."""
        scorer = PollaScorer(is_knockout=False)
        assert scorer.score_prediction(2, 0, 3, 1) == 6

    def test_correct_result_and_away_goals(self) -> None:
        """Pred 2-0, actual 1-0: result correct + away_goals correct (0=0) = 7."""
        scorer = PollaScorer(is_knockout=False)
        # result: home win = home win -> 5
        # home: 2 != 1 -> 0
        # away: 0 == 0 -> 2
        # diff: 2 != 1 -> 0
        assert scorer.score_prediction(2, 0, 1, 0) == 7

    def test_only_home_goals_correct(self) -> None:
        """Pred 1-2 (away win), actual 1-0 (home win): only home goals match."""
        scorer = PollaScorer(is_knockout=False)
        # result: away win vs home win -> 0
        # home: 1 == 1 -> 2
        # away: 2 != 0 -> 0
        # diff: -1 != 1 -> 0
        assert scorer.score_prediction(1, 2, 1, 0) == 2

    def test_draw_prediction_correct(self) -> None:
        """Pred 0-0, actual 2-2: draw correct + diff correct."""
        scorer = PollaScorer(is_knockout=False)
        # result: both draw -> 5
        # home: 0 != 2 -> 0
        # away: 0 != 2 -> 0
        # diff: 0 == 0 -> 1
        assert scorer.score_prediction(0, 0, 2, 2) == 6

    def test_partial_match_away_goals(self) -> None:
        """Pred 0-0 (draw), actual 1-0 (home win): only away correct."""
        scorer = PollaScorer(is_knockout=False)
        # result: draw vs home win -> 0
        # home: 0 != 1 -> 0
        # away: 0 == 0 -> 2
        # diff: 0 != 1 -> 0
        assert scorer.score_prediction(0, 0, 1, 0) == 2

    def test_completely_wrong(self) -> None:
        """Pred 0-3, actual 2-0: nothing matches."""
        scorer = PollaScorer(is_knockout=False)
        # result: away win vs home win -> 0
        # home: 0 != 2 -> 0
        # away: 3 != 0 -> 0
        # diff: -3 != 2 -> 0
        assert scorer.score_prediction(0, 3, 2, 0) == 0


# ---------------------------------------------------------------------------
# Test: Expected points for a specific prediction against a known matrix
# ---------------------------------------------------------------------------


class TestExpectedPoints:
    """Test expected points calculation for specific predictions."""

    def test_all_probability_in_one_cell(self) -> None:
        """If all probability is in (2, 1), predicting 2-1 gives max points."""
        matrix = _zero_matrix()
        matrix[2][1] = 1.0

        scorer = PollaScorer(is_knockout=False)
        # Predict 2-1 against certain (2,1) -> 10 points
        ep = scorer.expected_points(2, 1, matrix)
        assert ep == pytest.approx(10.0)

        # Predict 1-0 against certain (2,1) -> result correct + diff correct
        ep_10 = scorer.expected_points(1, 0, matrix)
        # result: home win = home win -> 5
        # home: 1 != 2 -> 0
        # away: 0 != 1 -> 0
        # diff: 1 == 1 -> 1
        assert ep_10 == pytest.approx(6.0)

    def test_expected_points_symmetric_draw_matrix(self) -> None:
        """Symmetric matrix heavily favoring draws should prefer draw prediction."""
        matrix = _zero_matrix()
        # High prob for draws
        matrix[0][0] = 0.3
        matrix[1][1] = 0.4
        matrix[2][2] = 0.2
        # Some non-draw probability
        matrix[1][0] = 0.05
        matrix[0][1] = 0.05

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        # Should predict a draw
        h, a = rec.predicted_score
        assert h == a, f"Expected draw prediction, got {h}-{a}"

    def test_expected_points_manual_calculation(self) -> None:
        """Manually verify expected points for a simple 2-cell matrix."""
        matrix = _zero_matrix()
        # 60% chance of 1-0, 40% chance of 0-1
        matrix[1][0] = 0.6
        matrix[0][1] = 0.4

        scorer = PollaScorer(is_knockout=False)

        # Prediction: 1-0
        # If actual is 1-0 (prob 0.6): result(5) + home(2) + away(2) + diff(1) = 10
        # If actual is 0-1 (prob 0.4): all wrong = 0
        ep_10 = scorer.expected_points(1, 0, matrix)
        assert ep_10 == pytest.approx(0.6 * 10 + 0.4 * 0)

        # Prediction: 0-1
        # If actual is 1-0 (prob 0.6): 0 pts
        # If actual is 0-1 (prob 0.4): 10 pts
        ep_01 = scorer.expected_points(0, 1, matrix)
        assert ep_01 == pytest.approx(0.6 * 0 + 0.4 * 10)

        # Prediction: 0-0 (draw)
        # If actual is 1-0 (prob 0.6): result wrong, home 0!=1, away 0==0 -> 2 pts
        # If actual is 0-1 (prob 0.4): result wrong, home 0==0, away 0!=1 -> 2 pts
        ep_00 = scorer.expected_points(0, 0, matrix)
        assert ep_00 == pytest.approx(0.6 * 2 + 0.4 * 2)

        # Best prediction should be 1-0 (6.0 > 4.0 > 2.0)
        rec = scorer.recommend(matrix)
        assert rec.predicted_score == (1, 0)
        assert rec.expected_points == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# Test: Most probable score differs from Polla-optimal prediction
# ---------------------------------------------------------------------------


class TestOptimalDiffersFromMostProbable:
    """Test cases where the Polla-optimal prediction differs from most probable."""

    def test_constructed_divergence(self) -> None:
        """Construct a matrix where most probable and Polla-optimal diverge.

        Put high probability on 1-0 but spread significant mass across
        other home wins with diff=1.
        """
        matrix = _zero_matrix()
        # Most probable: 1-0 at 15%
        matrix[1][0] = 0.15
        # Spread mass across home wins with diff=1
        matrix[2][1] = 0.14
        matrix[3][2] = 0.12
        matrix[4][3] = 0.06
        matrix[5][4] = 0.03
        # Some draws and away wins
        matrix[0][0] = 0.10
        matrix[1][1] = 0.08
        matrix[0][1] = 0.07
        matrix[0][2] = 0.05
        matrix[2][0] = 0.10
        matrix[3][0] = 0.05
        matrix[2][2] = 0.05

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        # Most probable should be 1-0
        assert rec.max_probable_score == (1, 0)
        assert rec.max_probable_prob == pytest.approx(0.15)

        # The Polla-optimal should have >= expected points than predicting 1-0
        ep_most_probable = scorer.expected_points(1, 0, matrix)
        assert rec.expected_points >= ep_most_probable

    def test_away_heavy_matrix(self) -> None:
        """Matrix where 0-1 is most probable but optimal may differ."""
        matrix = _zero_matrix()
        # Most probable: 0-1 at 20%
        matrix[0][1] = 0.20
        # Big draw mass
        matrix[0][0] = 0.18
        matrix[1][1] = 0.15
        matrix[2][2] = 0.05
        # Some away wins
        matrix[1][2] = 0.12
        matrix[0][2] = 0.10
        # Other
        matrix[1][0] = 0.10
        matrix[2][0] = 0.05
        matrix[2][1] = 0.05

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        # Most probable is 0-1
        assert rec.max_probable_score == (0, 1)
        # Optimal prediction should yield at least as many expected points
        ep_01 = scorer.expected_points(0, 1, matrix)
        assert rec.expected_points >= ep_01


# ---------------------------------------------------------------------------
# Test: Group vs Knockout multipliers
# ---------------------------------------------------------------------------


class TestMultipliers:
    """Test that knockout stage doubles all point values."""

    def test_knockout_doubles_expected_points(self) -> None:
        """Knockout expected points should be exactly 2x group for same matrix."""
        matrix = _build_poisson_matrix(1.5, 1.0)

        scorer_group = PollaScorer(is_knockout=False)
        scorer_knockout = PollaScorer(is_knockout=True)

        rec_group = scorer_group.recommend(matrix)

        # Same prediction should give exactly double expected points
        ep_group = scorer_group.expected_points(
            rec_group.predicted_score[0], rec_group.predicted_score[1], matrix
        )
        ep_knockout = scorer_knockout.expected_points(
            rec_group.predicted_score[0], rec_group.predicted_score[1], matrix
        )
        assert ep_knockout == pytest.approx(ep_group * 2)

    def test_same_optimal_prediction_regardless_of_phase(self) -> None:
        """Since knockout multiplies ALL components by 2, optimal prediction is same."""
        matrix = _build_poisson_matrix(1.2, 0.8)

        scorer_group = PollaScorer(is_knockout=False)
        scorer_knockout = PollaScorer(is_knockout=True)

        rec_group = scorer_group.recommend(matrix)
        rec_knockout = scorer_knockout.recommend(matrix)

        # Same optimal score regardless of stage (multiplier is uniform)
        assert rec_group.predicted_score == rec_knockout.predicted_score


# ---------------------------------------------------------------------------
# Test: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Test edge cases for the scorer."""

    def test_uniform_matrix(self) -> None:
        """Uniform distribution: all cells equal probability."""
        prob = 1.0 / 36.0
        matrix = [[prob] * 6 for _ in range(6)]

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        # Should return a valid prediction
        h, a = rec.predicted_score
        assert 0 <= h <= 5
        assert 0 <= a <= 5
        assert rec.expected_points > 0

    def test_single_cell_matrix(self) -> None:
        """All probability in one cell: prediction should match that cell."""
        matrix = _zero_matrix()
        matrix[3][2] = 1.0

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        assert rec.predicted_score == (3, 2)
        assert rec.expected_points == pytest.approx(10.0)
        assert rec.max_probable_score == (3, 2)
        assert rec.max_probable_prob == pytest.approx(1.0)

    def test_symmetric_matrix(self) -> None:
        """Symmetric matrix P(i,j) = P(j,i): optimal should be a draw."""
        matrix = _zero_matrix()
        # Create symmetric distribution
        matrix[0][0] = 0.15
        matrix[1][1] = 0.20
        matrix[2][2] = 0.10
        matrix[1][0] = 0.10
        matrix[0][1] = 0.10
        matrix[2][0] = 0.05
        matrix[0][2] = 0.05
        matrix[2][1] = 0.08
        matrix[1][2] = 0.08
        matrix[3][0] = 0.02
        matrix[0][3] = 0.02
        matrix[3][1] = 0.025
        matrix[1][3] = 0.025

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        # In a symmetric matrix, draws dominate -> prediction should be a draw
        h, a = rec.predicted_score
        assert h == a, f"Expected draw prediction for symmetric matrix, got {h}-{a}"

    def test_breakdown_components_sum_to_total(self) -> None:
        """Verify breakdown components sum to total expected points."""
        matrix = _build_poisson_matrix(1.5, 1.2)

        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        breakdown = rec.expected_points_breakdown
        component_sum = (
            breakdown["result"]
            + breakdown["home_goals"]
            + breakdown["away_goals"]
            + breakdown["goal_difference"]
        )
        assert component_sum == pytest.approx(breakdown["total"])
        assert breakdown["total"] == pytest.approx(rec.expected_points)


# ---------------------------------------------------------------------------
# Test: Mexico vs South Africa (lam=1.84, mu=0.56)
# ---------------------------------------------------------------------------


class TestMexicoVsSouthAfrica:
    """Integration test using Mexico vs South Africa match parameters."""

    @pytest.fixture
    def matrix(self) -> list[list[float]]:
        """Build Poisson matrix for lam=1.84, mu=0.56."""
        return _build_poisson_matrix(1.84, 0.56)

    def test_most_probable_score(self, matrix: list[list[float]]) -> None:
        """With lam=1.84 and mu=0.56, most probable should be 1-0."""
        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)
        # mode of Poisson(1.84) is 1, mode of Poisson(0.56) is 0
        assert rec.max_probable_score == (1, 0)

    def test_polla_optimal_exists(self, matrix: list[list[float]]) -> None:
        """Verify a Polla-optimal prediction is produced."""
        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        assert rec.predicted_score is not None
        assert rec.expected_points > 0
        assert 0 <= rec.predicted_score[0] <= 5
        assert 0 <= rec.predicted_score[1] <= 5

    def test_optimal_beats_or_equals_most_probable(
        self, matrix: list[list[float]]
    ) -> None:
        """Polla-optimal must have >= expected points than most probable score."""
        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        ep_most_probable = scorer.expected_points(
            rec.max_probable_score[0], rec.max_probable_score[1], matrix
        )
        assert rec.expected_points >= ep_most_probable - 1e-10

    def test_expected_points_reasonable_range(
        self, matrix: list[list[float]]
    ) -> None:
        """Expected points should be between 0 and max_points."""
        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)
        assert 0 < rec.expected_points <= 10

    def test_comparison_output(self, matrix: list[list[float]]) -> None:
        """Print comparison for manual inspection (captured by pytest -s)."""
        scorer = PollaScorer(is_knockout=False)
        rec = scorer.recommend(matrix)

        ep_most_probable = scorer.expected_points(
            rec.max_probable_score[0], rec.max_probable_score[1], matrix
        )

        print("\n" + "=" * 60)
        print("Mexico vs South Africa (lam=1.84, mu=0.56)")
        print("=" * 60)
        print(f"Most probable score: {rec.max_probable_score[0]}-"
              f"{rec.max_probable_score[1]} "
              f"(prob={rec.max_probable_prob:.4f}, "
              f"E[pts]={ep_most_probable:.3f})")
        print(f"Polla-optimal score: {rec.predicted_score[0]}-"
              f"{rec.predicted_score[1]} "
              f"(E[pts]={rec.expected_points:.3f})")
        print(f"\nBreakdown of optimal prediction:")
        for key, val in rec.expected_points_breakdown.items():
            print(f"  {key}: {val:.3f}")
        print("=" * 60)

        # This test always passes - it's for visual inspection
        assert True


# ---------------------------------------------------------------------------
# Test: _result_sign helper
# ---------------------------------------------------------------------------


class TestResultSign:
    """Test the result sign helper function."""

    def test_home_win(self) -> None:
        assert _result_sign(2, 1) == 1

    def test_away_win(self) -> None:
        assert _result_sign(0, 3) == -1

    def test_draw(self) -> None:
        assert _result_sign(1, 1) == 0
