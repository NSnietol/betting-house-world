"""Retro-feedback module for comparing predictions against actual results.

Provides prediction accuracy tracking by comparing previously predicted
scores against actual match results. Tracks per-bookmaker accuracy to
inform bookmaker reliability scoring.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

from src.cache_store import CacheStore
from src.models import AggregateMetrics, MatchComparison

logger = logging.getLogger(__name__)


def _determine_1x2_outcome(home_goals: int, away_goals: int) -> str:
    """Determine the 1X2 outcome from a scoreline.

    Args:
        home_goals: Goals scored by the home team.
        away_goals: Goals scored by the away team.

    Returns:
        '1' for home win, 'X' for draw, '2' for away win.
    """
    if home_goals > away_goals:
        return "1"
    elif home_goals == away_goals:
        return "X"
    else:
        return "2"


class RetroFeedback:
    """Compares predictions against actual results for a given date.

    Retrieves actual match results and compares them to stored predictions,
    computing accuracy metrics per match and in aggregate. Supports
    per-bookmaker tracking for multi-source adapter data.
    """

    def __init__(self, cache_store: CacheStore, sport: str) -> None:
        """Initialize RetroFeedback.

        Args:
            cache_store: CacheStore instance for accessing predictions and results.
            sport: Sport/league key (e.g., 'soccer_epl').
        """
        self.cache_store = cache_store
        self.sport = sport

    def run(self, retro_date: str) -> list[MatchComparison]:
        """Run retro-feedback analysis for the given date.

        Fetches actual results, compares to predictions, and prints
        the retro-feedback table to stdout.

        Args:
            retro_date: Date in YYYY-MM-DD format to analyze.

        Returns:
            List of MatchComparison objects for all matched predictions.
        """
        logger.info(
            "Retro-feedback mode for sport=%s, date=%s", self.sport, retro_date
        )

        predictions = self._get_predictions(retro_date)
        if not predictions:
            print(
                f"[Retro-feedback] No predictions found for {self.sport} "
                f"on {retro_date}."
            )
            return []

        comparisons = self._build_comparisons(predictions)
        if not comparisons:
            print(
                f"[Retro-feedback] No actual results available for "
                f"{self.sport} on {retro_date}."
            )
            return []

        self._print_table(comparisons)
        metrics = self._compute_aggregate_metrics(comparisons)
        self._print_aggregate_metrics(metrics)
        self._update_bookmaker_scores(comparisons)

        return comparisons

    def _get_predictions(self, retro_date: str) -> list[dict]:
        """Query predictions for the given sport and date.

        Args:
            retro_date: Date in YYYY-MM-DD format.

        Returns:
            List of prediction row dicts.
        """
        def _query(conn: sqlite3.Connection) -> list[dict]:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport_key, event_id, home_team, away_team,
                       predicted_home_goals, predicted_away_goals,
                       lambda_home, mu_away, bookmaker_source, prediction_date
                FROM predictions
                WHERE sport_key = ? AND prediction_date = ?
                """,
                (self.sport, retro_date),
            )
            return [dict(row) for row in cursor.fetchall()]

        return self.cache_store._execute_with_retry(_query)

    def _get_actual_result(self, event_id: str) -> dict | None:
        """Query actual result for a specific event.

        Args:
            event_id: The event identifier.

        Returns:
            Dict with actual result data or None if not found.
        """
        def _query(conn: sqlite3.Connection) -> dict | None:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport_key, event_id, home_team, away_team,
                       home_goals, away_goals, match_date
                FROM actual_results
                WHERE sport_key = ? AND event_id = ?
                """,
                (self.sport, event_id),
            )
            row = cursor.fetchone()
            return dict(row) if row else None

        return self.cache_store._execute_with_retry(_query)

    def _build_comparisons(
        self, predictions: list[dict]
    ) -> list[MatchComparison]:
        """Build MatchComparison objects for predictions with actual results.

        For each prediction, looks up the actual result. If no result is
        found in the database, the prediction is skipped with a log message.

        Args:
            predictions: List of prediction row dicts.

        Returns:
            List of MatchComparison objects.
        """
        comparisons: list[MatchComparison] = []

        for pred in predictions:
            result = self._get_actual_result(pred["event_id"])
            if result is None:
                logger.info(
                    "No actual result for event %s (%s vs %s). Skipping.",
                    pred["event_id"],
                    pred["home_team"],
                    pred["away_team"],
                )
                continue

            predicted_home = int(pred["predicted_home_goals"])
            predicted_away = int(pred["predicted_away_goals"])
            actual_home = int(result["home_goals"])
            actual_away = int(result["away_goals"])
            pred_lambda = float(pred["lambda_home"])
            pred_mu = float(pred["mu_away"])

            predicted_outcome = _determine_1x2_outcome(
                predicted_home, predicted_away
            )
            actual_outcome = _determine_1x2_outcome(actual_home, actual_away)

            x1x2_correct = predicted_outcome == actual_outcome
            score_correct = (
                predicted_home == actual_home and predicted_away == actual_away
            )
            lambda_error = abs(pred_lambda - actual_home)
            mu_error = abs(pred_mu - actual_away)

            comparison = MatchComparison(
                home_team=pred["home_team"],
                away_team=pred["away_team"],
                predicted_score=(predicted_home, predicted_away),
                actual_score=(actual_home, actual_away),
                predicted_lambda=pred_lambda,
                predicted_mu=pred_mu,
                x1x2_correct=x1x2_correct,
                score_correct=score_correct,
                lambda_error=lambda_error,
                mu_error=mu_error,
                bookmaker_source=pred["bookmaker_source"],
            )
            comparisons.append(comparison)

        return comparisons

    def _print_table(self, comparisons: list[MatchComparison]) -> None:
        """Print the retro-feedback table to stdout.

        Columns: Match | Predicted Score | Actual Score | 1X2 Correct |
        Score Correct | λ Error | μ Error | Bookmaker Source

        Args:
            comparisons: List of MatchComparison objects to display.
        """
        header = (
            "Match | Predicted Score | Actual Score | "
            "1X2 Correct | Score Correct | λ Error | μ Error | "
            "Bookmaker Source"
        )
        print(header)

        for c in comparisons:
            match_name = f"{c.home_team} vs {c.away_team}"
            pred_score = f"{c.predicted_score[0]}-{c.predicted_score[1]}"
            actual_score = f"{c.actual_score[0]}-{c.actual_score[1]}"
            x1x2_val = 1 if c.x1x2_correct else 0
            score_val = 1 if c.score_correct else 0
            print(
                f"{match_name} | {pred_score} | {actual_score} | "
                f"{x1x2_val} | {score_val} | "
                f"{c.lambda_error:.4f} | {c.mu_error:.4f} | "
                f"{c.bookmaker_source}"
            )

    def _compute_aggregate_metrics(
        self, comparisons: list[MatchComparison]
    ) -> AggregateMetrics:
        """Compute aggregate accuracy metrics across all comparisons.

        Args:
            comparisons: List of MatchComparison objects.

        Returns:
            AggregateMetrics with hit rates and mean errors.
        """
        total = len(comparisons)
        x1x2_hits = sum(1 for c in comparisons if c.x1x2_correct)
        score_hits = sum(1 for c in comparisons if c.score_correct)
        total_lambda_error = sum(c.lambda_error for c in comparisons)
        total_mu_error = sum(c.mu_error for c in comparisons)

        return AggregateMetrics(
            x1x2_hit_rate=(x1x2_hits / total * 100) if total > 0 else 0.0,
            score_hit_rate=(score_hits / total * 100) if total > 0 else 0.0,
            mean_lambda_error=(total_lambda_error / total) if total > 0 else 0.0,
            mean_mu_error=(total_mu_error / total) if total > 0 else 0.0,
        )

    def _print_aggregate_metrics(self, metrics: AggregateMetrics) -> None:
        """Print aggregate metrics below the retro-feedback table.

        Args:
            metrics: AggregateMetrics to display.
        """
        print("---")
        print(f"1X2 Hit Rate: {metrics.x1x2_hit_rate:.2f}%")
        print(f"Score Hit Rate: {metrics.score_hit_rate:.2f}%")
        print(f"Mean λ Error: {metrics.mean_lambda_error:.4f}")
        print(f"Mean μ Error: {metrics.mean_mu_error:.4f}")

    def _update_bookmaker_scores(
        self, comparisons: list[MatchComparison]
    ) -> None:
        """Update bookmaker_scores table with retro-feedback results.

        Accumulates per-bookmaker accuracy stats for reliability tracking.

        Args:
            comparisons: List of MatchComparison objects.
        """
        # Group comparisons by bookmaker
        bookmaker_data: dict[str, list[MatchComparison]] = {}
        for c in comparisons:
            bookmaker_data.setdefault(c.bookmaker_source, []).append(c)

        def _update(conn: sqlite3.Connection) -> None:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            for bk_key, bk_comparisons in bookmaker_data.items():
                new_matches = len(bk_comparisons)
                new_correct = sum(
                    1 for c in bk_comparisons if c.x1x2_correct
                )
                new_lambda_err = sum(c.lambda_error for c in bk_comparisons)
                new_mu_err = sum(c.mu_error for c in bk_comparisons)

                # Check if bookmaker already exists
                cursor.execute(
                    "SELECT total_matches, correct_1x2, sum_lambda_error, "
                    "sum_mu_error FROM bookmaker_scores WHERE bookmaker_key = ?",
                    (bk_key,),
                )
                existing = cursor.fetchone()

                if existing:
                    total = existing["total_matches"] + new_matches
                    correct = existing["correct_1x2"] + new_correct
                    sum_lam = existing["sum_lambda_error"] + new_lambda_err
                    sum_mu = existing["sum_mu_error"] + new_mu_err
                else:
                    total = new_matches
                    correct = new_correct
                    sum_lam = new_lambda_err
                    sum_mu = new_mu_err

                # Compute reliability score
                hit_rate = correct / total if total > 0 else 0.0
                mean_lam = sum_lam / total if total > 0 else 0.0
                mean_mu = sum_mu / total if total > 0 else 0.0

                # Normalize errors (cap at 5.0 for scaling purposes)
                norm_lam = min(mean_lam / 5.0, 1.0)
                norm_mu = min(mean_mu / 5.0, 1.0)

                reliability = (
                    0.5 * hit_rate
                    + 0.25 * (1 - norm_lam)
                    + 0.25 * (1 - norm_mu)
                )

                is_active = 1 if reliability >= 0.30 else 0

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO bookmaker_scores
                        (bookmaker_key, total_matches, correct_1x2,
                         sum_lambda_error, sum_mu_error,
                         reliability_score, is_active, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        bk_key, total, correct, sum_lam, sum_mu,
                        reliability, is_active, now,
                    ),
                )

        try:
            self.cache_store._execute_with_retry(_update)
        except Exception as exc:
            logger.warning("Failed to update bookmaker scores: %s", exc)
