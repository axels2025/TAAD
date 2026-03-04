# Trading System Core Analysis Report

**Date:** 2026-02-27
**Status:** Paper-trading capable with solid foundation; gaps remain for real-money autonomy.

---

## Current State: Where We Are

The system is **paper-trading capable with a solid foundation** — the execution pipeline, position management, and risk controls are mature. But several areas need work before it can run truly autonomously with real money.

| Area | Score | Verdict |
|------|-------|---------|
| Order Execution | 85% | Production-ready |
| Position Management | 90% | Strong |
| Risk Management | 80% | Good, but portfolio-level gaps |
| Market Data | 75% | Works, but fragile |
| Learning/Adaptation | 70% | Infrastructure done, feedback loop thin |
| Alerting | 85% | Multi-channel, graceful |
| Recovery/Resilience | 60% | **Weakest area** |
| Multi-Exchange | 80% | Clean exchange profiles |
| Reporting | 60% | Basic dashboards, missing analytics |
| Scheduling | 85% | Market-aware, multi-exchange |

---

## The Big Gaps: What Professional Traders Would Expect

### 1. Portfolio Greeks Dashboard (MISSING — HIGH PRIORITY)

Every professional options platform — thinkorswim, tastytrade, IBKR Risk Navigator — shows **portfolio-level Greeks at a glance**. Our system tracks individual position Greeks but has no aggregate view.

**What's needed:**
- **Portfolio Delta** — Total directional exposure (sum of all position deltas, beta-weighted to SPY)
- **Portfolio Theta** — How much earned per day from time decay across all positions
- **Portfolio Gamma** — How fast delta changes on a 1% move
- **Portfolio Vega** — Exposure to IV changes
- **"What-if" scenarios** — If SPY drops 5%, what happens to the portfolio? If VIX spikes to 35?

**Why it matters for autonomy:** The daemon makes per-position decisions but has no concept of "my total portfolio is too short delta" or "I'm over-exposed to vega." Without this, the AI can stack correlated risk without knowing it.

### 2. Correlation-Aware Position Sizing (MISSING — HIGH PRIORITY)

The risk governor checks sector concentration (max 30%) but doesn't check **correlation**. Selling naked puts on AAPL, MSFT, and GOOGL looks like 3 different positions across the sector limit, but they're ~0.85 correlated — they all crash together.

**What's needed:**
- Correlation matrix between open positions (using historical returns)
- Max portfolio correlation limit (e.g., reject new position if portfolio correlation > 0.7)
- Beta-weighted position sizing (a $200 stock put ≠ a $30 stock put in risk terms)

### 3. Process Watchdog / Crash Recovery (WEAK — HIGH PRIORITY)

This is the #1 operational risk for autonomous systems. If the daemon crashes at 10:15 AM with open positions, **nobody is watching them**. The system has:
- Connection recovery (good)
- Kill switch persistence (good)
- Order reconciliation (good)

But it's **missing**:
- **Watchdog process** — A lightweight supervisor that restarts the daemon if it dies (systemd unit, launchd plist, or Python watchdog)
- **Heartbeat alerting** — If no heartbeat for 2 minutes, send emergency notification
- **State snapshot on crash** — Dump current positions/orders to disk on SIGTERM so restart can resume
- **Startup reconciliation** — On restart, automatically reconcile all positions before resuming trading

Research shows automated recovery decreases mean-time-to-recovery by 70-85% over manual intervention.

### 4. Economic/Earnings Calendar (MISSING — MEDIUM PRIORITY)

The risk governor has a 7-day earnings check, but it's not wired to real data. Professional traders never sell puts blindly across:
- **Earnings dates** — IV crush is real, but so is gap risk
- **FOMC meetings** — 8 per year, each can move markets 2-3%
- **CPI/Jobs/GDP releases** — Scheduled market-moving events
- **Quad witching / OpEx weeks** — Unusual options flow and pin risk

**What's needed:**
- External earnings calendar API (e.g., Alpha Vantage, Financial Modeling Prep)
- FOMC/economic calendar (static — dates are published annually)
- Entry rule: widen OTM% or skip trading around these events
- Dashboard display of upcoming events

### 5. Execution Quality Analysis (MISSING — MEDIUM PRIORITY)

Fill prices are recorded but execution quality isn't analyzed over time.

**What's needed:**
- **Slippage tracking** — How much worse was the fill vs. the mid-price at entry?
- **Fill rate analysis** — What % of limit orders fill? How long do they take?
- **Time-of-day analysis** — Are 9:31 AM fills worse than 10:00 AM fills?
- **Spread cost tracking** — How much paid in bid-ask spread per trade?

This feeds directly into the learning engine — if Monday 9:31 fills are consistently 15% worse than Monday 10:00 fills, the system should learn to delay.

### 6. Drawdown / Equity Curve Management (PARTIAL — MEDIUM PRIORITY)

The risk governor has a daily loss limit (-2%) and a weekly loss limit, but professional traders also track:
- **Rolling max drawdown** — Peak-to-trough decline across the life of the strategy
- **Drawdown duration** — How many days from peak to recovery?
- **Equity curve visualization** — A simple line chart of cumulative P&L over time
- **Underwater chart** — How deep below the high-water mark are you?
- **Automatic throttling** — If drawdown hits -10%, reduce position sizes by 50% (not just halt)

### 7. Alert Throttling & Digest Mode (MISSING — MEDIUM PRIORITY)

The notification system sends every alert individually. In a busy session with 8 open positions, the daemon could fire 20+ alerts in minutes. Professional systems have:
- **Deduplication** — Same alert within 5 minutes → suppress
- **Digest mode** — Batch alerts into a single summary every 15 minutes
- **Escalation ladder** — If 3 warnings in 10 minutes, auto-escalate to CRITICAL
- **Quiet hours** — Don't send non-critical alerts outside market hours

### 8. Trade Journal / Decision Audit Trail (PARTIAL — MEDIUM PRIORITY)

`DecisionAudit` exists in the database (AI reasoning stored), but professional traders want:
- **Why was this trade entered?** — Already captured (AI reasoning)
- **Why was this trade exited?** — Partially captured (exit_reason field)
- **What was the market doing at entry/exit?** — Captured via snapshots (98 fields)
- **Screenshot/chart at entry** — Not captured (would need chart generation)
- **Post-trade review notes** — No way to annotate trades after the fact
- **Tagging system** — Tag trades as "earnings play", "VIX spike entry", "Monday routine", etc. for filtering

### 9. Flex Query Integration for Complete History (DOCUMENTED BUT NOT BUILT)

MEMORY.md already flags this as critical. The 24-hour `ib.fills()` limitation means trades can be lost. Flex Queries give years of history with actual fill prices and commissions. This is documented in ROADMAP.md but not implemented. For true autonomy, this is mandatory — the system can't learn from trades it doesn't know about.

### 10. Dashboard Usability (USER-REPORTED ISSUES)

Known pain points — they matter for autonomy because if you can't trust the dashboard, you'll keep manually checking IBKR:
- **Clear/reset button** — No way to clear stale messages and events
- **Guardrail noise** — Hallucination guardrails blocking legitimate actions; needs tuning, not just on/off
- **Message overload** — Events pile up with no filtering or pagination
- **Navigation inconsistency** — Pages have different layouts, no unified nav bar
- **No mobile responsiveness** — Can't check positions from phone

---

## What's Actually Strong (Don't Overlook These)

The system already has things many professional tools lack:

| Strength | Why It Matters |
|----------|---------------|
| **7-step pre-flight validation** | Most retail bots just fire orders; ours validates margin, premium, expiration, connection health first |
| **Dual-source position tracking** (DB + IBKR) | Survives API cache issues — most bots rely solely on broker API |
| **AI reasoning audit trail** | Every decision has stored reasoning — invaluable for debugging and learning |
| **Entry/exit snapshots** (98 fields) | Captures complete market context at trade time — most systems only save price |
| **Multi-exchange profiles** | Clean abstraction for US + ASX; rare in retail systems |
| **Kill switch persistence** | Survives daemon restart — critical safety feature |
| **Pattern detection across 15 dimensions** | Statistical learning from actual trades, not just backtested rules |

---

## Detailed Component Analysis

### Order Execution (85%)

**Strengths:**
- Complete pipeline: idea → order → fill → tracking with 7 validation steps
- Pre-flight validation: IBKR connection, contract data, premium reasonableness, expiration, margin
- Dry-run mode for testing without placing real orders
- Paper trading guard: verifies `PAPER_TRADING=true` and `IBKR_PORT=7497`
- Entry snapshot integration: captures 98-field context on trade placement
- Slippage calculation: `slippage = fill_price - opportunity.premium`
- Post-trade margin verification

**Gaps:**
- No retry logic for rejected orders (relies on external scheduler)
- Partial fill handling logs but doesn't auto-resubmit unfilled remainder

### Position Management (90%)

**Strengths:**
- Dual-source: DB as source of truth, IBKR for live pricing
- Real-time P&L: `(current_price - entry_premium) * contracts * 100`
- Greeks monitoring: delta, theta, gamma, vega from IBKR chains
- Alert generation for profit targets, stop losses, expiration
- Expired position auto-close with assignment detection
- 15-minute configurable refresh intervals

**Gaps:**
- Option pricing may be stale with frozen data
- Greeks freshness depends on IBKR market data subscription tier

### Risk Management (80%)

**Implemented controls:**
- Daily loss limit: -2% (configurable)
- Position loss limit: -$500 per position
- Max positions: 10 simultaneous
- Max positions per day: 10 new
- Sector concentration: 30% max
- Margin utilization: 80% max
- Weekly loss tracking (rolling 7-day)
- Max drawdown tracking (peak-to-trough)
- Earnings avoidance (7-day window)
- Kill switch (persistent across restarts)
- Emergency halt capability

**Gaps:**
- No portfolio-level correlation checks
- No total delta/gamma/vega portfolio constraints
- No VIX spike circuit breaker (auto-halt on VIX +20%)
- No margin degradation modeling (project impact of adverse moves)
- No slippage reserve in margin calculations

### Market Data (75%)

**Strengths:**
- 4 data modes (live, frozen, delayed, delayed-frozen)
- Market data health checks (tests SPY quote to ensure flow)
- Contract caching to avoid redundant lookups
- Error filtering for harmless IBKR error codes

**Gaps:**
- `ib.fills()` only returns last 24 hours of execution history
- Mark-to-market vs mid-price confusion on orphan imports
- No automatic refresh interval; relies on explicit refresh calls

### Learning/Adaptation (70%)

**Strengths:**
- Pattern detection across 15+ dimensions (delta, IV rank, DTE, VIX regime, sector, day of week, etc.)
- Statistical validation with p-value testing
- Multi-dimensional pattern combiner
- A/B experiment framework with effect size tracking
- Bayesian parameter optimization

**Gaps:**
- Patterns don't auto-update during trading (nightly refresh only)
- No real-time pattern invalidation on regime change
- 30-trade minimum limits early learning speed
- Thin feedback loop between live trading and learning engine

### Alerting (85%)

**Strengths:**
- Multi-channel: logging, email (SMTP), webhook (Slack/Discord)
- Severity-based routing (INFO → log, WARNING → email, CRITICAL → all channels)
- Graceful degradation (failed notifications don't crash trading)
- 10-second timeout prevents blocking trades

**Gaps:**
- No alert throttling/deduplication
- No digest mode (batching)
- No SMS support
- No in-app notification system

### Recovery/Resilience (60%)

**Strengths:**
- IBKR auto-reconnection with exponential backoff
- Order reconciliation (DB vs IBKR state comparison)
- Kill switch persistence to disk
- Multi-checkpoint execution saves (9:30, 9:35, 10:30)

**Gaps:**
- No automated daemon restart (no watchdog)
- Orphan position import uses wrong entry premium (avgCost, not fill price)
- No transaction durability (crash during order placement → inconsistent state)
- 24-hour execution history limit (fills lost after 24 hours)

### Multi-Exchange (80%)

**Strengths:**
- Clean exchange profile abstraction (US + ASX)
- Exchange-specific parameters: timezone, market hours, currency, multipliers
- Env var selection (`EXCHANGE=US` or `EXCHANGE=ASX`)

**Gaps:**
- Single account per connection only
- Single exchange at a time per daemon
- No spread trading support

### Reporting (60%)

**Strengths:**
- Dashboard with real-time positions, P&L, decisions, guardrail metrics
- CLI commands for positions, staged trades, daemon status
- Entry/exit snapshots (98 fields each)
- Pattern detection reports

**Gaps:**
- No tax reporting (wash sales, cost basis)
- No monthly P&L statements
- No portfolio Greeks exposure summary
- No equity curve / drawdown visualization
- No Sharpe ratio, Sortino, or other performance metrics
- No risk attribution (which positions caused losses)

### Scheduling (85%)

**Strengths:**
- Market session detection (pre-market, regular, after-hours, closed, weekend, holiday)
- Exchange-aware (different hours for US vs ASX)
- Two-tier execution scheduling (9:30 AM + conditional Tier 2)
- Holiday calendar

**Gaps:**
- Holiday calendar hard-coded (annual maintenance burden)
- No earnings calendar integration
- No FOMC/economic calendar (Phase 7 in roadmap)
- No automated EOD reporting routine

---

## Prioritized Roadmap: What to Build Next

### Tier 1 — Required for Real-Money Autonomy

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 1 | **Process watchdog** (launchd/systemd + heartbeat alerts) | 1-2 days | Prevents unmonitored positions |
| 2 | **Portfolio Greeks aggregate** (dashboard card + risk limits) | 3-5 days | Portfolio-level risk awareness |
| 3 | **Correlation-aware sizing** (reject correlated positions) | 2-3 days | Prevents hidden concentration risk |
| 4 | **Flex Query integration** (complete trade history) | 3-5 days | Learning engine needs full data |
| 5 | **Startup reconciliation** (auto-reconcile on daemon restart) | 1-2 days | Safe crash recovery |

### Tier 2 — Important for Trust & Tuning

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 6 | **Economic calendar** (FOMC, CPI, earnings) | 2-3 days | Avoid event risk |
| 7 | **Guardrail tuning** (reduce false positives) | 2-3 days | Stop blocking legitimate trades |
| 8 | **Dashboard cleanup** (clear button, pagination, unified nav) | 2-3 days | Usability and trust |
| 9 | **Alert throttling + digest** | 1-2 days | Reduce notification noise |
| 10 | **Execution quality analysis** (slippage, fill rate tracking) | 2-3 days | Optimize entry timing |

### Tier 3 — Professional Polish

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 11 | **Equity curve + drawdown charts** | 2-3 days | Visual performance tracking |
| 12 | **Trade tagging + post-trade notes** | 1-2 days | Better trade journaling |
| 13 | **Drawdown-based throttling** (auto-reduce size in drawdown) | 1-2 days | Adaptive risk management |
| 14 | **Backtesting framework** (see backtesting plan) | 2-4 weeks | Strategy validation |
| 15 | **Tax reporting** (wash sales, cost basis) | 3-5 days | End-of-year compliance |

---

## The Autonomy Gap

The system's current autonomy bottleneck isn't the AI reasoning — it's **operational resilience**. The AI can decide what to trade, but:

- If it crashes, nobody restarts it
- If it stacks correlated risk, nobody catches it at portfolio level
- If it trades through earnings/FOMC, nobody stops it
- If the dashboard is noisy, you don't trust it and intervene manually

True autonomy = **you can walk away for a week and trust the system**. That requires items 1-5 from Tier 1 above. The AI reasoning and learning are already at 70%+ — the infrastructure around it needs to catch up.

---

## References

- [5 Essential Features for Modern Options Platforms (ETNA)](https://www.etnasoft.com/5-essential-features-every-modern-options-trading-platform-must-have-in-2025/)
- [How to Design an Institutional Trading System (DayTrading.com)](https://www.daytrading.com/design-institutional-trading-system)
- [Systemic Failures in Algorithmic Trading (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC8978471/)
- [Error Handling in Autonomous Systems (Fractary)](https://www.fractary.com/blog/error-handling-recovery-autonomous-systems/)
- [Options Command Center Dashboard (TradesViz)](https://www.tradesviz.com/blog/options-command-center/)
- [Analyzing Options Greeks (tastytrade)](https://tastytrade.com/learn/trading-products/options/analyzing-options-greeks/)
- [Best Options Trading Platforms (StockBrokers.com)](https://www.stockbrokers.com/guides/optionstrading)
- [Automated Options Trading Guide 2025](https://advancedautotrades.com/automated-options-trading-guide/)
