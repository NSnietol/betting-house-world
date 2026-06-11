"""Lambda Optimizer module for reverse-engineering Expected Goals (λ, μ).

Uses scipy.optimize.minimize with the L-BFGS-B method to find λ (home xG)
and μ (away xG) that best fit the real probabilities derived from bookmaker
odds via a Poisson model.
"""

from __future__ import annotations

import logging
import math

from scipy.optimize import minimize

logger = logging.getLogger(__name__)


class LambdaOptimizer:
    """Fits expected goals parameters using scipy L-BFGS-B optimization."""

    INITIAL_GUESS = (1.5, 1.2)
    BOUNDS = ((0.1, 5.0), (0.1, 5.0))
    CONVERGENCE_THRESHOLD = 1e-6

    def optimize(
        self,
        real_probs_1x2: tuple[float, float, float],
        real_prob_over_2_5: float,
    ) -> tuple[float, float] | None:
        """Find λ (home xG) and μ (away xG) that minimize sum of squared diffs.

        Objective function:
            f(λ, μ) = (P_home_poisson - p_home)²
                     + (P_draw_poisson - p_draw)²
                     + (P_away_poisson - p_away)²
                     + (P_over_2.5_poisson - p_over)²

        Args:
            real_probs_1x2: (p_home, p_draw, p_away) real probabilities.
            real_prob_over_2_5: Real probability of over 2.5 goals.

        Returns:
            (λ, μ) rounded to 4 decimal places, or None on failure.
        """

        def objective(params: list[float]) -> float:
            lam, mu = params
            p_home_poisson, p_draw_poisson, p_away_poisson = self._poisson_1x2(
                lam, mu
            )
            p_over_poisson = self._poisson_over_2_5(lam, mu)

            p_home, p_draw, p_away = real_probs_1x2

            return (
                (p_home_poisson - p_home) ** 2
                + (p_draw_poisson - p_draw) ** 2
                + (p_away_poisson - p_away) ** 2
                + (p_over_poisson - real_prob_over_2_5) ** 2
            )

        result = minimize(
            objective,
            x0=self.INITIAL_GUESS,
            method="L-BFGS-B",
            bounds=self.BOUNDS,
        )

        if not result.success or result.fun >= self.CONVERGENCE_THRESHOLD:
            logger.warning(
                "Convergence failure: success=%s, objective=%.8f",
                result.success,
                result.fun,
            )
            return None

        lam = round(result.x[0], 4)
        mu = round(result.x[1], 4)
        return (lam, mu)

    def _poisson_1x2(
        self, lam: float, mu: float, max_goals: int = 10
    ) -> tuple[float, float, float]:
        """Calculate P(home win), P(draw), P(away win) from independent Poisson.

        Sums over i, j from 0 to max_goals:
        - P(home) = sum of P(X=i)*P(Y=j) where i > j
        - P(draw) = sum of P(X=i)*P(Y=j) where i == j
        - P(away) = sum of P(X=i)*P(Y=j) where i < j

        Args:
            lam: Home team expected goals (λ).
            mu: Away team expected goals (μ).
            max_goals: Upper bound for goal summation (inclusive).

        Returns:
            (p_home, p_draw, p_away) probabilities.
        """
        p_home = 0.0
        p_draw = 0.0
        p_away = 0.0

        for i in range(max_goals + 1):
            p_i = self._poisson_pmf(i, lam)
            for j in range(max_goals + 1):
                p_j = self._poisson_pmf(j, mu)
                prob = p_i * p_j
                if i > j:
                    p_home += prob
                elif i == j:
                    p_draw += prob
                else:
                    p_away += prob

        return (p_home, p_draw, p_away)

    def _poisson_over_2_5(
        self, lam: float, mu: float, max_goals: int = 10
    ) -> float:
        """Calculate P(total goals > 2.5) from independent Poisson.

        P(over 2.5) = 1 - sum of P(X=i)*P(Y=j) where i+j <= 2

        Args:
            lam: Home team expected goals (λ).
            mu: Away team expected goals (μ).
            max_goals: Upper bound for goal summation (inclusive).

        Returns:
            Probability of total goals exceeding 2.5.
        """
        p_under = 0.0

        for i in range(max_goals + 1):
            p_i = self._poisson_pmf(i, lam)
            for j in range(max_goals + 1):
                if i + j <= 2:
                    p_j = self._poisson_pmf(j, mu)
                    p_under += p_i * p_j

        return 1.0 - p_under

    @staticmethod
    def _poisson_pmf(k: int, lam: float) -> float:
        """Compute Poisson probability mass function P(X=k) for parameter λ.

        Uses math.exp and math.factorial for numerical stability at small k.

        Args:
            k: Number of events (goals).
            lam: Expected rate (λ or μ).

        Returns:
            P(X = k) under Poisson(lam).
        """
        return (lam**k) * math.exp(-lam) / math.factorial(k)
