"""Playwright script to submit predictions to golpredictor.com.

Reads predictions from a JSON file and fills them into the website.
Does NOT generate predictions — that's done separately by the main pipeline.

Input file format (predictions.json):
[
    {"match": "México - Sudáfrica", "home": 1, "away": 0},
    {"match": "Corea del Sur - República Checa", "home": 0, "away": 0},
    ...
]

Environment Variables:
    GOLPREDICTOR_USER: Login username
    GOLPREDICTOR_PASS: Login password

Usage:
    uv run python scripts/submit_polla.py predictions.json
    uv run python scripts/submit_polla.py output/week1.json
"""
from __future__ import annotations

import json
import os
import sys
import unicodedata
from pathlib import Path

LOGIN_URL = "https://www.golpredictor.com/login.aspx"
POOL_URL = (
    "https://www.golpredictor.com/pooldetail.aspx"
    "?pid=0%2cc1ca22da-764b-41b1-8a14-485e9733f23b"
)


def normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def fuzzy_match(a: str, b: str) -> float:
    sa = set(normalize(a).replace("-", " ").split())
    sb = set(normalize(b).replace("-", " ").split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def fill_page(page, predictions: list[dict]) -> tuple[int, list[dict]]:
    """Fill prediction fields on the current page for matches in the JSON only.
    
    Returns:
        Tuple of (number filled, remaining predictions not found on this page).
    """
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
    remaining = []

    for pred in predictions:
        best = None
        best_score = 0.0
        for pm in page_data:
            score = fuzzy_match(pred["match"], pm["match"])
            if score > best_score and score > 0.3:
                best_score = score
                best = pm

        if best is None:
            remaining.append(pred)
            continue

        # Skip already filled
        if best["homeVal"] and best["awayVal"]:
            continue

        home_el = page.locator(f"#{best['homeId']}")
        away_el = page.locator(f"#{best['awayId']}")
        if home_el.is_visible() and away_el.is_visible():
            home_el.fill(str(pred["home"]))
            away_el.fill(str(pred["away"]))
            print(f"    {best['match']}: {pred['home']}-{pred['away']}")
            filled += 1
        else:
            remaining.append(pred)

    return filled, remaining


def main():
    if len(sys.argv) < 2:
        print("Usage: uv run python scripts/submit_polla.py <predictions.json>")
        print("\nGenerate predictions first:")
        print("  uv run python -m src.main --sport soccer_world_cup --polla --output predictions.json")
        sys.exit(1)

    predictions_file = sys.argv[1]
    if not Path(predictions_file).exists():
        print(f"ERROR: File not found: {predictions_file}")
        sys.exit(1)

    with open(predictions_file) as f:
        predictions = json.load(f)

    print(f"Loaded {len(predictions)} predictions from {predictions_file}")

    user = os.environ.get("GOLPREDICTOR_USER")
    password = os.environ.get("GOLPREDICTOR_PASS")
    if not user or not password:
        print("ERROR: Set GOLPREDICTOR_USER and GOLPREDICTOR_PASS env vars.")
        sys.exit(1)

    from playwright.sync_api import sync_playwright

    print("\n🏆 Opening browser (incognito)...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context()
        page = context.new_page()
        page.set_default_timeout(15000)

        # LOGIN
        page.goto(LOGIN_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        user_field = page.locator(
            "#ctl00_ContentPlaceInner_Login1_UserName, "
            "input[name*='ContentPlaceInner'][type='text']"
        ).first
        pass_field = page.locator(
            "#ctl00_ContentPlaceInner_Login1_Password, "
            "input[name*='ContentPlaceInner'][type='password']"
        ).first

        if user_field.count() == 0:
            user_field = page.locator("input[type='text']").last
            pass_field = page.locator("input[type='password']").first

        user_field.fill(user)
        pass_field.fill(password)
        page.locator(
            "#ctl00_ContentPlaceInner_btnLogin, "
            "#ctl00_ContentPlaceInner_Login1_LoginButton"
        ).first.click()
        page.wait_for_timeout(5000)

        if "login" in page.url.lower() and "Bienvenido" not in page.content():
            print("  ERROR: Login failed.")
            context.close()
            browser.close()
            sys.exit(1)
        print("  ✓ Logged in")

        # NAVIGATE TO POOL
        page.goto(POOL_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(2000)

        # FILL & SAVE PER PAGE
        total = 0
        current_page = 1
        remaining = list(predictions)  # Only fill what's in the JSON

        while remaining:
            filled, remaining = fill_page(page, remaining)
            total += filled

            if filled > 0:
                save = page.locator("#ctl00_ContentPlaceInner_butGuardar")
                if save.count() > 0 and save.is_visible():
                    save.click()
                    page.wait_for_timeout(3000)
                    print(f"  ✓ Page {current_page}: saved {filled}")

            # If no predictions left to match, stop
            if not remaining:
                break

            current_page += 1
            next_link = page.locator(f"a:text-is('{current_page}')")
            if next_link.count() > 0 and next_link.is_visible():
                next_link.click()
                page.wait_for_timeout(2000)
            else:
                break

        print(f"\n  ✅ Done! {total} predictions submitted.")
        context.close()
        browser.close()


if __name__ == "__main__":
    main()
