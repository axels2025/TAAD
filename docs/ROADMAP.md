# Trading System Roadmap

**Last Updated:** 2026-02-21

This document tracks future improvements, enhancements, and technical debt for the trading system.

---

## Priority Levels

- 🔴 **CRITICAL** - System stability, data integrity issues
- 🟡 **HIGH** - Important improvements, significant value
- 🟢 **MEDIUM** - Nice-to-have enhancements
- 🔵 **LOW** - Future considerations, exploratory

---

## Current Backlog

### 🔴 CRITICAL

#### Automated Candidate Selection (Auto-Select)
**Status:** In Progress — Phase 1
**Date Added:** 2026-02-21
**Effort:** Large (12-16 days across 5 phases)
**Value:** Eliminates manual scanning/selection bottleneck; risk-first trade selection

**Problem Solved:**
- Scanner currently requires manual strike selection per stock and hardcodes `contracts: 1`
- No budget/margin awareness at scan time — can stage more than account can handle
- Claude "Ask AI" prompt only sees symbol names, not prices/IV/Greeks/margin
- No automated way to find the lowest-risk strike that still meets premium threshold

**Automation Levels:**

| Level | Name | Description |
|-------|------|-------------|
| **A** | Assisted | Scanner suggests quantity + highlights best strikes; user clicks each |
| **B** | Semi-Auto | "Auto-Select Best" button runs full pipeline; user reviews in Tonight's Lineup |
| **C** | Full Auto | Daemon scans + selects + stages on schedule (e.g., Sunday 6 PM) |

**Phased Implementation:**

| Phase | Name | Key Deliverable |
|-------|------|-----------------|
| 1 | Foundation | Settings panel in Scanner, quantity input, budget display |
| 2 | Smart Strike Selection | Best-strike algorithm per symbol, batch chains, improved Claude prompt |
| 3 | Auto-Select (Level B) | One-click portfolio building within margin budget |
| 4 | Full Automation (Level C) | Daemon-driven scheduled scanning + staging |
| 5 | Learning Integration | Track config vs outcomes, A/B test scoring weights |

**Key Design Decisions:**
- Risk-first ranking: 40% safety + 30% liquidity + 20% AI + 10% efficiency (configurable)
- Delta sweet spot: 0.065 (from SPX rulebook), search range 0.05-0.30, configurable
- Min OTM: 10% (not 5%) for stock naked puts
- One best strike per symbol: composite-scored including margin requirement
- Shortest DTE preferred for faster capital turnover
- Margin budget: 20% of NLV (conservative start, increase as confidence grows)
- Max 5 underlyings per sector (up from 3)
- **No IBKR = no trade** — no fallback to estimated margin or default budget
- **Live data only** — both semi-auto and full-auto scan at market open with live data
- Margin calculated per-strike during selection (Phase 2), not just portfolio ranking
- Claude receives margin data to make better qualitative assessments
- Override button for testing during closed markets (stale data warning)

**References:**
- Full implementation plan: `docs/AUTOSELECT_IMPLEMENTATION_PLAN.md`
- SPX rulebook: `docs/research/spx-options-trading-rulebook-2026-02-17.md`

---

### 🟡 HIGH PRIORITY

#### Option C: IBKR Flex Queries for Nightly Sync
**Status:** Logged for future implementation
**Date Added:** 2026-02-07
**Effort:** Medium (1-2 days setup + implementation)
**Value:** Eliminates all execution tracking gaps

**Problem Solved:**
- Captures same-day manual round-trips
- 100% accurate entry/exit prices (no avgCost estimation)
- No 24-hour API limitation
- Official IBKR data as source of truth

**Implementation Steps:**
1. Set up Flex Web Service in IBKR Account Management
2. Create Trade Confirmation Flex Query template
3. Generate API token
4. Implement `src/services/flex_query_sync.py`
5. Add `reconcile-eod` command using Flex Query
6. Schedule nightly via cron

**Dependencies:**
- ibflex Python library
- One-time IBKR Account Management setup

**References:**
- Research: `/docs/research/flex_queries_research_2026-02-07.md`
- Flex Web Service: https://www.interactivebrokers.com/campus/ibkr-api-page/flex-web-service/
- ibflex library: https://github.com/csingley/ibflex

---

### 🟢 MEDIUM PRIORITY

#### Historical Trade Import via Flex Queries
**Status:** Planned
**Date Added:** 2026-02-07
**Effort:** Small (1 day)
**Value:** One-time backfill capability

**Problem Solved:**
- Import trades from before system was active
- Disaster recovery (rebuild database from IBKR)
- Onboarding with existing trading history

**Implementation:**
- New command: `import-historical-trades --start-date 2025-01-01 --end-date 2026-02-07`
- Uses Activity Flex Query with date range
- Creates trade records with full execution data

---

#### Commission Tracking & Analysis
**Status:** Planned
**Date Added:** 2026-02-07
**Effort:** Small (half day)
**Value:** More accurate learning engine

**Enhancement:**
- Add `commission` field to trades table
- Capture actual commissions from Flex Queries
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
**Long-term:** Option C (Flex Queries) eliminates root cause

---

## Decision Log

### 2026-02-21: Auto-Select — Level B First, Risk-First Scoring

**Context:** Need to automate the manual scan → select → stage workflow

**Decisions:**
- Start with Level B (semi-auto: one-click "Auto-Select Best") before Level C (full daemon automation)
- Ranking weights: safety 40%, liquidity 30%, AI 20%, efficiency 10% — configurable via settings panel
- Delta target: 0.065 (from SPX weekly rulebook), search range 0.05-0.30
- Min OTM: 10% for stock naked puts (XSP/SPX system uses different rules)
- Per-symbol: pick single best strike using composite score (includes margin)
- DTE: prefer shortest expiration for faster capital turnover
- Settings page: collapsible panel inside Scanner page (not a separate tab)
- Track configuration snapshots per trade for learning engine correlation
- Margin budget: 20% of NLV (conservative start). No fallback — IBKR required.
- Max per sector: 5 underlyings (realistic diversification)
- Both B and C run at market open with live data — better trades than Sunday staging
- Auto-execute at open (L2+), user reviews results afterward (Australian timezone)
- Override button for testing pipeline during closed markets

**Rationale:**
- Risk reduction is more important than premium collected
- Liquidity ranked above efficiency because "can I exit?" is a risk concern
- No IBKR fallback because margin calls are worse than missed trades
- Live data at market open eliminates overnight gap staleness problem
- Configurable weights enable future A/B testing via learning engine
- 20% NLV budget is conservative — increase as paper trading proves the system

**Trade-offs Accepted:**
- Phase 1-3 still requires user to trigger "Auto-Select" manually during market hours
- Full automation (Level C) deferred until confidence in scoring proven via paper trading
- No trading when IBKR is offline — accepted as a feature, not a limitation

---

### 2026-02-07: Chose Option A Over Option C

**Context:** Entry premium sync issue - multiple solutions available

**Decision:**
- Fix current data manually
- Use execute-two-tier for all new positions
- Run nightly reconcile-positions
- Log Option C (Flex Queries) for future

**Rationale:**
- Option A works with current code
- Minimal complexity
- Option C deferred until proven need
- Can implement Option C later without rework

**Trade-offs Accepted:**
- Cannot do same-day manual round-trips
- Manual positions imported with estimated entry prices
- 24-hour execution window limitation

---

## Ideas / Exploratory

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

### Market Regime Detection Enhancement
**Status:** Exploratory idea
**Date Added:** TBD

Improve learning engine with market regime classification (high VIX, low VIX, trending, ranging)

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
