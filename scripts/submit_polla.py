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
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")
import unicodedata
from pathlib import Path

LOGIN_URL = "https://www.golpredictor.com/login.aspx"
POOL_URL = (
    "https://www.golpredictor.com/pooldetail.aspx"
    "?pid=0%2cc1ca22da-764b-41b1-8a14-485e9733f23b"
)

# English → Spanish team name translation for golpredictor.com
_NAME_MAP: dict[str, str] = {
    "germany": "alemania",
    "netherlands": "paises bajos",
    "england": "inglaterra",
    "spain": "espana",
    "france": "francia",
    "belgium": "belgica",
    "switzerland": "suiza",
    "sweden": "suecia",
    "portugal": "portugal",
    "argentina": "argentina",
    "brazil": "brasil",
    "usa": "estados unidos",
    "south korea": "corea del sur",
    "south africa": "sudafrica",
    "saudi arabia": "arabia saudita",
    "ivory coast": "costa de marfil",
    "czech republic": "republica checa",
    "new zealand": "nueva zelanda",
    "bosnia & herzegovina": "bosnia-herzegovina",
    "bosnia and herzegovina": "bosnia-herzegovina",
    "dr congo": "rd congo",
    "curacao": "curazao",
    "curaçao": "curazao",
    "cape verde": "cabo verde",
    "iran": "iran",
    "iraq": "irak",
    "qatar": "catar",
    "morocco": "marruecos",
    "tunisia": "tunez",
    "algeria": "argelia",
    "egypt": "egipto",
    "senegal": "senegal",
    "jordan": "jordania",
    "norway": "noruega",
    "croatia": "croacia",
    "turkey": "turquia",
    "scotland": "escocia",
    "haiti": "haiti",
    "panama": "panama",
    "japan": "japon",
    "australia": "australia",
    "paraguay": "paraguay",
    "colombia": "colombia",
    "uzbekistan": "uzbekistan",
    "ghana": "ghana",
    "austria": "austria",
    "mexico": "mexico",
    "canada": "canada",
    "uruguay": "uruguay",
}


def normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def translate_to_spanish(name: str) -> str:
    """Translate English team names to Spanish equivalents for golpredictor."""
    norm = normalize(name)
    if norm in _NAME_MAP:
        return _NAME_MAP[norm]
    # Try splitting "Home - Away" and translating each part
    parts = norm.replace(" - ", "|").replace(" vs ", "|").split("|")
    translated = []
    for part in parts:
        part = part.strip()
        translated.append(_NAME_MAP.get(part, part))
    return " - ".join(translated)


def fuzzy_match(a: str, b: str) -> float:
    """Jaccard similarity with English→Spanish translation."""
    # Translate each team in the match name
    parts = a.replace(" - ", "|").replace(" vs ", "|").split("|")
    a_translated = " ".join(translate_to_spanish(p.strip()) for p in parts)
    a_norm = normalize(a_translated).replace("-", " ")
    b_norm = normalize(b).replace("-", " ")
    sa = set(a_norm.split())
    sb = set(b_norm.split())
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
    matched_page_ids = set()

    for pred in predictions:
        best = None
        best_score = 0.0
        for pm in page_data:
            if pm["homeId"] in matched_page_ids:
                continue
            score = fuzzy_match(pred["match"], pm["match"])
            if score > best_score and score > 0.3:
                best_score = score
                best = pm

        if best is None:
            remaining.append(pred)
            print(f"    ⚠ NOT FOUND on page: {pred['match']}")
            continue

        matched_page_ids.add(best["homeId"])

        # Check if already filled with same value
        if best["homeVal"] and best["awayVal"]:
            if str(pred["home"]) == best["homeVal"] and str(pred["away"]) == best["awayVal"]:
                print(f"    ✓ ALREADY CORRECT: {best['match']} = {pred['home']}-{pred['away']}")
                continue
            # Different value — overwrite
            print(f"    ↻ UPDATING: {best['match']}: {best['homeVal']}-{best['awayVal']} → {pred['home']}-{pred['away']}")

        home_el = page.locator(f"#{best['homeId']}")
        away_el = page.locator(f"#{best['awayId']}")
        if home_el.is_visible() and away_el.is_visible():
            home_el.fill(str(pred["home"]))
            away_el.fill(str(pred["away"]))
            if not best["homeVal"] and not best["awayVal"]:
                print(f"    ✚ NEW: {best['match']}: {pred['home']}-{pred['away']}")
            filled += 1
        else:
            remaining.append(pred)
            print(f"    🔒 LOCKED: {best['match']} (match already started)")

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
