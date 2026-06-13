"""Value output module for rendering detected value bets as a terminal table."""

from __future__ import annotations

from src.value_detector import ValueBet


class ValueOutput:
    """Renders detected value bets as a pipe-delimited terminal table.

    Displays match, outcome, bookmaker, offered odds, fair odds, edge
    percentage, and Kelly fraction for each detected value bet.
    """

    COLUMNS = [
        "Match",
        "Outcome",
        "Bookmaker",
        "Offered",
        "Fair",
        "Edge %",
        "Kelly %",
    ]

    def render(self, value_bets: list[ValueBet]) -> str:
        """Produce pipe-delimited table string with header and data rows.

        Args:
            value_bets: List of ValueBet instances to render.

        Returns:
            A string containing the formatted value bets table.
            Returns a "no value bets" message if the list is empty.
        """
        if not value_bets:
            return "No value bets detected."

        lines: list[str] = []

        lines.append("VALUE BETS DETECTED:")
        lines.append(" | ".join(self.COLUMNS))

        # Sort by edge descending for most attractive bets first
        sorted_bets = sorted(value_bets, key=lambda vb: vb.edge_pct, reverse=True)

        for vb in sorted_bets:
            row = self._format_row(vb)
            lines.append(" | ".join(row))

        return "\n".join(lines)

    def _format_row(self, vb: ValueBet) -> list[str]:
        """Format a single ValueBet into a list of string values.

        Args:
            vb: A ValueBet instance.

        Returns:
            List of formatted string values matching COLUMNS order.
        """
        return [
            f"{vb.home_team} vs {vb.away_team}",
            vb.outcome,
            vb.bookmaker_id,
            f"{vb.offered_odds:.2f}",
            f"{vb.fair_odds:.2f}",
            f"{vb.edge_pct:.1f}",
            f"{vb.kelly_fraction * 100:.1f}",
        ]
