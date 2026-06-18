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
# Each team has multiple aliases (English, Spanish, abbreviations, common variants)
# to ensure fuzzy matching works regardless of source format.
_TEAM_ALIASES: dict[str, list[str]] = {
    "alemania": ["germany", "alemania", "deutschland", "ger"],
    "paises bajos": ["netherlands", "paises bajos", "holanda", "holland", "ned", "paises"],
    "inglaterra": ["england", "inglaterra", "eng"],
    "espana": ["spain", "espana", "españa", "esp"],
    "francia": ["france", "francia", "fra"],
    "belgica": ["belgium", "belgica", "bélgica", "bel"],
    "suiza": ["switzerland", "suiza", "sui", "schweiz"],
    "suecia": ["sweden", "suecia", "swe"],
    "portugal": ["portugal", "por"],
    "argentina": ["argentina", "arg"],
    "brasil": ["brazil", "brasil", "bra"],
    "estados unidos": ["usa", "estados unidos", "united states", "us", "eeuu"],
    "corea del sur": ["south korea", "corea del sur", "korea republic", "korea", "kor"],
    "sudafrica": ["south africa", "sudafrica", "sudáfrica", "rsa"],
    "arabia saudita": ["saudi arabia", "arabia saudita", "ksa", "saudi"],
    "costa de marfil": ["ivory coast", "costa de marfil", "cote d'ivoire", "cote divoire", "civ"],
    "republica checa": ["czech republic", "republica checa", "república checa", "czechia", "cze"],
    "nueva zelanda": ["new zealand", "nueva zelanda", "nzl"],
    "bosnia-herzegovina": ["bosnia & herzegovina", "bosnia and herzegovina", "bosnia-herzegovina", "bosnia", "bih"],
    "rd congo": ["dr congo", "rd congo", "congo dr", "congo", "cod"],
    "curazao": ["curacao", "curaçao", "curazao", "cuw"],
    "cabo verde": ["cape verde", "cabo verde", "cpv"],
    "iran": ["iran", "irán", "iri"],
    "irak": ["iraq", "irak", "irq"],
    "catar": ["qatar", "catar", "qat"],
    "marruecos": ["morocco", "marruecos", "mar"],
    "tunez": ["tunisia", "tunez", "túnez", "tun"],
    "argelia": ["algeria", "argelia", "alg"],
    "egipto": ["egypt", "egipto", "egy"],
    "senegal": ["senegal", "sen"],
    "jordania": ["jordan", "jordania", "jor"],
    "noruega": ["norway", "noruega", "nor"],
    "croacia": ["croatia", "croacia", "cro"],
    "turquia": ["turkey", "turquia", "turquía", "tur"],
    "escocia": ["scotland", "escocia", "sco"],
    "haiti": ["haiti", "haití", "hai"],
    "panama": ["panama", "panamá", "pan"],
    "japon": ["japan", "japon", "japón", "jpn"],
    "australia": ["australia", "aus"],
    "paraguay": ["paraguay", "par"],
    "colombia": ["colombia", "col"],
    "uzbekistan": ["uzbekistan", "uzbekistán", "uzb"],
    "ghana": ["ghana", "gha"],
    "austria": ["austria", "aut"],
    "mexico": ["mexico", "méxico", "mex"],
    "canada": ["canada", "canadá", "can"],
    "uruguay": ["uruguay", "uru"],
}

# Build reverse lookup: any alias → canonical spanish name
_ALIAS_TO_CANONICAL: dict[str, str] = {}
for canonical, aliases in _TEAM_ALIASES.items():
    for alias in aliases:
        _ALIAS_TO_CANONICAL[alias.lower()] = canonical


def normalize(name: str) -> str:
    nfkd = unicodedata.normalize("NFKD", name)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _canonicalize_team(name: str) -> str:
    """Convert any team name variant to its canonical Spanish form."""
    norm = normalize(name)
    # Direct lookup
    if norm in _ALIAS_TO_CANONICAL:
        return _ALIAS_TO_CANONICAL[norm]
    # Try partial match (for cases like "Ivory d Coast" or typos)
    for alias, canonical in _ALIAS_TO_CANONICAL.items():
        if alias in norm or norm in alias:
            return canonical
    return norm


def translate_to_spanish(name: str) -> str:
    """Translate English team names to Spanish equivalents for golpredictor."""
    return _canonicalize_team(name)


def fuzzy_match(a: str, b: str) -> float:
    """Match prediction name against page name using canonical team names.

    Both sides are canonicalized to Spanish, then compared with Jaccard similarity.
    This handles: English vs Spanish, accents, abbreviations, and variant spellings.
    """
    # Split into team names and canonicalize each
    parts_a = a.replace(" - ", "|").replace(" vs ", "|").split("|")
    parts_b = b.replace(" - ", "|").replace(" vs ", "|").split("|")

    canon_a = set()
    for part in parts_a:
        canon_a.add(_canonicalize_team(part.strip()))

    canon_b = set()
    for part in parts_b:
        canon_b.add(_canonicalize_team(part.strip()))

    # If both teams match exactly, perfect score
    if canon_a == canon_b:
        return 1.0

    # Both teams must be present for a valid match
    if len(canon_a) >= 2 and len(canon_b) >= 2:
        if canon_a == canon_b:
            return 1.0
        # Check if both teams from prediction appear in the page match
        overlap = len(canon_a & canon_b)
        if overlap >= 2:
            return 1.0
        # Only ONE team matches — NOT a valid match (prevents Haiti-Scotland matching Brasil-Haiti)
        if overlap <= 1:
            return 0.0

    # Single team entries (edge case)
    overlap = len(canon_a & canon_b)
    total = len(canon_a | canon_b)
    if total == 0:
        return 0.0
    return overlap / total


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
            # Different value — update it (match hasn't been played yet if inputs are visible)
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
