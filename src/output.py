"""Terminal output module for rendering match results as pipe-delimited tables."""

from __future__ import annotations

from src.models import MatchResult


class TerminalOutput:
    """Renders match results as a pipe-delimited terminal table.

    All values are strictly numeric. Flag columns show 0.00 when not triggered.
    """

    COLUMNS = [
        "Match",
        "Real Home Prob %",
        "Real Draw Prob %",
        "Real Away Prob %",
        "xG Home (λ)",
        "xG Away (μ)",
        "Suggested Score",
        "Score Prob %",
        "Variance Flag",
        "High Margin",
        "Low Coverage",
    ]

    def render(self, results: list[MatchResult]) -> str:
        """Produce pipe-delimited table string with header and data rows.

        Args:
            results: List of MatchResult instances from the processing pipeline.
                     Only successful matches should be included.

        Returns:
            A string containing the pipe-delimited table with header row
            followed by one data row per match result.
        """
        lines: list[str] = []

        # Header row
        lines.append(" | ".join(self.COLUMNS))

        # Data rows
        for result in results:
            row = self._format_row(result)
            lines.append(" | ".join(row))

        return "\n".join(lines)

    def _format_row(self, result: MatchResult) -> list[str]:
        """Format a single MatchResult into a list of string values.

        Args:
            result: A MatchResult instance.

        Returns:
            List of formatted string values matching COLUMNS order.
        """
        home_prob, draw_prob, away_prob = result.real_probs

        return [
            f"{result.home_team} vs {result.away_team}",
            f"{home_prob * 100:.2f}",
            f"{draw_prob * 100:.2f}",
            f"{away_prob * 100:.2f}",
            f"{result.lambda_home:.2f}",
            f"{result.mu_away:.2f}",
            f"{result.suggested_score[0]}-{result.suggested_score[1]}",
            f"{result.score_probability * 100:.2f}",
            f"{result.variance_flag:.2f}",
            f"{result.high_margin:.2f}",
            f"{float(result.low_coverage):.2f}",
        ]
