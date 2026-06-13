"""Compares predictions vs actual results and generates actionable insights."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field


@dataclass
class MatchFeedback:
    """Feedback for a single match prediction vs actual result.

    Attributes:
        home_team: Home team name.
        away_team: Away team name.
        predicted_score: (home_goals, away_goals) predicted.
        actual_score: (home_goals, away_goals) actual.
        polla_points_earned: Actual points from the Polla scoring rules.
        expected_points: What the model predicted as expected points.
        result_correct: Whether Win/Draw/Loss was correct.
        score_correct: Whether exact score was correct.
        lambda_error: |predicted_lambda - actual_home_goals|.
        mu_error: |predicted_mu - actual_away_goals|.
    """

    home_team: str
    away_team: str
    predicted_score: tuple[int, int]
    actual_score: tuple[int, int]
    polla_points_earned: int
    expected_points: float
    result_correct: bool
    score_correct: bool
    lambda_error: float
    mu_error: float


@dataclass
class FeedbackSummary:
    """Aggregate summary of prediction feedback.

    Attributes:
        total_matches: Number of matches analyzed.
        total_polla_points: Total Polla points earned.
        avg_expected_points: Average expected points across predictions.
        result_accuracy: Percentage of matches where W/D/L was correct.
        score_accuracy: Percentage of exact scores correct.
        avg_lambda_error: Average |lambda - actual_home_goals|.
        avg_mu_error: Average |mu - actual_away_goals|.
        insights: Human-readable insights list.
    """

    total_matches: int
    total_polla_points: int
    avg_expected_points: float
    result_accuracy: float
    score_accuracy: float
    avg_lambda_error: float
    avg_mu_error: float
    insights: list[str] = field(default_factory=list)


class FeedbackAnalyzer:
    """Compares predictions vs actual results and generates insights.

    Uses the Polla Mundialista scoring rules (group stage) to calculate
    actual points earned and compare against model expectations.
    """

    MAX_RETRIES = 3
    RETRY_BACKOFF_SECONDS = 1

    # Group stage Polla scoring
    RESULT_POINTS = 5
    GOALS_POINTS = 2
    DIFF_POINTS = 1

    def __init__(self, db_path: str = "odds_cache.db") -> None:
        """Initialize the feedback analyzer.

        Args:
            db_path: Path to the SQLite database file.
        """
        self.db_path = db_path

    def _execute_with_retry(self, operation):
        """Execute a database operation with retry logic for locked DB.

        Args:
            operation: A callable that accepts a sqlite3.Connection and performs DB work.

        Returns:
            The result of the operation callable.
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

    def analyze_date(self, date: str) -> FeedbackSummary:
        """Compare predictions vs results for a specific date.

        Matches predictions to actual results by event_id and computes
        per-match and aggregate feedback.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            FeedbackSummary with aggregate metrics and insights.
        """
        feedbacks = self._get_feedbacks_for_date(date)
        return self._build_summary(feedbacks)

    def analyze_all(self) -> FeedbackSummary:
        """Analyze all available prediction-result pairs.

        Returns:
            FeedbackSummary with aggregate metrics and insights.
        """
        feedbacks = self._get_all_feedbacks()
        return self._build_summary(feedbacks)

    def generate_insights(self, feedbacks: list[MatchFeedback]) -> list[str]:
        """Generate actionable insights from the feedback data.

        Analyzes patterns in prediction errors to produce human-readable
        recommendations for model improvement.

        Args:
            feedbacks: List of MatchFeedback objects to analyze.

        Returns:
            List of insight strings.
        """
        if not feedbacks:
            return ["No matches available for analysis."]

        insights: list[str] = []
        total = len(feedbacks)

        # Insight 1: Draw prediction accuracy
        predicted_draws = sum(
            1 for f in feedbacks if f.predicted_score[0] == f.predicted_score[1]
        )
        actual_draws = sum(
            1 for f in feedbacks if f.actual_score[0] == f.actual_score[1]
        )
        if total >= 3:
            pred_draw_pct = predicted_draws / total * 100
            actual_draw_pct = actual_draws / total * 100
            if actual_draw_pct > pred_draw_pct + 10:
                insights.append(
                    f"Model underestimates draws: predicted draw in "
                    f"{pred_draw_pct:.0f}% of matches but "
                    f"{actual_draw_pct:.0f}% ended as draws"
                )
            elif pred_draw_pct > actual_draw_pct + 10:
                insights.append(
                    f"Model overestimates draws: predicted draw in "
                    f"{pred_draw_pct:.0f}% of matches but only "
                    f"{actual_draw_pct:.0f}% ended as draws"
                )

        # Insight 2: Total goals calibration
        predicted_total_goals = [
            f.predicted_score[0] + f.predicted_score[1] for f in feedbacks
        ]
        actual_total_goals = [
            f.actual_score[0] + f.actual_score[1] for f in feedbacks
        ]
        avg_predicted_total = sum(predicted_total_goals) / total
        avg_actual_total = sum(actual_total_goals) / total

        if avg_predicted_total > avg_actual_total + 0.3:
            insights.append(
                f"Over-prediction of goals: predicted avg "
                f"{avg_predicted_total:.1f} total goals, "
                f"actual avg {avg_actual_total:.1f}"
            )
        elif avg_actual_total > avg_predicted_total + 0.3:
            insights.append(
                f"Under-prediction of goals: predicted avg "
                f"{avg_predicted_total:.1f} total goals, "
                f"actual avg {avg_actual_total:.1f}"
            )

        # Insight 3: Home advantage calibration
        home_wins_predicted = sum(
            1 for f in feedbacks if f.predicted_score[0] > f.predicted_score[1]
        )
        home_wins_actual = sum(
            1 for f in feedbacks if f.actual_score[0] > f.actual_score[1]
        )
        if total >= 3:
            pred_home_pct = home_wins_predicted / total * 100
            actual_home_pct = home_wins_actual / total * 100
            if pred_home_pct > actual_home_pct + 15:
                insights.append(
                    f"Home advantage overestimated: predicted home win in "
                    f"{pred_home_pct:.0f}% but actual was {actual_home_pct:.0f}%"
                )

        # Insight 4: Lambda/mu error patterns
        avg_lam_err = sum(f.lambda_error for f in feedbacks) / total
        avg_mu_err = sum(f.mu_error for f in feedbacks) / total
        if avg_lam_err > avg_mu_err + 0.3:
            insights.append(
                f"Home goals harder to predict: avg λ error = "
                f"{avg_lam_err:.2f} vs avg μ error = {avg_mu_err:.2f}"
            )
        elif avg_mu_err > avg_lam_err + 0.3:
            insights.append(
                f"Away goals harder to predict: avg μ error = "
                f"{avg_mu_err:.2f} vs avg λ error = {avg_lam_err:.2f}"
            )

        # Insight 5: Expected points calibration
        avg_expected = sum(f.expected_points for f in feedbacks) / total
        avg_actual_pts = sum(f.polla_points_earned for f in feedbacks) / total
        if avg_expected > avg_actual_pts + 1.0:
            insights.append(
                f"Model overconfident: expected avg {avg_expected:.1f} pts "
                f"but earned avg {avg_actual_pts:.1f} pts per match"
            )
        elif avg_actual_pts > avg_expected + 1.0:
            insights.append(
                f"Model underconfident: expected avg {avg_expected:.1f} pts "
                f"but earned avg {avg_actual_pts:.1f} pts per match"
            )

        if not insights:
            insights.append("Model predictions are well-calibrated for this dataset.")

        return insights

    def print_report(self, date: str | None = None) -> None:
        """Print a formatted feedback report to stdout.

        Args:
            date: If provided, analyze only that date. Otherwise analyze all.
        """
        if date:
            feedbacks = self._get_feedbacks_for_date(date)
            header = f"FEEDBACK REPORT — {date}"
        else:
            feedbacks = self._get_all_feedbacks()
            header = "FEEDBACK REPORT — CUMULATIVE"

        summary = self._build_summary(feedbacks)

        print(f"\n{'='*60}")
        print(f"  {header}")
        print(f"{'='*60}")

        if summary.total_matches == 0:
            print("  No prediction-result pairs available.")
            print(f"{'='*60}\n")
            return

        # Per-match detail table
        print(f"\n  {'Match':<30} {'Pred':>6} {'Actual':>7} {'Pts':>4} {'Result':>7}")
        print(f"  {'-'*30} {'-'*6} {'-'*7} {'-'*4} {'-'*7}")
        for f in feedbacks:
            match_name = f"{f.home_team} vs {f.away_team}"
            if len(match_name) > 30:
                match_name = match_name[:27] + "..."
            pred_str = f"{f.predicted_score[0]}-{f.predicted_score[1]}"
            actual_str = f"{f.actual_score[0]}-{f.actual_score[1]}"
            result_str = "✓" if f.result_correct else "✗"
            print(
                f"  {match_name:<30} {pred_str:>6} {actual_str:>7} "
                f"{f.polla_points_earned:>4} {result_str:>7}"
            )
        print()

        print(f"  Matches analyzed:     {summary.total_matches}")
        print(f"  Total Polla points:   {summary.total_polla_points}")
        print(f"  Avg expected points:  {summary.avg_expected_points:.2f}")
        print(f"  Result accuracy:      {summary.result_accuracy:.1f}%")
        print(f"  Exact score accuracy: {summary.score_accuracy:.1f}%")
        print(f"  Avg λ error:          {summary.avg_lambda_error:.3f}")
        print(f"  Avg μ error:          {summary.avg_mu_error:.3f}")

        if summary.insights:
            print(f"\n  {'─'*50}")
            print("  INSIGHTS:")
            for insight in summary.insights:
                print(f"    • {insight}")

        print(f"{'='*60}\n")

    def calculate_polla_points(
        self, pred_h: int, pred_a: int, actual_h: int, actual_a: int
    ) -> int:
        """Calculate Polla points for a prediction given the actual result.

        Group stage rules:
        - Correct result (W/D/L): 5 points
        - Correct home goals: 2 points
        - Correct away goals: 2 points
        - Correct goal difference: 1 point
        - Maximum: 10 points

        Args:
            pred_h: Predicted home goals.
            pred_a: Predicted away goals.
            actual_h: Actual home goals.
            actual_a: Actual away goals.

        Returns:
            Total Polla points earned (0-10).
        """
        points = 0

        # Correct result (winner/draw)
        pred_sign = _result_sign(pred_h, pred_a)
        actual_sign = _result_sign(actual_h, actual_a)
        if pred_sign == actual_sign:
            points += self.RESULT_POINTS

        # Correct home goals
        if pred_h == actual_h:
            points += self.GOALS_POINTS

        # Correct away goals
        if pred_a == actual_a:
            points += self.GOALS_POINTS

        # Correct goal difference
        if (pred_h - pred_a) == (actual_h - actual_a):
            points += self.DIFF_POINTS

        return points

    def _get_feedbacks_for_date(self, date: str) -> list[MatchFeedback]:
        """Get matched prediction-result feedback for a specific date.

        Args:
            date: Date in YYYY-MM-DD format.

        Returns:
            List of MatchFeedback objects.
        """

        def _query(conn: sqlite3.Connection) -> list[dict]:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.home_team, p.away_team,
                       p.predicted_home_goals, p.predicted_away_goals,
                       p.lambda_home, p.mu_away, p.expected_points,
                       r.home_goals AS actual_home, r.away_goals AS actual_away
                FROM predictions p
                INNER JOIN actual_results r
                    ON p.event_id = r.event_id AND p.sport_key = r.sport_key
                WHERE p.prediction_date = ?
                """,
                (date,),
            )
            return [dict(row) for row in cursor.fetchall()]

        rows = self._execute_with_retry(_query)
        return self._rows_to_feedbacks(rows)

    def _get_all_feedbacks(self) -> list[MatchFeedback]:
        """Get all matched prediction-result feedback pairs.

        Returns:
            List of MatchFeedback objects.
        """

        def _query(conn: sqlite3.Connection) -> list[dict]:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT p.home_team, p.away_team,
                       p.predicted_home_goals, p.predicted_away_goals,
                       p.lambda_home, p.mu_away, p.expected_points,
                       r.home_goals AS actual_home, r.away_goals AS actual_away
                FROM predictions p
                INNER JOIN actual_results r
                    ON p.event_id = r.event_id AND p.sport_key = r.sport_key
                """
            )
            return [dict(row) for row in cursor.fetchall()]

        rows = self._execute_with_retry(_query)
        return self._rows_to_feedbacks(rows)

    def _rows_to_feedbacks(self, rows: list[dict]) -> list[MatchFeedback]:
        """Convert query result rows to MatchFeedback objects.

        Args:
            rows: List of result dictionaries from the SQL join.

        Returns:
            List of MatchFeedback objects.
        """
        feedbacks: list[MatchFeedback] = []
        for row in rows:
            pred_h = int(row["predicted_home_goals"])
            pred_a = int(row["predicted_away_goals"])
            actual_h = int(row["actual_home"])
            actual_a = int(row["actual_away"])
            lambda_home = float(row["lambda_home"])
            mu_away = float(row["mu_away"])
            expected_pts = float(row["expected_points"])

            polla_pts = self.calculate_polla_points(pred_h, pred_a, actual_h, actual_a)
            result_correct = (
                _result_sign(pred_h, pred_a) == _result_sign(actual_h, actual_a)
            )
            score_correct = pred_h == actual_h and pred_a == actual_a

            feedbacks.append(
                MatchFeedback(
                    home_team=row["home_team"],
                    away_team=row["away_team"],
                    predicted_score=(pred_h, pred_a),
                    actual_score=(actual_h, actual_a),
                    polla_points_earned=polla_pts,
                    expected_points=expected_pts,
                    result_correct=result_correct,
                    score_correct=score_correct,
                    lambda_error=abs(lambda_home - actual_h),
                    mu_error=abs(mu_away - actual_a),
                )
            )
        return feedbacks

    def _build_summary(self, feedbacks: list[MatchFeedback]) -> FeedbackSummary:
        """Build a FeedbackSummary from a list of feedbacks.

        Args:
            feedbacks: List of MatchFeedback objects.

        Returns:
            FeedbackSummary with computed metrics and insights.
        """
        total = len(feedbacks)
        if total == 0:
            return FeedbackSummary(
                total_matches=0,
                total_polla_points=0,
                avg_expected_points=0.0,
                result_accuracy=0.0,
                score_accuracy=0.0,
                avg_lambda_error=0.0,
                avg_mu_error=0.0,
                insights=["No prediction-result pairs available for analysis."],
            )

        total_pts = sum(f.polla_points_earned for f in feedbacks)
        avg_expected = sum(f.expected_points for f in feedbacks) / total
        result_correct_count = sum(1 for f in feedbacks if f.result_correct)
        score_correct_count = sum(1 for f in feedbacks if f.score_correct)
        avg_lam_err = sum(f.lambda_error for f in feedbacks) / total
        avg_mu_err = sum(f.mu_error for f in feedbacks) / total

        insights = self.generate_insights(feedbacks)

        return FeedbackSummary(
            total_matches=total,
            total_polla_points=total_pts,
            avg_expected_points=avg_expected,
            result_accuracy=result_correct_count / total * 100,
            score_accuracy=score_correct_count / total * 100,
            avg_lambda_error=avg_lam_err,
            avg_mu_error=avg_mu_err,
            insights=insights,
        )


def _result_sign(home: int, away: int) -> int:
    """Compute result sign: 1 for home win, -1 for away win, 0 for draw.

    Args:
        home: Home team goals.
        away: Away team goals.

    Returns:
        1, 0, or -1 representing the match outcome.
    """
    if home > away:
        return 1
    elif home < away:
        return -1
    return 0
