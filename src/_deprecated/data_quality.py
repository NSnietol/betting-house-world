"""Data Quality Analyzer for betting odds.

Computes quality flags for each match:
- Variance Flag: max standard deviation of implied probabilities across bookmakers
- High Margin: max overround across bookmakers
- Low Coverage: number of bookmakers when below threshold
"""

from __future__ import annotations

import math


class DataQualityAnalyzer:
    """Analyzes data quality of match odds and produces numeric flags."""

    VARIANCE_THRESHOLD = 0.03
    HIGH_MARGIN_THRESHOLD = 0.10
    LOW_COVERAGE_THRESHOLD = 3

    def analyze(self, match_odds: dict) -> dict:
        """Analyze match odds and return quality flags.

        Args:
            match_odds: Dictionary with key "bookmaker_odds" containing a list
                of dicts, each with "decimal_odds": (home, draw, away).

        Returns:
            {
                "variance_flag": float,  # max std dev across 1X2 outcomes, 0.00 if below threshold
                "high_margin": float,    # max overround across bookmakers, 0.00 if below threshold
                "low_coverage": int      # number of bookmakers, 0 if >= threshold
            }
        """
        bookmaker_odds_list = match_odds.get("bookmaker_odds", [])
        num_bookmakers = len(bookmaker_odds_list)

        # Compute implied probabilities for each bookmaker
        bookmaker_probs: list[tuple[float, float, float]] = []
        for bm in bookmaker_odds_list:
            odds = bm["decimal_odds"]
            home_imp = 1.0 / odds[0]
            draw_imp = 1.0 / odds[1]
            away_imp = 1.0 / odds[2]
            bookmaker_probs.append((home_imp, draw_imp, away_imp))

        # Variance flag
        variance_flag = 0.00
        if len(bookmaker_probs) >= 2:
            max_std = self._compute_std_dev(bookmaker_probs)
            if max_std > self.VARIANCE_THRESHOLD:
                variance_flag = max_std

        # High margin flag
        high_margin = 0.00
        max_overround = 0.00
        for bm in bookmaker_odds_list:
            odds = bm["decimal_odds"]
            overround = self._compute_overround(odds)
            if overround > max_overround:
                max_overround = overround
        if max_overround > self.HIGH_MARGIN_THRESHOLD:
            high_margin = max_overround

        # Low coverage flag
        low_coverage = 0
        if num_bookmakers < self.LOW_COVERAGE_THRESHOLD:
            low_coverage = num_bookmakers

        return {
            "variance_flag": round(variance_flag, 4),
            "high_margin": round(high_margin, 4),
            "low_coverage": low_coverage,
        }

    def _compute_std_dev(
        self, bookmaker_probs: list[tuple[float, float, float]]
    ) -> float:
        """Compute max standard deviation across home/draw/away implied probs.

        Calculates population standard deviation for each outcome position
        across all bookmakers, then returns the maximum of the three.

        Args:
            bookmaker_probs: List of (home_prob, draw_prob, away_prob) tuples.

        Returns:
            Maximum population standard deviation across the three outcomes.
        """
        n = len(bookmaker_probs)
        if n < 2:
            return 0.0

        # Extract each outcome position
        home_probs = [bp[0] for bp in bookmaker_probs]
        draw_probs = [bp[1] for bp in bookmaker_probs]
        away_probs = [bp[2] for bp in bookmaker_probs]

        std_home = self._population_std_dev(home_probs)
        std_draw = self._population_std_dev(draw_probs)
        std_away = self._population_std_dev(away_probs)

        return max(std_home, std_draw, std_away)

    def _compute_overround(self, decimal_odds: tuple[float, float, float]) -> float:
        """Compute overround as sum of implied probabilities minus 1.0.

        Args:
            decimal_odds: (home_odd, draw_odd, away_odd) in decimal format.

        Returns:
            The overround value (sum of reciprocals minus 1.0).
        """
        return (1.0 / decimal_odds[0]) + (1.0 / decimal_odds[1]) + (1.0 / decimal_odds[2]) - 1.0

    @staticmethod
    def _population_std_dev(values: list[float]) -> float:
        """Compute population standard deviation of a list of values.

        Args:
            values: List of numeric values.

        Returns:
            Population standard deviation.
        """
        n = len(values)
        if n == 0:
            return 0.0
        mean = sum(values) / n
        variance = sum((x - mean) ** 2 for x in values) / n
        return math.sqrt(variance)
