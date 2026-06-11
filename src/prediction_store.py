"""Stores and retrieves Polla predictions in SQLite for feedback tracking."""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timezone


class PredictionStore:
    """Stores and retrieves Polla predictions in SQLite for feedback tracking."""

    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1

    def __init__(self, db_path: str = "odds_cache.db") -> None:
        """Initialize the prediction store and run migrations.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._migrate()

    def _execute_with_retry(self, operation):
        """Execute a database operation with retry logic for locked DB.

        Args:
            operation: A callable that accepts a sqlite3.Connection and performs DB work.

        Returns:
            The result of the operation callable.

        Raises:
            sqlite3.OperationalError: If the database remains locked after all retries.
        """
        last_error = None
        for attempt in range(self.MAX_RETRIES):
            try:
                conn = sqlite3.connect(self.db_path, timeout=5)
                conn.row_factory = sqlite3.Row
                try:
                    result = operation(conn)
                    conn.commit()
                    return result
                finally:
                    conn.close()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower():
                    last_error = e
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(self.RETRY_BACKOFF_SECONDS)
                else:
                    raise
        raise last_error

    def _migrate(self) -> None:
        """Run migrations to add expected_points column if missing."""

        def _do_migrate(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            # Ensure predictions table exists (CacheStore normally creates it)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS predictions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sport_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    predicted_home_goals INTEGER NOT NULL,
                    predicted_away_goals INTEGER NOT NULL,
                    lambda_home REAL NOT NULL,
                    mu_away REAL NOT NULL,
                    bookmaker_source TEXT NOT NULL,
                    prediction_date TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expected_points REAL DEFAULT 0,
                    UNIQUE(sport_key, event_id, bookmaker_source)
                )
            """)
            # Migration: add expected_points column to existing databases
            cursor.execute("PRAGMA table_info(predictions)")
            columns = {row[1] for row in cursor.fetchall()}
            if "expected_points" not in columns:
                cursor.execute(
                    "ALTER TABLE predictions ADD COLUMN expected_points REAL DEFAULT 0"
                )

            # Ensure actual_results table exists
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS actual_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sport_key TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    home_team TEXT NOT NULL,
                    away_team TEXT NOT NULL,
                    home_goals INTEGER NOT NULL,
                    away_goals INTEGER NOT NULL,
                    match_date TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    UNIQUE(sport_key, event_id)
                )
            """)

        self._execute_with_retry(_do_migrate)

    def save_prediction(
        self,
        sport: str,
        event_id: str,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        lambda_home: float,
        mu_away: float,
        prediction_date: str,
        expected_points: float,
    ) -> None:
        """Save a prediction to the database.

        Uses INSERT OR REPLACE to upsert based on the unique constraint
        (sport_key, event_id, bookmaker_source).

        Args:
            sport: Sport/league key (e.g., 'soccer_fifa_world_cup').
            event_id: Unique event identifier.
            home_team: Home team name.
            away_team: Away team name.
            home_goals: Predicted home goals.
            away_goals: Predicted away goals.
            lambda_home: Poisson lambda parameter for home goals.
            mu_away: Poisson mu parameter for away goals.
            prediction_date: Date of the match in YYYY-MM-DD format.
            expected_points: Expected Polla points from the model.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _insert(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO predictions
                    (sport_key, event_id, home_team, away_team,
                     predicted_home_goals, predicted_away_goals,
                     lambda_home, mu_away, bookmaker_source,
                     prediction_date, created_at, expected_points)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    sport, event_id, home_team, away_team,
                    home_goals, away_goals, lambda_home, mu_away,
                    "polla_optimizer", prediction_date, now, expected_points,
                ),
            )

        self._execute_with_retry(_insert)

    def save_batch(self, predictions: list[dict]) -> None:
        """Save multiple predictions at once.

        Each dict must contain keys: sport, event_id, home_team, away_team,
        home_goals, away_goals, lambda_home, mu_away, prediction_date,
        expected_points.

        Args:
            predictions: List of prediction dictionaries.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _batch_insert(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            for pred in predictions:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO predictions
                        (sport_key, event_id, home_team, away_team,
                         predicted_home_goals, predicted_away_goals,
                         lambda_home, mu_away, bookmaker_source,
                         prediction_date, created_at, expected_points)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pred["sport"], pred["event_id"],
                        pred["home_team"], pred["away_team"],
                        pred["home_goals"], pred["away_goals"],
                        pred["lambda_home"], pred["mu_away"],
                        "polla_optimizer", pred["prediction_date"],
                        now, pred["expected_points"],
                    ),
                )

        self._execute_with_retry(_batch_insert)

    def get_predictions(self, date: str) -> list[dict]:
        """Get all predictions for a date.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            List of prediction dictionaries.
        """

        def _query(conn: sqlite3.Connection) -> list[dict]:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport_key, event_id, home_team, away_team,
                       predicted_home_goals, predicted_away_goals,
                       lambda_home, mu_away, bookmaker_source,
                       prediction_date, created_at, expected_points
                FROM predictions
                WHERE prediction_date = ?
                ORDER BY created_at
                """,
                (date,),
            )
            return [dict(row) for row in cursor.fetchall()]

        return self._execute_with_retry(_query)

    def get_all_predictions(self) -> list[dict]:
        """Get all stored predictions.

        Returns:
            List of all prediction dictionaries.
        """

        def _query(conn: sqlite3.Connection) -> list[dict]:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport_key, event_id, home_team, away_team,
                       predicted_home_goals, predicted_away_goals,
                       lambda_home, mu_away, bookmaker_source,
                       prediction_date, created_at, expected_points
                FROM predictions
                ORDER BY prediction_date, created_at
                """
            )
            return [dict(row) for row in cursor.fetchall()]

        return self._execute_with_retry(_query)
