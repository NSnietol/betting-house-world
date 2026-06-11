"""Margin Eliminator module for removing bookmaker overround from odds.

This module converts raw decimal odds into real probabilities by removing
the bookmaker's commission (overround) using either the Shin method or
the Logarithmic method.
"""

from __future__ import annotations

import math


class AnomalousOddsError(Exception):
    """Raised when implied probabilities sum to 1.0 or less.

    This indicates anomalous odds that cannot have overround removed,
    as the bookmaker margin (overround) should always cause implied
    probabilities to exceed 1.0.
    """

    def __init__(self, implied_sum: float, odds: tuple[float, float, float]):
        self.implied_sum = implied_sum
        self.odds = odds
        super().__init__(
            f"Anomalous odds detected: implied probabilities sum to "
            f"{implied_sum:.4f} (≤ 1.0) for odds {odds}"
        )


class MarginEliminator:
    """Removes bookmaker overround to produce real probabilities.

    Supports two margin elimination methods:
    - 'shin': Shin's method accounting for favourite-longshot bias
    - 'logarithmic': Logarithmic normalization in log-odds space
    """

    VALID_METHODS = ("shin", "logarithmic")

    def __init__(self, method: str = "shin"):
        """Initialize MarginEliminator with the specified method.

        Args:
            method: Margin removal method, either 'shin' or 'logarithmic'.
                    Defaults to 'shin'.

        Raises:
            ValueError: If method is not 'shin' or 'logarithmic'.
        """
        if method not in self.VALID_METHODS:
            raise ValueError(
                f"Invalid method '{method}'. Must be one of: {self.VALID_METHODS}"
            )
        self.method = method

    def eliminate(
        self, decimal_odds: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """Convert 1X2 decimal odds to real probabilities.

        Converts decimal odds to implied probabilities, validates that
        the overround exists (sum > 1.0), then applies the configured
        margin elimination method.

        Args:
            decimal_odds: Tuple of (home_odd, draw_odd, away_odd) in decimal format.

        Returns:
            Tuple of (p_home, p_draw, p_away) summing to 1.0 (±0.001).

        Raises:
            AnomalousOddsError: If implied probabilities sum to ≤ 1.0.
        """
        # Convert decimal odds to implied probabilities (1 / odd)
        implied_probs = tuple(1.0 / odd for odd in decimal_odds)
        implied_sum = sum(implied_probs)

        # Validate overround exists
        if implied_sum <= 1.0:
            raise AnomalousOddsError(implied_sum, decimal_odds)

        implied_probs_typed: tuple[float, float, float] = (
            implied_probs[0],
            implied_probs[1],
            implied_probs[2],
        )

        # Apply the configured method
        if self.method == "shin":
            return self._shin_method(implied_probs_typed)
        else:
            return self._logarithmic_method(implied_probs_typed)

    def _shin_method(
        self, implied_probs: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """Apply Shin's method to remove bookmaker margin.

        Shin's method accounts for the favourite-longshot bias present
        in bookmaker odds by modeling the proportion of insider trading (z).

        For n outcomes with implied probabilities p_i summing to S:
            z = (S - 1) / (S - 1 + n)  [simplified 3-outcome approximation]
            true_p_i = (sqrt(z² + 4*(1-z) * p_i² / S) - z) / (2*(1-z))

        Args:
            implied_probs: Tuple of implied probabilities (home, draw, away).

        Returns:
            Tuple of true probabilities (p_home, p_draw, p_away).
        """
        n = 3  # Number of outcomes (1X2)
        s = sum(implied_probs)

        # Compute proportion of insider trading
        z = (s - 1.0) / (s - 1.0 + n)

        # Compute true probabilities using Shin's formula
        true_probs = []
        for p_i in implied_probs:
            numerator = math.sqrt(z**2 + 4.0 * (1.0 - z) * p_i**2 / s) - z
            denominator = 2.0 * (1.0 - z)
            true_probs.append(numerator / denominator)

        # Normalize to ensure sum = 1.0 exactly
        total = sum(true_probs)
        if total > 0:
            true_probs = [p / total for p in true_probs]

        return (true_probs[0], true_probs[1], true_probs[2])

    def _logarithmic_method(
        self, implied_probs: tuple[float, float, float]
    ) -> tuple[float, float, float]:
        """Apply logarithmic normalization to remove bookmaker margin.

        Distributes the overround proportionally using power normalization:
            true_p_i = (p_i ^ k) / sum(p_j ^ k)

        where k is found such that the true probabilities sum to 1.0.
        This is equivalent to normalizing in log-odds space.

        The value of k is found via bisection search. For overround > 0,
        k > 1 will reduce the sum of powered probabilities to 1.0.

        Args:
            implied_probs: Tuple of implied probabilities (home, draw, away).

        Returns:
            Tuple of true probabilities (p_home, p_draw, p_away).
        """
        # Find k using bisection method
        k = self._find_k(implied_probs)

        # Apply power normalization
        powered = [p**k for p in implied_probs]
        total = sum(powered)

        true_probs = [p / total for p in powered]

        return (true_probs[0], true_probs[1], true_probs[2])

    def _find_k(
        self,
        implied_probs: tuple[float, float, float],
        tol: float = 1e-10,
        max_iter: int = 100,
    ) -> float:
        """Find the exponent k such that sum(p_i^k) / sum(p_j^k) sums to 1.0.

        Actually, we need k such that sum(p_i^k) = 1.0 when the p_i are the
        implied probs. Since sum(p_i) > 1.0 (overround), we need k > 1.

        Uses bisection on the function f(k) = sum(p_i^k) - 1.0.

        Args:
            implied_probs: Tuple of implied probabilities.
            tol: Convergence tolerance.
            max_iter: Maximum iterations for bisection.

        Returns:
            The value of k.
        """
        # At k=1, sum = S > 1 (overround). As k increases, sum decreases
        # (since all p_i < 1 for reasonable odds). We need sum(p_i^k) = 1.
        lo = 1.0
        hi = 100.0

        # Verify bounds
        def f(k: float) -> float:
            return sum(p**k for p in implied_probs) - 1.0

        # Expand hi if needed
        while f(hi) > 0 and hi < 1e6:
            hi *= 2.0

        # Bisection
        for _ in range(max_iter):
            mid = (lo + hi) / 2.0
            val = f(mid)
            if abs(val) < tol:
                return mid
            if val > 0:
                lo = mid
            else:
                hi = mid

        return (lo + hi) / 2.0
