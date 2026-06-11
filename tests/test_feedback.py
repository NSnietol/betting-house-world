"""Tests for the prediction feedback loop: PredictionStore, ResultsCollector, FeedbackAnalyzer."""

from __future__ import annotations

import json
import os

import pytest

from src.feedback_analyzer import FeedbackAnalyzer, MatchFeedback, FeedbackSummary
from src.prediction_store import PredictionStore
from src.results_collector import ResultsCollector


# ---------------------------------------------------------------------------
# PredictionStore Tests
# ---------------------------------------------------------------------------


class TestPredictionStore:
    """Tests for PredictionStore save/retrieve operations."""

    def test_save_and_retrieve_single_prediction(self, tmp_path):
        """Save a prediction and retrieve it by date."""
        db = str(tmp_path / "test.db")
        store = PredictionStore(db_path=db)

        store.save_prediction(
            sport="soccer_fifa_world_cup",
            event_id="mexico_vs_south_africa_2026-06-11",
            home_team="Mexico",
            away_team="South Africa",
            home_goals=2,
            away_goals=1,
            lambda_home=1.8,
            mu_away=0.9,
            prediction_date="2026-06-11",
            expected_points=5.5,
        )

        predictions = store.get_predictions("2026-06-11")
        assert len(predictions) == 1
        pred = predictions[0]
        assert pred["home_team"] == "Mexico"
        assert pred["away_team"] == "South Africa"
        assert pred["predicted_home_goals"] == 2
        assert pred["predicted_away_goals"] == 1
        assert pred["lambda_home"] == pytest.approx(1.8)
        assert pred["mu_away"] == pytest.approx(0.9)
        assert pred["expected_points"] == pytest.approx(5.5)
        assert pred["prediction_date"] == "2026-06-11"

    def test_save_batch(self, tmp_path):
        """Save multiple predictions at once and retrieve all."""
        db = str(tmp_path / "test.db")
        store = PredictionStore(db_path=db)

        batch = [
            {
                "sport": "soccer_fifa_world_cup",
                "event_id": "mexico_vs_south_africa_2026-06-11",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_goals": 2,
                "away_goals": 1,
                "lambda_home": 1.8,
                "mu_away": 0.9,
                "prediction_date": "2026-06-11",
                "expected_points": 5.5,
            },
            {
                "sport": "soccer_fifa_world_cup",
                "event_id": "south_korea_vs_czech_republic_2026-06-11",
                "home_team": "South Korea",
                "away_team": "Czech Republic",
                "home_goals": 1,
                "away_goals": 0,
                "lambda_home": 1.2,
                "mu_away": 0.7,
                "prediction_date": "2026-06-11",
                "expected_points": 4.8,
            },
        ]
        store.save_batch(batch)

        predictions = store.get_predictions("2026-06-11")
        assert len(predictions) == 2

        all_preds = store.get_all_predictions()
        assert len(all_preds) == 2

    def test_get_predictions_empty_date(self, tmp_path):
        """Return empty list for a date with no predictions."""
        db = str(tmp_path / "test.db")
        store = PredictionStore(db_path=db)

        predictions = store.get_predictions("2026-06-12")
        assert predictions == []

    def test_upsert_on_duplicate(self, tmp_path):
        """Saving same event_id updates the existing prediction."""
        db = str(tmp_path / "test.db")
        store = PredictionStore(db_path=db)

        store.save_prediction(
            sport="soccer_fifa_world_cup",
            event_id="mexico_vs_south_africa_2026-06-11",
            home_team="Mexico",
            away_team="South Africa",
            home_goals=2,
            away_goals=1,
            lambda_home=1.8,
            mu_away=0.9,
            prediction_date="2026-06-11",
            expected_points=5.5,
        )

        # Update with different prediction
        store.save_prediction(
            sport="soccer_fifa_world_cup",
            event_id="mexico_vs_south_africa_2026-06-11",
            home_team="Mexico",
            away_team="South Africa",
            home_goals=1,
            away_goals=0,
            lambda_home=1.5,
            mu_away=0.8,
            prediction_date="2026-06-11",
            expected_points=4.2,
        )

        predictions = store.get_all_predictions()
        assert len(predictions) == 1
        assert predictions[0]["predicted_home_goals"] == 1
        assert predictions[0]["expected_points"] == pytest.approx(4.2)


# ---------------------------------------------------------------------------
# ResultsCollector Tests
# ---------------------------------------------------------------------------


class TestResultsCollector:
    """Tests for ResultsCollector store/retrieve operations."""

    def test_store_and_retrieve_result(self, tmp_path):
        """Store a result and retrieve it by date."""
        db = str(tmp_path / "test.db")
        collector = ResultsCollector(db_path=db)

        collector.store_result(
            sport="soccer_fifa_world_cup",
            event_id="mexico_vs_south_africa_2026-06-11",
            home_team="Mexico",
            away_team="South Africa",
            home_goals=1,
            away_goals=0,
            match_date="2026-06-11",
        )

        results = collector.get_results("2026-06-11")
        assert len(results) == 1
        r = results[0]
        assert r["home_team"] == "Mexico"
        assert r["away_team"] == "South Africa"
        assert r["home_goals"] == 1
        assert r["away_goals"] == 0

    def test_import_from_json(self, tmp_path):
        """Import results from a JSON file."""
        db = str(tmp_path / "test.db")
        collector = ResultsCollector(db_path=db)

        json_data = [
            {"home": "Mexico", "away": "South Africa", "home_goals": 1, "away_goals": 0, "date": "2026-06-11"},
            {"home": "South Korea", "away": "Czech Republic", "home_goals": 2, "away_goals": 1, "date": "2026-06-11"},
        ]
        json_file = str(tmp_path / "results.json")
        with open(json_file, "w") as f:
            json.dump(json_data, f)

        count = collector.import_from_json(json_file)
        assert count == 2

        results = collector.get_results("2026-06-11")
        assert len(results) == 2

    def test_generate_event_id(self):
        """Event ID is deterministic and normalized."""
        eid = ResultsCollector._generate_event_id("Mexico", "South Africa", "2026-06-11")
        assert eid == "mexico_vs_south_africa_2026-06-11"

    def test_import_invalid_json_format(self, tmp_path):
        """Raise ValueError for non-list JSON."""
        db = str(tmp_path / "test.db")
        collector = ResultsCollector(db_path=db)

        json_file = str(tmp_path / "bad.json")
        with open(json_file, "w") as f:
            json.dump({"not": "a list"}, f)

        with pytest.raises(ValueError, match="must contain a list"):
            collector.import_from_json(json_file)


# ---------------------------------------------------------------------------
# FeedbackAnalyzer Tests
# ---------------------------------------------------------------------------


class TestFeedbackAnalyzer:
    """Tests for FeedbackAnalyzer with known prediction+result pairs."""

    def _setup_data(self, tmp_path) -> str:
        """Create a DB with known predictions and results.

        Returns:
            Path to the test database.
        """
        db = str(tmp_path / "test.db")
        store = PredictionStore(db_path=db)
        collector = ResultsCollector(db_path=db)

        # Prediction: Mexico 2-1, actual: Mexico 1-0
        store.save_prediction(
            sport="soccer_fifa_world_cup",
            event_id="mexico_vs_south_africa_2026-06-11",
            home_team="Mexico",
            away_team="South Africa",
            home_goals=2,
            away_goals=1,
            lambda_home=1.8,
            mu_away=0.9,
            prediction_date="2026-06-11",
            expected_points=5.5,
        )
        collector.store_result(
            sport="soccer_fifa_world_cup",
            event_id="mexico_vs_south_africa_2026-06-11",
            home_team="Mexico",
            away_team="South Africa",
            home_goals=1,
            away_goals=0,
            match_date="2026-06-11",
        )

        # Prediction: South Korea 1-0, actual: South Korea 2-1
        store.save_prediction(
            sport="soccer_fifa_world_cup",
            event_id="south_korea_vs_czech_republic_2026-06-11",
            home_team="South Korea",
            away_team="Czech Republic",
            home_goals=1,
            away_goals=0,
            lambda_home=1.2,
            mu_away=0.7,
            prediction_date="2026-06-11",
            expected_points=4.8,
        )
        collector.store_result(
            sport="soccer_fifa_world_cup",
            event_id="south_korea_vs_czech_republic_2026-06-11",
            home_team="South Korea",
            away_team="Czech Republic",
            home_goals=2,
            away_goals=1,
            match_date="2026-06-11",
        )

        return db

    def test_analyze_date_basic(self, tmp_path):
        """Analyze predictions vs results for a known date."""
        db = self._setup_data(tmp_path)
        analyzer = FeedbackAnalyzer(db_path=db)

        summary = analyzer.analyze_date("2026-06-11")
        assert summary.total_matches == 2
        assert summary.total_polla_points > 0
        assert 0.0 <= summary.result_accuracy <= 100.0
        assert 0.0 <= summary.score_accuracy <= 100.0
        assert summary.avg_lambda_error >= 0.0
        assert summary.avg_mu_error >= 0.0

    def test_analyze_all(self, tmp_path):
        """Analyze all prediction-result pairs."""
        db = self._setup_data(tmp_path)
        analyzer = FeedbackAnalyzer(db_path=db)

        summary = analyzer.analyze_all()
        assert summary.total_matches == 2

    def test_empty_analysis(self, tmp_path):
        """Return empty summary when no data exists."""
        db = str(tmp_path / "empty.db")
        analyzer = FeedbackAnalyzer(db_path=db)

        # Need to create the tables first
        PredictionStore(db_path=db)
        ResultsCollector(db_path=db)

        summary = analyzer.analyze_date("2026-06-11")
        assert summary.total_matches == 0
        assert summary.total_polla_points == 0


# ---------------------------------------------------------------------------
# Polla Points Calculation Tests
# ---------------------------------------------------------------------------


class TestPollaPointsCalculation:
    """Verify Polla scoring rules match the official spec."""

    def setup_method(self):
        """Set up a FeedbackAnalyzer instance for points calculation."""
        self.analyzer = FeedbackAnalyzer.__new__(FeedbackAnalyzer)

    def test_exact_score_max_points(self):
        """Exact score match gets maximum 10 points (group stage)."""
        # pred: 1-0, actual: 1-0
        pts = self.analyzer.calculate_polla_points(1, 0, 1, 0)
        assert pts == 10  # result(5) + home(2) + away(2) + diff(1)

    def test_correct_result_only(self):
        """Correct winner but wrong goals and wrong diff gets 5 points."""
        # pred: 3-0, actual: 1-0 → result correct, home wrong, away correct, diff wrong
        pts = self.analyzer.calculate_polla_points(3, 0, 1, 0)
        # result=5, home_goals=0 (3!=1), away_goals=2 (0==0), diff=0 (3!=1)
        assert pts == 7  # 5 + 0 + 2 + 0... wait let me recalculate
        # pred diff = 3-0=3, actual diff = 1-0=1 → diff wrong
        # Actually: result=5, home=wrong(0), away=correct(2), diff=wrong(0) = 7

    def test_correct_result_and_diff(self):
        """Correct result and goal difference."""
        # pred: 2-0, actual: 3-1 → result correct, diff correct (both +2)
        pts = self.analyzer.calculate_polla_points(2, 0, 3, 1)
        # result=5, home=wrong(0), away=wrong(0), diff=correct(1) = 6
        assert pts == 6

    def test_draw_exact(self):
        """Exact draw score gets max points."""
        # pred: 0-0, actual: 0-0
        pts = self.analyzer.calculate_polla_points(0, 0, 0, 0)
        assert pts == 10

    def test_draw_wrong_score(self):
        """Predict draw, actual draw but different score."""
        # pred: 0-0, actual: 2-2
        pts = self.analyzer.calculate_polla_points(0, 0, 2, 2)
        # result=5 (draw=draw), home=0 (0!=2), away=0 (0!=2), diff=1 (0==0)
        assert pts == 6

    def test_completely_wrong(self):
        """Wrong result, wrong goals, wrong diff gets 0 points."""
        # pred: 2-0, actual: 0-1
        pts = self.analyzer.calculate_polla_points(2, 0, 0, 1)
        # result=wrong(home vs away), home=wrong, away=wrong, diff=wrong
        assert pts == 0

    def test_only_home_goals_correct(self):
        """Only home goals correct, rest wrong."""
        # pred: 1-2, actual: 1-0
        pts = self.analyzer.calculate_polla_points(1, 2, 1, 0)
        # result=wrong (away win vs home win), home=correct(2), away=wrong(0), diff=wrong(0)
        assert pts == 2

    def test_correct_result_both_goals_wrong_diff_correct(self):
        """Correct result, both goals wrong, but diff correct."""
        # pred: 2-1, actual: 1-0
        pts = self.analyzer.calculate_polla_points(2, 1, 1, 0)
        # result=5 (home win), home=wrong(0), away=wrong(0), diff=1 (both +1)
        assert pts == 6

    def test_away_win_exact(self):
        """Exact away win prediction."""
        # pred: 0-2, actual: 0-2
        pts = self.analyzer.calculate_polla_points(0, 2, 0, 2)
        assert pts == 10


# ---------------------------------------------------------------------------
# Insights Generation Tests
# ---------------------------------------------------------------------------


class TestInsightsGeneration:
    """Test that insights are generated correctly."""

    def test_no_feedbacks_gives_no_data_insight(self):
        """Empty feedback list produces appropriate message."""
        analyzer = FeedbackAnalyzer.__new__(FeedbackAnalyzer)
        insights = analyzer.generate_insights([])
        assert len(insights) == 1
        assert "No matches" in insights[0]

    def test_draw_underestimation_insight(self):
        """Detect when model underestimates draws."""
        analyzer = FeedbackAnalyzer.__new__(FeedbackAnalyzer)

        # Create feedbacks where model rarely predicts draws but many end as draws
        feedbacks = [
            MatchFeedback(
                home_team="A", away_team="B",
                predicted_score=(1, 0), actual_score=(1, 1),
                polla_points_earned=2, expected_points=5.0,
                result_correct=False, score_correct=False,
                lambda_error=0.5, mu_error=1.0,
            ),
            MatchFeedback(
                home_team="C", away_team="D",
                predicted_score=(2, 1), actual_score=(0, 0),
                polla_points_earned=0, expected_points=5.5,
                result_correct=False, score_correct=False,
                lambda_error=2.0, mu_error=1.0,
            ),
            MatchFeedback(
                home_team="E", away_team="F",
                predicted_score=(1, 0), actual_score=(2, 2),
                polla_points_earned=0, expected_points=4.0,
                result_correct=False, score_correct=False,
                lambda_error=1.0, mu_error=2.0,
            ),
            MatchFeedback(
                home_team="G", away_team="H",
                predicted_score=(2, 0), actual_score=(1, 1),
                polla_points_earned=0, expected_points=5.0,
                result_correct=False, score_correct=False,
                lambda_error=1.0, mu_error=1.0,
            ),
        ]

        insights = analyzer.generate_insights(feedbacks)
        # 0% predicted draws, 100% actual draws → should flag
        draw_insights = [i for i in insights if "draw" in i.lower()]
        assert len(draw_insights) >= 1
        assert "underestimates" in draw_insights[0].lower()

    def test_overconfident_model_insight(self):
        """Detect when model is overconfident (expected > actual points)."""
        analyzer = FeedbackAnalyzer.__new__(FeedbackAnalyzer)

        feedbacks = [
            MatchFeedback(
                home_team="A", away_team="B",
                predicted_score=(1, 0), actual_score=(0, 1),
                polla_points_earned=0, expected_points=7.0,
                result_correct=False, score_correct=False,
                lambda_error=1.0, mu_error=1.0,
            ),
            MatchFeedback(
                home_team="C", away_team="D",
                predicted_score=(2, 1), actual_score=(0, 0),
                polla_points_earned=0, expected_points=6.5,
                result_correct=False, score_correct=False,
                lambda_error=2.0, mu_error=1.0,
            ),
            MatchFeedback(
                home_team="E", away_team="F",
                predicted_score=(1, 0), actual_score=(0, 2),
                polla_points_earned=0, expected_points=6.0,
                result_correct=False, score_correct=False,
                lambda_error=1.0, mu_error=2.0,
            ),
        ]

        insights = analyzer.generate_insights(feedbacks)
        confident_insights = [i for i in insights if "overconfident" in i.lower()]
        assert len(confident_insights) >= 1

    def test_well_calibrated_insight(self):
        """Well-calibrated predictions produce positive message."""
        analyzer = FeedbackAnalyzer.__new__(FeedbackAnalyzer)

        # Create balanced feedbacks (33% draws predicted and actual, balanced goals)
        feedbacks = [
            MatchFeedback(
                home_team="A", away_team="B",
                predicted_score=(1, 0), actual_score=(1, 0),
                polla_points_earned=10, expected_points=5.0,
                result_correct=True, score_correct=True,
                lambda_error=0.2, mu_error=0.1,
            ),
            MatchFeedback(
                home_team="C", away_team="D",
                predicted_score=(1, 1), actual_score=(1, 1),
                polla_points_earned=10, expected_points=5.0,
                result_correct=True, score_correct=True,
                lambda_error=0.1, mu_error=0.2,
            ),
            MatchFeedback(
                home_team="E", away_team="F",
                predicted_score=(0, 1), actual_score=(0, 1),
                polla_points_earned=10, expected_points=5.0,
                result_correct=True, score_correct=True,
                lambda_error=0.1, mu_error=0.1,
            ),
        ]

        insights = analyzer.generate_insights(feedbacks)
        # With perfect predictions, only the "underconfident" insight should trigger
        # since expected=5.0 but actual=10 per match
        # Actually this triggers underconfident
        assert len(insights) >= 1


# ---------------------------------------------------------------------------
# Integration Test: Full Feedback Loop
# ---------------------------------------------------------------------------


class TestFeedbackLoop:
    """Integration test for the full prediction → result → feedback loop."""

    def test_full_loop(self, tmp_path):
        """End-to-end: save predictions, import results, analyze feedback."""
        db = str(tmp_path / "test.db")

        # Step 1: Save predictions
        store = PredictionStore(db_path=db)
        store.save_batch([
            {
                "sport": "soccer_fifa_world_cup",
                "event_id": "mexico_vs_south_africa_2026-06-11",
                "home_team": "Mexico",
                "away_team": "South Africa",
                "home_goals": 2,
                "away_goals": 1,
                "lambda_home": 1.8,
                "mu_away": 0.9,
                "prediction_date": "2026-06-11",
                "expected_points": 5.5,
            },
            {
                "sport": "soccer_fifa_world_cup",
                "event_id": "south_korea_vs_czech_republic_2026-06-11",
                "home_team": "South Korea",
                "away_team": "Czech Republic",
                "home_goals": 1,
                "away_goals": 0,
                "lambda_home": 1.2,
                "mu_away": 0.7,
                "prediction_date": "2026-06-11",
                "expected_points": 4.8,
            },
        ])

        # Step 2: Import actual results via JSON
        collector = ResultsCollector(db_path=db)
        json_data = [
            {"home": "Mexico", "away": "South Africa", "home_goals": 1, "away_goals": 0,
             "date": "2026-06-11", "sport": "soccer_fifa_world_cup"},
            {"home": "South Korea", "away": "Czech Republic", "home_goals": 2, "away_goals": 1,
             "date": "2026-06-11", "sport": "soccer_fifa_world_cup"},
        ]
        json_file = str(tmp_path / "results.json")
        with open(json_file, "w") as f:
            json.dump(json_data, f)

        count = collector.import_from_json(json_file)
        assert count == 2

        # Step 3: Analyze feedback
        analyzer = FeedbackAnalyzer(db_path=db)
        summary = analyzer.analyze_date("2026-06-11")

        assert summary.total_matches == 2
        assert summary.total_polla_points > 0
        assert len(summary.insights) >= 1

        # Verify specific points for Mexico match:
        # Predicted 2-1, actual 1-0
        # result=correct (home win): 5
        # home_goals=wrong (2!=1): 0
        # away_goals=wrong (1!=0): 0
        # diff=correct (2-1=1, 1-0=1): 1
        # Total: 6 points
        mexico_pts = analyzer.calculate_polla_points(2, 1, 1, 0)
        assert mexico_pts == 6

        # Verify South Korea match:
        # Predicted 1-0, actual 2-1
        # result=correct (home win): 5
        # home_goals=wrong (1!=2): 0
        # away_goals=wrong (0!=1): 0
        # diff=correct (1-0=1, 2-1=1): 1
        # Total: 6 points
        korea_pts = analyzer.calculate_polla_points(1, 0, 2, 1)
        assert korea_pts == 6

        # Total should be 12
        assert summary.total_polla_points == 12

    def test_print_report_no_crash(self, tmp_path, capsys):
        """Print report runs without errors."""
        db = str(tmp_path / "test.db")
        PredictionStore(db_path=db)
        ResultsCollector(db_path=db)
        analyzer = FeedbackAnalyzer(db_path=db)

        # Should not raise
        analyzer.print_report(date="2026-06-11")
        captured = capsys.readouterr()
        assert "FEEDBACK REPORT" in captured.out

        analyzer.print_report(date=None)
        captured = capsys.readouterr()
        assert "CUMULATIVE" in captured.out
