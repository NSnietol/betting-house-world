"""Collects actual World Cup match results from public sources."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class ResultsCollector:
    """Collects actual World Cup match results from public sources.

    Sources (in order of preference):
    1. Manual input via JSON file
    2. Public football-data API (future)
    3. Kambi API post-match results (future)
    """

    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1

    def __init__(self, db_path: str = "odds_cache.db") -> None:
        """Initialize the results collector.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path
        self._ensure_table()

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

    def _ensure_table(self) -> None:
        """Ensure the actual_results table exists."""

        def _create(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
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

        self._execute_with_retry(_create)

    def collect_results(self, date: str) -> list[dict]:
        """Fetch actual results for a given date and return them.

        Currently supports manual JSON import as the primary source.
        Future versions will add Kambi API and public football-data API.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            List of result dictionaries stored for that date.
        """
        # For now, return what's already stored for the date
        return self.get_results(date)

    def store_result(
        self,
        sport: str,
        event_id: str,
        home_team: str,
        away_team: str,
        home_goals: int,
        away_goals: int,
        match_date: str,
    ) -> None:
        """Store an actual result.

        Uses INSERT OR REPLACE to upsert based on the unique constraint
        (sport_key, event_id).

        Args:
            sport: Sport/league key (e.g., 'soccer_fifa_world_cup').
            event_id: Unique event identifier.
            home_team: Home team name.
            away_team: Away team name.
            home_goals: Actual home goals scored.
            away_goals: Actual away goals scored.
            match_date: Date of the match in YYYY-MM-DD format.
        """
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _insert(conn: sqlite3.Connection) -> None:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO actual_results
                    (sport_key, event_id, home_team, away_team,
                     home_goals, away_goals, match_date, retrieved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (sport, event_id, home_team, away_team,
                 home_goals, away_goals, match_date, now),
            )

        self._execute_with_retry(_insert)

    def get_results(self, date: str) -> list[dict]:
        """Get stored results for a date.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            List of result dictionaries.
        """

        def _query(conn: sqlite3.Connection) -> list[dict]:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT sport_key, event_id, home_team, away_team,
                       home_goals, away_goals, match_date, retrieved_at
                FROM actual_results
                WHERE match_date = ?
                ORDER BY retrieved_at
                """,
                (date,),
            )
            return [dict(row) for row in cursor.fetchall()]

        return self._execute_with_retry(_query)

    def import_from_json(self, filepath: str) -> int:
        """Import results from a JSON file.

        Expected format:
        [
            {
                "home": "Mexico",
                "away": "South Africa",
                "home_goals": 1,
                "away_goals": 0,
                "date": "2026-06-11"
            }
        ]

        The event_id is generated as a normalized key from team names and date.
        Sport key defaults to 'soccer_fifa_world_cup'.

        Args:
            filepath: Path to the JSON file.

        Returns:
            Number of results imported.
        """
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, list):
            raise ValueError("JSON file must contain a list of result objects.")

        count = 0
        for entry in data:
            home = entry["home"]
            away = entry["away"]
            home_goals = int(entry["home_goals"])
            away_goals = int(entry["away_goals"])
            match_date = entry["date"]
            sport = entry.get("sport", "soccer_fifa_world_cup")

            # Generate a deterministic event_id from teams and date
            event_id = self._generate_event_id(home, away, match_date)

            self.store_result(
                sport=sport,
                event_id=event_id,
                home_team=home,
                away_team=away,
                home_goals=home_goals,
                away_goals=away_goals,
                match_date=match_date,
            )
            count += 1
            logger.info("Imported result: %s %d-%d %s (%s)", home, home_goals, away_goals, away, match_date)

        return count

    @staticmethod
    def _generate_event_id(home: str, away: str, date: str) -> str:
        """Generate a deterministic event ID from team names and date.

        Args:
            home: Home team name.
            away: Away team name.
            date: Match date in YYYY-MM-DD format.

        Returns:
            A normalized string event ID.
        """
        home_norm = home.lower().replace(" ", "_")
        away_norm = away.lower().replace(" ", "_")
        return f"{home_norm}_vs_{away_norm}_{date}"
