"""CLI to input actual match results for feedback tracking.

Usage:
    # Import from JSON file
    uv run python scripts/collect_results.py --file results.json

    # Interactive input for a date
    uv run python scripts/collect_results.py --date 2026-06-11
"""

from __future__ import annotations

import argparse
import sys

sys.path.insert(0, ".")

from src.results_collector import ResultsCollector


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments.

    Args:
        argv: Argument list. Defaults to sys.argv[1:].

    Returns:
        Parsed namespace.
    """
    parser = argparse.ArgumentParser(
        prog="collect_results",
        description="Input actual match results for Polla feedback tracking.",
    )
    parser.add_argument(
        "--file",
        default=None,
        help="Path to JSON file with results.",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date (YYYY-MM-DD) for interactive result input.",
    )
    parser.add_argument(
        "--db",
        default="odds_cache.db",
        help="Path to SQLite database (default: odds_cache.db).",
    )
    return parser.parse_args(argv)


def interactive_input(collector: ResultsCollector, date: str) -> None:
    """Interactively collect match results from the user.

    Args:
        collector: ResultsCollector instance.
        date: Match date in YYYY-MM-DD format.
    """
    print(f"Enter match results for {date}. Type 'done' when finished.\n")
    count = 0

    while True:
        home = input("Home team (or 'done'): ").strip()
        if home.lower() == "done":
            break

        away = input("Away team: ").strip()
        if not away:
            print("Away team cannot be empty. Skipping.")
            continue

        try:
            home_goals = int(input(f"  {home} goals: ").strip())
            away_goals = int(input(f"  {away} goals: ").strip())
        except (ValueError, EOFError):
            print("Invalid goal input. Skipping this match.")
            continue

        event_id = ResultsCollector._generate_event_id(home, away, date)
        collector.store_result(
            sport="soccer_fifa_world_cup",
            event_id=event_id,
            home_team=home,
            away_team=away,
            home_goals=home_goals,
            away_goals=away_goals,
            match_date=date,
        )
        count += 1
        print(f"  ✓ Stored: {home} {home_goals}-{away_goals} {away}\n")

    print(f"\nDone. {count} result(s) stored for {date}.")


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the collect_results CLI.

    Args:
        argv: Optional argument list for testing.
    """
    args = parse_args(argv)
    collector = ResultsCollector(db_path=args.db)

    if args.file:
        count = collector.import_from_json(args.file)
        print(f"Imported {count} result(s) from {args.file}.")
    elif args.date:
        interactive_input(collector, args.date)
    else:
        print("Error: Provide either --file or --date.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
