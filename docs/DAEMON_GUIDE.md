# TAAD — The Autonomous Agentic Trading Daemon

## Complete Operational Guide

> **What is TAAD?** A 24-hour background process that monitors markets, scans for naked put opportunities, manages open positions, and learns from every trade — all while you sleep.

---

## Part 1: How It Works (Plain English)

### Starting the Daemon

Open a terminal and run:

```bash
nakedtrader daemon start --fg
```

This starts the daemon in the foreground (you'll see logs scroll by). In a second terminal, start the web dashboard:

```bash
nakedtrader dashboard
```

Then open **http://127.0.0.1:8080** in your browser. The dashboard is your control center — it shows live status, open positions, pending decisions, staged trades, costs, and logs.

### The 24-Hour Cycle

The daemon runs continuously and adapts its behavior based on whether the market is open or closed. Here's what a typical day looks like:

#### Before Market Open (overnight)

The daemon is **idle**. It maintains a heartbeat every 60 seconds to confirm it's alive, but takes no trading actions. The dashboard shows "Market is CLOSED" with the next open time.

#### Market Open (~9:30 AM ET)

When the daemon detects the market is open, it emits a **MARKET_OPEN** event. This triggers the following sequence:

1. **Reset event detector** — Clears VIX baseline from the previous session
2. **Auto-scan** (if enabled) — Waits a configurable delay (default 5 minutes) for spreads to settle, then:
   - Runs the IBKR scanner to find high-IV stocks
   - Loads option chains for all discovered symbols
   - Fetches earnings dates for risk awareness
   - Filters candidates by delta, premium, OTM distance
   - Scores each strike on safety, liquidity, and efficiency
   - Sends the best strikes to Claude AI for analysis
   - Selects a portfolio within your margin budget
   - Stages the selected trades on the dashboard for review

#### During Market Hours (9:30 AM – 4:00 PM ET)

The daemon actively monitors your portfolio with two parallel loops:

**Every 15 minutes — Scheduled Check:**
1. **Monitor positions** — Checks all open positions against exit rules (profit target, stop loss, time exit)
2. **Execute exits** — If a position hits an exit trigger, the daemon closes it immediately
3. **Assemble context** — Gathers market data (VIX, SPY), position P&L, staged candidates
4. **Reason with Claude** — Sends the full context to Claude AI for analysis
5. **Act on decision** — Based on Claude's recommendation and the current autonomy level, either executes, escalates to you, or monitors only

**Every 5 minutes — Event Detection:**
- Checks VIX for intraday spikes (>15% change from session open)
- Checks positions for critical alerts (approaching stop loss, assignment risk)
- If a threshold is breached, emits an immediate **RISK_LIMIT_BREACH** event

#### Market Close (4:00 PM ET)

The daemon emits a **MARKET_CLOSE** event and performs end-of-day housekeeping:

1. **EOD Sync** — Reconciles all orders and positions with IBKR to catch any fills, orphans, or discrepancies
2. **Close expired positions** — Any positions past their expiration date are closed
3. **Calibrate confidence** — Feeds today's closed trades into the confidence calibrator (compares Claude's predicted confidence with actual outcomes)
4. **Persist guardrail metrics** — Writes calibration, entropy, and audit data to the database
5. **Record clean day** — If no errors or human overrides occurred today, counts it toward autonomy promotion
6. **Auto-unstage** — Expires any remaining staged candidates so they don't carry over to the next day

#### After Hours (4:30 PM ET)

The daemon emits an **EOD_REFLECTION** event at 4:30 PM ET. This gives Claude an opportunity to reflect on the day's performance and record learning outcomes.

After this, the daemon returns to idle mode until the next trading day.

### Autonomy Levels

The daemon starts at **Level 1** (recommend only) and can promote itself over time:

| Level | Name | Behavior |
|-------|------|----------|
| **L1** | Recommend | All actions require your approval. Claude recommends, you decide. |
| **L2** | Notify | Executes routine trades (confidence ≥ 70%), notifies you. Escalates edge cases. |
| **L3** | Supervised | Executes most trades (confidence ≥ 50%), escalates only unusual situations. |
| **L4** | Autonomous | Full autonomy. You review a daily summary. |

**Promotion criteria** (all must be met):
- Minimum 10 trades at the current level
- 5 consecutive "clean days" (no errors, no human overrides)
- Win rate above 60% over the last 30 days

**Demotion triggers** (immediate):
- 3 consecutive losing trades
- Any human override

#### 9 Mandatory Escalation Triggers

These **always** require human approval, regardless of autonomy level:

1. First trade of the day
2. Trading a symbol for the first time
3. Single-trade loss exceeds 2x premium
4. Margin utilization above 60%
5. VIX above 30 or 20%+ daily spike
6. 3+ consecutive losing trades
7. AI wants to change strategy parameters
8. Market data is stale (>5 minutes old)
9. AI confidence below 60%

### Managing the Daemon

**From the Dashboard (http://127.0.0.1:8080):**
- Start/Stop/Pause/Resume the daemon
- View open positions and staged trades
- Approve or reject pending decisions
- Trigger a manual auto-scan
- Unstage individual or all staged trades
- View decision audit log with full reasoning
- Monitor Claude API costs
- Review guardrail activity

**From the CLI:**

| Command | Description |
|---------|-------------|
| `nakedtrader daemon start --fg` | Start the daemon |
| `nakedtrader daemon status` | Show daemon health |
| `nakedtrader daemon context` | Show working memory |
| `nakedtrader daemon pause` | Pause event processing |
| `nakedtrader daemon resume` | Resume processing |
| `nakedtrader daemon pending` | Show decisions awaiting approval |
| `nakedtrader daemon approve <ID>` | Approve a pending decision |
| `nakedtrader daemon reject <ID>` | Reject a pending decision |
| `nakedtrader daemon set-autonomy <1-4>` | Set autonomy level |
| `nakedtrader daemon audit` | Show decision audit log |
| `nakedtrader daemon costs` | Show Claude API costs |
| `nakedtrader daemon emergency-stop` | Halt all trading immediately |

### Settings

All configuration lives in two files:

| File | What It Controls |
|------|-----------------|
| `config/phase5.yaml` | Daemon behavior, autonomy rules, Claude models, exit rules, auto-scan, guardrails |
| `config/scanner_settings.yaml` | Scanner filters, ranking weights, budget limits, earnings filter |

Both can be edited via the dashboard's **Settings** page (`http://127.0.0.1:8080/config`).

### Emergency Stop

If anything goes wrong, you have multiple ways to stop trading:

1. **Dashboard** — Click "Stop Daemon"
2. **CLI** — `nakedtrader daemon emergency-stop`
3. **Keyboard** — Press Ctrl+C in the daemon terminal
4. **Kill switch** — The kill switch halts all trading and persists across restarts

---

## Part 2: The Scanner — How It Works in Detail

### Overview

The scanner is a multi-stage pipeline that finds, scores, and selects naked put trades. It can run automatically at market open (if `auto_scan.enabled: true`) or be triggered manually from the dashboard.

### Stage 1: IBKR Scanner

The first step uses Interactive Brokers' built-in market scanner to find stocks with high implied volatility — the foundation of premium selling strategies.

**Scanner preset:** `naked-put` (maps to IBKR's `HIGH_OPT_IMP_VOLAT` scan code)

**What it returns:** The top 50 stocks ranked by implied volatility, along with metadata:
- Symbol, exchange, industry, category
- Distance from benchmark
- Projection data

**What's stored:** Each scan creates a `ScanResult` row (timestamp, source, preset) and one `ScanOpportunity` row per symbol (status: `PENDING`).

### Stage 2: Option Chain Loading

For each of the ~50 symbols discovered, the pipeline loads the full option chain from IBKR:
- All expirations up to `max_dte` days out (default: 7)
- All put strikes with Greeks (delta, IV, theta)
- Bid/ask prices, volume, open interest

### Stage 3: Earnings Detection

**Always runs**, regardless of the earnings filter setting. For each symbol:
- Fetches the next earnings date (via Yahoo Finance, cached)
- Calculates days to earnings and whether earnings fall within the option's DTE
- This information is passed to Claude and stored in the database

### Stage 4: Candidate Filtering

For each symbol, the pipeline examines every put option across all loaded expirations and applies the following filters:

| Filter | Default | Configurable? | Setting |
|--------|---------|--------------|---------|
| Delta range | 0.05 – 0.15 | Yes | `filters.delta_min`, `filters.delta_max` |
| Minimum bid price | $0.30 | Yes | `filters.min_premium` |
| Minimum OTM % | 15% | Yes | `filters.min_otm_pct` |
| Maximum DTE | 7 days | Yes | `filters.max_dte` |
| Delta is not None | Required | No (hardcoded) | — |
| Bid > 0 | Required | No (hardcoded) | — |

**Earnings adjustment:** If `earnings.enabled: true` and a stock has earnings within the option's DTE:
- The `min_otm_pct` filter is increased by `earnings.additional_otm_pct` (default: +15%)
- This means a stock with earnings requires 30% OTM instead of the usual 15%

If `earnings.enabled: false`, earnings are still detected and flagged as warnings but don't change the filter thresholds.

### Stage 5: Strike Selection & Scoring (3-Weight)

For each symbol that has candidates passing the filters, the pipeline selects the **single best strike** using a 3-weight composite score:

#### Safety Score (contributes to 40% of ranking weight)

Two sub-components, weighted 60/40:

**Delta proximity (60%):** How close is the delta to the target?
- Distance = |actual_delta - delta_target|
- Score = max(0, 1.0 - distance / max(delta_target, 0.30))
- **Hard penalty:** If delta > 0.20, the score is capped at 0.3

**OTM distance (40%):** How far out-of-the-money is the strike?

| OTM % | Score |
|-------|-------|
| ≥ 20% | 1.0 |
| ≥ 15% | 0.8 |
| ≥ 10% | 0.5 |
| ≥ 5% | 0.3 |
| < 5% | 0.1 |

#### Liquidity Score (contributes to 30% of ranking weight)

Three equal sub-components (1/3 each):

**Open Interest:**

| OI | Score |
|----|-------|
| ≥ 1000 | 1.0 |
| ≥ 500 | 0.7 |
| ≥ 100 | 0.4 |
| < 100 | 0.1 |

**Bid-Ask Spread:**

| Spread % | Score |
|----------|-------|
| ≤ 5% | 1.0 |
| 5–10% | 0.7 |
| 10–20% | 0.4 |
| > 20% | 0.1 |

**Daily Volume:**

| Volume | Score |
|--------|-------|
| ≥ 500 | 1.0 |
| ≥ 100 | 0.6 |
| > 0 | 0.3 |
| = 0 | 0.1 |

#### Efficiency Score (contributes to 10% of ranking weight)

Based on annualized return on margin: `(bid × 100 / margin) × (365 / dte)`

| Annualized Return | Score |
|-------------------|-------|
| ≥ 30% | 1.0 |
| 20–30% | 0.8 |
| 10–20% | 0.6 |
| 5–10% | 0.4 |
| < 5% | 0.2 |

#### DTE Preference

When `filters.dte_prefer_shortest: true` (default), the pipeline picks the highest-scoring strike from the **shortest available DTE**. This favors faster time decay.

#### Margin Calculation

For each candidate, margin is calculated in order of preference:
1. **IBKR WhatIf** — Exact margin from IBKR's margin simulation (most accurate)
2. **Reg-T estimate** — `max(20% × stock_price - OTM_amount + premium, 10% × stock_price) × 100`
3. **Fallback** — Estimated if neither is available

### Stage 6: AI Enrichment (Claude)

The best strike per symbol is sent to Claude (model: `claude-sonnet-4-5-20250929`) for analysis. The prompt includes:

**Per-symbol data sent to Claude:**
- Stock price, best strike, delta, OTM %, IV, DTE
- Premium (bid), bid-ask spread %, open interest, volume
- Margin per contract, premium/margin ratio, annualized return
- Composite score from Stage 5
- Sector classification
- Earnings date, days to earnings, whether earnings fall in DTE

**What Claude returns (per symbol):**
- Score: 1–10 integer
- Recommendation: `strong_buy`, `buy`, `neutral`, `avoid`
- Reasoning: Free-text explanation
- Risk flags: Array of flags like `earnings_soon`, `high_volatility`, `low_liquidity`, `downtrend`, `small_cap`, `sector_concentration`, `binary_event`, `overvalued`

**Cost control:** Claude API calls are tracked and capped at `$10.00/day` (configurable in `phase5.yaml`).

### Stage 7: Portfolio Selection (4-Weight)

After AI scoring, each candidate gets a **4-weight composite score**:

| Component | Weight | Source |
|-----------|--------|--------|
| Safety | 40% | Delta proximity + OTM distance |
| Liquidity | 30% | Open interest + spread + volume |
| AI Score | 20% | Claude's 1–10 score (normalized to 0.0–1.0) |
| Efficiency | 10% | Annualized return on margin |

If no AI score is available (e.g., Claude cap reached), a 3-weight fallback is used (safety + liquidity + efficiency only, reweighted).

**Greedy portfolio selection:**

Candidates are sorted by composite score (highest first) and selected one by one, subject to these constraints:

| Constraint | Default | Configurable? | Setting |
|------------|---------|--------------|---------|
| Max positions | 10 | Yes | `budget.max_positions` |
| No duplicate symbols | Always | No (hardcoded) | — |
| Max per sector | 5 | Yes | `budget.max_per_sector` |
| Total margin ≤ budget | Always | Yes | `budget.margin_budget_pct` (% of NLV) |

**Contract sizing:**

| Stock Price | Max Contracts | Setting |
|-------------|---------------|---------|
| > $90 (threshold) | 3 | `budget.max_contracts_expensive` |
| ≤ $90 (threshold) | 5 | `budget.max_contracts_cheap` |

### Stage 8: Staging

Selected candidates are written to the `ScanOpportunity` table with state `STAGED`. Each record includes:
- Full contract details (strike, expiration, bid, ask, premium, delta, IV, DTE)
- Margin required and margin source (IBKR WhatIf vs estimate)
- AI score, recommendation, reasoning, and risk flags
- Earnings data (date, days to earnings, timing)
- Full config snapshot for audit trail

Staged trades appear on the dashboard's "Tonight's Lineup" section. You can:
- **Unstage** individual trades or all at once
- **Review** all details before the daemon considers them for execution
- Let them be **auto-expired** at end of day if not acted upon

### Complete Pipeline Flow

```
IBKR Scanner (50 stocks by IV)
    │
    ▼
Load Option Chains (all exps ≤ 7 DTE)
    │
    ▼
Fetch Earnings Dates (always)
    │
    ▼
Filter Candidates
├── delta ∈ [0.05, 0.15]
├── bid ≥ $0.30
├── OTM ≥ 15% (or 30% if earnings)
└── DTE ≤ 7
    │
    ▼
Score Strikes (3-weight: safety + liquidity + efficiency)
    │
    ▼
Select Best Strike Per Symbol
    │
    ▼
Claude AI Analysis (score 1-10, risk flags)
    │
    ▼
Portfolio Selection (4-weight: safety + liquidity + AI + efficiency)
├── Sort by composite score
├── Max 10 positions
├── Max 5 per sector
└── Within margin budget (20% of NLV)
    │
    ▼
Stage on Dashboard
```

---

## Part 3: Exit Rules & Position Management

### How Exits Work

The daemon monitors all open positions every 15 minutes (on each `SCHEDULED_CHECK`). Three exit conditions are checked:

| Exit Rule | Default | What It Means |
|-----------|---------|---------------|
| Profit target | 50% | Exit when you've captured 50% of maximum profit (the premium received) |
| Stop loss | -200% | Exit when loss reaches 2x the premium received |
| Time exit | 2 DTE | Close position 2 days before expiration (set to -1 to let expire) |

**Example:** You sell a put for $0.50 premium.
- **Profit target:** Exit when you can buy it back for $0.25 or less (50% of $0.50 captured)
- **Stop loss:** Exit when the option price rises to $1.50 ($0.50 × 3 = $1.50, meaning you've lost 2× your premium)
- **Time exit:** Close 2 days before expiration regardless of P&L

### Exit Execution Flow

1. Check pending exit orders (did any previous exits fill?)
2. Evaluate all positions against exit rules
3. For triggered exits, place market/limit orders via IBKR
4. Emit `POSITION_CLOSED` event
5. Record trade outcome for learning + governor

### EOD Cleanup

At market close, the daemon also:
- Closes any positions past their expiration date
- Reconciles all orders/positions with IBKR
- Imports any "orphan" orders/positions found in IBKR but not in the database

---

## Part 4: Guardrails & Safety

### Pre-Claude Validation (Context)

Before sending data to Claude:
- **Data freshness check** — Is the market data recent enough?
- **Consistency check** — Are the numbers internally consistent?
- **Null sanitization** — Are there missing values that could mislead?

### Post-Claude Validation (Output)

After Claude returns a decision:
- **Action plausibility** — Does the proposed action make sense?
- **Symbol cross-reference** — Are the referenced symbols real?
- **Reasoning coherence** — Is the reasoning logically consistent?
- **Numerical grounding** — Do the numbers in reasoning match the input data?

### Execution Gate

Before any order is placed:
- **VIX movement check** — Block if VIX moved >15% since context was assembled
- **SPY movement check** — Block if SPY moved >2% since context was assembled
- **Rate limiting** — Max 5 orders per minute

### Post-Decision Monitoring

At end of day:
- **Confidence calibration** — Compares Claude's confidence scores with actual trade outcomes
- **Reasoning entropy** — Detects if Claude is giving repetitive/formulaic reasoning
- **Daily audit dashboard** — Summary of blocks, warnings, and guardrail activity

---

## Part 5: Configuration Reference

### phase5.yaml — Core Settings

```yaml
autonomy:
  initial_level: 1               # Start at L1 (recommend only)
  promotion_clean_days: 5        # Clean days needed for promotion
  promotion_min_trades: 10       # Trades needed at level before promotion
  promotion_min_win_rate: 0.60   # Min 60% win rate for promotion
  demotion_loss_streak: 3        # Consecutive losses trigger demotion
  max_level: 4                   # Max autonomy level (safety cap)

claude:
  reasoning_model: "claude-sonnet-4-5-20250929"
  daily_cost_cap_usd: 10.00     # Hard cap on daily Claude spend

daemon:
  client_id: 10                  # IBKR client ID (avoid conflicts)
  heartbeat_interval_seconds: 60
  event_poll_interval_seconds: 5
  max_events_per_cycle: 10

dashboard:
  host: "127.0.0.1"
  port: 8080

auto_scan:
  enabled: false                 # Set true to auto-scan at market open
  delay_minutes: 5               # Wait for spreads to settle
  scanner_preset: "naked-put"    # IBKR scanner preset
  auto_stage: true               # Stage selected trades automatically
  require_ibkr: true             # Skip if IBKR offline

exit_rules:
  profit_target: 0.50            # 50% of max profit
  stop_loss: -2.00               # 2x premium loss
  time_exit_dte: 2               # Close 2 days before expiry

guardrails:
  enabled: true
  vix_movement_block_pct: 15.0
  spy_movement_block_pct: 2.0
  max_orders_per_minute: 5
```

### scanner_settings.yaml — Scanner Settings

```yaml
filters:
  delta_min: 0.05                # Minimum option delta
  delta_max: 0.15                # Maximum option delta
  delta_target: 0.065            # Ideal delta for scoring
  min_premium: 0.30              # Minimum bid price ($)
  min_otm_pct: 0.15              # Minimum out-of-the-money %
  max_dte: 7                     # Maximum days to expiration
  dte_prefer_shortest: true      # Prefer shortest DTE

ranking:
  safety: 40                     # Weight for delta + OTM (must sum to 100)
  liquidity: 30                  # Weight for OI + spread + volume
  ai_score: 20                   # Weight for Claude AI score
  efficiency: 10                 # Weight for annualized return

budget:
  margin_budget_pct: 0.20        # Max 20% of NLV for margin
  max_positions: 10              # Max open positions
  max_per_sector: 5              # Max positions per sector
  price_threshold: 90.0          # Divider for contract sizing
  max_contracts_expensive: 3     # Contracts for stocks > $90
  max_contracts_cheap: 5         # Contracts for stocks ≤ $90

earnings:
  enabled: false                 # Adjust filters for earnings
  additional_otm_pct: 0.15       # Extra OTM required if earnings in DTE
  lookahead_days: 0              # Days ahead to check for earnings
```

### Hardcoded Values (Not Configurable)

| Value | Where | Purpose |
|-------|-------|---------|
| Claude model: `claude-sonnet-4-5-20250929` | auto_select_pipeline.py | AI analysis model |
| Claude timeout: 120s | auto_select_pipeline.py | Max wait for Claude response |
| Daily cost cap: $10.00 | auto_select_pipeline.py | Claude API spend limit |
| Delta penalty at 0.20 | auto_selector.py | Safety score cap for high delta |
| Time emitter interval: 30s | daemon.py | How often market hours are checked |
| Scheduled check interval: 15min | daemon.py | Position monitoring frequency |
| Event detector poll: 5min | event_detector.py | VIX/alert check frequency |
| VIX spike threshold: 15% | event_detector.py | Intraday VIX change alert |
| Min ExcessLiquidity: 10% of NLV | risk_governor.py | Account health floor |
| Max margin utilization: 80% | risk_governor.py | Hard margin cap |
| Max daily loss: -2% | risk_governor.py | Circuit breaker |
| Max weekly loss: -5% | risk_governor.py | Circuit breaker |
| Max drawdown: -10% | risk_governor.py | Circuit breaker |

---

## Part 6: Dashboard Pages

| Page | URL | Purpose |
|------|-----|---------|
| Main Dashboard | `http://127.0.0.1:8080/` | Status, positions, decisions, logs |
| Option Scanner | `http://127.0.0.1:8080/scanner` | Manual scanner interface |
| Settings | `http://127.0.0.1:8080/config` | Edit phase5.yaml + scanner_settings.yaml |
| Guardrails | `http://127.0.0.1:8080/guardrails` | Guardrail metrics and audit |
| Decision Detail | `http://127.0.0.1:8080/decision/<ID>` | Full reasoning for a single decision |

### Dashboard API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/status` | GET | Daemon health and stats |
| `/api/positions` | GET | Open positions |
| `/api/staged` | GET | Staged trade candidates |
| `/api/decisions` | GET | Recent decision audit log |
| `/api/decisions/<id>` | GET | Full detail for one decision |
| `/api/queue` | GET | Decisions pending human approval |
| `/api/approve/<id>` | POST | Approve a pending decision |
| `/api/reject/<id>` | POST | Reject a pending decision |
| `/api/pause` | POST | Pause the daemon |
| `/api/resume` | POST | Resume the daemon |
| `/api/start` | POST | Start daemon as background process |
| `/api/stop` | POST | Stop daemon via SIGTERM |
| `/api/unstage/<id>` | POST | Unstage a single trade |
| `/api/unstage-all` | POST | Unstage all trades |
| `/api/auto-scan/trigger` | POST | Manually trigger auto-scan |
| `/api/auto-scan/status` | GET | Auto-scan config and last scan info |
| `/api/logs` | GET | Recent daemon log lines |
| `/api/costs` | GET | Claude API cost summary |
| `/api/guardrails` | GET | Guardrail activity summary |

---

## Quick Start Checklist

1. Ensure IBKR TWS or Gateway is running (paper trading port 7497)
2. Start the daemon: `nakedtrader daemon start --fg`
3. Start the dashboard: `nakedtrader dashboard`
4. Open `http://127.0.0.1:8080`
5. Enable auto-scan in Settings if desired (`auto_scan.enabled: true`)
6. Review staged trades when they appear
7. Approve or reject pending decisions
8. Monitor positions and exits throughout the day
9. Review the daily guardrail report after market close
