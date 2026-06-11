"""Bookmaker reliability scoring module.

Tracks per-bookmaker accuracy statistics over time and flags unreliable
bookmakers for exclusion from future processing.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone

from src.cache_store import CacheStore

logger = logging.getLogger(__name__)


@dataclass
class RetroResult:
    """Per-bookmaker comparison result from a retro-feedback run.

    Attributes:
        bookmaker_key: Adapter bookmaker_id (e.g., 'onexbet', 'betfair').
        correct_1x2: Whether the 1X2 outcome was correctly predicted (1 or 0).
        lambda_error: Absolute error between predicted λ and actual home goals.
        mu_error: Absolute error between predicted μ and actual away goals.
    """

    bookmaker_key: str
    correct_1x2: int
    lambda_error: float
    mu_error: float


class BookmakerScorer:
    """Manages bookmaker reliability scores and exclusion logic.

    Accumulates per-bookmaker accuracy statistics from retro-feedback runs
    and computes a reliability score. Bookmakers falling below the threshold
    are flagged as unreliable and excluded from extraction.

    The reliability score formula:
        0.5 * (1X2_hit_rate) + 0.25 * (1 - norm_lambda_error) + 0.25 * (1 - norm_mu_error)

    Where normalized errors are scaled to [0, 1] relative to the worst-performing
    bookmaker. Adapter IDs (like 'onexbet', 'unibet', 'betfair', 'the_odds_api')
    are used as bookmaker_key in the scores table.
    """

    def __init__(
        self,
        cache_store: CacheStore,
        reliability_threshold: float = 0.30,
    ) -> None:
        """Initialize BookmakerScorer.

        Args:
            cache_store: CacheStore instance for accessing bookmaker_scores table.
            reliability_threshold: Score below which a bookmaker is excluded.
                Defaults to 0.30.
        """
        self.cache_store = cache_store
        self.reliability_threshold = reliability_threshold

    def update_scores(self, retro_results: list[RetroResult]) -> None:
        """Update bookmaker scores from retro-feedback comparison results.

        For each bookmaker in the results:
            1. Increment total_matches
            2. Update correct_1x2 count
            3. Accumulate lambda/mu errors (sum_lambda_error, sum_mu_error)
            4. Recompute reliability_score using the formula
            5. If reliability_score falls below threshold, set is_active = 0

        The reliability score normalizes lambda/mu errors relative to the
        worst-performing bookmaker across all tracked bookmakers.

        Args:
            retro_results: List of per-bookmaker, per-match comparison results.
        """
        if not retro_results:
            return

        try:
            conn = sqlite3.connect(self.cache_store.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

            # Step 1: Upsert per-bookmaker stats
            for result in retro_results:
                cursor.execute(
                    "SELECT total_matches, correct_1x2, sum_lambda_error, "
                    "sum_mu_error FROM bookmaker_scores WHERE bookmaker_key = ?",
                    (result.bookmaker_key,),
                )
                row = cursor.fetchone()

                if row is None:
                    # Insert new bookmaker record
                    cursor.execute(
                        "INSERT INTO bookmaker_scores "
                        "(bookmaker_key, total_matches, correct_1x2, "
                        "sum_lambda_error, sum_mu_error, reliability_score, "
                        "is_active, last_updated) "
                        "VALUES (?, 1, ?, ?, ?, 1.0, 1, ?)",
                        (
                            result.bookmaker_key,
                            result.correct_1x2,
                            result.lambda_error,
                            result.mu_error,
                            now,
                        ),
                    )
                else:
                    # Update existing record
                    new_total = row["total_matches"] + 1
                    new_correct = row["correct_1x2"] + result.correct_1x2
                    new_sum_lambda = row["sum_lambda_error"] + result.lambda_error
                    new_sum_mu = row["sum_mu_error"] + result.mu_error

                    cursor.execute(
                        "UPDATE bookmaker_scores SET "
                        "total_matches = ?, correct_1x2 = ?, "
                        "sum_lambda_error = ?, sum_mu_error = ?, "
                        "last_updated = ? "
                        "WHERE bookmaker_key = ?",
                        (
                            new_total,
                            new_correct,
                            new_sum_lambda,
                            new_sum_mu,
                            now,
                            result.bookmaker_key,
                        ),
                    )

            conn.commit()

            # Step 2: Recompute reliability scores for all bookmakers
            self._recompute_all_scores(cursor, now)
            conn.commit()
            conn.close()

        except Exception as exc:
            logger.warning("Failed to update bookmaker scores: %s", exc)

    def _recompute_all_scores(
        self, cursor: sqlite3.Cursor, now: str
    ) -> None:
        """Recompute reliability scores for all bookmakers.

        Normalizes lambda/mu errors relative to the worst-performing bookmaker
        and applies the reliability formula. Flags bookmakers below threshold.

        Args:
            cursor: Active SQLite cursor within a transaction.
            now: Current UTC timestamp string.
        """
        cursor.execute(
            "SELECT bookmaker_key, total_matches, correct_1x2, "
            "sum_lambda_error, sum_mu_error "
            "FROM bookmaker_scores WHERE total_matches > 0"
        )
        rows = cursor.fetchall()

        if not rows:
            return

        # Compute mean errors for each bookmaker
        bookmaker_stats: list[dict] = []
        for row in rows:
            total = row["total_matches"]
            hit_rate = row["correct_1x2"] / total
            mean_lambda = row["sum_lambda_error"] / total
            mean_mu = row["sum_mu_error"] / total
            bookmaker_stats.append(
                {
                    "bookmaker_key": row["bookmaker_key"],
                    "hit_rate": hit_rate,
                    "mean_lambda": mean_lambda,
                    "mean_mu": mean_mu,
                }
            )

        # Find worst (maximum) mean errors for normalization
        max_lambda = max(s["mean_lambda"] for s in bookmaker_stats)
        max_mu = max(s["mean_mu"] for s in bookmaker_stats)

        # Compute reliability score for each bookmaker
        for stats in bookmaker_stats:
            # Normalize errors to [0, 1] relative to worst performer
            norm_lambda = (
                stats["mean_lambda"] / max_lambda if max_lambda > 0 else 0.0
            )
            norm_mu = stats["mean_mu"] / max_mu if max_mu > 0 else 0.0

            # Reliability formula: 0.5 * hit_rate + 0.25 * (1 - norm_lambda) + 0.25 * (1 - norm_mu)
            reliability_score = (
                0.5 * stats["hit_rate"]
                + 0.25 * (1.0 - norm_lambda)
                + 0.25 * (1.0 - norm_mu)
            )

            # Flag bookmaker as inactive if below threshold
            is_active = 1 if reliability_score >= self.reliability_threshold else 0

            cursor.execute(
                "UPDATE bookmaker_scores SET "
                "reliability_score = ?, is_active = ?, last_updated = ? "
                "WHERE bookmaker_key = ?",
                (reliability_score, is_active, now, stats["bookmaker_key"]),
            )

    def get_excluded_bookmakers(self) -> list[str]:
        """Return list of bookmaker keys flagged as unreliable.

        Queries the bookmaker_scores table for bookmakers whose
        is_active == 0. These correspond to adapter bookmaker_ids
        (e.g., 'onexbet', 'betfair', 'the_odds_api') that should
        be excluded from extraction.

        Returns:
            List of bookmaker key strings to exclude from extraction.
        """
        excluded: list[str] = []
        try:
            conn = sqlite3.connect(self.cache_store.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT bookmaker_key FROM bookmaker_scores WHERE is_active = 0"
            )
            rows = cursor.fetchall()
            conn.close()
            excluded = [row["bookmaker_key"] for row in rows]
        except Exception as exc:
            logger.warning("Failed to query excluded bookmakers: %s", exc)
        return excluded

    def enable_bookmaker(self, bookmaker_key: str) -> None:
        """Re-enable a previously excluded bookmaker.

        Args:
            bookmaker_key: The bookmaker key (adapter ID) to re-enable.
        """
        try:
            conn = sqlite3.connect(self.cache_store.db_path, timeout=5)
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE bookmaker_scores SET is_active = 1 WHERE bookmaker_key = ?",
                (bookmaker_key,),
            )
            conn.commit()
            affected = cursor.rowcount
            conn.close()
            if affected > 0:
                print(f"Bookmaker '{bookmaker_key}' re-enabled successfully.")
            else:
                print(f"Bookmaker '{bookmaker_key}' not found in scores table.")
        except Exception as exc:
            logger.warning("Failed to re-enable bookmaker '%s': %s", bookmaker_key, exc)

    def print_report(self) -> None:
        """Print bookmaker reliability report table to stdout.

        Outputs a pipe-delimited table with columns:
        [Bookmaker] | [Matches] | [1X2 Hit Rate %] | [Mean λ Error] |
        [Mean μ Error] | [Reliability Score] | [Status]

        All values are strictly numeric (no text labels in data columns).
        """
        try:
            conn = sqlite3.connect(self.cache_store.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT bookmaker_key, total_matches, correct_1x2, "
                "sum_lambda_error, sum_mu_error, reliability_score, is_active "
                "FROM bookmaker_scores ORDER BY reliability_score DESC"
            )
            rows = cursor.fetchall()
            conn.close()

            if not rows:
                print("No bookmaker scores recorded yet.")
                return

            header = (
                "Bookmaker | Matches | 1X2 Hit Rate % | "
                "Mean λ Error | Mean μ Error | Reliability Score | Status"
            )
            print(header)

            for row in rows:
                total = row["total_matches"]
                hit_rate = (
                    (row["correct_1x2"] / total * 100) if total > 0 else 0.0
                )
                mean_lam = (
                    (row["sum_lambda_error"] / total) if total > 0 else 0.0
                )
                mean_mu = (
                    (row["sum_mu_error"] / total) if total > 0 else 0.0
                )
                status = "active" if row["is_active"] else "excluded"
                print(
                    f"{row['bookmaker_key']} | {total} | {hit_rate:.2f} | "
                    f"{mean_lam:.4f} | {mean_mu:.4f} | "
                    f"{row['reliability_score']:.4f} | {status}"
                )
        except Exception as exc:
            logger.warning("Failed to generate bookmaker report: %s", exc)
            print("Error generating bookmaker report.")
