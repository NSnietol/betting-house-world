"""Score Matrix Generator module.

Generates a 6×6 Poisson probability grid and selects the most probable exact score.
"""

from __future__ import annotations

import math


class ScoreMatrixGenerator:
    """Generates a Poisson probability grid and selects the most probable exact score.

    Uses the independent Poisson model where P(home=i, away=j) = P(X=i) * P(Y=j),
    with X ~ Poisson(λ) and Y ~ Poisson(μ).
    """

    MAX_GOALS = 5  # 0 to 5 inclusive → 6×6 grid

    def generate(self, lam: float, mu: float) -> dict | None:
        """Generate the score matrix and select the most probable scoreline.

        Args:
            lam: Expected goals for the home team (λ).
            mu: Expected goals for the away team (μ).

        Returns:
            A dict with:
                "matrix": 6×6 probability grid (list of lists),
                "suggested_score": (home_goals, away_goals) tuple,
                "score_probability": probability of the suggested score as a fraction.
            Returns None if λ or μ is unavailable or non-positive.
        """
        if lam is None or mu is None or lam <= 0 or mu <= 0:
            return None

        # Build the 6×6 matrix
        matrix: list[list[float]] = []
        for i in range(self.MAX_GOALS + 1):
            row: list[float] = []
            for j in range(self.MAX_GOALS + 1):
                prob = _poisson_pmf(i, lam) * _poisson_pmf(j, mu)
                row.append(prob)
            matrix.append(row)

        # Select the cell with the highest probability (with tiebreakers)
        home_goals, away_goals = self._select_max_cell(matrix)

        return {
            "matrix": matrix,
            "suggested_score": (home_goals, away_goals),
            "score_probability": matrix[home_goals][away_goals],
        }

    def _select_max_cell(self, matrix: list[list[float]]) -> tuple[int, int]:
        """Select the cell with the highest probability.

        Tiebreaker 1: lowest total goals (home + away).
        Tiebreaker 2: fewer home goals.

        Args:
            matrix: 6×6 probability grid.

        Returns:
            (home_goals, away_goals) of the selected cell.
        """
        best: tuple[int, int] = (0, 0)
        best_prob = matrix[0][0]

        for i in range(len(matrix)):
            for j in range(len(matrix[i])):
                prob = matrix[i][j]
                if prob > best_prob:
                    best = (i, j)
                    best_prob = prob
                elif prob == best_prob:
                    # Tiebreaker 1: lowest total goals
                    current_total = i + j
                    best_total = best[0] + best[1]
                    if current_total < best_total:
                        best = (i, j)
                    elif current_total == best_total:
                        # Tiebreaker 2: fewer home goals
                        if i < best[0]:
                            best = (i, j)

        return best


def _poisson_pmf(k: int, lam: float) -> float:
    """Compute the Poisson probability mass function.

    P(X = k) = (λ^k * e^(-λ)) / k!

    Args:
        k: Number of occurrences (non-negative integer).
        lam: Rate parameter (λ > 0).

    Returns:
        Probability of exactly k occurrences.
    """
    return (lam ** k) * math.exp(-lam) / math.factorial(k)
