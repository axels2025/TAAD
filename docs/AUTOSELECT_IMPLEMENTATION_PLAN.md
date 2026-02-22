# Automated Candidate Selection — Implementation Plan

**Created:** 2026-02-21
**Last Updated:** 2026-02-21
**Status:** Planned
**Goal:** Automate the end-to-end process of finding, scoring, sizing, and staging naked put trade candidates using live market data

---

## Design Philosophy

> "If I had to say what's more important it's keeping my money — so reducing risk is above the premium collected."

This system prioritizes **capital preservation** over premium maximization. The scoring, ranking, and selection logic all reflect this: lowest acceptable delta first, liquidity second (can I exit?), AI qualitative assessment third, margin efficiency last.

**Core principles:**
- **Live data only** — scanning and selection must use real-time IBKR data. No fallbacks to defaults or estimates for critical decisions like margin.
- **No IBKR, no trade** — if we can't confirm margin from IBKR, we don't stage. Over-extending is worse than missing a trade.
- **Market-open execution** — best trades are found with live data. Both semi-auto and full-auto run at market open.
- **Conservative start** — 20% of NLV margin budget. Increase as confidence grows through paper trading results.

---

## Automation Levels

| Level | Name | Description | User Effort |
|-------|------|-------------|-------------|
| **A** | Assisted | Scanner suggests quantity + highlights best strikes; user clicks each | Manual per stock |
| **B** | Semi-Auto | "Auto-Select Best" button runs full pipeline during market hours; user reviews | One click + review |
| **C** | Full Auto | Daemon scans + selects + executes at market open; user reviews results afterward | Zero clicks |

**Implementation order:** B first (Phases 1-3), then C (Phase 4).

**Override mode:** Both B and C include an override button to test the pipeline during closed markets using last-available data, with a clear "STALE DATA" warning. This is for development and testing only.

---

## Phase 1: Foundation — Settings, Quantity, and Budget Display

**Goal:** Make the scanner aware of account constraints and add missing UI controls.
**Effort:** 2-3 days

### 1.1 Scanner Settings Panel

Add a collapsible "Settings" panel at the top of the Scanner page with these configurable parameters:

**Strike Selection:**
| Setting | Default | Range | Description |
|---------|---------|-------|-------------|
| Delta Min | 0.05 | 0.01-0.50 | Minimum delta to consider |
| Delta Max | 0.30 | 0.05-0.50 | Maximum delta to consider |
| Delta Target | 0.065 | 0.01-0.50 | Sweet spot — prefer strikes closest to this |
| Min Premium | $0.30 | $0.05-$5.00 | Minimum bid price to accept |
| Min OTM % | 10% | 1%-50% | Minimum out-of-the-money percentage |
| Max DTE | 7 | 1-90 | Maximum days to expiration |
| DTE Preference | Shortest | Shortest / Best risk-reward | Which expiration to prefer per symbol |

**Ranking Weights (must sum to 100%):**
| Setting | Default | Description |
|---------|---------|-------------|
| Safety Weight | 40% | Delta proximity to target + OTM distance |
| Liquidity Weight | 30% | Open interest + volume + bid-ask spread |
| AI Score Weight | 20% | Claude recommendation score (1-10) |
| Efficiency Weight | 10% | Premium / margin ratio (annualized) |

**Budget & Sizing:**
| Setting | Default | Description |
|---------|---------|-------------|
| Margin Budget % | 20% | Fraction of NLV to allocate (conservative start, increase as confidence grows) |
| Max Positions | 10 | Maximum trades to stage |
| Max Per Sector | 5 | Maximum different underlyings per sector |
| Price Threshold | $90 | Stocks above this get fewer contracts |
| Max Contracts (expensive) | 3 | For stocks > price threshold |
| Max Contracts (cheap) | 5 | For stocks <= price threshold |

Settings persist to a YAML file or database so they survive page reloads.

### 1.2 Quantity Input in Chain Selection

Currently `contracts: 1` is hardcoded in the JS `selectContract()` function. Add:
- A quantity `<input type="number">` per chain row (or per symbol)
- Server-side calculation via `PositionSizer` that pre-fills the recommended quantity
- The `/api/scanner/stage` endpoint already accepts `contracts` — just need the UI to send it

### 1.3 Budget Display

Add a budget summary bar above the chain selection area:
```
Available Margin: $8,000 of $40,000  |  Already Staged: $32,000  |  NLV: $200,000
```

- Query IBKR for NLV via `get_account_summary()` — **required, no fallback**
- If IBKR is not connected: show "IBKR OFFLINE — cannot determine margin budget" and disable staging
- Query already-staged trades' margin from DB
- New endpoint: `GET /api/scanner/budget` returning `{nlv, budget, staged_margin, available, ibkr_connected}`

### 1.4 Files to Modify

| File | Changes |
|------|---------|
| `src/agentic/scanner_api.py` | Settings panel HTML, budget endpoint, quantity pre-calculation |
| `src/services/position_sizer.py` | No changes — already works, just wire it in |
| `config/scanner_settings.yaml` (new) | Persist scanner settings |

---

## Phase 2: Smart Strike Selection — Per-Symbol Best Pick

**Goal:** For each scanned stock, automatically find the optimal strike across all expirations, including margin in the assessment.
**Effort:** 3-4 days

### 2.1 Best-Strike Algorithm

For each symbol with loaded chains, find the single best strike using the composite scoring formula:

```
1. Collect ALL puts across all expirations (within DTE limit)
2. Filter: delta in [delta_min, delta_max] AND bid >= min_premium AND OTM% >= min_otm (10%)
3. For each candidate strike, calculate:
   a. Margin requirement (IBKR whatIfOrder — required, skip symbol if IBKR unavailable)
   b. Composite score using the same formula as portfolio ranking:
      - safety_score (delta proximity to target + OTM distance)
      - liquidity_score (open_interest + volume + bid_ask_spread)
      - efficiency_score (premium / margin, annualized by DTE)
      NOTE: AI score not yet available at this stage — applied at portfolio level
4. Rank all valid strikes by: expiration ASC (shortest DTE first), then composite score DESC
5. Pick the top-scoring strike from the shortest DTE
6. If no valid strikes meet all criteria: skip this symbol entirely
```

**Key design choices:**
- Margin is part of the per-strike scoring (via efficiency component). Two strikes with similar delta but different margin will naturally favor the lower-margin one.
- **No IBKR margin = skip the symbol.** We cannot risk staging a trade without knowing the real margin requirement.
- The scanner may find 5+ valid strikes for SLV but picks only the single best one — lowest-risk with acceptable premium and known margin.

### 2.2 Batch Chain Loading

Currently chains load one-by-one per user click. Add:
- "Load All Chains" button that fetches chains for all selected scan results in parallel
- New endpoint: `POST /api/scanner/chains-batch` accepting `{symbols: ["AAPL", "SLV", ...], max_dte: 7}`
- Reuses existing `IBKRScannerService.get_option_chain()` but batched
- Returns best strike per symbol (pre-selected) plus full chain data for manual override

### 2.3 Improved Claude Prompt

Replace the current prompt (which only sees symbol names) with one that includes real data including margin:

**Data sent to Claude per symbol:**
```json
{
  "symbol": "SLV", "stock_price": 27.50, "sector": "Materials",
  "best_strike": 24.0, "delta": 0.072, "otm_pct": 0.127,
  "premium_bid": 0.35, "iv": 0.42, "dte": 5,
  "bid_ask_spread_pct": 0.12, "open_interest": 8200,
  "margin_per_contract": 650, "premium_margin_ratio": 0.054,
  "annualized_return_pct": 3.93,
  "scanner_rank": 3
}
```

**Improved system prompt additions:**
- Portfolio context: available budget, existing positions (avoid overlap), current VIX
- Margin data per candidate: margin requirement and premium/margin ratio
- Explicit instruction: "Score lower for stocks near earnings, in downtrends, or with wide spreads"
- Explicit instruction: "Consider margin efficiency — a trade using less margin for similar premium is preferred"
- Score should reflect suitability for a **safety-first** naked put strategy

### 2.4 Files to Create/Modify

| File | Changes |
|------|---------|
| `src/services/auto_selector.py` (new) | Best-strike algorithm, composite scoring, portfolio selection |
| `src/agentic/scanner_api.py` | Batch chain endpoint, improved Claude prompt, best-strike display |
| `src/services/ibkr_scanner.py` | Batch chain method |

---

## Phase 3: Auto-Select — One-Click Portfolio Building

**Goal:** "Auto-Select Best" button that runs the full pipeline and stages a margin-aware portfolio.
**Effort:** 3-4 days

### 3.1 The Auto-Select Pipeline

New endpoint: `POST /api/scanner/auto-select`

```
Step 1: Verify IBKR connection — ABORT if offline (no fallbacks)
Step 2: Get budget from IBKR (NLV × margin_budget_pct) and subtract existing margin
Step 3: Load chains for all scan results (batch)
Step 4: Find best strike per symbol with margin (Phase 2 algorithm, includes whatIfOrder)
Step 5: Call Claude with fully enriched data (including margin per candidate) for AI scores
Step 6: Calculate quantity per candidate (PositionSizer with live VIX)
Step 7: Compute final composite score per candidate (now including AI score)
Step 8: Rank and select within budget (greedy, reuse PortfolioBuilder logic)
Step 9: Stage selected trades
Step 10: Return portfolio plan with margin breakdown
```

**Key change from original plan:** Margin is calculated in Step 4 (during best-strike selection), BEFORE the Claude call in Step 5. This means Claude sees the full picture including margin when scoring candidates.

### 3.2 Composite Scoring Formula

```python
composite = (
    safety_weight    * safety_score(delta, otm_pct, delta_target) +
    liquidity_weight * liquidity_score(open_interest, volume, spread_pct) +
    ai_weight        * ai_score / 10.0 +
    efficiency_weight * efficiency_score(premium, margin, dte)
)
```

**Safety score** (0.0-1.0):
- How close is delta to `delta_target` (6.5%)? Closer = higher score
- How far OTM? Further = higher score
- Penalty for delta > 0.20 (getting risky)
- Bonus for delta 0.05-0.08 range (sweet spot)

**Liquidity score** (0.0-1.0):
- Open interest > 1000 → full score; < 100 → near zero
- Bid-ask spread < 5% → full score; > 20% → near zero
- Volume today > 500 → bonus

**Efficiency score** (0.0-1.0):
- Annualized: `(premium / margin) * (365 / dte)`
- Normalized against the candidate set

### 3.3 Greedy Portfolio Selection

Reuse the logic from `PortfolioBuilder`:

```python
for candidate in sorted_by_composite_desc:
    if cumulative_margin + candidate.total_margin > available_budget:
        skip("exceeds budget")
    elif sector_counts[candidate.sector] >= max_per_sector:
        skip("sector concentration — max 5 per sector")
    elif len(selected) >= max_positions:
        break
    elif candidate.symbol in selected_symbols:
        skip("duplicate symbol")  # Already picked best strike for this symbol
    else:
        select(candidate)
        cumulative_margin += candidate.total_margin
```

### 3.4 UI: Auto-Select Button + Portfolio Preview

Add to Scanner page:
- **"Auto-Select Best" button** — runs the full pipeline (disabled if IBKR offline)
- **"Override: Test with Stale Data" checkbox** — allows running during closed markets for testing, with a clear "STALE DATA — FOR TESTING ONLY" banner
- **Portfolio preview panel** showing:
  - Selected trades with rank, symbol, strike, delta, premium, margin, composite score
  - Skipped trades with reason (budget, sector limit, low score, no IBKR margin)
  - Margin budget bar (used / remaining / total)
  - "Stage All" to confirm, or remove individual trades before staging

### 3.5 Configuration Snapshot

When trades are staged via auto-select, save the settings snapshot to each `ScanOpportunity.enrichment_snapshot`:
```json
{
  "auto_select_config": {
    "delta_target": 0.065,
    "delta_range": [0.05, 0.30],
    "min_premium": 0.30,
    "min_otm_pct": 0.10,
    "margin_budget_pct": 0.20,
    "weights": {"safety": 0.40, "liquidity": 0.30, "ai": 0.20, "efficiency": 0.10},
    "composite_score": 0.78,
    "ibkr_margin_source": "whatif",
    "vix_at_selection": 18.5,
    "market_hours": true
  }
}
```

This enables the learning engine to later correlate configuration with outcomes.

### 3.6 Files to Create/Modify

| File | Changes |
|------|---------|
| `src/services/auto_selector.py` | Full pipeline orchestrator |
| `src/agentic/scanner_api.py` | Auto-select endpoint, portfolio preview UI, override toggle |
| `src/services/portfolio_builder.py` | Minor: expose ranking logic for reuse |

---

## Phase 4: Full Automation — Market-Open Scan & Execute

**Goal:** The daemon autonomously scans, selects, and executes trades at market open using live data. User reviews results afterward.
**Effort:** 2-3 days

### 4.1 Market-Open Auto-Scan

Add to the daemon's event handling:
- On `MARKET_OPEN` event (fires once per day when market opens at 9:30 AM ET):
  1. Check if auto-scan is enabled
  2. Verify IBKR connection is live — **abort if offline**
  3. Run IBKR scanner with saved preset
  4. Run auto-select pipeline (Phase 3) with live data
  5. Stage the portfolio
  6. At L2+: immediately transition to EXECUTING (daemon's normal execution flow takes over)
  7. Log full portfolio plan to audit trail

**Why market open, not Sunday evening:**
- Live data produces better strike selection — no overnight gap adjustments needed
- Premium, delta, and margin are all accurate at execution time
- Eliminates the two-stage pre-market validation dance (STAGED → VALIDATING → READY → CONFIRMED) because the data IS the live data
- Aligns with Australian timezone: scan + execute at market open (12:30 AM AEDT), user reviews results in the morning

### 4.2 Configurable Schedule

Add to `config/phase5.yaml`:
```yaml
auto_scan:
  enabled: false                    # Opt-in (default off for safety)
  trigger: "market_open"            # When to scan (market_open = 9:30 AM ET)
  delay_minutes: 5                  # Wait 5 min after open for spreads to settle
  scanner_preset: "naked-put"       # Which IBKR scanner preset to use
  auto_execute: true                # Execute immediately (L2+ required)
  require_ibkr: true                # Hard requirement — no IBKR = no scan
```

### 4.3 Override Button for Testing

Add to the dashboard:
- **"Run Auto-Scan Now" button** — triggers the auto-scan pipeline manually
- Shows "MARKET CLOSED — STALE DATA" warning when market is closed
- Useful for testing the pipeline end-to-end without waiting for market open
- Does NOT auto-execute when using override — stages only for review

### 4.4 Dashboard Notification & Review

When auto-scan completes:
- Show a notification badge on the dashboard: "Auto-scan: 6 trades executed at open"
- Trade history shows all auto-executed trades with entry details
- Tonight's Lineup shows any still-staged (not yet filled) trades
- User can review P&L when they wake up (positions already in play)
- User can close positions via dashboard if something looks wrong

### 4.5 Files to Modify

| File | Changes |
|------|---------|
| `src/agentic/daemon.py` | Auto-scan on MARKET_OPEN event, IBKR verification |
| `config/phase5.yaml` | Auto-scan configuration |
| `src/agentic/dashboard_api.py` | Override button, auto-scan notification, execution results |

---

## Phase 5: Learning Integration — Track What Works

**Goal:** Feed auto-select configuration into the learning engine to optimize over time.
**Effort:** 2 days

### 5.1 Configuration Tracking

Each auto-selected trade already saves its `auto_select_config` snapshot (Phase 3.5). After trade closure, the learning engine can:
- Correlate scoring weights with win rate and P&L
- Identify which delta targets produce best risk-adjusted returns
- Surface insights: "Trades with delta < 0.08 had 95% win rate but 40% less premium vs delta 0.12-0.15 with 88% win rate"
- Compare margin efficiency across different margin_budget_pct settings

### 5.2 A/B Testing Weights

Use the existing experiment engine to test weight configurations:
- Control: current weights (40/30/20/10)
- Test: alternative weights (e.g., 30/30/25/15)
- After N trades, compare win rate, avg P&L, Sharpe ratio
- Surface recommendation: "Test weights outperformed by X%"

### 5.3 Dashboard Insights

Add a "Scanner Performance" section showing:
- Win rate by delta bucket (0.05-0.08, 0.08-0.12, 0.12-0.20, 0.20-0.30)
- Average P&L by composite score quartile
- Best-performing scanner preset
- Weight configuration comparison (if A/B test running)
- Margin efficiency trends over time

---

## Summary of All Phases

| Phase | Name | Effort | Key Deliverable |
|-------|------|--------|-----------------|
| 1 | Foundation | 2-3 days | Settings panel, quantity input, budget display (IBKR required) |
| 2 | Smart Strike Selection | 3-4 days | Best-strike algorithm with margin, batch chains, improved Claude prompt |
| 3 | Auto-Select (Level B) | 3-4 days | One-click portfolio building within margin budget |
| 4 | Full Automation (Level C) | 2-3 days | Market-open scan + execute, user reviews afterward |
| 5 | Learning Integration | 2 days | Track configurations, A/B test weights, surface insights |

**Total estimated effort:** 12-16 days

---

## Key Design Decisions

### 1. Risk-First Ranking
The composite score weights safety (40%) and liquidity (30%) above AI assessment (20%) and margin efficiency (10%). This reflects the principle that capital preservation comes before premium optimization. Weights are configurable and can be tuned over time as the learning engine gathers data.

### 2. Delta Sweet Spot at 6.5%
Borrowed from the SPX weekly options rulebook (WealthyOption / BigERN systems). For individual stocks, the sweet spot may differ — the configurable range (0.05-0.30) allows the scanner to find what works. The learning engine will identify the actual optimal delta per market regime.

### 3. One Best Strike Per Symbol
The scanner may find 5+ valid strikes for SLV. Instead of presenting all of them, the auto-selector picks the single best one using the composite scoring formula (which includes margin efficiency). This prevents analysis paralysis and focuses on quality.

### 4. Shortest DTE First
Prefer nearest expiration for faster capital turnover and less time exposure. For weekly naked puts, 2-5 DTE is the sweet spot — enough theta decay to collect premium without excessive overnight gap risk.

### 5. No IBKR = No Trade
The system refuses to stage trades without a live IBKR connection for margin verification. There is no fallback to estimated margin or default budget. This prevents over-extending on positions where the actual margin requirement could be significantly higher than the Reg-T estimate. It's better to miss a trade than to get a margin call.

### 6. Live Data at Market Open
Both semi-auto and full-auto scan at market open with live data. This eliminates the overnight gap problem where Sunday-staged trades go stale by Monday morning. The pre-market validation pipeline (STAGED → VALIDATING → READY → CONFIRMED) is less critical when the initial selection already uses live data. For the Australian timezone, this means trades execute at ~12:30 AM AEDT and the user reviews results in the morning.

### 7. Conservative Margin Budget (20% NLV)
Starting at 20% of NLV instead of the system's default 50%. This gives a large safety buffer while learning which trades work best. Can be increased via the settings panel as paper trading builds confidence.

### 8. Margin in Strike Selection
Margin requirement is calculated for every candidate strike during the best-strike selection (Phase 2), not just at portfolio ranking time (Phase 3). This means margin efficiency influences which strike is "best" for a given symbol, not just whether the trade fits in the portfolio.

---

## Existing Building Blocks (Already Built)

| Component | Location | Reused In |
|-----------|----------|-----------|
| `PositionSizer` | `src/services/position_sizer.py` | Phase 1 (quantity) |
| `PortfolioBuilder` | `src/services/portfolio_builder.py` | Phase 3 (greedy selection) |
| `StrikeFinder` | `src/services/strike_finder.py` | Phase 2 (strike algorithm) |
| `IBKRClient.get_actual_margin()` | `src/tools/ibkr_client.py` | Phase 2+3 (margin queries) |
| `IBKRClient.get_account_summary()` | `src/tools/ibkr_client.py` | Phase 1 (NLV/budget) |
| `RiskGovernor` | `src/execution/risk_governor.py` | Phase 3 (constraint checks) |
| `IBKRScannerService` | `src/services/ibkr_scanner.py` | All phases (scanning) |
| Experiment Engine | `src/learning/experiment_engine.py` | Phase 5 (A/B testing) |
| Unstage buttons | `src/agentic/dashboard_api.py` | Phase 3+4 (user override) |
| MARKET_OPEN event | `src/agentic/daemon.py` | Phase 4 (trigger) |
