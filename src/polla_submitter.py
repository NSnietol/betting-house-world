"""Automated Polla Mundialista prediction submitter.

Generates predictions using the Poisson model, then submits them
to golpredictor.com via Playwright browser automation.

Environment Variables Required:
    GOLPREDICTOR_USER: Login username
    GOLPREDICTOR_PASS: Login password

Usage:
    # Dry run — show predictions without submitting
    uv run python -m src.polla_submitter --dry-run

    # Submit today's matches
    uv run python -m src.polla_submitter

    # Submit a specific date range
    uv run python -m src.polla_submitter --from 2026-06-12 --to 2026-06-15

    # Submit full week from today
    uv run python -m src.polla_submitter --week
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Page Map — golpredictor.com structure
# ──────────────────────────────────────────────────────────────────────────────
#
# LOGIN PAGE: https://www.golpredictor.com/home.aspx
#   - If not logged in, shows login form:
#     - Username: input#ctl00_ContentPlaceInner_Login1_UserName
#     - Password: input#ctl00_ContentPlaceInner_Login1_Password
#     - Submit:   input#ctl00_ContentPlaceInner_Login1_LoginButton
#   - If logged in, shows "Bienvenido, {Name}" in header
#
# POOL PAGE: https://www.golpredictor.com/pooldetail.aspx?pid=0%2cc1ca22da-764b-41b1-8a14-485e9733f23b
#   - Tabs: Pronósticos | Posiciones | Info General
#   - Match table (paginated, 24 matches per page):
#     - Columns: Id | Horario | Partido | Pronóstico | Resultado | Puntaje
#     - Each editable match row has:
#       - Home goals input: #ctl00_ContentPlaceInner_gvPartidos_ctl{NN}_txtGolLocal
#       - Away goals input: #ctl00_ContentPlaceInner_gvPartidos_ctl{NN}_txtGolVisitante
#       - NN starts at 02 for first row, increments by 1
#     - Locked matches (already started): show "-" without input fields
#   - Save button: input[type="image"]#ctl00_ContentPlaceInner_butGuardar
#   - Pagination: links with __doPostBack('ctl00$ContentPlaceInner$gvPartidos','Page$N')
#
# IMPORTANT: Click GUARDAR per page. Each page must be saved separately.
# ──────────────────────────────────────────────────────────────────────────────

POOL_URL = (
    "https://www.golpredictor.com/pooldetail.aspx"
    "?pid=0%2cc1ca22da-764b-41b1-8a14-485e9733f23b"
)
LOGIN_URL = "https://www.golpredictor.com/home.aspx"


@dataclass
class MatchPrediction:
    """A prediction ready to submit."""

    match_name: str
    home_goals: int
    away_goals: int
    expected_points: float
    match_date: str


def generate_predictions(
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[MatchPrediction]:
    """Run the prediction model and return predictions for the date range.

    Args:
        from_date: Start date YYYY-MM-DD (defaults to today).
        to_date: End date YYYY-MM-DD (defaults to from_date + 6 days).

    Returns:
        List of MatchPrediction objects.
    """
    import tempfile

    from src.adapters.registry import AdapterRegistry
    from src.adapters.unibet import UnibetAdapter
    from src.cache_store import CacheStore
    from src.lambda_optimizer import LambdaOptimizer
    from src.margin_eliminator import AnomalousOddsError, MarginEliminator
    from src.odds_extractor import OddsExtractor
    from src.polla_scorer import PollaScorer
    from src.score_matrix import ScoreMatrixGenerator

    registry = AdapterRegistry()
    registry.register(UnibetAdapter())

    cache = CacheStore(
        db_path=os.path.join(tempfile.gettempdir(), "polla_submitter.db")
    )
    extractor = OddsExtractor(registry=registry, cache_store=cache)
    extractor.MIN_BOOKMAKERS = 1

    me = MarginEliminator(method="shin")
    lo = LambdaOptimizer()
    smg = ScoreMatrixGenerator()
    polla = PollaScorer(is_knockout=False)

    events = extractor.extract("soccer_world_cup")

    # Date range
    if from_date:
        start = datetime.strptime(from_date, "%Y-%m-%d").date()
    else:
        start = datetime.now(timezone.utc).date()

    if to_date:
        end = datetime.strptime(to_date, "%Y-%m-%d").date()
    else:
        end = start + timedelta(days=6)

    predictions: list[MatchPrediction] = []

    for event in events:
        event_date = event.event_timestamp.date()
        if event_date < start or event_date > end:
            continue

        odds_1x2 = list(event.bookmaker_odds_1x2.values())
        if not odds_1x2:
            continue

        try:
            real_probs = me.eliminate(odds_1x2[0])
        except (AnomalousOddsError, Exception):
            continue

        over_prob = 0.5
        if event.bookmaker_odds_over_under:
            ov, un = list(event.bookmaker_odds_over_under.values())[0]
            over_prob = (1 / ov) / (1 / ov + 1 / un)

        result = lo.optimize(real_probs, over_prob)
        if not result:
            continue
        lam, mu = result

        score_result = smg.generate(lam, mu)
        if not score_result:
            continue

        rec = polla.recommend(score_result["matrix"])
        date_str = event.event_timestamp.strftime("%d %b")

        predictions.append(
            MatchPrediction(
                match_name=f"{event.home_team} - {event.away_team}",
                home_goals=rec.predicted_score[0],
                away_goals=rec.predicted_score[1],
                expected_points=rec.expected_points,
                match_date=date_str,
            )
        )

    return predictions


def _normalize(name: str) -> str:
    """Normalize team name for fuzzy matching."""
    nfkd = unicodedata.normalize("NFKD", name)
    ascii_name = "".join(c for c in nfkd if not unicodedata.combining(c))
    return ascii_name.lower().strip()


def _fuzzy_match(pred_name: str, page_name: str) -> float:
    """Jaccard similarity between team name tokens."""
    pred_parts = set(_normalize(pred_name).replace("-", " ").split())
    page_parts = set(_normalize(page_name).replace("-", " ").split())
    if not pred_parts or not page_parts:
        return 0.0
    return len(pred_parts & page_parts) / len(pred_parts | page_parts)


def submit_predictions(
    predictions: list[MatchPrediction],
    dry_run: bool = False,
) -> None:
    """Submit predictions to golpredictor.com.

    Args:
        predictions: List of predictions to submit.
        dry_run: If True, only print what would be submitted.
    """
    if dry_run:
        print("\n🔍 DRY RUN — Predictions:\n")
        for pred in predictions:
            print(
                f"  {pred.match_date} | {pred.match_name}: "
                f"{pred.home_goals}-{pred.away_goals} "
                f"(E[pts]={pred.expected_points:.2f})"
            )
        print(f"\n  Total: {len(predictions)} matches")
        return

    # Check credentials
    user = os.environ.get("GOLPREDICTOR_USER")
    password = os.environ.get("GOLPREDICTOR_PASS")
    if not user or not password:
        print("ERROR: Set GOLPREDICTOR_USER and GOLPREDICTOR_PASS environment variables.")
        sys.exit(1)

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: pip install playwright && playwright install chromium")
        sys.exit(1)

    print("\n🏆 Submitting predictions to GolPredictor...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        page = browser.new_page()

        # Step 1: Login
        page.goto(LOGIN_URL, timeout=30000)
        page.wait_for_load_state("networkidle")

        if "Bienvenido" not in page.content():
            print("  Logging in...")
            user_input = page.locator("#ctl00_ContentPlaceInner_Login1_UserName")
            pass_input = page.locator("#ctl00_ContentPlaceInner_Login1_Password")
            login_btn = page.locator("#ctl00_ContentPlaceInner_Login1_LoginButton")

            if user_input.is_visible():
                user_input.fill(user)
                pass_input.fill(password)
                login_btn.click()
                page.wait_for_load_state("networkidle")
                print("  ✓ Logged in")
            else:
                print("  ERROR: Login form not found")
                browser.close()
                return
        else:
            print("  ✓ Already logged in")

        # Step 2: Navigate to pool
        page.goto(POOL_URL, timeout=30000)
        page.wait_for_load_state("networkidle")

        # Step 3: Fill predictions across all pages
        total_filled = 0
        current_page = 1
        max_pages = 3

        while current_page <= max_pages:
            filled_on_page = _fill_page(page, predictions)
            total_filled += filled_on_page

            if filled_on_page > 0:
                # Click save
                save_btn = page.locator("#ctl00_ContentPlaceInner_butGuardar")
                if save_btn.is_visible():
                    save_btn.click()
                    page.wait_for_load_state("networkidle")
                    print(f"  ✓ Page {current_page}: saved {filled_on_page} predictions")
                else:
                    print(f"  ⚠️  Page {current_page}: filled {filled_on_page} but save button not found")
            else:
                print(f"  - Page {current_page}: no matches to fill")

            # Navigate to next page
            current_page += 1
            if current_page <= max_pages:
                next_link = page.locator(f"a:has-text('{current_page}')")
                if next_link.is_visible():
                    next_link.click()
                    page.wait_for_load_state("networkidle")
                else:
                    break

        print(f"\n  ✅ Done! Submitted {total_filled} predictions total.")
        browser.close()


def _fill_page(page, predictions: list[MatchPrediction]) -> int:
    """Fill predictions on the current page of golpredictor.

    Args:
        page: Playwright page object.
        predictions: All predictions to attempt matching.

    Returns:
        Number of matches filled on this page.
    """
    # Extract matches from current page
    page_data = page.evaluate("""
        () => {
            const rows = document.querySelectorAll('tr');
            const data = [];
            rows.forEach(r => {
                const cells = r.querySelectorAll('td');
                if (cells.length >= 4) {
                    const match = cells[2]?.textContent?.trim();
                    const inputs = cells[3]?.querySelectorAll('input');
                    if (match && inputs && inputs.length >= 2) {
                        data.push({
                            match: match,
                            homeId: inputs[0]?.id || '',
                            awayId: inputs[1]?.id || '',
                            homeVal: inputs[0]?.value || '',
                            awayVal: inputs[1]?.value || '',
                        });
                    }
                }
            });
            return data;
        }
    """)

    filled = 0
    for pred in predictions:
        # Find best matching row on the page
        best_match = None
        best_score = 0.0

        for pm in page_data:
            score = _fuzzy_match(pred.match_name, pm["match"])
            if score > best_score and score > 0.3:
                best_score = score
                best_match = pm

        if best_match is None:
            continue

        # Skip if already has a value (don't overwrite)
        if best_match["homeVal"] and best_match["awayVal"]:
            continue

        # Fill the inputs
        home_input = page.locator(f"#{best_match['homeId']}")
        away_input = page.locator(f"#{best_match['awayId']}")

        if home_input.is_visible() and away_input.is_visible():
            home_input.fill(str(pred.home_goals))
            away_input.fill(str(pred.away_goals))
            filled += 1

    return filled


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Submit Polla Mundialista predictions to golpredictor.com"
    )
    parser.add_argument(
        "--from", dest="from_date", default=None,
        help="Start date YYYY-MM-DD (default: today)",
    )
    parser.add_argument(
        "--to", dest="to_date", default=None,
        help="End date YYYY-MM-DD (default: from + 6 days)",
    )
    parser.add_argument(
        "--week", action="store_true",
        help="Submit predictions for the next 7 days",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show predictions without submitting",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Handle --week flag
    from_date = args.from_date
    to_date = args.to_date
    if args.week:
        from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    # Generate predictions
    print("Generating predictions from live odds...")
    predictions = generate_predictions(from_date, to_date)

    if not predictions:
        print("No predictions generated for the specified date range.")
        print("(Bookmaker odds may not be available yet)")
        return

    print(f"Generated {len(predictions)} predictions")
    submit_predictions(predictions, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
