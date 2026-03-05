# Position Exit Rearchitecture: Rule Engine + Claude Ambiguity Resolver

**Created:** 2026-03-05
**Status:** Phase A in progress

---

## Problem

The current `POSITION_EXIT_CHECK` system routes ALL positions through Claude when
they hit +50% profit or -100% loss. Claude applies deterministic threshold rules
(+70% = close, -150% = close) and wraps them in prose. This is a rules engine with
extra latency, API cost, and hallucination surface.

Claude adds no value over `if pnl >= 70: close()` for clear-cut cases.

## Solution Architecture

```
Rule engine (Python, zero cost, instant):
  pnl >= +70%      -> CLOSE (deterministic)
  pnl <= -150%     -> CLOSE (deterministic)
  dte == 0         -> CLOSE or let-expire (deterministic)

Grey zone -> Claude ambiguity resolver:
  +50% <= pnl < +70%  AND  dte <= 3       -> "approaching target, little time left"
  -70% <= pnl         AND  delta > 0.25   -> "stressed, delta deteriorating"
  any pnl              AND  abs(delta) > 0.35  -> "delta creep, assignment risk"
  +40% <= pnl < +70%  AND  dte <= 2       -> "expiration dynamics"
```

Claude's role: resolve genuine ambiguity where multiple valid considerations conflict.

---

## Phase A: Prompt + Pre-Filter (Current Sprint)

**Goal:** Stop wasting Claude API calls on obvious decisions. Enrich ambiguous
escalations with available data.

### Changes

1. **Rule-engine pre-filter in `_emit_material_position_checks`**
   - Positions hitting hard thresholds (>=+70%, <=-150%, DTE=0) are closed
     deterministically via ExitManager — no Claude call, no approval queue
   - Only grey-zone positions are emitted as `POSITION_EXIT_CHECK` events
   - Event payload includes `escalation_reason` explaining WHY the rule engine
     couldn't decide

2. **New system prompt** (`POSITION_EXIT_SYSTEM_PROMPT`)
   - Removes deterministic threshold instructions (rule engine handles those)
   - Requires TENSION in reasoning (what makes this genuinely ambiguous)
   - Adds `learning_signal` field for pattern detection
   - Includes confidence calibration guidance

3. **Enriched user message** (`_build_position_exit_message`)
   - Queries `PositionSnapshot` for current delta, theta, IV, stock_price
   - Queries historical snapshots for delta_trend (last 3 days)
   - Adds sector context (other positions in same sector)
   - Fields not yet available: `"Unknown"`

4. **Response parsing** — extract `learning_signal` from Claude's response

### Data Available Today (from PositionSnapshot table)

| Field | Source | Available? |
|-------|--------|-----------|
| delta (current) | PositionSnapshot.delta | Yes (when snapshot exists) |
| theta | PositionSnapshot.theta | Yes |
| iv | PositionSnapshot.iv | Yes |
| stock_price | PositionSnapshot.stock_price | Yes |
| distance_to_strike_pct | PositionSnapshot.distance_to_strike_pct | Yes |
| delta_trend (multi-day) | Query last 3 snapshots | Yes (computed) |
| entry_delta | Not recorded at entry | No -> "Unknown" |
| entry_iv | Not recorded at entry | No -> "Unknown" |
| iv_trend | Compute from snapshots | Yes (computed) |
| stock_trend | Compute from snapshots | Yes (computed) |
| earnings_within_dte | No calendar integration | No -> "Unknown" |
| portfolio_delta | portfolio_greeks.py | Partial -> best effort |
| margin_utilisation | Not tracked | No -> "Unknown" |
| sector_positions | sector_map.py + open trades | Yes |

### Files Modified

- `src/agentic/daemon.py` — `_emit_material_position_checks` pre-filter
- `src/agentic/reasoning_engine.py` — new system prompt + enriched user message + response parsing
- `src/agentic/config.py` — add `learning_signal` to DecisionOutput if needed

---

## Phase B: Data Pipeline Enrichment

**Goal:** Fill in the "Unknown" fields from Phase A with real data.
**Status:** Complete

### Changes

1. **~~Record entry Greeks at trade time~~** — NOT NEEDED
   - Discovered `TradeEntrySnapshot` table already captures entry delta, IV,
     stock_price at trade open time. No new columns on Trade model needed.
   - Query `TradeEntrySnapshot` by trade_id in `_get_latest_snapshot_data()`

2. **Delta trend computation** — Done (Phase A)
   - Query last 3 PositionSnapshots for the position
   - Compute trajectory: "deteriorating: 0.12 -> 0.19 -> 0.26 over 3 days"
   - Implemented in `_get_latest_snapshot_data()` in daemon.py

3. **IV trend computation** — Done (Phase A)
   - Same approach: last 3 snapshots, compute "expanding", "stable", "crushing"

4. **Stock trend computation** — Done (Phase A)
   - From snapshots: stock_price over last 3 days
   - Classify: "downtrend", "sideways", "recovering"

5. **Portfolio delta from portfolio_greeks.py** — Done
   - Queried once before the position loop in `_emit_material_position_checks()`
   - Passed as `portfolio_delta` in event payload

6. **Replace "Unknown" placeholders in user message** — Done
   - Entry IV, entry delta, entry stock price now populated from TradeEntrySnapshot
   - Portfolio delta added to Context section
   - All fields use `fmt()` helper for graceful null handling

### Files Modified

- `src/agentic/daemon.py` — `_get_latest_snapshot_data()` queries TradeEntrySnapshot;
  `_emit_material_position_checks()` queries portfolio delta
- `src/agentic/reasoning_engine.py` — replaced "Unknown" with real data from snapshot

---

## Phase C: Advanced Context
**Goal:** Add the genuinely hard-to-get context that makes Claude's judgment most valuable.
**Status:** Complete

### Changes

1. **Earnings calendar integration** — Done
   - Uses existing `EarningsService` + `get_cached_earnings()` (Yahoo Finance, 24h cache)
   - Queries per-position in `_emit_material_position_checks()`
   - Populates `earnings_in_dte`, `days_to_earnings`, `earnings_date` in event payload
   - Replaces "Unknown" in user message with formatted earnings proximity

2. **OpEx week detection** — Done
   - Extracted `is_opex_week()` as module-level function from `MarketContextService._is_opex_week()`
   - Computed once before the position loop in daemon.py
   - Replaces "Unknown" in user message

3. **Margin utilisation** — Done
   - Queries `get_account_summary()` once before position loop
   - Computes `MaintMarginReq / NetLiquidation * 100` as percentage
   - Added as new line in Context section of user message

4. **Sector stress flag** — Done
   - Enhanced `_get_sector_context()` to flag peers with P&L < -50%
   - Appends "⚠ N peer(s) also stressed" when sector concentration + stress overlap

### Deferred

| Item | Reason |
|------|--------|
| IV rank/percentile | Needs 52-week historical IV per underlying — requires new data pipeline |
| Full correlation matrix | Sector stress flag covers 90% of the value |
| VIX percentile rank | Nice-to-have proxy; can add later from VIX history |

### Files Modified

- `src/agentic/daemon.py` — earnings, opex, margin queries + sector stress flag
- `src/agentic/reasoning_engine.py` — replaced "Unknown" placeholders, added margin line + `_format_earnings()` helper
- `src/services/market_context.py` — extracted `is_opex_week()` as standalone function

---

## Success Criteria

### Phase A
- [ ] Hard-threshold positions close without Claude API call
- [ ] Grey-zone positions escalate with `escalation_reason`
- [ ] Claude's reasoning includes TENSION section
- [ ] `learning_signal` captured in decision audit
- [ ] API cost reduction observable (fewer POSITION_EXIT_CHECK calls to Claude)

### Phase B
- [x] Entry Greeks available via TradeEntrySnapshot (already recorded)
- [x] Delta/IV/stock trends computed from historical snapshots
- [x] No "Unknown" for fields that have snapshot data
- [x] Portfolio delta wired into exit check context

### Phase C
- [x] Earnings data populated for positions with upcoming earnings
- [x] OpEx week detection wired into user prompt
- [x] Portfolio-level margin context in user prompt
- [x] Sector stress flag for correlated peer losses
- [ ] IV rank context available (deferred — needs data pipeline)
