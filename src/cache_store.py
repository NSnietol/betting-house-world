"""SQLite-backed cache store for raw API responses and prediction data."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone


class CacheStore:
    """Manages local SQLite caching of API responses and prediction/results data."""

    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1

    def __init__(self, db_path: str = "odds_cache.db"):
        """Initialize the cache store and create tables if they don't exist.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._create_tables()

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
                if "locked" in str(e).lower() or "database is locked" in str(e).lower():
                    last_error = e
                    if attempt < self.MAX_RETRIES - 1:
                        time.sleep(self.RETRY_BACKOFF_SECONDS)
                else:
                    raise
        raise last_error

    def _create_tables(self) -> None:
        """Create all required tables and indexes if they don't exist."""

        def _setup(conn: sqlite3.Connection):
            cursor = conn.cursor()

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sport_key TEXT NOT NULL,
                    market_type TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    bookmaker_keys TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    retrieved_at TEXT NOT NULL,
                    UNIQUE(sport_key, market_type, event_id, bookmaker_keys)
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_cache_lookup
                ON cache(sport_key, market_type, event_id, bookmaker_keys)
            """)

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
                    UNIQUE(sport_key, event_id, bookmaker_source)
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_predictions_date
                ON predictions(sport_key, prediction_date)
            """)

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

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_actual_results_date
                ON actual_results(sport_key, match_date)
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS bookmaker_scores (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bookmaker_key TEXT NOT NULL UNIQUE,
                    total_matches INTEGER NOT NULL DEFAULT 0,
                    correct_1x2 INTEGER NOT NULL DEFAULT 0,
                    sum_lambda_error REAL NOT NULL DEFAULT 0.0,
                    sum_mu_error REAL NOT NULL DEFAULT 0.0,
                    reliability_score REAL NOT NULL DEFAULT 1.0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_updated TEXT NOT NULL
                )
            """)

            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_bookmaker_scores_active
                ON bookmaker_scores(is_active)
            """)

        self._execute_with_retry(_setup)

    def get(
        self,
        sport_key: str,
        market_type: str,
        event_id: str,
        bookmaker_keys: tuple[str, ...],
    ) -> dict | None:
        """Return cached response if it exists.

        Looks up a cache entry by the composite key of sport_key, market_type,
        event_id, and a sorted JSON array of bookmaker keys.

        Args:
            sport_key: The sport/league key (e.g., "soccer_epl").
            market_type: The market type (e.g., "h2h", "totals").
            event_id: The unique event identifier.
            bookmaker_keys: Tuple of bookmaker keys to match.

        Returns:
            A dict with 'response_json' and 'retrieved_at' if a cache entry
            exists, or None if no matching entry is found.
        """
        sorted_keys = json.dumps(sorted(bookmaker_keys))

        def _query(conn: sqlite3.Connection):
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT response_json, retrieved_at
                FROM cache
                WHERE sport_key = ?
                  AND market_type = ?
                  AND event_id = ?
                  AND bookmaker_keys = ?
                """,
                (sport_key, market_type, event_id, sorted_keys),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return {
                "response_json": row["response_json"],
                "retrieved_at": row["retrieved_at"],
            }

        return self._execute_with_retry(_query)

    def put(
        self,
        sport_key: str,
        market_type: str,
        event_id: str,
        bookmaker_keys: tuple[str, ...],
        response_json: str,
        retrieved_at: datetime,
    ) -> None:
        """Store a raw JSON response with metadata in the cache.

        Uses INSERT OR REPLACE to upsert entries based on the unique constraint
        (sport_key, market_type, event_id, bookmaker_keys).

        Args:
            sport_key: The sport/league key (e.g., "soccer_epl").
            market_type: The market type (e.g., "h2h", "totals").
            event_id: The unique event identifier.
            bookmaker_keys: Tuple of bookmaker keys associated with the response.
            response_json: The raw JSON response string.
            retrieved_at: UTC datetime when the response was retrieved.
        """
        sorted_keys = json.dumps(sorted(bookmaker_keys))
        timestamp = retrieved_at.strftime("%Y-%m-%dT%H:%M:%SZ")

        def _insert(conn: sqlite3.Connection):
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO cache
                    (sport_key, market_type, event_id, bookmaker_keys, response_json, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (sport_key, market_type, event_id, sorted_keys, response_json, timestamp),
            )

        self._execute_with_retry(_insert)

    def is_expired(self, retrieved_at: datetime, ttl_hours: int) -> bool:
        """Check if a cached entry has exceeded its TTL threshold.

        Args:
            retrieved_at: UTC datetime when the entry was originally retrieved.
            ttl_hours: Time-to-live in hours (1-48).

        Returns:
            True if the entry is expired (current time - retrieved_at > ttl_hours),
            False otherwise.
        """
        now = datetime.now(timezone.utc)
        # Ensure retrieved_at is timezone-aware
        if retrieved_at.tzinfo is None:
            retrieved_at = retrieved_at.replace(tzinfo=timezone.utc)
        elapsed = now - retrieved_at
        ttl_seconds = ttl_hours * 3600
        return elapsed.total_seconds() > ttl_seconds
