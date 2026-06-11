---
inclusion: always
---

# Project Standards — Betting Odds Calculator

## Environment

- **Package manager**: `uv` (use `uv pip install`, `uv venv`, `uv run`)
- **Python version**: 3.9+ (target compatibility), runtime is 3.14
- **Virtual environment**: `.venv/` — always run tests and scripts via `uv run` or activate first
- **Run tests**: `uv run pytest tests/ -v`
- **Run the tool**: `uv run python -m src.main --sport soccer_epl --date 2024-06-01`

## Git Workflow

- Branch: `main` for stable code
- Commit after each meaningful unit of work (task completion, bug fix)
- Commit messages follow Conventional Commits: `feat:`, `fix:`, `test:`, `refactor:`, `docs:`
- Never commit `.env`, `*.db`, or `.venv/`

## Code Style

- Type annotations on all function signatures
- Docstrings on all public classes and methods (Google style)
- Use `from __future__ import annotations` for modern type syntax
- Dataclasses for data containers, not plain dicts
- Custom exceptions for domain errors (e.g., `AnomalousOddsError`, `OddsExtractionError`)
- No qualitative text in output — strictly numeric values

## Testing

- Unit tests in `tests/test_<module>.py`
- Property-based tests use `hypothesis` library
- Integration tests in `tests/test_integration.py`
- All tests must pass before committing: `uv run pytest tests/ -v`
- Use `pytest.approx()` for floating-point comparisons
- Use `tmp_path` fixture for SQLite tests (no leftover DB files)

## Dependencies

- **Runtime only**: `requests`, `scipy`, `numpy` (+ stdlib)
- **Dev only**: `hypothesis`, `pytest`
- No other third-party packages allowed (Requirement 11.5)
- If a new dependency is needed, discuss first

## Architecture

- Single entry point: `src/main.py`
- Modules are independent and testable in isolation
- Pipeline flow: OddsExtractor → MarginEliminator → LambdaOptimizer → ScoreMatrixGenerator → DataQualityAnalyzer → TerminalOutput
- SQLite is the sole persistence layer (no external DB)
- Cache-first strategy for API calls to conserve credits

## API Usage

- Data source: The Odds API v4 (https://the-odds-api.com)
- API key stored in `ODDS_API_KEY` environment variable
- Never hardcode API keys or commit them
- 30-second timeout on all HTTP requests
- Cache all raw responses in SQLite before processing
- Default cache TTL: 24 hours (configurable 1-48h)

## File References

- Requirements: #[[file:.kiro/specs/betting-odds-calculator/requirements.md]]
- Design: #[[file:.kiro/specs/betting-odds-calculator/design.md]]
- Tasks: #[[file:.kiro/specs/betting-odds-calculator/tasks.md]]
