"""Unit tests for the BookmakerScorer module."""
from __future__ import annotations

import sqlite3

import pytest

from src.bookmaker_scorer import BookmakerScorer, RetroResult
from src.cache_store import CacheStore


@pytest.fixture()
def cache_store(tmp_path):
    """Create a CacheStore with a temporary database."""
    db_path = str(tmp_path / "test_scorer.db")
    return CacheStore(db_path=db_path)


@pytest.fixture()
def scorer(cache_store):
    """Create a BookmakerScorer instance."""
    return BookmakerScorer(cache_store=cache_store, reliability_threshold=0.30)


class TestUpdateScores:
    """Tests for BookmakerScorer.update_scores method."""

    def test_update_scores_single_result(self, scorer, cache_store):
        """Test updating scores with a single retro result."""
        results = [
            RetroResult(
                bookmaker_key="onexbet",
                correct_1x2=1,
                lambda_error=0.5,
                mu_error=0.3,
            )
        ]
        scorer.update_scores(results)

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bookmaker_scores WHERE bookmaker_key = ?",
            ("onexbet",),
        )
        row = cursor.fetchone()
        conn.close()

        assert row is not None
        assert row["total_matches"] == 1
        assert row["correct_1x2"] == 1
        assert row["sum_lambda_error"] == pytest.approx(0.5)
        assert row["sum_mu_error"] == pytest.approx(0.3)
        assert row["is_active"] == 1

    def test_update_scores_accumulates(self, scorer, cache_store):
        """Test that multiple calls accumulate statistics."""
        results_1 = [
            RetroResult(
                bookmaker_key="betfair",
                correct_1x2=1,
                lambda_error=0.2,
                mu_error=0.1,
            )
        ]
        results_2 = [
            RetroResult(
                bookmaker_key="betfair",
                correct_1x2=0,
                lambda_error=0.8,
                mu_error=0.5,
            )
        ]

        scorer.update_scores(results_1)
        scorer.update_scores(results_2)

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM bookmaker_scores WHERE bookmaker_key = ?",
            ("betfair",),
        )
        row = cursor.fetchone()
        conn.close()

        assert row["total_matches"] == 2
        assert row["correct_1x2"] == 1
        assert row["sum_lambda_error"] == pytest.approx(1.0)
        assert row["sum_mu_error"] == pytest.approx(0.6)

    def test_update_scores_multiple_bookmakers(self, scorer, cache_store):
        """Test updating scores for multiple bookmakers simultaneously."""
        results = [
            RetroResult(
                bookmaker_key="onexbet",
                correct_1x2=1,
                lambda_error=0.3,
                mu_error=0.2,
            ),
            RetroResult(
                bookmaker_key="unibet",
                correct_1x2=0,
                lambda_error=1.5,
                mu_error=1.0,
            ),
            RetroResult(
                bookmaker_key="betfair",
                correct_1x2=1,
                lambda_error=0.1,
                mu_error=0.1,
            ),
        ]
        scorer.update_scores(results)

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) as cnt FROM bookmaker_scores")
        count = cursor.fetchone()["cnt"]
        conn.close()

        assert count == 3

    def test_update_scores_empty_list(self, scorer, cache_store):
        """Test that empty results list is a no-op."""
        scorer.update_scores([])

        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM bookmaker_scores")
        count = cursor.fetchone()[0]
        conn.close()

        assert count == 0

    def test_reliability_score_formula(self, scorer, cache_store):
        """Test the reliability score formula computation.

        With a single bookmaker, its errors are the worst (norm=1.0).
        Score = 0.5 * hit_rate + 0.25 * (1 - 1.0) + 0.25 * (1 - 1.0)
             = 0.5 * hit_rate
        """
        results = [
            RetroResult(
                bookmaker_key="onexbet",
                correct_1x2=1,
                lambda_error=0.5,
                mu_error=0.3,
            )
        ]
        scorer.update_scores(results)

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT reliability_score FROM bookmaker_scores WHERE bookmaker_key = ?",
            ("onexbet",),
        )
        row = cursor.fetchone()
        conn.close()

        # Single bookmaker: max errors = its own errors, so norm = 1.0
        # score = 0.5 * (1/1) + 0.25 * (1 - 1.0) + 0.25 * (1 - 1.0) = 0.5
        assert row["reliability_score"] == pytest.approx(0.5)

    def test_reliability_score_with_multiple_bookmakers(self, scorer, cache_store):
        """Test normalization relative to worst-performing bookmaker."""
        results = [
            RetroResult(
                bookmaker_key="good_bm",
                correct_1x2=1,
                lambda_error=0.1,
                mu_error=0.1,
            ),
            RetroResult(
                bookmaker_key="bad_bm",
                correct_1x2=0,
                lambda_error=1.0,
                mu_error=1.0,
            ),
        ]
        scorer.update_scores(results)

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT reliability_score FROM bookmaker_scores "
            "WHERE bookmaker_key = ?",
            ("good_bm",),
        )
        good_row = cursor.fetchone()
        cursor.execute(
            "SELECT reliability_score FROM bookmaker_scores "
            "WHERE bookmaker_key = ?",
            ("bad_bm",),
        )
        bad_row = cursor.fetchone()
        conn.close()

        # good_bm: hit_rate=1.0, norm_lambda=0.1/1.0=0.1, norm_mu=0.1/1.0=0.1
        # score = 0.5*1.0 + 0.25*(1-0.1) + 0.25*(1-0.1) = 0.5 + 0.225 + 0.225 = 0.95
        assert good_row["reliability_score"] == pytest.approx(0.95)

        # bad_bm: hit_rate=0.0, norm_lambda=1.0/1.0=1.0, norm_mu=1.0/1.0=1.0
        # score = 0.5*0.0 + 0.25*(1-1.0) + 0.25*(1-1.0) = 0.0
        assert bad_row["reliability_score"] == pytest.approx(0.0)

    def test_bookmaker_flagged_when_below_threshold(self, scorer, cache_store):
        """Test that bookmaker is flagged inactive when below threshold."""
        # Create a bookmaker with 0% hit rate and worst errors
        results = [
            RetroResult(
                bookmaker_key="terrible_bm",
                correct_1x2=0,
                lambda_error=2.0,
                mu_error=2.0,
            ),
            RetroResult(
                bookmaker_key="decent_bm",
                correct_1x2=1,
                lambda_error=0.1,
                mu_error=0.1,
            ),
        ]
        scorer.update_scores(results)

        conn = sqlite3.connect(cache_store.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_active, reliability_score FROM bookmaker_scores "
            "WHERE bookmaker_key = ?",
            ("terrible_bm",),
        )
        row = cursor.fetchone()
        conn.close()

        # terrible_bm: hit_rate=0, norm_lambda=1.0, norm_mu=1.0 → score=0.0
        assert row["is_active"] == 0
        assert row["reliability_score"] < 0.30


class TestGetExcludedBookmakers:
    """Tests for BookmakerScorer.get_excluded_bookmakers method."""

    def test_no_excluded_bookmakers_initially(self, scorer):
        """Test that no bookmakers are excluded in a fresh database."""
        excluded = scorer.get_excluded_bookmakers()
        assert excluded == []

    def test_returns_inactive_bookmakers(self, scorer, cache_store):
        """Test that inactive bookmakers are returned as excluded."""
        # Directly insert an inactive bookmaker
        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bookmaker_scores "
            "(bookmaker_key, total_matches, correct_1x2, "
            "sum_lambda_error, sum_mu_error, reliability_score, "
            "is_active, last_updated) "
            "VALUES (?, 10, 1, 5.0, 5.0, 0.10, 0, '2024-01-01T00:00:00Z')",
            ("bad_adapter",),
        )
        conn.commit()
        conn.close()

        excluded = scorer.get_excluded_bookmakers()
        assert "bad_adapter" in excluded

    def test_adapter_ids_used_as_bookmaker_keys(self, scorer, cache_store):
        """Test that adapter IDs like 'onexbet' work as bookmaker keys."""
        # Simulate the full flow: update_scores → get_excluded
        results = [
            RetroResult(
                bookmaker_key="onexbet",
                correct_1x2=0,
                lambda_error=3.0,
                mu_error=3.0,
            ),
            RetroResult(
                bookmaker_key="betfair",
                correct_1x2=1,
                lambda_error=0.1,
                mu_error=0.1,
            ),
        ]
        scorer.update_scores(results)

        excluded = scorer.get_excluded_bookmakers()
        # onexbet should be excluded (score = 0.0)
        assert "onexbet" in excluded
        # betfair should not be excluded
        assert "betfair" not in excluded


class TestEnableBookmaker:
    """Tests for BookmakerScorer.enable_bookmaker method."""

    def test_enable_inactive_bookmaker(self, scorer, cache_store, capsys):
        """Test re-enabling a previously excluded bookmaker."""
        # Insert an inactive bookmaker
        conn = sqlite3.connect(cache_store.db_path)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO bookmaker_scores "
            "(bookmaker_key, total_matches, correct_1x2, "
            "sum_lambda_error, sum_mu_error, reliability_score, "
            "is_active, last_updated) "
            "VALUES (?, 5, 1, 3.0, 3.0, 0.20, 0, '2024-01-01T00:00:00Z')",
            ("onexbet",),
        )
        conn.commit()
        conn.close()

        scorer.enable_bookmaker("onexbet")

        captured = capsys.readouterr()
        assert "re-enabled" in captured.out

        # Verify it's no longer excluded
        excluded = scorer.get_excluded_bookmakers()
        assert "onexbet" not in excluded

    def test_enable_nonexistent_bookmaker(self, scorer, capsys):
        """Test enabling a bookmaker that doesn't exist."""
        scorer.enable_bookmaker("nonexistent")

        captured = capsys.readouterr()
        assert "not found" in captured.out


class TestPrintReport:
    """Tests for BookmakerScorer.print_report method."""

    def test_empty_report(self, scorer, capsys):
        """Test report output when no scores exist."""
        scorer.print_report()

        captured = capsys.readouterr()
        assert "No bookmaker scores recorded yet." in captured.out

    def test_report_with_data(self, scorer, cache_store, capsys):
        """Test report output with bookmaker data."""
        results = [
            RetroResult(
                bookmaker_key="onexbet",
                correct_1x2=1,
                lambda_error=0.3,
                mu_error=0.2,
            ),
            RetroResult(
                bookmaker_key="betfair",
                correct_1x2=1,
                lambda_error=0.1,
                mu_error=0.1,
            ),
        ]
        scorer.update_scores(results)
        scorer.print_report()

        captured = capsys.readouterr()
        assert "onexbet" in captured.out
        assert "betfair" in captured.out
        assert "Bookmaker | Matches" in captured.out


class TestIntegrationWithOddsExtractor:
    """Integration tests verifying excluded bookmakers flow to OddsExtractor."""

    def test_excluded_bookmakers_passed_to_extractor(self, scorer, cache_store):
        """Test that excluded bookmaker list can be passed to OddsExtractor."""
        # Create a scenario where a bookmaker gets excluded
        results = [
            RetroResult(
                bookmaker_key="the_odds_api",
                correct_1x2=0,
                lambda_error=5.0,
                mu_error=5.0,
            ),
            RetroResult(
                bookmaker_key="unibet",
                correct_1x2=1,
                lambda_error=0.1,
                mu_error=0.1,
            ),
        ]
        scorer.update_scores(results)

        excluded = scorer.get_excluded_bookmakers()

        # the_odds_api should be excluded (score = 0.0 < 0.30)
        assert "the_odds_api" in excluded

        # This list would be passed to OddsExtractor(excluded_bookmakers=excluded)
        # Verify the format is compatible (list of strings)
        assert all(isinstance(k, str) for k in excluded)
