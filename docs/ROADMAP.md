# Trading System Roadmap

**Last Updated:** 2026-02-23

This document tracks future improvements, enhancements, and technical debt for the trading system.

---

## Priority Levels

- 🔴 **CRITICAL** - System stability, data integrity issues
- 🟡 **HIGH** - Important improvements, significant value
- 🟢 **MEDIUM** - Nice-to-have enhancements
- 🔵 **LOW** - Future considerations, exploratory

---

## Current Backlog

### 🟡 HIGH PRIORITY

#### Phase 6: Market Regime Detection
**Status:** Planned (optional — validate core system first)
**Date Added:** 2026-02-23
**Effort:** Large (1-2 weeks)
**Value:** Adapt strategy parameters to current market conditions automatically

**Problem Solved:**
- System currently uses static parameters regardless of market environment
- Naked put selling performs differently in high-vol vs low-vol regimes
- No automatic detection of regime transitions (bull → bear, low VIX → high VIX)

**Capabilities:**
- Classify current market regime (trending up, trending down, ranging, high volatility, crisis)
- Adjust strategy parameters per regime (OTM%, DTE, position sizing)
- Detect regime transitions and alert the daemon
- Historical regime analysis to validate parameter adjustments

**Prerequisites:**
- 3-6 months of paper trading data from Phases 0-5
- Proven core learning loop (patterns detected, promotions earned)

---

#### Phase 7: Event Risk Analysis
**Status:** Planned (optional — validate core system first)
**Date Added:** 2026-02-23
**Effort:** Large (1-2 weeks)
**Value:** Avoid holding positions through high-risk events

**Problem Solved:**
- System doesn't know about upcoming earnings, FOMC, CPI releases
- Can be caught holding naked puts through binary events
- No calendar-aware risk adjustment

**Capabilities:**
- Economic calendar integration (FOMC, CPI, NFP, GDP)
- Earnings date tracking per underlying
- Pre-event position sizing reduction or exit
- Post-event opportunity detection (IV crush)

**Prerequisites:**
- Phase 6 regime detection (events cause regime shifts)
- Reliable external data source for economic calendar

---

### 🟢 MEDIUM PRIORITY

#### Phase 8: Portfolio Optimization
**Status:** Planned (optional — validate core system first)
**Date Added:** 2026-02-23
**Effort:** Large (2-3 weeks)
**Value:** Optimal capital allocation across positions

**Problem Solved:**
- Current position sizing is simple (fixed contracts or % of NLV)
- No correlation-aware allocation (overlapping sector risk)
- No portfolio-level Greeks management (total delta, gamma, vega exposure)

**Capabilities:**
- Portfolio-level Greeks tracking and limits
- Correlation-aware position sizing (reduce allocation when positions are correlated)
- Kelly criterion or similar optimal sizing
- Rebalancing recommendations

**Prerequisites:**
- Phase 6 + 7 for regime and event awareness
- 6+ months of trading data for correlation analysis

---

#### Commission Tracking & Analysis
**Status:** Planned
**Date Added:** 2026-02-07
**Effort:** Small (half day)
**Value:** More accurate learning engine

**Enhancement:**
- Add `commission` field to trades table
- Capture actual commissions from execution data
- Calculate true net P&L after fees
- Learning engine: analyze commission impact on profitability

**Expected Improvement:** 1-2% more accurate ROI calculations

---

### 🔵 LOW PRIORITY

#### Tax Reporting Automation
**Status:** Future consideration
**Date Added:** 2026-02-07
**Effort:** Medium (2-3 days)

**Feature:**
- Generate IRS-compliant tax reports
- Wash sale tracking
- Cost basis calculations
- Export to TurboTax format

**When:** After 1 year of trading data accumulated

---

## Completed Items

#### Autonomous Position Manager: Complete the Daemon Loop
**Status:** Completed
**Date Completed:** 2026-02-23
**Effort:** Large (4 workstreams)
**Value:** Closes the full autonomy loop — daemon can now scan, execute, monitor, exit, learn, and promote

**What was built:**
- Wired ExitManager + PositionMonitor into daemon with dashboard-configurable exit rules (`profit_target`, `stop_loss`, `time_exit_dte`)
- `_monitor_positions()` runs every SCHEDULED_CHECK: reconciles pending exits, evaluates exits, executes closes
- Expiration handling at MARKET_CLOSE
- Fixed broken `ActionExecutor._handle_close()` to use injected ExitManager
- Created `src/agentic/event_detector.py`: VIX spike detection (>15% from session open) + critical position alerts on 5-minute poll
- Trade outcome feedback loop: immediate per-trade learning, governor win/loss tracking, promotion checks
- Clean day recording at MARKET_CLOSE for autonomy promotion progress
- Governor counter persistence (`_save_counters`/`_load_counters`) — promotion progress survives daemon restarts
- Raised `max_level: 4` for full L1→L4 paper trading validation
- 31 unit tests covering all workstreams

---

#### Guardrail Monitoring
**Status:** Completed
**Date Completed:** 2026-02-22
**Effort:** Medium (2 days)
**Value:** Safety layer — hallucination detection, execution gates, calibration monitoring

**What was built:**
- Output validation (hallucination detection, data freshness checks)
- Reasoning entropy monitoring
- Calibration error tracking
- Daily audit dashboard
- Execution gates (VIX/SPY movement thresholds, order rate limiting)

---

#### NakedTrader: Daily SPX/XSP/SPY Put Selling
**Status:** Completed
**Date Completed:** 2026-02-18
**Effort:** Medium (3 days)
**Value:** Mechanical daily income strategy independent of weekly Barchart pipeline

**What was built:**
- `src/nakedtrader/` package: config, chain, strike_selector, order_manager, trade_recorder, watcher, workflow
- YAML-driven configuration (`config/daily_spx_options.yaml`) with Pydantic validation and CLI overrides
- Delta-based strike selection (target 6.5 delta, range 5.5-7.5) with automatic range widening
- Index option chain retrieval supporting SPX/SPXW, XSP/XSPW, and SPY trading classes
- IBKR native bracket orders: parent SELL + profit-take BUY (GTC) + optional stop-loss BUY (GTC)
- Position watcher with bracket fill detection and automatic trade closing
- Three CLI commands: `nt`, `nt-watch`, `nt-status`
- Database schema additions: `trade_strategy`, `exit_order_id`, `stop_order_id`, `bracket_status` on trades table

**References:**
- Strategy rulebook: `docs/research/spx-options-trading-rulebook-2026-02-17.md`

---

## Technical Debt

### Entry Premium Data Quality Issue ✅ RESOLVED
**Resolution Date:** 2026-02-07
**Resolution:** Manual fix applied for 5 affected trades
**Prevention:** Option A approach (execute-two-tier only, nightly reconcile)

---

## Decision Log

### 2026-02-23: Phase 6-8 Added as Optional Roadmap Items

**Context:** Core Phases 0-5 complete. Spec recommends 3-6 months paper trading validation before adding intelligence agents.

**Decision:** Add Phases 6 (Market Regime Detection), 7 (Event Risk Analysis), and 8 (Portfolio Optimization) as planned roadmap items. Prioritize runtime validation of the core system first.

---

### 2026-02-07: Chose Option A Over Option C

**Context:** Entry premium sync issue - multiple solutions available

**Decision:**
- Fix current data manually
- Use execute-two-tier for all new positions
- Run nightly reconcile-positions

**Rationale:**
- Option A works with current code
- Minimal complexity

**Trade-offs Accepted:**
- Cannot do same-day manual round-trips
- Manual positions imported with estimated entry prices
- 24-hour execution window limitation

---

## Ideas / Exploratory

### Daemon-Integrated NakedTrader (XSP Weekly Puts)
**Status:** Planned — parked until current system is running well
**Date Added:** 2026-02-23
**Effort:** Moderate (1 day)
**Value:** Fully automated mechanical XSP weekly put selling via the daemon

**Problem Solved:**
- NakedTrader currently requires manual CLI execution (`nakedtrader sell XSP`)
- No way to run the mechanical SPX/XSP rulebook strategy alongside the AI-assisted stock scanner

**Proposed Approach:**
- Dashboard checkbox to enable/disable ("NakedTrader Weekly")
- At MARKET_OPEN, daemon runs NT workflow before the stock auto-scan
- Calls existing self-contained NT components directly: `get_underlying_price()` → `get_valid_expirations()` → `get_chain_with_greeks()` → `select_strike()` → `place_bracket_order()` → `record_trade()`
- Purely mechanical — no Claude AI, no autonomy governor (per rulebook Rule 10: "No Timing, No Indicators, No Opinions")
- Logs to working memory so Claude is aware of the position when reasoning about stock trades
- Duplicate guard: skip if open NT position already exists

**Config:** New `NakedTraderDaemonConfig` in phase5.yaml with `enabled`, `symbol` (XSP), `contracts` (3), `delay_seconds`, `skip_if_position_open`

**Files to modify:** `config.py`, `daemon.py`, `config_api.py`, `phase5.yaml` (zero changes to `src/nakedtrader/` components)

**Risks:** IBKR client contention (mitigated by sequential execution), margin overlap (mitigated by NT running first), duplicate positions (mitigated by open-position check)

**Plan detail:** Full implementation plan available at `.claude/plans/polymorphic-munching-seahorse.md`

---

### Adaptive Strike Selection at Market Open
**Status:** Proposed
**Date Added:** 2026-02-12
**Effort:** Medium (1-2 days)
**Value:** Significantly more trades executed; fewer stale rejections

**Problem Solved:**
Currently, Stage 2 validation (9:28 AM ET) checks if the *original staged strike* still meets OTM% and premium thresholds. If the stock moved overnight, the trade is flagged stale and cancelled — even though the *thesis* (sell a put at ~X% OTM for ~Y premium) is still valid. This killed all 13 candidates on Feb 11.

**Proposed Approach:**
Instead of validate-or-kill on the original strike:
1. **Fast path:** Check if original strike still works → proceed
2. **Adapt:** If not, find the new strike that gives the same OTM% as originally staged
3. **Validate premium:** Check if premium at new strike ≥ minimum threshold → swap strike and proceed
4. **Cancel only** if no strike within parameters has sufficient premium (opportunity genuinely gone)

**Key Benefits:**
- Turns rejections into adjustments — adapts to overnight moves
- Maintains risk parameters (OTM%, minimum premium)
- Much higher fill rate without compromising risk discipline
- Replaces the fragile "premium deviation %" check (Bug 6) with a fundamentally better approach

**Guards:**
- Max strike adjustment range (e.g., don't move more than 3 strikes)
- Liquidity check on new strike (volume/OI if available)
- If stock moved so far that no strike works, that's a genuine kill — not a false stale

**Dependencies:**
- Real-time option chain data at 9:28 AM (already available via IBKR)
- Bug 6 fix can be superseded by this approach

---

### Margin Over-Allocation with Fill-Rate Optimization
**Status:** Very low priority — future optimization
**Date Added:** 2026-02-21

**Problem:** When staging trades up to the 20% NLV margin ceiling, not all trades will fill. Unfilled trades leave margin headroom unused, reducing capital efficiency. The current approach (fill gaps with new trades next day) naturally converges but may take several days to fully utilize the budget.

**Proposed Approach:**
- Over-allocate staged margin by 10-20% beyond the ceiling
- At market open, feed orders into IBKR sequentially
- Monitor running margin in real-time via `FullMaintMarginReq`
- Stop submitting orders once actual IBKR margin hits the ceiling
- Cancel remaining unfilled orders

**Complexity:** High — requires real-time fill monitoring, order queue management, and a "stop feeding" mechanism. Risk of briefly exceeding the ceiling between fill and detection.

**When to revisit:** After Level B auto-select is live and fill-rate data shows consistent under-utilization (e.g., <70% of staged trades filling).

---

### Automated TWS Startup via IBC (Interactive Brokers Controller)
**Status:** Planned — investigate separately as part of installation process
**Date Added:** 2026-03-04
**Effort:** Medium (1-2 days setup + testing)
**Value:** Eliminates the "forgot to start TWS" failure mode — fully autonomous startup chain

**Problem Solved:**
- If TWS is not running when the daemon starts, the entire trading session is lost (March 3 US session: zero trades placed)
- The daemon's reconnection logic (periodic retry) can recover once TWS is up, but if the user forgets to start TWS altogether, the session is wasted with no recovery
- Current workflow requires manual TWS startup before or shortly after daemon start

**Proposed Approach:**
- Use [IBC](https://github.com/IbcAlpha/IBC) (Interactive Brokers Controller) to automate TWS/Gateway startup
- IBC handles login, 2FA, and keeps TWS running with auto-restart on crash
- Configure as a launchd service that starts before the daemon (dependency chain: IBC → TWS → daemon)
- Daemon's periodic reconnection loop handles the timing gap (TWS takes ~30-60s to initialise after IBC starts it)

**Installation Steps (to investigate):**
1. Install IBC alongside TWS
2. Configure `config.ini` with login credentials (encrypted) and TWS settings
3. Create launchd plist for IBC service (start on boot/login)
4. Ensure daemon's launchd plist depends on IBC service being loaded
5. Test: reboot → IBC starts TWS → daemon connects automatically

**Guards & Risks:**
- IBC needs its own credential management (separate from .env)
- TWS requires a display — may need headless mode (IB Gateway) or keep a GUI session active
- Must not conflict with user manually opening TWS for interactive use
- IBC auto-restarts TWS on crash — could mask underlying TWS issues
- 2FA handling: IBC supports IBKR's 2FA but needs initial setup

**References:**
- IBC GitHub: https://github.com/IbcAlpha/IBC
- IBC User Guide: https://github.com/IbcAlpha/IBC/wiki/User-Guide
- Incident: `docs/LOG_ANALYSIS_2026-03-03_US.md` — full session lost due to TWS not running

---

### Multi-Account Support
**Status:** Future consideration
**Date Added:** TBD

Support multiple IBKR accounts (live + paper, or multiple strategies)

---

## How to Use This Document

1. **Adding Items:** Add to appropriate priority section with full context
2. **Updating Status:** Move items between sections as priorities change
3. **Completing Items:** Move to "Completed Items" with resolution notes
4. **Reviewing:** Review quarterly to reprioritize

---

## Quick Reference Template

```markdown
#### [Feature Name]
**Status:** [Planned/In Progress/Blocked]
**Date Added:** YYYY-MM-DD
**Effort:** [Small/Medium/Large] (time estimate)
**Value:** [Business value / problem solved]

**Problem Solved:**
- Bullet points

**Implementation:**
- High-level steps

**Dependencies:**
- What's needed

**References:**
- Links
```
