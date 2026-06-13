"""Polla Mundialista Expected Points Optimizer.

Takes a Poisson score matrix (6×6 grid of probabilities) and finds the
prediction that maximizes expected Polla points rather than just picking
the most probable score.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PollaResult:
    """Optimal prediction for the Polla Mundialista.

    Attributes:
        predicted_home_goals: Optimal home goals prediction.
        predicted_away_goals: Optimal away goals prediction.
        expected_points: Expected points for this prediction.
        prob_exact_score: Probability of exact score match.
        prob_correct_result: Probability of correct 1X2.
        prob_correct_home_goals: Probability home goals are right.
        prob_correct_away_goals: Probability away goals are right.
        prob_correct_diff: Probability goal difference is right.
        breakdown: Human-readable breakdown.
    """

    predicted_home_goals: int
    predicted_away_goals: int
    expected_points: float
    prob_exact_score: float
    prob_correct_result: float
    prob_correct_home_goals: float
    prob_correct_away_goals: float
    prob_correct_diff: float
    breakdown: str


class PollaOptimizer:
    """Finds the prediction maximizing expected Polla Mundialista points.

    The Polla scoring awards points for:
    - Correct result (winner or draw): 5 pts group / 10 pts knockout
    - Correct home goals: 2 pts group / 4 pts knockout
    - Correct away goals: 2 pts group / 4 pts knockout
    - Correct goal difference: 1 pt group / 2 pts knockout
    - Maximum: 10 pts group / 20 pts knockout

    Only 90 minutes + stoppage time counts (no extra time or penalties).
    """

    def __init__(self, is_knockout: bool = False) -> None:
        """Initialize with match phase.

        Args:
            is_knockout: True for elimination rounds (double points).
        """
        self.is_knockout = is_knockout
        self.result_pts = 10 if is_knockout else 5
        self.goals_pts = 4 if is_knockout else 2
        self.diff_pts = 2 if is_knockout else 1

    def optimize(self, matrix: list[list[float]]) -> PollaResult:
        """Find the score prediction maximizing expected Polla points.

        For each candidate prediction (h, a) where h, a in [0, 5]:
        - Compute expected points by iterating over ALL possible actual outcomes
        - For each actual outcome (i, j), calculate the Polla points earned
          if prediction is (h, a) and actual is (i, j), weighted by P(i, j)
        - Return the prediction with highest expected points

        Args:
            matrix: 6×6 Poisson probability grid (matrix[i][j] = P(home=i, away=j))

        Returns:
            PollaResult with optimal prediction and expected value breakdown.
        """
        best_h = 0
        best_a = 0
        best_expected = -1.0

        # Evaluate all 36 candidate predictions
        for pred_h in range(6):
            for pred_a in range(6):
                ep = self._compute_expected_points(pred_h, pred_a, matrix)
                if ep > best_expected:
                    best_expected = ep
                    best_h = pred_h
                    best_a = pred_a

        # Compute marginal probabilities for the optimal prediction
        prob_exact = matrix[best_h][best_a]
        prob_result = self._prob_correct_result(best_h, best_a, matrix)
        prob_home = self._prob_correct_home_goals(best_h, matrix)
        prob_away = self._prob_correct_away_goals(best_a, matrix)
        prob_diff = self._prob_correct_diff(best_h, best_a, matrix)

        # Build breakdown string
        phase = "knockout" if self.is_knockout else "group"
        breakdown = (
            f"Prediction: {best_h}-{best_a} | Phase: {phase}\n"
            f"  E[points] = {best_expected:.2f}\n"
            f"  P(exact score) = {prob_exact:.3f}\n"
            f"  P(correct result) = {prob_result:.3f} → "
            f"contributes {prob_result * self.result_pts:.2f} E[pts]\n"
            f"  P(correct home goals) = {prob_home:.3f} → "
            f"contributes {prob_home * self.goals_pts:.2f} E[pts]\n"
            f"  P(correct away goals) = {prob_away:.3f} → "
            f"contributes {prob_away * self.goals_pts:.2f} E[pts]\n"
            f"  P(correct goal diff) = {prob_diff:.3f} → "
            f"contributes {prob_diff * self.diff_pts:.2f} E[pts]"
        )

        return PollaResult(
            predicted_home_goals=best_h,
            predicted_away_goals=best_a,
            expected_points=best_expected,
            prob_exact_score=prob_exact,
            prob_correct_result=prob_result,
            prob_correct_home_goals=prob_home,
            prob_correct_away_goals=prob_away,
            prob_correct_diff=prob_diff,
            breakdown=breakdown,
        )

    def _compute_expected_points(
        self, pred_h: int, pred_a: int, matrix: list[list[float]]
    ) -> float:
        """Compute expected Polla points for a given prediction.

        Iterates all possible actual outcomes, calculates points for each,
        weights by probability.

        Args:
            pred_h: Predicted home goals.
            pred_a: Predicted away goals.
            matrix: 6×6 probability grid.

        Returns:
            Expected points (weighted sum across all outcomes).
        """
        expected = 0.0
        for actual_h in range(6):
            for actual_a in range(6):
                prob = matrix[actual_h][actual_a]
                if prob <= 0:
                    continue
                pts = self._score_prediction(pred_h, pred_a, actual_h, actual_a)
                expected += prob * pts
        return expected

    def _score_prediction(
        self, pred_h: int, pred_a: int, actual_h: int, actual_a: int
    ) -> int:
        """Calculate Polla points for a prediction given the actual result.

        Rules:
        - result_pts if winner/draw matches
        - goals_pts for each correct team goal count
        - diff_pts if goal difference matches

        Args:
            pred_h: Predicted home goals.
            pred_a: Predicted away goals.
            actual_h: Actual home goals.
            actual_a: Actual away goals.

        Returns:
            Points earned for this prediction/result combination.
        """
        points = 0

        # Correct result (winner/draw)
        pred_result = 1 if pred_h > pred_a else (-1 if pred_h < pred_a else 0)
        actual_result = (
            1 if actual_h > actual_a else (-1 if actual_h < actual_a else 0)
        )
        if pred_result == actual_result:
            points += self.result_pts

        # Correct home goals
        if pred_h == actual_h:
            points += self.goals_pts

        # Correct away goals
        if pred_a == actual_a:
            points += self.goals_pts

        # Correct goal difference
        if (pred_h - pred_a) == (actual_h - actual_a):
            points += self.diff_pts

        return points

    def _prob_correct_result(
        self, pred_h: int, pred_a: int, matrix: list[list[float]]
    ) -> float:
        """Sum of all matrix cells where the 1X2 outcome matches the prediction.

        Args:
            pred_h: Predicted home goals.
            pred_a: Predicted away goals.
            matrix: 6×6 probability grid.

        Returns:
            Probability of predicting the correct 1X2 result.
        """
        pred_result = 1 if pred_h > pred_a else (-1 if pred_h < pred_a else 0)
        total = 0.0
        for i in range(6):
            for j in range(6):
                actual_result = 1 if i > j else (-1 if i < j else 0)
                if actual_result == pred_result:
                    total += matrix[i][j]
        return total

    def _prob_correct_home_goals(
        self, pred_h: int, matrix: list[list[float]]
    ) -> float:
        """Sum of matrix[pred_h][j] for all j.

        Args:
            pred_h: Predicted home goals.
            matrix: 6×6 probability grid.

        Returns:
            Probability that home goals match the prediction.
        """
        return sum(matrix[pred_h][j] for j in range(6))

    def _prob_correct_away_goals(
        self, pred_a: int, matrix: list[list[float]]
    ) -> float:
        """Sum of matrix[i][pred_a] for all i.

        Args:
            pred_a: Predicted away goals.
            matrix: 6×6 probability grid.

        Returns:
            Probability that away goals match the prediction.
        """
        return sum(matrix[i][pred_a] for i in range(6))

    def _prob_correct_diff(
        self, pred_h: int, pred_a: int, matrix: list[list[float]]
    ) -> float:
        """Sum of all matrix[i][j] where i-j == pred_h - pred_a.

        Args:
            pred_h: Predicted home goals.
            pred_a: Predicted away goals.
            matrix: 6×6 probability grid.

        Returns:
            Probability that the goal difference matches the prediction.
        """
        target_diff = pred_h - pred_a
        total = 0.0
        for i in range(6):
            for j in range(6):
                if (i - j) == target_diff:
                    total += matrix[i][j]
        return total
