"""CLI entry point for the Polla Mundialista Optimizer.

Parses command-line arguments, initializes the adapter system, and runs
the polla pipeline: OddsExtractor → MarginEliminator → LambdaOptimizer →
ScoreMatrixGenerator → PollaScorer → PredictionStore.
"""
from __future__ import annotations

import argparse
import logging
from datetime import date as date_type
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from src.adapters.registry import AdapterRegistry
from src.cache_store import CacheStore
from src.dixon_coles import apply_dixon_coles_correction, apply_world_cup_chill
from src.lambda_optimizer import LambdaOptimizer
from src.margin_eliminator import AnomalousOddsError, MarginEliminator
from src.models import AggregatedEvent
from src.odds_extractor import OddsExtractor
from src.polla_scorer import PollaScorer
from src.score_matrix import ScoreMatrixGenerator

logger = logging.getLogger(__name__)

# Display names for adapter IDs in output
_ADAPTER_DISPLAY_NAMES: dict[str, str] = {
    "unibet": "Kambi/Unibet",
    "onexbet": "1xBet",
    "betfair": "Betfair",
    "the_odds_api": "The Odds API",
}

# Sharpness weights for bookmaker probability aggregation.
# Higher weight = more trusted source (sharper lines, lower margins).
# Betfair Exchange and Pinnacle are the gold standard; The Odds API
# aggregates ~24 bookmakers internally so it's a robust consensus.
SHARPNESS_WEIGHTS: dict[str, float] = {
    "betfair": 0.45,
    "the_odds_api": 0.35,
    "onexbet": 0.20,
    "unibet": 0.20,
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list to parse. Defaults to sys.argv[1:].

    Returns:
        Parsed namespace with all CLI arguments.
    """
    parser = argparse.ArgumentParser(
        prog="polla-optimizer",
        description="Polla Mundialista optimizer: extract odds, compute Poisson matrix, maximize E[points].",
    )
    parser.add_argument(
        "--sport",
        required=True,
        help="Sport/league key (e.g., 'soccer_fifa_world_cup').",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date filter in YYYY-MM-DD format. If omitted, fetches all available matches.",
    )
    parser.add_argument(
        "--round",
        default=None,
        help="League round identifier to process all matches in that round.",
    )
    parser.add_argument(
        "--knockout",
        action="store_true",
        default=False,
        help="Use knockout stage scoring (double points).",
    )
    parser.add_argument(
        "--retro",
        nargs="?",
        const="auto",
        default=None,
        metavar="DATE",
        help="Run retro-feedback mode. If DATE is given, analyze that date. "
        "If no date, auto-fetch results from The Odds API and run cumulative feedback.",
    )
    parser.add_argument(
        "--list-adapters",
        action="store_true",
        default=False,
        help="List all registered adapters with their health status and exit.",
    )
    return parser.parse_args(argv)


def list_adapters(registry: AdapterRegistry) -> None:
    """Print a table of registered adapters with their health status.

    Args:
        registry: Initialized AdapterRegistry with discovered adapters.
    """
    adapters = registry.list_with_status()
    if not adapters:
        print("No adapters registered.")
        return

    header = "Adapter ID | Name | Health"
    print(header)
    for adapter_info in adapters:
        print(
            f"{adapter_info['id']} | {adapter_info['name']} | {adapter_info['health']}"
        )


def _compute_over_probability(event: AggregatedEvent) -> float:
    """Compute average real probability of over 2.5 goals from over/under odds.

    Args:
        event: AggregatedEvent with bookmaker_odds_over_under data.

    Returns:
        Average real probability of over 2.5 goals. Defaults to 0.5
        if no over/under data is available.
    """
    if not event.bookmaker_odds_over_under:
        return 0.5

    over_probs: list[float] = []
    for _bm_id, (over_odds, under_odds) in event.bookmaker_odds_over_under.items():
        imp_over = 1.0 / over_odds
        imp_under = 1.0 / under_odds
        total = imp_over + imp_under
        if total > 0:
            over_probs.append(imp_over / total)

    if not over_probs:
        return 0.5

    return sum(over_probs) / len(over_probs)


def _compute_multi_line_ou_probs(
    ou_lines: dict[str, dict[float, tuple[float, float]]],
) -> list[tuple[float, float]]:
    """Convert raw O/U odds to fair over probabilities.

    Averages across bookmakers and normalizes to remove overround for
    each threshold line.

    Args:
        ou_lines: Mapping of bookmaker_id -> {threshold: (over_odds, under_odds)}.

    Returns:
        List of (threshold, over_probability) sorted by threshold.
    """
    if not ou_lines:
        return []

    threshold_probs: dict[float, list[float]] = {}
    for _bm_id, lines in ou_lines.items():
        for threshold, (over_odds, under_odds) in lines.items():
            if over_odds <= 1.0 or under_odds <= 1.0:
                continue
            imp_over = 1.0 / over_odds
            imp_under = 1.0 / under_odds
            total = imp_over + imp_under
            if total > 0:
                fair_over = imp_over / total
                threshold_probs.setdefault(threshold, []).append(fair_over)

    result: list[tuple[float, float]] = []
    for threshold in sorted(threshold_probs.keys()):
        probs = threshold_probs[threshold]
        avg_prob = sum(probs) / len(probs)
        result.append((threshold, avg_prob))

    return result


def run_polla_pipeline(
    events: list[AggregatedEvent],
    knockout: bool = False,
    sport: str = "soccer_fifa_world_cup",
    prediction_date: str | None = None,
) -> None:
    """Run the Polla Mundialista optimization pipeline.

    Processes each event through margin elimination → lambda optimization →
    ScoreMatrixGenerator → PollaScorer to find the optimal prediction
    maximizing expected Polla points. Saves predictions to the database.

    Args:
        events: List of AggregatedEvent from OddsExtractor.
        knockout: Whether to use knockout stage scoring (double points).
        sport: Sport/league key for storing predictions.
        prediction_date: Date string for predictions. Defaults to today.
    """
    from src.prediction_store import PredictionStore

    margin_eliminator = MarginEliminator(method="shin")
    lambda_optimizer = LambdaOptimizer()
    score_matrix_gen = ScoreMatrixGenerator()
    scorer = PollaScorer(is_knockout=knockout)

    if prediction_date is None:
        prediction_date = date_type.today().isoformat()

    predictions: list[tuple] = []
    store_data: list[dict] = []

    for event in events:
        # Step 1: Get real probabilities with sharpness-weighted aggregation
        real_probs_by_source: list[tuple[str, tuple[float, float, float]]] = []
        for bm_id, odds_1x2 in event.bookmaker_odds_1x2.items():
            try:
                real_probs = margin_eliminator.eliminate(odds_1x2)
                real_probs_by_source.append((bm_id, real_probs))
            except AnomalousOddsError:
                continue

        if not real_probs_by_source:
            continue

        # Sharpness-weighted average (falls back to equal weights for unknown sources)
        default_weight = 0.25
        total_weight = 0.0
        weighted_home = 0.0
        weighted_draw = 0.0
        weighted_away = 0.0

        for bm_id, (p_h, p_d, p_a) in real_probs_by_source:
            w = SHARPNESS_WEIGHTS.get(bm_id, default_weight)
            weighted_home += w * p_h
            weighted_draw += w * p_d
            weighted_away += w * p_a
            total_weight += w

        avg_home = weighted_home / total_weight
        avg_draw = weighted_draw / total_weight
        avg_away = weighted_away / total_weight
        avg_probs = (avg_home, avg_draw, avg_away)

        real_prob_over = _compute_over_probability(event)

        # Step 2: Lambda optimization — prefer weighted when multi-line data available
        overall_ou_targets = _compute_multi_line_ou_probs(event.overall_ou_lines)
        home_team_ou_targets = _compute_multi_line_ou_probs(event.home_team_ou_lines)
        away_team_ou_targets = _compute_multi_line_ou_probs(event.away_team_ou_lines)

        has_multi_line = bool(overall_ou_targets or home_team_ou_targets or away_team_ou_targets)

        if has_multi_line:
            opt_result = lambda_optimizer.optimize_weighted(
                avg_probs,
                overall_ou_targets=overall_ou_targets,
                home_team_ou_targets=home_team_ou_targets,
                away_team_ou_targets=away_team_ou_targets,
            )
            if opt_result is not None:
                lam, mu = opt_result.primary_lambda, opt_result.primary_mu
            else:
                result = lambda_optimizer.optimize(avg_probs, real_prob_over)
                if result is None:
                    continue
                lam, mu = result
        else:
            result = lambda_optimizer.optimize(avg_probs, real_prob_over)
            if result is None:
                continue
            lam, mu = result

        # Step 3: Build Poisson score matrix with corrections
        score_result = score_matrix_gen.generate(lam, mu)
        if score_result is None:
            continue
        matrix = score_result["matrix"]

        # Apply Dixon-Coles correlation correction (low-scoring corner)
        matrix = apply_dixon_coles_correction(matrix, lam, mu)

        # Apply World Cup chill factor (teams protect slim leads)
        matrix = apply_world_cup_chill(matrix, lam, mu)

        # Step 4: Polla optimization using corrected matrix + direct 1X2 probs
        rec = scorer.recommend(matrix, real_probs_1x2=avg_probs)

        # Collect per-source odds for display
        source_odds: dict[str, tuple[float, float, float]] = event.bookmaker_odds_1x2

        predictions.append((
            f"{event.home_team} vs {event.away_team}",
            f"{rec.predicted_score[0]}-{rec.predicted_score[1]}",
            rec.predicted_score[0],
            rec.predicted_score[1],
            rec.expected_points,
            rec.max_probable_score,
            rec.max_probable_prob,
            source_odds,
        ))
        store_data.append({
            "sport": sport,
            "event_id": event.home_team.lower().replace(" ", "_")
            + "_vs_"
            + event.away_team.lower().replace(" ", "_")
            + "_"
            + prediction_date,
            "home_team": event.home_team,
            "away_team": event.away_team,
            "home_goals": rec.predicted_score[0],
            "away_goals": rec.predicted_score[1],
            "lambda_home": lam,
            "mu_away": mu,
            "prediction_date": prediction_date,
            "expected_points": rec.expected_points,
        })

    if not predictions:
        print("No matches could be processed through the Polla pipeline.")
        return

    # Save predictions to database for feedback tracking
    if store_data:
        try:
            prediction_store = PredictionStore()
            prediction_store.save_batch(store_data)
            logger.info("Saved %d predictions to database.", len(store_data))
        except Exception as exc:
            logger.warning("Failed to save predictions: %s", exc)

    # Print table
    phase = "KNOCKOUT" if knockout else "GROUP"
    print(f"\n{'='*72}")
    print(f"  POLLA MUNDIALISTA — Optimal Predictions ({phase} STAGE)")
    print(f"{'='*72}")
    print(f"{'Match':<35} {'Optimal':>8} {'E[Pts]':>8} {'Most Prob':>10} {'Prob%':>6}")
    print(f"{'-'*35} {'-'*8} {'-'*8} {'-'*10} {'-'*6}")

    for match_name, score_str, h, a, exp_pts, most_prob, mp_prob, source_odds in predictions:
        mp_str = f"{most_prob[0]}-{most_prob[1]}"
        differs = " *" if (h, a) != most_prob else ""
        print(
            f"{match_name:<35} {score_str:>8} {exp_pts:>8.2f} "
            f"{mp_str:>10} {mp_prob*100:>5.1f}%{differs}"
        )
        # Per-source breakdown: show win probabilities (not raw odds)
        for bm_id, odds in source_odds.items():
            bm_name = _ADAPTER_DISPLAY_NAMES.get(bm_id, bm_id)
            imp_h = 1.0 / odds[0]
            imp_d = 1.0 / odds[1]
            imp_a = 1.0 / odds[2]
            total = imp_h + imp_d + imp_a
            prob_h = imp_h / total * 100
            prob_d = imp_d / total * 100
            prob_a = imp_a / total * 100
            print(f"    └─ {bm_name:<18} Home {prob_h:.0f}%  Draw {prob_d:.0f}%  Away {prob_a:.0f}%")

    print(f"{'-'*72}")
    print("  * = optimal differs from most probable score")
    print(f"{'='*72}\n")


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the Polla Mundialista optimizer CLI.

    Args:
        argv: Optional argument list for testing. Defaults to sys.argv[1:].
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    args = parse_args(argv)

    # Initialize adapter registry and discover adapters
    registry = AdapterRegistry()
    registry.discover()

    # Initialize cache store
    cache_store = CacheStore()

    # Handle --list-adapters
    if args.list_adapters:
        list_adapters(registry)
        return

    # Handle --retro mode
    if args.retro:
        if args.retro == "auto":
            from src.feedback_analyzer import FeedbackAnalyzer
            from src.results_collector import ResultsCollector

            collector = ResultsCollector()
            print(f"Fetching actual results from The Odds API for {args.sport}...")
            try:
                count = collector.collect_from_api(sport=args.sport, days_back=3)
                print(f"  ✓ Collected {count} completed match result(s).\n")
            except RuntimeError as exc:
                print(f"  ✗ {exc}\n")
                return

            analyzer = FeedbackAnalyzer()
            analyzer.print_report(date=None)
        else:
            from src.retro_feedback import RetroFeedback
            retro = RetroFeedback(cache_store, args.sport)
            retro.run(args.retro)
        return

    # Initialize OddsExtractor — accept single-source events
    extractor = OddsExtractor(
        registry=registry,
        cache_store=cache_store,
        excluded_bookmakers=set(),
    )
    extractor.MIN_BOOKMAKERS = 1

    # Determine date
    extraction_date = args.date

    # Extract odds
    events = extractor.extract(
        sport=args.sport,
        date=extraction_date,
        round_id=args.round,
    )

    if not events:
        print("No events found meeting the minimum bookmaker threshold.")
        return

    # Run polla pipeline
    run_polla_pipeline(
        events,
        knockout=args.knockout,
        sport=args.sport,
        prediction_date=extraction_date,
    )


if __name__ == "__main__":
    main()
