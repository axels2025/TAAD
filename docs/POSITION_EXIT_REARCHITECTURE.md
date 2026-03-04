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

## Phase B: Data Pipeline Enrichment (Next Session)

**Goal:** Fill in the "Unknown" fields from Phase A with real data.

### Changes

1. **Record entry Greeks at trade time**
   - When a trade is opened, capture delta, IV, stock_price from IBKR
   - Store as `entry_delta`, `entry_iv`, `entry_stock_price` on the Trade model
   - Schema migration for new columns

2. **Delta trend computation**
   - Query last 3-5 PositionSnapshots for the position
   - Compute trajectory: "deteriorating: 0.12 -> 0.19 -> 0.26 over 3 days"
   - Already possible with existing snapshot data — just needs the query

3. **IV trend computation**
   - Same approach: last 3-5 snapshots, compute "expanding", "stable", "crushing"

4. **Stock trend computation**
   - From snapshots: stock_price over last 3-5 days
   - Classify: "downtrend", "sideways", "recovering"

5. **Portfolio delta from portfolio_greeks.py**
   - Already exists — wire into the exit check context

### Files Modified

- `src/data/models.py` — add entry_delta, entry_iv columns to Trade
- `src/data/database.py` — schema migration
- `src/nakedtrader/trade_recorder.py` — capture entry Greeks
- `src/agentic/reasoning_engine.py` — use real data instead of "Unknown"

---

## Phase C: Advanced Context (Future)

**Goal:** Add the genuinely hard-to-get context that makes Claude's judgment most valuable.

### Changes

1. **Earnings calendar integration**
   - API source: Financial Modeling Prep, Alpha Vantage, or IBKR fundamentals
   - Check if earnings date falls within DTE window
   - Populate `earnings_within_dte: True/False`

2. **IV surface data**
   - IV rank and IV percentile for the underlying
   - Historical IV context: is current IV elevated vs. 30-day range?

3. **Margin utilisation**
   - Query IBKR account summary for margin usage
   - Relevant for portfolio-level exit priority decisions

4. **Correlation analysis**
   - When multiple positions are stressed, identify if they're correlated
   - "Two tech positions both approaching stop loss" is different from "one tech, one energy"

### Files Modified

- New: `src/services/earnings_calendar.py`
- `src/agentic/reasoning_engine.py` — add new context fields
- `config/phase5.yaml` — earnings API configuration

---

## Success Criteria

### Phase A
- [ ] Hard-threshold positions close without Claude API call
- [ ] Grey-zone positions escalate with `escalation_reason`
- [ ] Claude's reasoning includes TENSION section
- [ ] `learning_signal` captured in decision audit
- [ ] API cost reduction observable (fewer POSITION_EXIT_CHECK calls to Claude)

### Phase B
- [ ] Entry Greeks recorded on every new trade
- [ ] Delta/IV/stock trends computed from historical snapshots
- [ ] No "Unknown" for fields that have snapshot data

### Phase C
- [ ] Earnings data populated for positions with upcoming earnings
- [ ] IV rank context available
- [ ] Portfolio-level margin context in user prompt
