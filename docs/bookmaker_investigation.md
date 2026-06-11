# Bookmaker Investigation Report

## Executive Summary

This document investigates 7 world-class bookmakers for automated odds extraction feasibility. Each bookmaker is classified into extraction difficulty tiers and documented with URL patterns, HTML structure analysis, odds formats, and anti-bot measures.

**Tier Classification:**
- **Tier 1** — Simple HTTP scraping (requests + BeautifulSoup): server-rendered HTML, minimal anti-bot
- **Tier 2** — Requires Playwright headless browser: JavaScript-rendered SPA, moderate protection
- **Tier 3** — Heavily protected / infeasible without paid access or specialized tooling

**Result Summary:**

| Bookmaker | Tier | Extraction Method | Odds Format | Feasibility |
|-----------|------|-------------------|-------------|-------------|
| Pinnacle | 2 | Headless Browser | Decimal | Medium |
| Bet365 | 3 | Infeasible (heavy protection) | Fractional/Decimal | Very Low |
| Betfair (Exchange) | 2 | Headless Browser / API | Decimal | Medium-High |
| William Hill | 2 | Headless Browser | Fractional | Medium |
| 1xBet | 1 | HTTP Scraping | Decimal | High |
| DraftKings | 3 | Infeasible (geo-blocked + heavy JS) | American | Low |
| Unibet | 2 | Headless Browser | Decimal | Medium |

**Recommended adapters for initial implementation (3 minimum):**
1. 1xBet (Tier 1 — HTTP)
2. Betfair (Tier 2 — Headless/API)
3. Pinnacle (Tier 2 — Headless)

---

## Detailed Bookmaker Analysis

### 1. Pinnacle

**Website:** https://www.pinnacle.com

**Tier Classification: 2 (Headless Browser Required)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Main | `https://www.pinnacle.com/en/soccer/matchups/` |
| EPL Specific | `https://www.pinnacle.com/en/soccer/england-premier-league/matchups/` |
| La Liga | `https://www.pinnacle.com/en/soccer/spain-la-liga/matchups/` |
| Match Detail | `https://www.pinnacle.com/en/soccer/england-premier-league/{match-slug}/1x2/` |

#### HTML Structure

- **Rendering:** JavaScript SPA (React-based). The initial HTML payload is a minimal shell; odds data is loaded via client-side API calls and rendered into the DOM dynamically.
- **Data Delivery:** Odds data is fetched from internal GraphQL/REST endpoints (`/api/v1/lines/...`) and injected into React component state. The DOM only contains odds after JS execution.
- **Key Observation:** Server-rendered HTML contains no odds values — a headless browser is mandatory.

#### CSS Selectors (Post-JS Rendering)

```css
/* Match container */
div[data-test-id="Matchup"]

/* Team names */
span[data-test-id="participant-name"]

/* 1X2 Odds values */
span[data-test-id="price"] 

/* Market selection tabs */
button[data-test-id="market-tab"]

/* Over/Under market */
div[data-test-id="totals-market"]
```

#### Odds Format

- **Primary:** Decimal (e.g., 2.45, 3.10, 1.85)
- **Configurable:** Users can switch to American/Fractional/Hong Kong/Malay in the UI, but the default API delivers decimal.

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| CloudFlare | Yes — standard CF protection with JS challenge on suspicious requests |
| Rate Limiting | Moderate — API endpoints return 429 after ~30 requests/minute from same IP |
| CAPTCHA | Rarely triggered (mainly on login flows) |
| IP Blocking | Not aggressive, but persistent high-frequency access results in temporary blocks |
| Fingerprinting | Basic browser fingerprint validation (canvas, WebGL headers) |

#### Recommended Countermeasures

1. Use Playwright with realistic viewport and user-agent
2. Rate limit to 1 request per 5 seconds
3. Rotate user agents across sessions
4. Add random delays (jitter ±30%) between page loads
5. Optionally route through residential proxy for production use

---

### 2. Bet365

**Website:** https://www.bet365.com

**Tier Classification: 3 (Heavily Protected — Infeasible)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Main | `https://www.bet365.com/#/AC/B1/C1/D13/E1/F1/` (hash-based routing) |
| In-Play | `https://www.bet365.com/#/IP/` |
| Match Detail | `https://www.bet365.com/#/AC/B1/C1/D13/E{event_id}/F2/` |

#### HTML Structure

- **Rendering:** Entirely JavaScript-rendered custom framework. The site uses hash-based routing (`#/AC/B1/...`) with no server-rendered content.
- **Data Delivery:** All odds data arrives via WebSocket connections (`wss://premws-*.bet365.com`) with proprietary binary/encoded protocol. Standard HTTP requests return empty shells.
- **Key Observation:** Even headless browsers struggle because odds arrive via WebSocket, not standard DOM rendering from XHR.

#### CSS Selectors

```css
/* These selectors are unstable — Bet365 uses obfuscated class names that change frequently */
div.rcl-ParticipantFixtureDetails_TeamNames
div.sgl-ParticipantOddsOnly80_Odds
span.ovm-FixtureDetailsTwoWay_TextWrapper

/* Note: Class names are minified and rotate on deployments */
```

#### Odds Format

- **Primary:** Fractional (e.g., 5/2, 7/4, 1/3) — UK market default
- **Also Available:** Decimal upon user preference toggle

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| CloudFlare Enterprise | Aggressive — includes JS challenge, behavioral analysis, TLS fingerprinting |
| WebSocket Protocol | Proprietary encoded protocol — not standard JSON |
| CAPTCHA | Frequent CAPTCHAs on automated access patterns |
| IP Blocking | Aggressive — blocks datacenter IPs proactively |
| Bot Detection | Advanced behavioral analysis: mouse movements, scroll patterns, timing |
| CSS Obfuscation | Class names are minified/randomized per deployment |
| Session Validation | Requires valid session with geo-matching |
| Geo-Restriction | Blocked in many jurisdictions; requires matching IP/geo |

#### Recommended Countermeasures

**Not recommended for automated extraction.** The combination of WebSocket delivery, aggressive bot detection, CSS obfuscation, and legal restrictions makes Bet365 infeasible for reliable scraping. Use The Odds API as a fallback to obtain Bet365 pricing indirectly.

---

### 3. Betfair (Exchange)

**Website:** https://www.betfair.com

**Tier Classification: 2 (Headless Browser / Public API)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Exchange | `https://www.betfair.com/exchange/plus/football` |
| EPL Markets | `https://www.betfair.com/exchange/plus/football/english-premier-league` |
| Match Detail | `https://www.betfair.com/exchange/plus/football/market/{market_id}` |
| Sportsbook | `https://www.betfair.com/sport/football` |

#### Alternative: Betfair API (Free Tier)

Betfair offers a **free developer API** (requires account registration):
- **Endpoint:** `https://api.betfair.com/exchange/betting/rest/v1.0/`
- **Auth:** Session token (SSOID) obtained via login endpoint
- **Key Endpoints:**
  - `listMarketCatalogue` — discover available markets
  - `listMarketBook` — get current odds/prices
- **Rate Limits:** 12 requests/second for free tier
- **Advantage:** Structured JSON responses, no scraping required

#### HTML Structure

- **Rendering:** React SPA — odds rendered client-side from API data.
- **Data Delivery:** Internal REST endpoints serve JSON which React components render. The exchange UI polls for price updates.
- **Key Observation:** The exchange API is the preferred extraction route — more reliable than scraping the UI.

#### CSS Selectors (Sportsbook Fallback)

```css
/* Runner/selection container */
div.runner-line

/* Odds button */
button.bet-button-price span.bet-button-price

/* Event name */
span.event-name

/* Market type header */
h3.market-name

/* Back/Lay columns (exchange specific) */
td.back-cell span.price
td.lay-cell span.price
```

#### Odds Format

- **Primary:** Decimal (e.g., 2.40, 3.50, 1.72)
- **Exchange:** Always decimal, represents back/lay prices

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| CloudFlare | Yes — standard protection, less aggressive than Bet365 |
| Rate Limiting | Exchange API: 12 req/s; Website: moderate |
| CAPTCHA | Rare on browsing (common on login) |
| IP Blocking | Moderate — datacenter IPs may be blocked after sustained traffic |
| Account Required | API access requires free Betfair account registration |

#### Recommended Countermeasures

1. **Preferred:** Register free Betfair developer account and use Exchange API directly (structured JSON, no scraping)
2. **Fallback:** Headless browser with Playwright for sportsbook pages
3. Rate limit to 10 requests/second for API, 1 per 3 seconds for web
4. User-agent rotation if using web scraping approach

---

### 4. William Hill

**Website:** https://www.williamhill.com

**Tier Classification: 2 (Headless Browser Required)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Main | `https://sports.williamhill.com/betting/en-gb/football` |
| EPL | `https://sports.williamhill.com/betting/en-gb/football/english-premier-league` |
| Match Detail | `https://sports.williamhill.com/betting/en-gb/football/OB_EV{event_id}` |
| Today's Football | `https://sports.williamhill.com/betting/en-gb/football/today` |

#### HTML Structure

- **Rendering:** Hybrid — some structural HTML is server-rendered, but odds values are injected via JavaScript API calls. The initial page load contains match listings but odds values are populated asynchronously.
- **Data Delivery:** REST API endpoints (`/api/v2/...`) serve JSON with odds data; the JS framework then renders them into the DOM.
- **Key Observation:** A headless browser is needed to wait for odds to populate in the DOM after initial page load.

#### CSS Selectors (Post-JS Rendering)

```css
/* Event/match row */
div.sp-o-market__row

/* Team names */
span.sp-o-market__participant-name

/* Odds button */
button.sp-o-market__button

/* Odds value inside button */
span.sp-o-market__button-odds

/* Market header */
h3.sp-o-market__title

/* 1X2 specific market */
div[data-market-type="match-winner"]

/* Over/Under market */
div[data-market-type="total-goals-over-under"]
```

#### Odds Format

- **Primary:** Fractional (e.g., 5/2, 7/4, 1/3) — traditional UK bookmaker
- **Also Available:** Decimal toggle in settings

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| CloudFlare | Yes — standard tier protection |
| Rate Limiting | Moderate — similar to typical e-commerce sites |
| CAPTCHA | Rare during browsing |
| IP Blocking | Low-moderate — datacenter IPs may be flagged after sustained patterns |
| Geo-Restriction | Some markets restricted by jurisdiction |
| Session Cookies | Required for full page rendering |

#### Recommended Countermeasures

1. Use Playwright headless browser with realistic viewport
2. Wait for `span.sp-o-market__button-odds` elements to appear (indicates odds loaded)
3. Rate limit to 1 page per 4 seconds
4. Rotate user agents
5. Parse fractional odds using `parse_odds_value()` converter
6. Handle geo-redirects (may redirect based on IP location)

---

### 5. 1xBet

**Website:** https://1xbet.com

**Tier Classification: 1 (HTTP Scraping Feasible)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Main | `https://1xbet.com/en/line/football/` |
| EPL | `https://1xbet.com/en/line/football/88637-england-premier-league/` |
| La Liga | `https://1xbet.com/en/line/football/127733-spain-la-liga/` |
| Match Detail | `https://1xbet.com/en/line/football/88637-england-premier-league/{event_id}/` |
| Live Odds | `https://1xbet.com/en/live/football/` |

#### Alternative: Public JSON Endpoints

1xBet exposes public endpoints that return structured JSON without authentication:
- **Line endpoint:** `https://1xbet.com/LineFeed/Get1x2_VZip?sports=1&count=50&lng=en&tf=2200000&tz=0`
- **Event details:** `https://1xbet.com/LineFeed/GetGameZip?id={event_id}&lng=en`
- **League events:** `https://1xbet.com/LineFeed/GetChampZip?sport=1&champ={league_id}&lng=en`

These return JSON with odds data directly — **no HTML parsing required**.

#### HTML Structure

- **Rendering:** Server-side rendered with hydration. The initial HTML contains significant structured data including odds values embedded in data attributes and visible text.
- **Data Delivery:** Primary odds data available in server-rendered HTML AND via public JSON feed endpoints (LineFeed API).
- **Key Observation:** The LineFeed JSON API is the most reliable extraction method — returns structured data without needing to parse HTML.

#### CSS Selectors (HTML Scraping Alternative)

```css
/* Event container */
div.c-events__item

/* Team names */
span.c-events__team

/* 1X2 odds buttons */
div.c-bets__bet span.c-bets__inner

/* Odds value */
span.c-bets__price

/* Market group (Match Result / Totals) */
div.c-bets__item[data-market-type]

/* Over/Under */
div.c-bets__bet[data-type="over"] span.c-bets__price
div.c-bets__bet[data-type="under"] span.c-bets__price
```

#### Odds Format

- **Primary:** Decimal (e.g., 2.45, 3.10, 1.85)
- **JSON API:** Always returns decimal odds as float values

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| CloudFlare | Minimal — basic bot screening, no aggressive challenges |
| Rate Limiting | Low — the JSON endpoints handle moderate traffic without throttling |
| CAPTCHA | Very rare (mainly on account actions) |
| IP Blocking | Low — tolerant of automated access patterns |
| Geo-Restriction | Blocked in some jurisdictions but widely accessible |
| Headers Required | Standard browser headers (User-Agent, Accept) sufficient |

#### Recommended Countermeasures

1. **Preferred:** Use LineFeed JSON endpoints directly with `requests` library
2. Set standard browser User-Agent header
3. Rate limit to 1 request per 3 seconds (conservative)
4. Handle JSON responses directly — no BeautifulSoup needed for this path
5. Fallback to HTML scraping with CSS selectors if JSON endpoint changes
6. Rotate user agents as standard precaution

---

### 6. DraftKings

**Website:** https://sportsbook.draftkings.com

**Tier Classification: 3 (Geo-Blocked + Heavy JS — Infeasible for International Use)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Main | `https://sportsbook.draftkings.com/leagues/soccer` |
| EPL | `https://sportsbook.draftkings.com/leagues/soccer/england---premier-league` |
| Match Detail | `https://sportsbook.draftkings.com/event/{event_id}` |

#### HTML Structure

- **Rendering:** Full React SPA — all content loaded via JavaScript.
- **Data Delivery:** Internal API endpoints serve JSON; heavy client-side rendering.
- **Key Observation:** Requires US-based IP address. Most international users cannot access the sportsbook at all.

#### CSS Selectors

```css
/* Event card */
div.sportsbook-event-accordion__wrapper

/* Team names */
th.sportsbook-table__column-row span.event-cell__name

/* Odds */
span.sportsbook-odds

/* Market columns */
th.sportsbook-table__column-header

/* Note: DraftKings uses dynamic class names from CSS modules */
```

#### Odds Format

- **Primary:** American (e.g., +150, -200, +240)
- **US Market Default:** American odds are standard, decimal not commonly displayed

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| Geo-Restriction | **Strict** — US/Canada only (state-by-state restrictions) |
| CloudFlare Enterprise | Aggressive protection |
| Bot Detection | Behavioral analysis, device fingerprinting |
| Rate Limiting | Strict API rate limits |
| CAPTCHA | Common on automated patterns |
| VPN Detection | Actively detects and blocks VPN/proxy usage |

#### Recommended Countermeasures

**Not recommended for extraction.** DraftKings is:
1. Only available in select US states — international users are completely blocked
2. Actively detects and blocks VPN/proxy connections
3. Uses aggressive bot detection
4. Legal restrictions on automated access (US gambling regulations)

Use The Odds API if DraftKings pricing data is needed.

---

### 7. Unibet

**Website:** https://www.unibet.com

**Tier Classification: 2 (Headless Browser Required)**

#### URL Patterns

| Market | URL Pattern |
|--------|------------|
| Soccer Main | `https://www.unibet.com/betting/sports/filter/football` |
| EPL | `https://www.unibet.com/betting/sports/filter/football/england/premier_league` |
| Match Detail | `https://www.unibet.com/betting/sports/filter/football/england/premier_league/{event_slug}` |
| Odds Page | `https://www.unibet.com/betting#/filter/football` (hash routing) |

#### Alternative: Kambi API (Public Endpoints)

Unibet uses the **Kambi** platform as its odds provider. Kambi exposes semi-public offering endpoints:
- **Endpoint Pattern:** `https://eu-offering-api.kambicdn.com/offering/v2018/ub/listView/football/england/premier_league.json`
- **Response:** Structured JSON with events, markets, and odds
- **Auth:** No authentication required — publicly accessible
- **Key Advantage:** Direct JSON with structured odds data, no HTML parsing needed

#### HTML Structure

- **Rendering:** React SPA powered by Kambi sports client. Minimal server-rendered HTML.
- **Data Delivery:** Kambi offering API serves JSON; the React client renders markets.
- **Key Observation:** The Kambi offering API is publicly accessible and returns structured JSON — this is the preferred extraction path.

#### CSS Selectors (Headless Fallback)

```css
/* Event list item */
li.KambiBC-event-item

/* Team names */
div.KambiBC-event-participants__name

/* Odds button */
button.KambiBC-mod-outcome__button

/* Odds value */
span.KambiBC-mod-outcome__odds

/* Market container */
div.KambiBC-collapsible-container

/* 1X2 market */
div[data-market-name="Full Time"]
```

#### Odds Format

- **Primary:** Decimal (e.g., 2.30, 3.45, 1.90)
- **Kambi API:** Returns decimal odds in JSON

#### Anti-Bot Measures

| Measure | Details |
|---------|---------|
| CloudFlare | Standard protection on main website |
| Kambi API | Minimal protection — public CDN endpoints |
| Rate Limiting | Moderate on website; low on Kambi API CDN |
| CAPTCHA | Rare (mainly on login/registration) |
| IP Blocking | Low — Kambi CDN is permissive |
| Geo-Restriction | Some markets restricted by jurisdiction |

#### Recommended Countermeasures

1. **Preferred:** Use Kambi offering API directly via HTTP (`requests` library)
2. Rate limit to 1 request per 3 seconds
3. Standard User-Agent headers sufficient for Kambi CDN
4. Fallback to Playwright headless if Kambi endpoint structure changes
5. Rotate user agents as standard precaution

---

## Tier Summary and Adapter Priority

### Tier 1 — HTTP Scraping (requests + BeautifulSoup/JSON)

| Bookmaker | Extraction Path | Confidence | Priority |
|-----------|----------------|-----------|----------|
| 1xBet | LineFeed JSON API | High | 1 (highest) |
| Unibet (Kambi) | Kambi Offering API | High | 2 |

### Tier 2 — Headless Browser (Playwright)

| Bookmaker | Extraction Path | Confidence | Priority |
|-----------|----------------|-----------|----------|
| Betfair | Exchange API (free) or headless | Medium-High | 3 |
| Pinnacle | Headless + DOM scraping | Medium | 4 |
| William Hill | Headless + wait for odds | Medium | 5 |

### Tier 3 — Infeasible / Not Recommended

| Bookmaker | Reason | Alternative |
|-----------|--------|-------------|
| Bet365 | WebSocket protocol, aggressive bot detection, CSS obfuscation | Use The Odds API |
| DraftKings | US geo-lock, VPN detection, legal restrictions | Use The Odds API |

---

## Recommended Implementation Order

Based on the investigation findings, the following adapter implementation order is recommended:

### Phase 1 — Minimum Viable (3 adapters for threshold)

1. **1xBet Adapter** (Tier 1, HTTP)
   - Uses LineFeed JSON API — most reliable, structured data
   - No HTML parsing needed — direct JSON response
   - Minimal anti-bot measures
   - **File:** `src/adapters/onexbet.py`

2. **Unibet/Kambi Adapter** (Tier 1, HTTP)
   - Uses Kambi offering API — public CDN endpoints
   - Structured JSON responses
   - Low anti-bot resistance
   - **File:** `src/adapters/unibet.py`

3. **Betfair Adapter** (Tier 2, API/Headless)
   - Free developer API available (requires account)
   - Structured JSON via Exchange API
   - Good pricing accuracy (exchange-based)
   - **File:** `src/adapters/betfair.py`

### Phase 2 — Extended Coverage

4. **Pinnacle Adapter** (Tier 2, Headless)
   - Excellent pricing accuracy (sharp bookmaker)
   - Requires Playwright for DOM scraping
   - **File:** `src/adapters/pinnacle.py`

5. **William Hill Adapter** (Tier 2, Headless)
   - Traditional UK bookmaker with fractional odds
   - Requires Playwright for odds loading
   - **File:** `src/adapters/williamhill.py`

### Phase 3 — Fallback

6. **Odds API Adapter** (API, optional)
   - Provides indirect access to Bet365, DraftKings data
   - Requires paid API key
   - **File:** `src/adapters/odds_api.py`

---

## Technical Notes

### Odds Format Conversion

The `HTTPScraper.parse_odds_value()` method must handle:

| Format | Example | Decimal Equivalent | Formula |
|--------|---------|-------------------|---------|
| Decimal | 2.50 | 2.50 | Direct use |
| Fractional | 3/2 | 2.50 | (numerator/denominator) + 1 |
| American (+) | +150 | 2.50 | (american/100) + 1 |
| American (-) | -200 | 1.50 | (100/abs(american)) + 1 |

### Common Soccer League IDs

| League | 1xBet ID | Kambi Path | Sport Key |
|--------|----------|-----------|-----------|
| English Premier League | 88637 | england/premier_league | soccer_epl |
| Spanish La Liga | 127733 | spain/la_liga | soccer_spain_la_liga |
| German Bundesliga | 96463 | germany/bundesliga | soccer_germany_bundesliga |
| Italian Serie A | 110163 | italy/serie_a | soccer_italy_serie_a |
| French Ligue 1 | 12821 | france/ligue_1 | soccer_france_ligue_one |
| UEFA Champions League | 118587 | champions_league | soccer_uefa_champs_league |

### Rate Limiting Configuration

| Bookmaker | Recommended Delay | Max Requests/Minute | Jitter |
|-----------|------------------|--------------------:|--------|
| 1xBet | 3 seconds | 20 | ±30% |
| Unibet/Kambi | 3 seconds | 20 | ±30% |
| Betfair API | 0.1 seconds | 12/sec (720/min) | ±10% |
| Pinnacle | 5 seconds | 10 | ±30% |
| William Hill | 4 seconds | 12 | ±30% |

### User-Agent Pool (Recommended)

All adapters should rotate through realistic browser user-agents:

```
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15
Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36
Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36
Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0
Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:125.0) Gecko/20100101 Firefox/125.0
Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 Edg/124.0.0.0
```

---

## Legal and Ethical Considerations

1. **Terms of Service:** Most bookmakers prohibit automated access in their ToS. This tool is intended for personal research and educational purposes only.
2. **Rate Limiting:** Always respect rate limits to avoid impacting bookmaker services. The configured delays are conservative by design.
3. **No Account Abuse:** The system does not log in to bookmaker accounts or access restricted content.
4. **Public Data Only:** All extraction targets publicly visible odds data that any browser visitor can see.
5. **Robots.txt:** Some bookmakers disallow scraping in robots.txt. Users should be aware of these guidelines.

---

## Revision History

| Date | Author | Changes |
|------|--------|---------|
| 2024-01-01 | Investigation Task | Initial research document |
