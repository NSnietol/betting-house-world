"""CLI to show feedback analysis of predictions vs actual results.

Usage:
    # Show report for a specific date
    uv run python scripts/feedback_report.py --date 2026-06-11

    # Show cumulative report
    uv run python scripts/feedback_report.py --all
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from src.feedback_analyzer import FeedbackAnalyzer


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument list. Defaults to sys.argv[1:].

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        prog="feedback_report",
        description="Show Polla prediction feedback analysis.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date (YYYY-MM-DD) to analyze.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        default=False,
        help="Show cumulative report across all dates.",
    )
    parser.add_argument(
        "--db",
        default="odds_cache.db",
        help="Path to SQLite database (default: odds_cache.db).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the feedback_report CLI.

    Args:
        argv: Optional argument list for testing.
    """
    args = parse_args(argv)
    analyzer = FeedbackAnalyzer(db_path=args.db)

    if args.date:
        analyzer.print_report(date=args.date)
    elif args.all:
        analyzer.print_report(date=None)
    else:
        print("Error: Provide either --date or --all.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
