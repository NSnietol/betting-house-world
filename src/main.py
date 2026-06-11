"""CLI entry point for the Betting Odds Calculator.

Parses command-line arguments, initializes the adapter system, and runs
the full pipeline: OddsExtractor → MarginEliminator → LambdaOptimizer →
ScoreMatrixGenerator → DataQualityAnalyzer → TerminalOutput.
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date as date_type

from src.adapters.registry import AdapterRegistry
from src.bookmaker_scorer import BookmakerScorer
from src.cache_store import CacheStore
from src.data_quality import DataQualityAnalyzer
from src.divergence import compute_divergence
from src.lambda_optimizer import LambdaOptimizer
from src.margin_eliminator import AnomalousOddsError, MarginEliminator
from src.models import AggregatedEvent, MatchResult, OptimizationResult
from src.odds_extractor import OddsExtractor
from src.output import TerminalOutput
from src.polla_scorer import PollaScorer
from src.retro_feedback import RetroFeedback
from src.score_matrix import ScoreMatrixGenerator
from src.value_detector import ValueBetDetector
from src.value_output import ValueOutput

logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments.

    Args:
        argv: Argument list to parse. Defaults to sys.argv[1:].

    Returns:
        Parsed namespace with all CLI arguments.
    """
    parser = argparse.ArgumentParser(
        prog="betting-odds",
        description="Extract betting odds, remove margins, and predict exact scores.",
    )
    parser.add_argument(
        "--sport",
        required=True,
        help="Sport/league key (e.g., 'soccer_epl', 'soccer_spain_la_liga').",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date filter in YYYY-MM-DD format. Defaults to today.",
    )
    parser.add_argument(
        "--round",
        default=None,
        help="League round identifier to process all matches in that round.",
    )
    parser.add_argument(
        "--retro",
        default=None,
        metavar="DATE",
        help="Run retro-feedback mode for the given date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--bookmaker-report",
        action="store_true",
        default=False,
        help="Display bookmaker reliability scores and exit.",
    )
    parser.add_argument(
        "--enable-bookmaker",
        default=None,
        metavar="KEY",
        help="Re-enable a previously excluded bookmaker by key.",
    )
    parser.add_argument(
        "--list-adapters",
        action="store_true",
        default=False,
        help="List all registered adapters with their health status and exit.",
    )
    parser.add_argument(
        "--value-bets",
        action="store_true",
        default=False,
        help="Run value bet detection and display value opportunities.",
    )
    parser.add_argument(
        "--edge-threshold",
        type=float,
        default=0.03,
        help="Minimum edge threshold for value bet detection (default: 0.03 = 3%%).",
    )
    parser.add_argument(
        "--polla",
        action="store_true",
        default=False,
        help="Run Polla Mundialista optimizer: find optimal predictions maximizing expected points.",
    )
    parser.add_argument(
        "--knockout",
        action="store_true",
        default=False,
        help="Use knockout stage scoring (double points) for --polla mode.",
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


def process_event(
    event: AggregatedEvent,
    margin_eliminator: MarginEliminator,
    lambda_optimizer: LambdaOptimizer,
    score_matrix_generator: ScoreMatrixGenerator,
    data_quality_analyzer: DataQualityAnalyzer,
) -> MatchResult | None:
    """Process a single aggregated event through the full math pipeline.

    Pipeline: MarginEliminator → LambdaOptimizer → ScoreMatrixGenerator →
    DataQualityAnalyzer.

    Args:
        event: AggregatedEvent with odds from multiple bookmakers.
        margin_eliminator: Configured MarginEliminator instance.
        lambda_optimizer: LambdaOptimizer instance.
        score_matrix_generator: ScoreMatrixGenerator instance.
        data_quality_analyzer: DataQualityAnalyzer instance.

    Returns:
        MatchResult if all pipeline steps succeed, None otherwise.
    """
    # Step 1: Margin elimination — get real probabilities per bookmaker
    real_probs_list: list[tuple[float, float, float]] = []
    valid_bookmaker_odds: list[dict] = []

    for bm_id, odds_1x2 in event.bookmaker_odds_1x2.items():
        try:
            real_probs = margin_eliminator.eliminate(odds_1x2)
            real_probs_list.append(real_probs)
            valid_bookmaker_odds.append({"decimal_odds": odds_1x2})
        except AnomalousOddsError:
            logger.warning(
                "Anomalous odds from '%s' for '%s vs %s', skipping.",
                bm_id,
                event.home_team,
                event.away_team,
            )
            continue

    if not real_probs_list:
        logger.warning(
            "No valid probabilities for '%s vs %s', skipping.",
            event.home_team,
            event.away_team,
        )
        return None

    # Average real probabilities across bookmakers
    avg_home = sum(p[0] for p in real_probs_list) / len(real_probs_list)
    avg_draw = sum(p[1] for p in real_probs_list) / len(real_probs_list)
    avg_away = sum(p[2] for p in real_probs_list) / len(real_probs_list)
    avg_probs: tuple[float, float, float] = (avg_home, avg_draw, avg_away)

    # Compute over 2.5 probability from over/under odds
    real_prob_over_2_5 = _compute_over_probability(event)

    # Step 2: Lambda optimization — use weighted when multi-line data available
    overall_ou_targets = _compute_multi_line_ou_probs(event.overall_ou_lines)
    home_team_ou_targets = _compute_multi_line_ou_probs(event.home_team_ou_lines)
    away_team_ou_targets = _compute_multi_line_ou_probs(event.away_team_ou_lines)

    has_multi_line = bool(overall_ou_targets or home_team_ou_targets or away_team_ou_targets)

    lam: float
    mu: float
    secondary_lambda: float | None = None
    secondary_mu: float | None = None
    lambda_divergence: float | None = None
    mu_divergence: float | None = None
    is_high_divergence: bool = False

    if has_multi_line:
        opt_result = lambda_optimizer.optimize_weighted(
            avg_probs,
            overall_ou_targets=overall_ou_targets,
            home_team_ou_targets=home_team_ou_targets,
            away_team_ou_targets=away_team_ou_targets,
        )
        if opt_result is None:
            # Fall back to legacy optimization
            legacy_result = lambda_optimizer.optimize(avg_probs, real_prob_over_2_5)
            if legacy_result is None:
                logger.warning(
                    "Lambda optimization failed for '%s vs %s', skipping.",
                    event.home_team,
                    event.away_team,
                )
                return None
            lam, mu = legacy_result
        else:
            lam = opt_result.primary_lambda
            mu = opt_result.primary_mu
            secondary_lambda = opt_result.secondary_lambda
            secondary_mu = opt_result.secondary_mu
            # Compute divergence
            div_result = compute_divergence(opt_result)
            if div_result is not None:
                lambda_divergence = div_result.lambda_divergence
                mu_divergence = div_result.mu_divergence
                is_high_divergence = div_result.is_high_divergence
    else:
        # Legacy path: single O/U 2.5 line
        result = lambda_optimizer.optimize(avg_probs, real_prob_over_2_5)
        if result is None:
            logger.warning(
                "Lambda optimization failed for '%s vs %s', skipping.",
                event.home_team,
                event.away_team,
            )
            return None
        lam, mu = result

    # Step 3: Score matrix generation
    score_result = score_matrix_generator.generate(lam, mu)
    if score_result is None:
        logger.warning(
            "Score matrix generation failed for '%s vs %s', skipping.",
            event.home_team,
            event.away_team,
        )
        return None

    suggested_score = score_result["suggested_score"]
    score_probability = score_result["score_probability"]

    # Step 4: Polla scoring optimization
    from src.polla_scorer import PollaScorer as _PollaScorer

    polla = _PollaScorer(is_knockout=False)
    polla_rec = polla.recommend(score_result["matrix"])

    # Step 5: Data quality analysis
    quality_flags = data_quality_analyzer.analyze(
        {"bookmaker_odds": valid_bookmaker_odds}
    )

    return MatchResult(
        home_team=event.home_team,
        away_team=event.away_team,
        real_probs=avg_probs,
        lambda_home=lam,
        mu_away=mu,
        suggested_score=suggested_score,
        score_probability=score_probability,
        variance_flag=quality_flags["variance_flag"],
        high_margin=quality_flags["high_margin"],
        low_coverage=quality_flags["low_coverage"],
        secondary_lambda=secondary_lambda,
        secondary_mu=secondary_mu,
        lambda_divergence=lambda_divergence,
        mu_divergence=mu_divergence,
        is_high_divergence=is_high_divergence,
        polla_optimal_score=polla_rec.predicted_score,
        polla_expected_points=polla_rec.expected_points,
    )


def _compute_over_probability(event: AggregatedEvent) -> float:
    """Compute average real probability of over 2.5 goals from over/under odds.

    Converts over/under decimal odds to implied probabilities, removes
    overround via simple normalization, and averages across bookmakers.

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

    # Collect all thresholds across bookmakers
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

    # Average across bookmakers for each threshold
    result: list[tuple[float, float]] = []
    for threshold in sorted(threshold_probs.keys()):
        probs = threshold_probs[threshold]
        avg_prob = sum(probs) / len(probs)
        result.append((threshold, avg_prob))

    return result


def run_polla_pipeline(events: list[AggregatedEvent], knockout: bool = False) -> None:
    """Run the Polla Mundialista optimization pipeline.

    Processes each event through margin elimination → lambda optimization →
    ScoreMatrixGenerator → PollaScorer to find the optimal prediction
    maximizing expected Polla points.

    Args:
        events: List of AggregatedEvent from OddsExtractor.
        knockout: Whether to use knockout stage scoring (double points).
    """
    margin_eliminator = MarginEliminator(method="shin")
    lambda_optimizer = LambdaOptimizer()
    score_matrix_gen = ScoreMatrixGenerator()
    scorer = PollaScorer(is_knockout=knockout)

    predictions: list[tuple[str, str, int, int, float, tuple[int, int], float]] = []

    for event in events:
        # Step 1: Get real probabilities
        real_probs_list: list[tuple[float, float, float]] = []
        for bm_id, odds_1x2 in event.bookmaker_odds_1x2.items():
            try:
                real_probs = margin_eliminator.eliminate(odds_1x2)
                real_probs_list.append(real_probs)
            except AnomalousOddsError:
                continue

        if not real_probs_list:
            continue

        avg_home = sum(p[0] for p in real_probs_list) / len(real_probs_list)
        avg_draw = sum(p[1] for p in real_probs_list) / len(real_probs_list)
        avg_away = sum(p[2] for p in real_probs_list) / len(real_probs_list)
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

        # Step 3: Build Poisson score matrix
        score_result = score_matrix_gen.generate(lam, mu)
        if score_result is None:
            continue
        matrix = score_result["matrix"]

        # Step 4: Polla optimization using the matrix
        rec = scorer.recommend(matrix)
        predictions.append((
            f"{event.home_team} vs {event.away_team}",
            f"{rec.predicted_score[0]}-{rec.predicted_score[1]}",
            rec.predicted_score[0],
            rec.predicted_score[1],
            rec.expected_points,
            rec.max_probable_score,
            rec.max_probable_prob,
        ))

    if not predictions:
        print("No matches could be processed through the Polla pipeline.")
        return

    # Print table
    phase = "KNOCKOUT" if knockout else "GROUP"
    print(f"\n{'='*72}")
    print(f"  POLLA MUNDIALISTA — Optimal Predictions ({phase} STAGE)")
    print(f"{'='*72}")
    print(f"{'Match':<35} {'Optimal':>8} {'E[Pts]':>8} {'Most Prob':>10} {'Prob%':>6}")
    print(f"{'-'*35} {'-'*8} {'-'*8} {'-'*10} {'-'*6}")

    for match_name, score_str, h, a, exp_pts, most_prob, mp_prob in predictions:
        mp_str = f"{most_prob[0]}-{most_prob[1]}"
        differs = " *" if (h, a) != most_prob else ""
        print(
            f"{match_name:<35} {score_str:>8} {exp_pts:>8.2f} "
            f"{mp_str:>10} {mp_prob*100:>5.1f}%{differs}"
        )

    print(f"{'-'*72}")
    print("  * = optimal differs from most probable score")
    print(f"{'='*72}\n")


def run_pipeline(
    events: list[AggregatedEvent],
    margin_eliminator: MarginEliminator,
    lambda_optimizer: LambdaOptimizer,
    score_matrix_generator: ScoreMatrixGenerator,
    data_quality_analyzer: DataQualityAnalyzer,
    terminal_output: TerminalOutput,
) -> None:
    """Run the full processing pipeline on extracted events and output results.

    Args:
        events: List of AggregatedEvent from OddsExtractor.
        margin_eliminator: Configured MarginEliminator instance.
        lambda_optimizer: LambdaOptimizer instance.
        score_matrix_generator: ScoreMatrixGenerator instance.
        data_quality_analyzer: DataQualityAnalyzer instance.
        terminal_output: TerminalOutput renderer.
    """
    results: list[MatchResult] = []

    for event in events:
        match_result = process_event(
            event,
            margin_eliminator,
            lambda_optimizer,
            score_matrix_generator,
            data_quality_analyzer,
        )
        if match_result is not None:
            results.append(match_result)

    if not results:
        print("No matches could be processed through the full pipeline.")
        return

    output = terminal_output.render(results)
    print(output)


def main(argv: list[str] | None = None) -> None:
    """Main entry point for the betting odds calculator CLI.

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

    # Initialize bookmaker scorer for exclusion and reporting
    scorer = BookmakerScorer(cache_store)

    # Handle --enable-bookmaker
    if args.enable_bookmaker:
        scorer.enable_bookmaker(args.enable_bookmaker)
        return

    # Handle --bookmaker-report
    if args.bookmaker_report:
        scorer.print_report()
        return

    # Handle --retro mode
    if args.retro:
        retro = RetroFeedback(cache_store, args.sport)
        retro.run(args.retro)
        return

    # Get excluded bookmakers
    excluded = scorer.get_excluded_bookmakers()

    # Initialize OddsExtractor with adapter system
    extractor = OddsExtractor(
        registry=registry,
        cache_store=cache_store,
        excluded_bookmakers=excluded,
    )

    # Determine date (default to today if not provided and no round)
    extraction_date = args.date
    if extraction_date is None and args.round is None:
        extraction_date = date_type.today().isoformat()

    # Extract odds
    events = extractor.extract(
        sport=args.sport,
        date=extraction_date,
        round_id=args.round,
    )

    if not events:
        print("No events found meeting the minimum bookmaker threshold.")
        return

    # Handle --value-bets mode
    if args.value_bets:
        detector = ValueBetDetector(edge_threshold=args.edge_threshold)
        all_value_bets = []
        for event in events:
            all_value_bets.extend(detector.detect(event))

        value_output = ValueOutput()
        print(value_output.render(all_value_bets))
        return

    # Handle --polla mode
    if args.polla:
        run_polla_pipeline(events, knockout=args.knockout)
        return

    # Initialize pipeline components
    margin_eliminator = MarginEliminator(method="shin")
    lambda_optimizer = LambdaOptimizer()
    score_matrix_generator = ScoreMatrixGenerator()
    data_quality_analyzer = DataQualityAnalyzer()
    terminal_output = TerminalOutput()

    # Run the full pipeline
    run_pipeline(
        events=events,
        margin_eliminator=margin_eliminator,
        lambda_optimizer=lambda_optimizer,
        score_matrix_generator=score_matrix_generator,
        data_quality_analyzer=data_quality_analyzer,
        terminal_output=terminal_output,
    )


if __name__ == "__main__":
    main()
