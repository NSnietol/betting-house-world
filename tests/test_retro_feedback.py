"""Tests for the retro-feedback module."""
from __future__ import annotations

import sqlite3

import pytest

from src.cache_store import CacheStore
from src.retro_feedback import RetroFeedback, _determine_1x2_outcome


class TestDetermine1x2Outcome:
    """Tests for the _determine_1x2_outcome helper."""

    def test_home_win(self) -> None:
        assert _determine_1x2_outcome(2, 1) == "1"

    def test_draw(self) -> None:
        assert _determine_1x2_outcome(1, 1) == "X"

    def test_away_win(self) -> None:
        assert _determine_1x2_outcome(0, 2) == "2"

    def test_home_win_large(self) -> None:
        assert _determine_1x2_outcome(5, 0) == "1"

    def test_zero_zero_draw(self) -> None:
        assert _determine_1x2_outcome(0, 0) == "X"


class TestRetroFeedback:
    """Tests for the RetroFeedback class."""

    @pytest.fixture
    def cache_store(self, tmp_path) -> CacheStore:
        """Create a CacheStore with a temp database."""
        db_path = str(tmp_path / "test_retro.db")
        return CacheStore(db_path=db_path)

    @pytest.fixture
    def retro(self, cache_store: CacheStore) -> RetroFeedback:
        """Create a RetroFeedback instance."""
        return RetroFeedback(cache_store=cache_store, sport="soccer_epl")

    def _insert_prediction(
        self,
        cache_store: CacheStore,
        event_id: str = "evt1",
        home_team: str = "Team A",
        away_team: str = "Team B",
        predicted_home: int = 2,
        predicted_away: int = 1,
        lambda_home: float = 1.8,
        mu_away: float = 1.2,
        bookmaker_source: str = "pinnacle",
        prediction_date: str = "2024-06-01",
    ) -> None:
        """Helper to insert a prediction row."""
        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO predictions
                (sport_key, event_id, home_team, away_team,
                 predicted_home_goals, predicted_away_goals,
                 lambda_home, mu_away, bookmaker_source,
                 prediction_date, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "soccer_epl", event_id, home_team, away_team,
                predicted_home, predicted_away, lambda_home, mu_away,
                bookmaker_source, prediction_date,
                "2024-06-01T10:00:00Z",
            ),
        )
        conn.commit()
        conn.close()

    def _insert_actual_result(
        self,
        cache_store: CacheStore,
        event_id: str = "evt1",
        home_team: str = "Team A",
        away_team: str = "Team B",
        home_goals: int = 2,
        away_goals: int = 0,
        match_date: str = "2024-06-01",
    ) -> None:
        """Helper to insert an actual result row."""
        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO actual_results
                (sport_key, event_id, home_team, away_team,
                 home_goals, away_goals, match_date, retrieved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "soccer_epl", event_id, home_team, away_team,
                home_goals, away_goals, match_date,
                "2024-06-02T10:00:00Z",
            ),
        )
        conn.commit()
        conn.close()

    def test_no_predictions_returns_empty(
        self, retro: RetroFeedback, capsys
    ) -> None:
        """When no predictions exist for the date, return empty list."""
        result = retro.run("2024-06-01")
        assert result == []
        captured = capsys.readouterr()
        assert "No predictions found" in captured.out

    def test_no_actual_results_returns_empty(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """When predictions exist but no actual results, return empty list."""
        self._insert_prediction(cache_store)
        result = retro.run("2024-06-01")
        assert result == []
        captured = capsys.readouterr()
        assert "No actual results available" in captured.out

    def test_correct_1x2_prediction(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test a correct 1X2 prediction (home win predicted and actual)."""
        self._insert_prediction(
            cache_store, predicted_home=2, predicted_away=1
        )
        self._insert_actual_result(
            cache_store, home_goals=3, away_goals=1
        )

        result = retro.run("2024-06-01")
        assert len(result) == 1
        assert result[0].x1x2_correct is True
        assert result[0].score_correct is False

    def test_correct_exact_score(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test a correct exact score prediction."""
        self._insert_prediction(
            cache_store, predicted_home=2, predicted_away=1
        )
        self._insert_actual_result(
            cache_store, home_goals=2, away_goals=1
        )

        result = retro.run("2024-06-01")
        assert len(result) == 1
        assert result[0].x1x2_correct is True
        assert result[0].score_correct is True

    def test_incorrect_1x2_prediction(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test an incorrect 1X2 prediction (predicted home, actual away)."""
        self._insert_prediction(
            cache_store, predicted_home=2, predicted_away=1
        )
        self._insert_actual_result(
            cache_store, home_goals=0, away_goals=2
        )

        result = retro.run("2024-06-01")
        assert len(result) == 1
        assert result[0].x1x2_correct is False
        assert result[0].score_correct is False

    def test_lambda_mu_error_computation(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test that lambda and mu errors are computed correctly."""
        self._insert_prediction(
            cache_store, lambda_home=1.8, mu_away=1.2
        )
        self._insert_actual_result(
            cache_store, home_goals=3, away_goals=0
        )

        result = retro.run("2024-06-01")
        assert len(result) == 1
        assert result[0].lambda_error == pytest.approx(abs(1.8 - 3), abs=0.001)
        assert result[0].mu_error == pytest.approx(abs(1.2 - 0), abs=0.001)

    def test_multiple_predictions_different_bookmakers(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test multiple predictions from different bookmakers for same date."""
        self._insert_prediction(
            cache_store,
            event_id="evt1",
            home_team="Team A",
            away_team="Team B",
            bookmaker_source="pinnacle",
        )
        self._insert_prediction(
            cache_store,
            event_id="evt2",
            home_team="Team C",
            away_team="Team D",
            predicted_home=1,
            predicted_away=1,
            lambda_home=1.3,
            mu_away=1.3,
            bookmaker_source="betfair",
        )
        self._insert_actual_result(
            cache_store,
            event_id="evt1",
            home_team="Team A",
            away_team="Team B",
            home_goals=2,
            away_goals=1,
        )
        self._insert_actual_result(
            cache_store,
            event_id="evt2",
            home_team="Team C",
            away_team="Team D",
            home_goals=1,
            away_goals=1,
        )

        result = retro.run("2024-06-01")
        assert len(result) == 2
        sources = {c.bookmaker_source for c in result}
        assert "pinnacle" in sources
        assert "betfair" in sources

    def test_aggregate_metrics_in_output(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test that aggregate metrics are printed."""
        self._insert_prediction(cache_store)
        self._insert_actual_result(cache_store, home_goals=2, away_goals=1)

        retro.run("2024-06-01")
        captured = capsys.readouterr()

        assert "1X2 Hit Rate:" in captured.out
        assert "Score Hit Rate:" in captured.out
        assert "Mean λ Error:" in captured.out
        assert "Mean μ Error:" in captured.out

    def test_table_header_in_output(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test that the table header is printed correctly."""
        self._insert_prediction(cache_store)
        self._insert_actual_result(cache_store, home_goals=2, away_goals=0)

        retro.run("2024-06-01")
        captured = capsys.readouterr()

        assert "Match | Predicted Score | Actual Score" in captured.out
        assert "Bookmaker Source" in captured.out

    def test_bookmaker_scores_updated(
        self, retro: RetroFeedback, cache_store: CacheStore
    ) -> None:
        """Test that bookmaker_scores table is updated after retro run."""
        self._insert_prediction(cache_store, bookmaker_source="pinnacle")
        self._insert_actual_result(cache_store, home_goals=2, away_goals=1)

        retro.run("2024-06-01")

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bookmaker_scores WHERE bookmaker_key = ?",
            ("pinnacle",),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["total_matches"] == 1
        assert row["correct_1x2"] == 1

    def test_draw_prediction_correct(
        self, retro: RetroFeedback, cache_store: CacheStore
    ) -> None:
        """Test draw prediction is correctly identified."""
        self._insert_prediction(
            cache_store, predicted_home=1, predicted_away=1
        )
        self._insert_actual_result(
            cache_store, home_goals=2, away_goals=2
        )

        result = retro.run("2024-06-01")
        assert len(result) == 1
        assert result[0].x1x2_correct is True
        assert result[0].score_correct is False

    def test_output_uses_numeric_values(
        self, retro: RetroFeedback, cache_store: CacheStore, capsys
    ) -> None:
        """Test that output uses 1/0 for correct/incorrect (Req 15.6)."""
        self._insert_prediction(cache_store)
        self._insert_actual_result(cache_store, home_goals=2, away_goals=1)

        retro.run("2024-06-01")
        captured = capsys.readouterr()
        lines = captured.out.strip().split("\n")

        # The data row (second line after header)
        data_line = lines[1]
        parts = [p.strip() for p in data_line.split("|")]
        # 1X2 Correct should be "1" or "0"
        assert parts[3] in ("1", "0")
        # Score Correct should be "1" or "0"
        assert parts[4] in ("1", "0")
