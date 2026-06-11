"""Playwright automation script to submit Polla predictions to golpredictor.com.

This is a standalone script — NOT part of the src/ package.
It calls the prediction model, then uses Playwright to fill and save.

Environment Variables:
    GOLPREDICTOR_USER: Login username
    GOLPREDICTOR_PASS: Login password

Usage:
    # Dry run (no browser)
    uv run python scripts/submit_polla.py --dry-run

    # Submit today
    uv run python scripts/submit_polla.py

    # Submit a week
    uv run python scripts/submit_polla.py --from 2026-06-12 --to 2026-06-17

    # Submit full week from today
    uv run python scripts/submit_polla.py --week
"""
from __future__ import annotations

import argparse
import os
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root to path so we can import src.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ──────────────────────────────────────────────────────────────────────────────
# Page Map — golpredictor.com
# ──────────────────────────────────────────────────────────────────────────────
#
# LOGIN: https://www.golpredictor.com/login.aspx
#   Fields (fallback order):
#     1. input[type="text"] (first one) → username
#     2. input[type="password"] (or second textbox) → password
#   Submit button: #ctl00_ContentPlaceInner_btnLogin (input type="image")
#
# POOL: https://www.golpredictor.com/pooldetail.aspx?pid=0%2cc1ca22da-764b-41b1-8a14-485e9733f23b
#   Match table (paginated ~24/page):
#     Row structure:
#       cells[0] = Id
#       cells[1] = Date/Time (e.g. "12 Jun - 14:00")
#       cells[2] = Match name (e.g. "Canadá - Bosnia-Herzegovina")
#       cells[3] = Prediction inputs (or "-" if locked):
#         input[name*="txtGolLocal"] → home goals
#         input[name*="txtGolVisitante"] → away goals
#   Save: #ctl00_ContentPlaceInner_butGuardar (input type="image")
#   Pagination: links "2", "3" trigger __doPostBack
#
# IMPORTANT: Must click GUARDAR per page.
# ──────────────────────────────────────────────────────────────────────────────

LOGIN_URL = "https://www.golpredictor.com/login.aspx"
POOL_URL = (
    "https://www.golpredictor.com/pooldetail.aspx"
    "?pid=0%2cc1ca22da-764b-41b1-8a14-485e9733f23b"
)


def generate_predictions(from_date: str | None, to_date: str | None) -> list[dict]:
    """Run the prediction model. Returns list of {match_name, home, away, expected_points, date}."""
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
    cache = CacheStore(db_path=os.path.join(tempfile.gettempdir(), "polla_submit.db"))
    extractor = OddsExtractor(registry=registry, cache_store=cache)
    extractor.MIN_BOOKMAKERS = 1

    me = MarginEliminator(method="shin")
    lo = LambdaOptimizer()
    smg = ScoreMatrixGenerator()
    polla = PollaScorer(is_knockout=False)

    events = extractor.extract("soccer_world_cup")

    start = datetime.strptime(from_date, "%Y-%m-%d").date() if from_date else datetime.now(timezone.utc).date()
    end = datetime.strptime(to_date, "%Y-%m-%d").date() if to_date else start + timedelta(days=6)

    predictions = []
    for event in events:
        if event.event_timestamp.date() < start or event.event_timestamp.date() > end:
            continue
        odds_list = list(event.bookmaker_odds_1x2.values())
        if not odds_list:
            continue
        try:
            real_probs = me.eliminate(odds_list[0])
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
        predictions.append({
            "match_name": f"{event.home_team} - {event.away_team}",
            "home": rec.predicted_score[0],
            "away": rec.predicted_score[1],
            "expected_points": rec.expected_points,
            "date": event.event_timestamp.strftime("%d %b"),
        })

    return predictions


def normalize(name: str) -> str:
    """Strip accents, lowercase."""
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def fuzzy_match(a: str, b: str) -> float:
    """Jaccard similarity on tokens."""
    sa = set(normalize(a).replace("-", " ").split())
    sb = set(normalize(b).replace("-", " ").split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def run_submission(predictions: list[dict]) -> None:
    """Open browser in incognito, login, fill predictions, save."""
    from playwright.sync_api import sync_playwright

    user = os.environ.get("GOLPREDICTOR_USER")
    password = os.environ.get("GOLPREDICTOR_PASS")
    if not user or not password:
        print("ERROR: Set GOLPREDICTOR_USER and GOLPREDICTOR_PASS env vars.")
        sys.exit(1)

    print("\n🏆 Opening browser (incognito)...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()  # Incognito — fresh session, no cookies
        page = context.new_page()
        page.set_default_timeout(15000)

        # ── LOGIN ──
        print("  Navigating to login...")
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # Fill login form — the main content area form
        # Username field: last text input before the password
        # Password field: only password-type input on the page
        user_field = page.locator("#ctl00_ContentPlaceInner_Login1_UserName, input[name*='ContentPlaceInner'][type='text']").first
        pass_field = page.locator("#ctl00_ContentPlaceInner_Login1_Password, input[name*='ContentPlaceInner'][type='password']").first

        if user_field.count() == 0:
            # Broader fallback
            user_field = page.locator("input[type='text']").last
            pass_field = page.locator("input[type='password']").first

        user_field.fill(user)
        pass_field.fill(password)

        # Click the content area login button
        login_btn = page.locator("#ctl00_ContentPlaceInner_btnLogin, #ctl00_ContentPlaceInner_Login1_LoginButton").first
        if login_btn.count() == 0:
            login_btn = page.locator("input[type='image']").last
        login_btn.click()
        page.wait_for_timeout(5000)

        # Check login success (might redirect to home)
        content = page.content()
        if "Bienvenido" in content or "home.aspx" in page.url or "myaccount" in page.url:
            print("  ✓ Logged in")
        elif "login" in page.url.lower():
            print("  ERROR: Login failed. Still on login page.")
            context.close()
            browser.close()
            return
        else:
            # Try navigating to pool directly — session might be valid
            print("  ⚠️  Uncertain login state, trying pool page...")

        # ── NAVIGATE TO POOL ──
        print("  Navigating to AMWELL pool...")
        page.goto(POOL_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # ── FILL & SAVE (per page) ──
        total_filled = 0
        current_page = 1

        while True:
            filled = fill_page(page, predictions)
            total_filled += filled

            if filled > 0:
                save_btn = page.locator("#ctl00_ContentPlaceInner_butGuardar")
                if save_btn.count() > 0 and save_btn.is_visible():
                    save_btn.click()
                    page.wait_for_timeout(3000)
                    print(f"  ✓ Page {current_page}: saved {filled} predictions")
                else:
                    print(f"  ⚠️  Page {current_page}: filled {filled} but no save button")
            else:
                print(f"  - Page {current_page}: nothing new to fill")

            # Try next page
            current_page += 1
            next_link = page.locator(f"a:text-is('{current_page}')")
            if next_link.count() > 0 and next_link.is_visible():
                next_link.click()
                page.wait_for_timeout(2000)
            else:
                break

        print(f"\n  ✅ Done! {total_filled} predictions submitted across {current_page - 1} pages.")
        context.close()
        browser.close()


def fill_page(page, predictions: list[dict]) -> int:
    """Fill empty prediction fields on the current page. Returns count filled."""
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
        best = None
        best_score = 0.0
        for pm in page_data:
            score = fuzzy_match(pred["match_name"], pm["match"])
            if score > best_score and score > 0.3:
                best_score = score
                best = pm

        if best is None:
            continue

        # Skip if already filled
        if best["homeVal"] and best["awayVal"]:
            continue

        # Fill
        home_el = page.locator(f"#{best['homeId']}")
        away_el = page.locator(f"#{best['awayId']}")
        if home_el.is_visible() and away_el.is_visible():
            home_el.fill(str(pred["home"]))
            away_el.fill(str(pred["away"]))
            print(f"    {best['match']}: {pred['home']}-{pred['away']} (E[pts]={pred['expected_points']:.2f})")
            filled += 1

    return filled


def main():
    parser = argparse.ArgumentParser(description="Submit Polla predictions")
    parser.add_argument("--from", dest="from_date", default=None)
    parser.add_argument("--to", dest="to_date", default=None)
    parser.add_argument("--week", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    from_date = args.from_date
    to_date = args.to_date
    if args.week:
        from_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        to_date = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d")

    print("Generating predictions...")
    predictions = generate_predictions(from_date, to_date)

    if not predictions:
        print("No predictions available for this date range.")
        return

    print(f"Got {len(predictions)} predictions:\n")
    for p in predictions:
        print(f"  {p['date']} | {p['match_name']}: {p['home']}-{p['away']} (E[pts]={p['expected_points']:.2f})")

    if args.dry_run:
        print("\n🔍 Dry run — nothing submitted.")
        return

    run_submission(predictions)


if __name__ == "__main__":
    main()
