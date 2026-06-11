# 🏆 Polla Mundialista — World Cup Score Predictor

A Python CLI tool that scrapes live betting odds from world-class bookmakers, reverse-engineers Expected Goals (xG) using a Poisson model, and picks the **optimal score prediction** that maximizes your expected Polla Mundialista points.

## How It Works

```
Bookmaker Odds → Margin Removal → xG Calculation → Score Matrix → Polla Optimizer
     (live)         (Shin method)    (Poisson fit)     (6×6 grid)    (max E[points])
```

1. **Scrapes live odds** from Kambi/Unibet public API (no auth needed)
2. **Removes the bookmaker's commission** (overround) using the Shin method to get true win/draw/loss probabilities
3. **Extracts all goal lines** — Over/Under at 0.5, 1.5, 2.5, 3.5, 4.5 + team-specific totals
4. **Reverse-engineers Expected Goals** (λ=home xG, μ=away xG) by fitting a Poisson distribution to match the bookmaker's implied probabilities
5. **Builds a 6×6 score probability matrix** — probability of every scoreline from 0-0 to 5-5
6. **Optimizes for Polla points** — evaluates all 36 possible predictions and picks the one with highest expected Polla score (not just most probable result)

### Why "Polla Optimizer" beats "Most Probable Score"

The Polla Mundialista awards partial points:
- Correct result (win/draw): **5 pts**
- Correct home goals: **2 pts**
- Correct away goals: **2 pts**
- Correct goal difference: **1 pt**

A prediction of **2-0** when the true result is **3-1** still earns 6 points (correct winner + correct difference). The optimizer accounts for all possible outcomes weighted by their probability, finding the score that maximizes your **expected total points** across the entire probability distribution.

## Quick Start

### Prerequisites

- Python 3.9+ (tested on 3.14)
- [uv](https://docs.astral.sh/uv/) package manager

### Install

```bash
# Clone the repo
git clone <repo-url>
cd betting-house-reading

# Install dependencies (uv handles the virtual environment)
uv sync
```

### Run — Get Today's Predictions

```bash
# World Cup predictions optimized for Polla scoring
uv run python -m src.main --sport soccer_world_cup --polla

# Specific date
uv run python -m src.main --sport soccer_world_cup --polla --date 2026-06-12

# Knockout stage (doubles all point values)
uv run python -m src.main --sport soccer_world_cup --polla --knockout
```

### Example Output

```
========================================================================
  POLLA MUNDIALISTA — Optimal Predictions (GROUP STAGE)
========================================================================
Match                               Optimal  E[Pts]  Most Prob  Prob%
------------------------------------------------------------------------
Mexico vs South Africa                  1-0    5.17        1-0  16.5%
South Korea vs Czech Republic           0-0    3.72        0-0  22.0%
Canada vs Bosnia & Herzegovina          1-0    4.56        1-0  25.1%
========================================================================
```

## All CLI Commands

```bash
# Polla predictions (main use case)
uv run python -m src.main --sport soccer_world_cup --polla

# Full pipeline output (probabilities, xG, score matrix, quality flags)
uv run python -m src.main --sport soccer_world_cup

# List available data adapters and their health
uv run python -m src.main --sport soccer_world_cup --list-adapters

# Value bet detection (for real money betting — finds mispriced odds)
uv run python -m src.main --sport soccer_world_cup --value-bets

# Retro-feedback (compare predictions to actual results after matchday)
uv run python -m src.main --sport soccer_world_cup --retro 2026-06-11

# Bookmaker reliability report
uv run python -m src.main --sport soccer_world_cup --bookmaker-report
```

### Supported Leagues

| Sport Key | League |
|-----------|--------|
| `soccer_world_cup` | FIFA World Cup 2026 |
| `soccer_epl` | English Premier League |
| `soccer_spain_la_liga` | Spanish La Liga |
| `soccer_germany_bundesliga` | German Bundesliga |
| `soccer_italy_serie_a` | Italian Serie A |
| `soccer_france_ligue_one` | French Ligue 1 |
| `soccer_uefa_champs_league` | UEFA Champions League |

## Architecture

```
src/
├── main.py                  # CLI entry point
├── polla_scorer.py          # Polla points optimizer
├── adapters/
│   ├── base.py              # Abstract adapter interface
│   ├── registry.py          # Auto-discovery registry
│   ├── unibet.py            # Kambi/Unibet adapter (live, working)
│   ├── onexbet.py           # 1xBet adapter
│   ├── betfair.py           # Betfair adapter
│   └── odds_api.py          # The Odds API fallback (needs key)
├── scraping/
│   ├── resilience.py        # Rate limiter, UA rotation, retry logic
│   ├── http_scraper.py      # requests + BeautifulSoup
│   └── headless.py          # Optional Playwright browser
├── odds_extractor.py        # Multi-source orchestrator
├── margin_eliminator.py     # Shin method overround removal
├── lambda_optimizer.py      # Poisson xG reverse-engineering
├── score_matrix.py          # 6×6 probability grid
├── data_quality.py          # Variance/margin flags
├── cache_store.py           # SQLite caching (24h TTL)
├── divergence.py            # Primary vs secondary xG comparison
├── value_detector.py        # Sharp vs soft bookmaker edge detection
├── retro_feedback.py        # Prediction accuracy tracking
├── bookmaker_scorer.py      # Bookmaker reliability scoring
└── output.py                # Terminal table rendering
```

## How the Math Works

### Step 1: True Probabilities (Margin Elimination)

Raw odds: Mexico 1.40, Draw 4.60, S. Africa 8.50
→ Implied probs sum to 104.9% (the 4.9% is the bookmaker's profit)
→ Shin method strips the overround → True: Mexico 69%, Draw 20%, S. Africa 11%

### Step 2: Expected Goals (Poisson Fitting)

The optimizer finds λ (home xG) and μ (away xG) that best explain the probabilities under a bivariate Poisson model. With multi-line data (O/U 0.5, 1.5, 2.5, 3.5, 4.5 + team totals), it has 10+ constraint points instead of just 2.

### Step 3: Score Matrix

For each scoreline (i, j): P(home=i) × P(away=j) using Poisson PMF
→ Produces a 6×6 grid of 36 probabilities

### Step 4: Polla Optimization

For each candidate prediction, compute:
```
E[points] = Σᵢ Σⱼ P(home=i, away=j) × polla_points(prediction, actual=(i,j))
```
Pick the prediction with the highest expected points.

## Development

```bash
# Run tests
uv run pytest tests/ -v

# Run a specific test file
uv run pytest tests/test_polla_scorer.py -v

# Current test count: 434 passing
```

### Adding a New Bookmaker

Create a file `src/adapters/your_bookmaker.py` implementing `OddsAdapter`:

```python
from src.adapters.base import OddsAdapter, ExtractionMethod, AdapterHealth, ScrapedMatch

class YourBookmakerAdapter(OddsAdapter):
    @property
    def bookmaker_id(self) -> str: return "your_bm"
    @property
    def bookmaker_name(self) -> str: return "Your Bookmaker"
    @property
    def priority(self) -> int: return 5
    @property
    def extraction_method(self) -> ExtractionMethod: return ExtractionMethod.HTTP
    @property
    def base_url(self) -> str: return "https://yourbookmaker.com"
    
    def fetch_1x2(self, sport, date=None) -> list[ScrapedMatch]: ...
    def fetch_over_under(self, sport, date=None) -> list[ScrapedMatch]: ...
    def health_check(self) -> AdapterHealth: ...
```

The registry auto-discovers it on next run — no other files need modification.

## Configuration

| Environment Variable | Purpose | Required |
|---------------------|---------|----------|
| `ODDS_API_KEY` | The Odds API key (free tier: 500 req/month) | No |
| `SCRAPER_PROXY` | HTTP proxy for scraping requests | No |

## Polla Mundialista Scoring Rules

| Category | Group Stage | Knockout |
|----------|:-:|:-:|
| Correct result (win/draw) | 5 pts | 10 pts |
| Correct home goals | 2 pts | 4 pts |
| Correct away goals | 2 pts | 4 pts |
| Correct goal difference | 1 pt | 2 pts |
| **Maximum per match** | **10 pts** | **20 pts** |

Only 90 minutes + stoppage time counts. No extra time, no penalties.

## License

Internal project — Polla Mundialista office pool tool.
