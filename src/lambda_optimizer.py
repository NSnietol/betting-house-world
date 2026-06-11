"""Lambda Optimizer module for reverse-engineering Expected Goals (λ, μ).

Uses scipy.optimize.minimize with the L-BFGS-B method to find λ (home xG)
and μ (away xG) that best fit the real probabilities derived from bookmaker
odds via a Poisson model.
"""

from __future__ import annotations

import logging
import math

from scipy.optimize import minimize
from scipy.stats import poisson as poisson_dist

from src.models import OptimizationResult

logger = logging.getLogger(__name__)

# Weights for the multi-line optimization objective
WEIGHT_OVERALL: float = 1.0
WEIGHT_TEAM: float = 2.0
WEIGHT_1X2: float = 1.0


class LambdaOptimizer:
    """Fits expected goals parameters using scipy L-BFGS-B optimization."""

    INITIAL_GUESS = (1.5, 1.2)
    BOUNDS = ((0.1, 5.0), (0.1, 5.0))
    CONVERGENCE_THRESHOLD = 0.02

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

    # ------------------------------------------------------------------
    # Multi-line weighted optimization
    # ------------------------------------------------------------------

    def optimize_weighted(
        self,
        real_probs_1x2: tuple[float, float, float],
        overall_ou_targets: list[tuple[float, float]] | None = None,
        home_team_ou_targets: list[tuple[float, float]] | None = None,
        away_team_ou_targets: list[tuple[float, float]] | None = None,
    ) -> OptimizationResult | None:
        """Find λ/μ using weighted multi-line objective.

        Uses 1X2 probabilities combined with multiple over/under thresholds
        (overall and team-specific) for a more robust estimation.

        Args:
            real_probs_1x2: (p_home, p_draw, p_away) real probabilities.
            overall_ou_targets: list of (threshold, over_probability) for
                total goals, e.g. [(0.5, 0.92), (2.5, 0.52)].
            home_team_ou_targets: list of (threshold, over_probability) for
                home team goals.
            away_team_ou_targets: list of (threshold, over_probability) for
                away team goals.

        Returns:
            OptimizationResult with primary (full) and secondary (team-only)
            estimates. Returns None if optimization fails to converge.
        """
        overall_ou_targets = overall_ou_targets or []
        home_team_ou_targets = home_team_ou_targets or []
        away_team_ou_targets = away_team_ou_targets or []

        # Primary optimization: uses all available data
        primary = self._run_weighted_optimization(
            real_probs_1x2,
            overall_ou_targets,
            home_team_ou_targets,
            away_team_ou_targets,
        )
        if primary is None:
            return None

        # Secondary optimization: uses only team targets + 1X2 (skip overall)
        secondary_lam: float | None = None
        secondary_mu: float | None = None

        if home_team_ou_targets or away_team_ou_targets:
            secondary = self._run_weighted_optimization(
                real_probs_1x2,
                [],  # no overall
                home_team_ou_targets,
                away_team_ou_targets,
            )
            if secondary is not None:
                secondary_lam, secondary_mu = secondary

        return OptimizationResult(
            primary_lambda=primary[0],
            primary_mu=primary[1],
            secondary_lambda=secondary_lam,
            secondary_mu=secondary_mu,
        )

    def _run_weighted_optimization(
        self,
        real_probs_1x2: tuple[float, float, float],
        overall_ou_targets: list[tuple[float, float]],
        home_team_ou_targets: list[tuple[float, float]],
        away_team_ou_targets: list[tuple[float, float]],
    ) -> tuple[float, float] | None:
        """Run a single weighted optimization pass.

        Args:
            real_probs_1x2: (p_home, p_draw, p_away) real probabilities.
            overall_ou_targets: list of (threshold, over_prob) for total goals.
            home_team_ou_targets: list of (threshold, over_prob) for home team.
            away_team_ou_targets: list of (threshold, over_prob) for away team.

        Returns:
            (λ, μ) rounded to 4 decimal places, or None on failure.
        """
        p_home, p_draw, p_away = real_probs_1x2

        def objective(params: list[float]) -> float:
            lam, mu = params

            # 1X2 errors
            p_h, p_d, p_a = self._poisson_1x2(lam, mu)
            err_1x2 = (p_h - p_home) ** 2 + (p_d - p_draw) ** 2 + (p_a - p_away) ** 2

            # Overall O/U errors
            err_overall = 0.0
            for threshold, target_prob in overall_ou_targets:
                model_prob = self._poisson_over_threshold(lam, mu, threshold)
                err_overall += (model_prob - target_prob) ** 2

            # Team O/U errors
            err_team = 0.0
            for threshold, target_prob in home_team_ou_targets:
                model_prob = self._poisson_team_over_threshold(lam, threshold)
                err_team += (model_prob - target_prob) ** 2
            for threshold, target_prob in away_team_ou_targets:
                model_prob = self._poisson_team_over_threshold(mu, threshold)
                err_team += (model_prob - target_prob) ** 2

            return (
                WEIGHT_1X2 * err_1x2
                + WEIGHT_OVERALL * err_overall
                + WEIGHT_TEAM * err_team
            )

        result = minimize(
            objective,
            x0=self.INITIAL_GUESS,
            method="L-BFGS-B",
            bounds=self.BOUNDS,
        )

        # Weighted optimization has more terms, so allow higher residual
        weighted_threshold = self.CONVERGENCE_THRESHOLD * 15  # ~0.30
        if not result.success or result.fun >= weighted_threshold:
            logger.warning(
                "Weighted convergence failure: success=%s, objective=%.8f",
                result.success,
                result.fun,
            )
            return None

        lam = round(result.x[0], 4)
        mu = round(result.x[1], 4)
        return (lam, mu)

    @staticmethod
    def _poisson_over_threshold(lam: float, mu: float, threshold: float) -> float:
        """Calculate P(home + away > threshold) using independent Poisson.

        Sums P(X=i)*P(Y=j) for all i+j <= floor(threshold) and returns
        1 - that sum.

        Args:
            lam: Home team expected goals (λ).
            mu: Away team expected goals (μ).
            threshold: Goal threshold (e.g. 2.5).

        Returns:
            Probability of total goals exceeding threshold.
        """
        max_k = int(math.floor(threshold))
        p_under = 0.0
        for i in range(max_k + 1):
            p_i = poisson_dist.pmf(i, lam)
            for j in range(max_k + 1 - i):
                p_j = poisson_dist.pmf(j, mu)
                p_under += p_i * p_j
        return 1.0 - p_under

    @staticmethod
    def _poisson_team_over_threshold(rate: float, threshold: float) -> float:
        """Calculate P(team > threshold) for a single team's Poisson rate.

        P(X > threshold) = 1 - sum(pmf(k, rate) for k in 0..floor(threshold))

        Args:
            rate: Team's expected goals (λ or μ).
            threshold: Goal threshold (e.g. 0.5, 1.5).

        Returns:
            Probability of team goals exceeding threshold.
        """
        max_k = int(math.floor(threshold))
        p_at_or_below = sum(
            poisson_dist.pmf(k, rate) for k in range(max_k + 1)
        )
        return 1.0 - p_at_or_below
