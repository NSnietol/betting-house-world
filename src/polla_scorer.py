"""Polla Mundialista Scoring Optimizer module.

Takes the Poisson score matrix (6x6 probability grid) and picks the score
that maximizes EXPECTED POLLA POINTS rather than just the most probable score.

Polla Mundialista Scoring Rules:
- Group Stage: result=5, goals=2 each, diff=1, max=10
- Knockout Stage: result=10, goals=4 each, diff=2, max=20
- Only 90 minutes + stoppage time counts.

Optimization enhancements:
- Uses direct 1X2 probabilities for the trend component to avoid
  truncation bias from the 6×6 grid (results with 6+ goals still
  contribute to the home/draw/away mass).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PollaRecommendation:
    """Optimal Polla Mundialista prediction with comparison to most probable score.

    Attributes:
        predicted_score: (home_goals, away_goals) that maximizes expected Polla points.
        expected_points: Expected Polla points for this optimal prediction.
        max_probable_score: The most probable score from the matrix (for comparison).
        max_probable_prob: Probability of the most probable score.
        expected_points_breakdown: Breakdown of expected points by component.
    """

    predicted_score: tuple[int, int]
    expected_points: float
    max_probable_score: tuple[int, int]
    max_probable_prob: float
    expected_points_breakdown: dict[str, float] = field(default_factory=dict)


class PollaScorer:
    """Finds the prediction that maximizes expected Polla Mundialista points.

    Given a 6x6 Poisson probability matrix where matrix[i][j] = P(home=i, away=j),
    evaluates all 36 candidate predictions and selects the one with the highest
    expected Polla points.

    The key insight: the most probable exact score is NOT always the best Polla
    prediction. A prediction with slightly lower probability but higher alignment
    with the overall probability mass (correct result type, goal counts, difference)
    can yield more expected points.
    """

    MAX_GOALS = 5  # 0 to 5 inclusive -> 6x6 grid

    def __init__(self, is_knockout: bool = False) -> None:
        """Initialize the scorer with match phase.

        Args:
            is_knockout: True for elimination rounds (double all point values).
        """
        self.is_knockout = is_knockout
        self._multiplier = 2 if is_knockout else 1

    @property
    def result_points(self) -> int:
        """Points awarded for correct result (winner/draw)."""
        return 5 * self._multiplier

    @property
    def goals_points(self) -> int:
        """Points awarded for each correct team goal count."""
        return 2 * self._multiplier

    @property
    def diff_points(self) -> int:
        """Points awarded for correct goal difference."""
        return 1 * self._multiplier

    @property
    def max_points(self) -> int:
        """Maximum points achievable per match."""
        return 10 * self._multiplier

    def recommend(
        self,
        matrix: list[list[float]],
        real_probs_1x2: tuple[float, float, float] | None = None,
    ) -> PollaRecommendation:
        """Find the optimal Polla prediction for the given probability matrix.

        For EACH candidate prediction (h_pred, a_pred) from 0-0 to 5-5:
            E[points | prediction=(h_pred, a_pred)] =
                sum_i sum_j P(home=i, away=j) * points(prediction=(h_pred, a_pred), actual=(i, j))

        When real_probs_1x2 is provided, the trend component uses these direct
        probabilities instead of summing over the truncated 6×6 grid. This avoids
        underestimating the trend EV for high-scoring matches where P(total>=6) is
        significant.

        Args:
            matrix: 6x6 Poisson probability grid where matrix[i][j] = P(home=i, away=j).
            real_probs_1x2: Optional (p_home, p_draw, p_away) from devigged odds.
                           If provided, used for the trend component calculation.

        Returns:
            PollaRecommendation with the optimal prediction and comparison data.
        """
        best_score: tuple[int, int] = (0, 0)
        best_expected: float = -1.0
        best_breakdown: dict[str, float] = {}

        # Evaluate all 36 candidate predictions
        for h_pred in range(self.MAX_GOALS + 1):
            for a_pred in range(self.MAX_GOALS + 1):
                expected, breakdown = self._expected_points_for(
                    h_pred, a_pred, matrix, real_probs_1x2
                )
                if expected > best_expected:
                    best_expected = expected
                    best_score = (h_pred, a_pred)
                    best_breakdown = breakdown

        # Find the most probable score for comparison
        max_prob_score, max_prob = self._find_most_probable(matrix)

        return PollaRecommendation(
            predicted_score=best_score,
            expected_points=best_expected,
            max_probable_score=max_prob_score,
            max_probable_prob=max_prob,
            expected_points_breakdown=best_breakdown,
        )

    def score_prediction(
        self, pred_h: int, pred_a: int, actual_h: int, actual_a: int
    ) -> int:
        """Calculate Polla points for a prediction given the actual result.

        Args:
            pred_h: Predicted home goals.
            pred_a: Predicted away goals.
            actual_h: Actual home goals.
            actual_a: Actual away goals.

        Returns:
            Total Polla points earned.
        """
        points = 0

        # Correct result (winner/draw)
        if _result_sign(pred_h, pred_a) == _result_sign(actual_h, actual_a):
            points += self.result_points

        # Correct home goals
        if pred_h == actual_h:
            points += self.goals_points

        # Correct away goals
        if pred_a == actual_a:
            points += self.goals_points

        # Correct goal difference
        if (pred_h - pred_a) == (actual_h - actual_a):
            points += self.diff_points

        return points

    def expected_points(
        self,
        h_pred: int,
        a_pred: int,
        matrix: list[list[float]],
        real_probs_1x2: tuple[float, float, float] | None = None,
    ) -> float:
        """Calculate expected points for a specific prediction.

        Public convenience method for external callers.

        Args:
            h_pred: Predicted home goals.
            a_pred: Predicted away goals.
            matrix: 6x6 probability grid.
            real_probs_1x2: Optional (p_home, p_draw, p_away) for trend calc.

        Returns:
            Expected Polla points for the given prediction.
        """
        ep, _ = self._expected_points_for(h_pred, a_pred, matrix, real_probs_1x2)
        return ep

    def _expected_points_for(
        self,
        h_pred: int,
        a_pred: int,
        matrix: list[list[float]],
        real_probs_1x2: tuple[float, float, float] | None = None,
    ) -> tuple[float, dict[str, float]]:
        """Compute expected Polla points and breakdown for a candidate prediction.

        When real_probs_1x2 is provided, the trend (result) component uses
        the direct probability from devigged odds rather than summing the
        truncated grid. This eliminates the underestimation caused by the
        6×6 grid not capturing results with 6+ total goals.

        Args:
            h_pred: Predicted home goals.
            a_pred: Predicted away goals.
            matrix: 6x6 probability grid.
            real_probs_1x2: Optional (p_home, p_draw, p_away) for trend calc.

        Returns:
            Tuple of (total_expected_points, breakdown_dict).
        """
        e_result = 0.0
        e_home_goals = 0.0
        e_away_goals = 0.0
        e_diff = 0.0

        pred_sign = _result_sign(h_pred, a_pred)
        pred_diff = h_pred - a_pred

        # Use direct 1X2 probs for the trend component if available
        if real_probs_1x2 is not None:
            p_home, p_draw, p_away = real_probs_1x2
            if pred_sign == 1:
                e_result = p_home * self.result_points
            elif pred_sign == 0:
                e_result = p_draw * self.result_points
            else:
                e_result = p_away * self.result_points

        for i in range(self.MAX_GOALS + 1):
            for j in range(self.MAX_GOALS + 1):
                prob = matrix[i][j]
                if prob <= 0:
                    continue

                # Correct result — only from grid if no direct probs
                if real_probs_1x2 is None:
                    if _result_sign(i, j) == pred_sign:
                        e_result += prob * self.result_points

                # Correct home goals
                if i == h_pred:
                    e_home_goals += prob * self.goals_points

                # Correct away goals
                if j == a_pred:
                    e_away_goals += prob * self.goals_points

                # Correct goal difference
                if (i - j) == pred_diff:
                    e_diff += prob * self.diff_points

        total = e_result + e_home_goals + e_away_goals + e_diff

        breakdown = {
            "result": e_result,
            "home_goals": e_home_goals,
            "away_goals": e_away_goals,
            "goal_difference": e_diff,
            "total": total,
        }

        return total, breakdown

    def _find_most_probable(
        self, matrix: list[list[float]]
    ) -> tuple[tuple[int, int], float]:
        """Find the most probable score in the matrix.

        Tiebreaker: lowest total goals, then fewer home goals.

        Args:
            matrix: 6x6 probability grid.

        Returns:
            Tuple of ((home, away), probability).
        """
        best: tuple[int, int] = (0, 0)
        best_prob = matrix[0][0]

        for i in range(self.MAX_GOALS + 1):
            for j in range(self.MAX_GOALS + 1):
                prob = matrix[i][j]
                if prob > best_prob:
                    best = (i, j)
                    best_prob = prob
                elif prob == best_prob:
                    current_total = i + j
                    best_total = best[0] + best[1]
                    if current_total < best_total:
                        best = (i, j)
                    elif current_total == best_total and i < best[0]:
                        best = (i, j)

        return best, best_prob


def _result_sign(home: int, away: int) -> int:
    """Compute result sign: 1 for home win, -1 for away win, 0 for draw.

    Args:
        home: Home team goals.
        away: Away team goals.

    Returns:
        1, 0, or -1 representing the match outcome.
    """
    if home > away:
        return 1
    elif home < away:
        return -1
    return 0
