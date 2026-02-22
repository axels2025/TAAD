# Implementation Plan: Naked Put Automation Fixes

**Date:** February 7, 2026  
**Scope:** Section-by-section implementation plan addressing ALL findings from Code Review Mapping  
**Approach:** Walk through each research section in order, fix everything — regardless of severity  
**Assumption:** All items marked ✅ IMPLEMENTED in the Code Review stay as-is unless explicitly noted

---

## How to Use This Plan

Each fix below is self-contained with:
- **Finding** — what the Code Review found
- **Root Cause** — why it's broken or missing
- **Fix** — exactly what to implement
- **Files to Modify/Create** — specific paths
- **Acceptance Criteria** — how to verify it works

Sections are ordered 1 → 9 matching the research document, not by severity. Implement them sequentially — some later sections depend on earlier ones.

---

## SECTION 1: PRE-TRADE — Finding & Qualifying Candidates

### 1.2A — OTM % Filter Not Explicit in Screener (⚠️ PARTIAL)

**Finding:** OTM% is calculated in scoring and validation, but not enforced as a hard filter. Delta serves as a proxy at the Barchart API stage, but a -0.20 delta on a $500 stock vs a $30 stock produces very different OTM percentages.

**Fix:** Add an explicit OTM% client-side filter to the Barchart result processing pipeline. After Barchart results are returned and before scoring, reject any candidate where OTM% falls outside the configured range.

**Files to modify:**
- `src/config/naked_put_options_config.py` — Add `otm_pct_min: float = 0.10` and `otm_pct_max: float = 0.25` to `BarchartScreenerSettings`
- `src/tools/barchart_csv_parser.py` — Add OTM% calculation and filter after parsing: `otm_pct = (stock_price - strike) / stock_price`. Reject candidates outside range.
- `src/tools/barchart_scanner.py` — Same OTM% filter applied to API results

**Acceptance criteria:**
- Candidates with OTM% < 10% or > 25% are excluded from results
- Filtered candidates are logged with reason "OTM% X.X% outside range 10-25%"
- Config values are loaded from .env (`BARCHART_OTM_PCT_MIN`, `BARCHART_OTM_PCT_MAX`)

---

### 1.2B — IV Rank vs. Raw IV Confusion in Screener (⚠️ PARTIAL)

**Finding:** Config filters on raw IV (0.30–0.80) rather than IV Rank/Percentile. A stock with historically high IV of 80% showing current IV of 30% has a low IV Rank (~10%) — meaning premiums are cheap relative to that stock's history. The scoring engine handles IV Rank correctly, but the screener lets low-IV-Rank candidates through the initial filter.

**Fix:** Rename the config fields to clarify they filter raw IV (which is what Barchart provides). Add an IV Rank minimum threshold that is enforced during IBKR validation, where we can compute IV Rank from historical IV data. Do NOT try to filter by IV Rank at the Barchart stage — Barchart returns raw IV, not rank.

**Files to modify:**
- `src/config/naked_put_options_config.py` — Rename `iv_min`/`iv_max` to `raw_iv_min`/`raw_iv_max` with clear docstring explaining this is raw IV, not rank. Add `iv_rank_min: float = 0.30` to `IBKRValidationSettings` (30% IV Rank minimum)
- `src/tools/ibkr_validator.py` — During IBKR validation, calculate IV Rank for each candidate: `iv_rank = (current_iv - 52wk_low_iv) / (52wk_high_iv - 52wk_low_iv)`. Reject candidates below `iv_rank_min`. If historical IV data unavailable, log warning and pass the candidate through (don't block on data gaps).

**Acceptance criteria:**
- Config clearly distinguishes raw IV (Barchart) from IV Rank (IBKR validation)
- Candidates with IV Rank below 30% are filtered out during IBKR validation
- Missing historical IV data logs a warning but doesn't block the candidate
- Existing scoring engine IV Rank logic remains unchanged

---

### 1.2C — DTE Minimum of 0 Allows Risky 0-DTE Options (🟢 LOW)

**Finding:** `dte_min=0` in config allows same-day expiration options to pass screening. For naked puts, 0 DTE means near-zero time for recovery if the trade goes against you, extreme gamma risk, and potential for after-hours assignment.

**Fix:** Change the default `dte_min` from 0 to 7 in the config. This is a one-line change.

**Files to modify:**
- `src/config/naked_put_options_config.py` — Change `dte_min: int = Field(default=0, ...)` to `default=7`. Update description to explain why 7 is the safe minimum for naked puts.

**Acceptance criteria:**
- Default DTE minimum is 7
- Barchart API call sends `minDTE=7`
- Can still be overridden via `.env` (`BARCHART_DTE_MIN=0`) for testing

---

### 1.3 — Earnings Calendar Not Wired Into Screening/Execution (🔴 CRITICAL)

**Finding:** `earnings_service.py` exists with Yahoo Finance and FMP integrations but is ONLY used for data capture in `entry_snapshot.py`. It is never called as a pre-trade filter or gate. The screening pipeline, risk governor, and order executor never import or call the earnings service. The system will sell naked puts into earnings events.

**Root cause:** The earnings service was built during the learning engine phase (Phase 2.6C) for data collection, not trade gating. Nobody wired it into the pre-trade validation chain.

**Fix:** Integrate earnings checking at TWO points in the pipeline:

**Point 1 — Screening stage (Barchart result processing):** After Barchart results are parsed and before scoring, check each candidate's earnings date. If earnings fall within the option's DTE window, mark the candidate as `earnings_blocked=True` and exclude it from the scored candidate list.

**Point 2 — Risk governor (pre-trade check):** Add a new check `_check_earnings_risk()` to the risk governor's `pre_trade_check()` chain. This catches any candidate that bypassed Point 1 (e.g., manual trade entry). If earnings fall within DTE, reject the trade with `reason="Earnings within DTE window"`.

**Files to modify:**
- `src/tools/barchart_csv_parser.py` — After parsing each candidate, call `get_cached_earnings(symbol, option_expiration)`. If `earnings_in_dte == True`, exclude the candidate and log "BLOCKED: {symbol} has earnings on {date}, within DTE window"
- `src/tools/barchart_scanner.py` — Same earnings check for API results
- `src/execution/risk_governor.py` — Add `_check_earnings_risk(opportunity)` method:
  1. Import `get_cached_earnings` from `src.services.earnings_service`
  2. Call `get_cached_earnings(opportunity.symbol, opportunity.expiration)`
  3. If `earnings_in_dte == True`, return `RiskLimitCheck(approved=False, reason="Earnings on {date} falls within DTE", limit_name="earnings_check", ...)`
  4. If earnings data unavailable (service returns None), log warning and PASS (don't block on data gaps — but do log it prominently)
  5. Add this check to `pre_trade_check()` chain between duplicate check and daily loss check
- `src/execution/risk_governor.py` — Update `__init__` logging to include "Earnings check: Enabled"

**Acceptance criteria:**
- Candidates with earnings within DTE are excluded at screening
- Risk governor rejects trades with earnings within DTE
- Logs clearly show "BLOCKED: earnings within DTE" or "WARNING: earnings data unavailable"
- Earnings data is cached (24h TTL already implemented in earnings_service)
- Test: Create a mock opportunity with earnings 5 days out and DTE 10 → should be rejected
- Test: Create a mock opportunity with earnings 20 days out and DTE 10 → should pass

---

### 1.4 — Diversification Rules Incomplete (⚠️ PARTIAL)

**Finding:** `DiversificationRules` dataclass exists with `max_positions_per_symbol=3`, `max_same_expiration_pct=0.50`, and `max_sector_concentration_pct=0.40`. Sector tracking is marked "Future" and expiration clustering is not enforced in the scorer.

**Fix:** This will be fully addressed in Section 2.2 (sector concentration in risk governor) and Section 2.4 (diversification). The scoring engine's diversification rules are informational — the real enforcement happens in the risk governor. No changes to the scorer are needed here; it correctly penalizes concentrated positions in its ranking.

**Deferred to:** Section 2.2

---

## SECTION 2: PRE-TRADE — Risk & Margin Validation

### 2.1A — IBKR WhatIf Margin Validation Missing (🔴 CRITICAL)

**Finding:** Zero references to WhatIf orders anywhere in the codebase. The risk governor uses `opportunity.margin_required` (an estimate) compared against `AvailableFunds` from account summary. This is the root cause of the margin overrun bug.

**Root cause:** WhatIf order API was never implemented. The system relies on upstream margin estimates that can be wrong by 50-200%.

**Fix:** Implement a `WhatIfMarginCheck` service and integrate it into the risk governor as Layer 2 validation.

**Files to create:**
- `src/services/margin_checker.py` — New service:

```
class MarginChecker:
    """Three-layer margin validation using IBKR WhatIf orders."""
    
    def __init__(self, ibkr_client: IBKRClient):
        self.client = ibkr_client
    
    async def check_whatif_margin(self, contract, order) -> WhatIfResult:
        """Submit a WhatIf order to IBKR and return actual margin impact.
        
        Uses ib_insync's whatIfOrder() method:
            order.whatIf = True
            trade = ib.placeOrder(contract, order)
            # Returns OrderState with:
            #   - initMarginAfter
            #   - maintMarginAfter  
            #   - equityWithLoanAfter
            #   - commission
        
        Returns WhatIfResult with:
            - init_margin_after: float
            - maint_margin_after: float
            - equity_after: float
            - estimated_commission: float
            - margin_impact: float (how much NEW margin this trade requires)
            - is_acceptable: bool (margin_impact < per_trade_cap AND total < max_utilization)
            - reject_reason: str (if not acceptable)
        """
```

**Implementation details for `check_whatif_margin`:**
1. Create a copy of the order with `order.whatIf = True`
2. Create the option contract (same as `order_executor._place_order()`)
3. Qualify the contract
4. Call `self.client.ib.whatIfOrder(contract, order)` — this returns an `OrderState` object
5. Extract `initMarginAfter`, `maintMarginAfter`, `equityWithLoanAfter`
6. Get current account state: `initMarginBefore` from account summary
7. Calculate `margin_impact = initMarginAfter - initMarginBefore`
8. Check: `margin_impact <= per_trade_cap` (new check, see 2.1C)
9. Check: `initMarginAfter / equityWithLoanAfter <= max_margin_utilization`
10. Return `WhatIfResult` with all values

**Files to modify:**
- `src/tools/ibkr_client.py` — Add `async def whatif_order(self, contract, order) -> OrderState` method that wraps `ib.whatIfOrder()` with error handling, timeout (5s), and audit logging
- `src/execution/risk_governor.py` — Replace `_check_margin_utilization()` internals:
  1. Keep the current estimated check as Layer 1 (fast reject for obviously over-limit trades)
  2. If Layer 1 passes, call `MarginChecker.check_whatif_margin()` as Layer 2
  3. If WhatIf fails (API error, timeout), fall back to Layer 1 result with a WARNING log: "WhatIf unavailable, using estimate — proceed with caution"
  4. Log the WhatIf result: "WhatIf margin: estimated=${X}, actual=${Y}, delta=${Z}"

**Acceptance criteria:**
- `ibkr_client.whatif_order()` returns IBKR's actual margin impact
- Risk governor uses WhatIf before approving margin
- If WhatIf API fails, system falls back to estimate with prominent warning
- Margin impact is logged for every trade decision
- Test: Submit a WhatIf for a known position and verify `initMarginAfter` matches TWS display

---

### 2.1B — Post-Trade Margin Verification Missing (❌ MISSING)

**Finding:** After a trade fills, there is no check to verify that actual margin consumed matches expectations.

**Fix:** After a fill is confirmed in the order executor or rapid-fire executor, poll account summary and log the margin state.

**Files to modify:**
- `src/services/margin_checker.py` — Add method:
```
    async def verify_post_trade_margin(self) -> PostTradeMarginResult:
        """Poll account summary after fill and verify margin state.
        
        Returns PostTradeMarginResult with:
            - available_funds: float
            - excess_liquidity: float  
            - margin_utilization_pct: float
            - is_healthy: bool (excess_liquidity > 10% of net_liq)
            - warning: str (if approaching danger)
        """
```
- `src/execution/order_executor.py` — In `_place_order()`, after status is "Filled", call `margin_checker.verify_post_trade_margin()` and log result. If `is_healthy == False`, trigger `risk_governor.emergency_halt("Post-trade margin danger")`
- `src/services/rapid_fire_executor.py` — After all orders in a batch are submitted, call `verify_post_trade_margin()` once and log/alert

**Acceptance criteria:**
- Every filled trade triggers a post-trade margin verification
- If ExcessLiquidity drops below 10% of NetLiquidation, trading halts
- Margin state is logged after every fill: "Post-trade margin: AvailFunds=${X}, ExcessLiq=${Y}, Util=${Z}%"

---

### 2.1C — Per-Trade Margin Cap Missing (🟠 HIGH)

**Finding:** No limit on a single trade's margin impact. One large-cap naked put could consume 30%+ of buying power.

**Fix:** Add a per-trade margin cap check to the margin checker and risk governor.

**Files to modify:**
- `src/execution/risk_governor.py` — Add constant `MAX_MARGIN_PER_TRADE_PCT = 0.10` (10% of net liquidation). Add to `__init__` logging.
- `src/services/margin_checker.py` — In `check_whatif_margin()`, add check: if `margin_impact > net_liquidation * MAX_MARGIN_PER_TRADE_PCT`, reject with reason "Single trade margin ${X} exceeds {Y}% cap"
- `src/config/baseline_strategy.py` — The existing `max_risk_per_trade_pct=0.02` should be RENAMED to `max_margin_per_trade_pct=0.10` and wired into risk governor. Or add a new env var `MAX_MARGIN_PER_TRADE_PCT=0.10`.

**Acceptance criteria:**
- No single trade can consume more than 10% of net liquidation value in margin
- Rejection logged: "Trade rejected: margin impact ${X} exceeds 10% cap (${Y})"
- Configurable via .env

---

### 2.2 — Sector Concentration Check Is a Stub (🐛 BUG)

**Finding:** `_check_sector_concentration()` always returns `approved=True`. Logs misleading "Sector concentration within limit" message. The `StockUniverseManager` has explicit sector classifications for 550+ symbols across tiers, and `MarketContextService` has a `SECTOR_ETFS` mapping.

**Root cause:** The stock universe categorizes symbols by sector in comments, but this isn't exposed as a lookup function. The screener's `_get_sector()` method returns a hardcoded "Technology" placeholder.

**Fix:** Implement a real sector concentration check using a sector lookup table derived from the stock universe's existing categorization.

**Files to create:**
- `src/data/sector_map.py` — A static lookup dictionary mapping symbols to sectors:
```
SYMBOL_SECTOR_MAP: dict[str, str] = {
    # Mega Tech
    "AAPL": "Technology", "MSFT": "Technology", "GOOGL": "Technology", ...
    # Finance  
    "JPM": "Financials", "V": "Financials", "MA": "Financials", ...
    # Healthcare
    "UNH": "Healthcare", "JNJ": "Healthcare", "LLY": "Healthcare", ...
    # Consumer
    "WMT": "Consumer", "HD": "Consumer", ...
    # Energy
    "XOM": "Energy", "CVX": "Energy", ...
    # ETFs
    "SPY": "Index ETF", "QQQ": "Index ETF", ...
}

def get_sector(symbol: str) -> str:
    """Return sector for symbol, or 'Unknown' if not mapped."""
    return SYMBOL_SECTOR_MAP.get(symbol, "Unknown")
```

Build this from the existing tier definitions in `stock_universe.py`. The tier1 universe already has inline sector comments grouping all 50 symbols. Extract those into the map. For symbols not in the map, return "Unknown".

**Files to modify:**
- `src/execution/risk_governor.py` — Replace the stub `_check_sector_concentration()`:
  1. Import `get_sector` from `src.data.sector_map`
  2. Get all open positions from `self.position_monitor.get_all_positions()`
  3. Count positions per sector (using `get_sector(pos.symbol)`)
  4. Get the new trade's sector: `new_sector = get_sector(opportunity.symbol)`
  5. Calculate: `sector_count = count of existing positions in new_sector + 1`
  6. Calculate: `sector_pct = sector_count / total_positions_including_new`
  7. If `sector_pct > self.MAX_SECTOR_CONCENTRATION` (0.30), reject with reason "Sector {name} concentration {X}% exceeds {Y}% limit ({N} of {M} positions)"
  8. "Unknown" sector should be treated as its own sector for concentration purposes

**Acceptance criteria:**
- Sector concentration is actually checked against 30% limit
- If 3 of 9 positions are Technology and new trade is Technology, it's rejected (4/10 = 40% > 30%)
- Log shows real sector name and percentage, not "approved" for everything
- "Unknown" sectors are counted and concentrated against
- Test: Mock 3 existing tech positions + 1 new tech opportunity → rejected
- Test: Mock 3 existing tech positions + 1 new healthcare opportunity → approved

---

### 2.3A — Fixed Fractional Sizing Not Wired In (❌ DEAD CONFIG)

**Finding:** `max_risk_per_trade_pct=0.02` exists in `baseline_strategy.py` but is never referenced anywhere. System always trades the fixed `position_size=5` contracts.

**Fix:** Implement position sizing logic that calculates contracts based on risk percentage. This works alongside the per-trade margin cap from 2.1C. The system should use the SMALLER of: fixed position size and risk-calculated size.

**Files to create:**
- `src/services/position_sizer.py` — New service:
```
class PositionSizer:
    """Calculate position size based on risk limits.
    
    Uses fixed fractional method:
    - max_risk_amount = account_equity * max_risk_per_trade_pct
    - max_loss_per_contract = strike * 100 (theoretical max for naked put)
    - risk_based_contracts = floor(max_risk_amount / max_loss_per_contract)
    - final_contracts = min(risk_based_contracts, fixed_position_size)
    
    For practical purposes, use a more realistic max loss:
    - practical_max_loss = strike * 0.25 * 100 (25% stock drop = margin call level)
    - This gives more usable contract counts while still limiting risk
    """
    
    def __init__(self, ibkr_client, config):
        self.client = ibkr_client
        self.max_risk_pct = config.max_risk_per_trade_pct  # 0.02 = 2%
        self.fixed_size = config.position_size  # 5
    
    def calculate_contracts(self, opportunity) -> int:
        """Return the safe number of contracts to trade.
        
        Returns min(fixed_size, risk_based_size), minimum 1.
        """
```

**Files to modify:**
- `src/config/baseline_strategy.py` — Keep `max_risk_per_trade_pct=0.02` but add docstring explaining it's now actively enforced
- Wherever `opportunity.contracts` is set (likely in portfolio builder or scoring) — call `PositionSizer.calculate_contracts()` to set the contract count instead of blindly using `position_size`

**Acceptance criteria:**
- Contract count is calculated as `min(fixed_size, risk_based_size)`
- A $500 stock naked put with $100K account and 2% risk: max_risk = $2,000, practical_max_loss ≈ $12,500/contract → 1 contract (not 5)
- A $30 stock naked put with $100K account and 2% risk: max_risk = $2,000, practical_max_loss ≈ $750/contract → 2 contracts (not 5)
- Log shows: "Position size: {N} contracts (fixed={F}, risk-based={R}, using min)"

---

## SECTION 3: ORDER PLACEMENT & EXECUTION

### 3.2A — Order-Time Spread Validation Missing (🟡 MEDIUM)

**Finding:** Bid-ask spread is checked during screening (via `IBKRValidationSettings.max_spread_pct=0.20`) but NOT rechecked at the moment of order placement. Spreads can widen significantly between screening (Sunday) and execution (Monday 9:30 AM).

**Fix:** Add a spread check to the `AdaptiveOrderExecutor.place_order()` flow, right after `get_live_quote()` returns and before the order is created.

**Files to modify:**
- `src/services/adaptive_order_executor.py` — In `place_order()`, after getting the `LiveQuote`:
  1. Calculate spread: `spread_pct = (quote.ask - quote.bid) / quote.bid if quote.bid > 0 else 999`
  2. If `spread_pct > self.max_spread_pct` (load from env, default 0.30 — slightly more lenient at execution than screening): return `OrderResult(success=False, error_message=f"Spread {spread_pct:.0%} exceeds max {max_spread_pct:.0%}")` 
  3. Log: "Spread check: bid=${bid}, ask=${ask}, spread={pct}% — {'OK' if pass else 'WIDE'}"

**Acceptance criteria:**
- Orders with spreads wider than 30% are not placed
- Logged for every order attempt
- Configurable via env var `MAX_EXECUTION_SPREAD_PCT=0.30`

---

### 3.2B — Price Stability Check Missing (❌ MISSING)

**Finding:** No check for rapid price movement before order placement. The staged limit price from Sunday could be far from Monday's reality even after the two-stage validation catches large moves.

**Fix:** In the adaptive order executor, compare the live quote's calculated limit against the staged limit. If deviation exceeds a threshold, log a warning. The existing `limit_deviation` field in `OrderResult` already captures this data, but there's no threshold-based rejection.

**Files to modify:**
- `src/services/adaptive_order_executor.py` — In `place_order()`, after calculating the live limit:
  1. `deviation_pct = abs(quote.limit - staged.staged_limit_price) / staged.staged_limit_price if staged.staged_limit_price > 0 else 0`
  2. If `deviation_pct > 0.50` (50%): return `OrderResult(success=False, error_message=f"Price unstable: live limit ${quote.limit:.2f} vs staged ${staged.staged_limit_price:.2f} ({deviation_pct:.0%} deviation)")` 
  3. If `deviation_pct > 0.20` (20%): log WARNING but proceed

**Acceptance criteria:**
- >50% deviation between staged and live limit → order rejected
- 20-50% deviation → warning logged, order proceeds
- Deviation stored in `OrderResult.limit_deviation` (already exists)

---

### 3.4A — Premium Sanity Check Incomplete (⚠️ PARTIAL)

**Finding:** `order_executor._validate_trade()` checks `premium > 0` but does not check for suspiciously HIGH premiums that could indicate a data error or an ITM option.

**Fix:** Add an upper bound premium check.

**Files to modify:**
- `src/execution/order_executor.py` — In `_validate_trade()`, add:
  1. `if opportunity.premium > opportunity.strike * 0.20:` — A premium > 20% of strike is suspicious for an OTM put. Return `{"valid": False, "reason": f"Premium ${opportunity.premium} suspiciously high (>{20% of strike})"}`
  2. Log at WARNING level

**Acceptance criteria:**
- Premium > 20% of strike price → trade rejected with reason
- Normal premiums (1-5% of strike) pass without issue

---

### 3.4B — Market Hours Not Enforced at Order-Executor Level (🟢 LOW)

**Finding:** The execution scheduler handles timing, but a direct call to `OrderExecutor.execute_trade()` would work outside market hours.

**Fix:** Add a market hours check at the beginning of `execute_trade()`.

**Files to modify:**
- `src/execution/order_executor.py` — In `execute_trade()`, before any validation:
  1. Import `MarketCalendar` from `src.services.market_calendar`
  2. `session = MarketCalendar().get_current_session()`
  3. If `session not in (MarketSession.REGULAR, MarketSession.PRE_MARKET)` and `not self.dry_run`: return `OrderResult(success=False, reason="Market closed", status=OrderStatus.REJECTED)`
  4. Allow PRE_MARKET because staged orders may be submitted slightly before 9:30

**Acceptance criteria:**
- Orders outside market hours (weekends, holidays, after-hours) are rejected
- Dry-run mode bypasses this check (for testing)
- Pre-market submission is allowed (needed for 9:30 AM execution)

---

## SECTION 4: POSITION MONITORING

### 4.2A — Delta Breach Alert Missing (🟡 MEDIUM)

**Finding:** No monitoring for delta exceeding -0.30 or -0.50 thresholds, which would indicate the option is moving closer to ITM.

**Fix:** Add delta-based alerts to `PositionMonitor.check_alerts()`.

**Files to modify:**
- `src/execution/position_monitor.py` — In `check_alerts()`, after existing alert checks, add:
  1. If `position.delta is not None` (Greeks may be unavailable):
     - If `abs(position.delta) > 0.50`: append CRITICAL alert "Delta breach: {symbol} delta={delta} (>0.50, deep ITM risk)"
     - If `abs(position.delta) > 0.30`: append WARNING alert "Delta elevated: {symbol} delta={delta} (>0.30, thesis weakening)"
  2. Only fire if `position.delta` is not None — don't alert on missing data

**Acceptance criteria:**
- Delta > 0.30 → WARNING alert
- Delta > 0.50 → CRITICAL alert
- No false alerts when Greeks are unavailable (None)

---

### 4.2B — Underlying Price Drop Alert Missing (🟡 MEDIUM)

**Finding:** No alert when the underlying stock drops significantly from entry, which is a leading indicator before option premium reflects it.

**Fix:** This requires knowing the stock price at entry. The `TradeEntrySnapshot` captures `stock_price_at_entry`. Use this to calculate the percentage drop.

**Files to modify:**
- `src/execution/position_monitor.py` — In `_get_position_status()`:
  1. After getting the live quote for the option, also get the current stock price (use `ibkr_client.get_quote()` on a `Stock(symbol)` contract or look up from the option's underlying data)
  2. Add `underlying_price: float | None = None` field to `PositionStatus` dataclass
- `src/execution/position_monitor.py` — In `check_alerts()`:
  1. Look up entry stock price from database (query `TradeEntrySnapshot` for the trade)
  2. If `underlying_price` available and entry price available: `drop_pct = (entry_price - current_price) / entry_price`
  3. If `drop_pct > 0.05`: WARNING "Underlying {symbol} down {pct}% from entry (${entry} → ${current})"
  4. If `drop_pct > 0.10`: CRITICAL "Underlying {symbol} down {pct}% — review position"

**Note:** This adds an extra IBKR data request per position. If performance is a concern, only fetch when position count is manageable (< 15). With max 10 positions, this is fine.

**Acceptance criteria:**
- 5%+ stock drop from entry → WARNING
- 10%+ stock drop from entry → CRITICAL
- Works even when option Greeks are unavailable
- No alert when stock data is unavailable

---

### 4.3A — Position Monitor `_save_position_to_db()` Never Persists (🐛 BUG)

**Finding:** The method creates a `Position` object but the actual save line is commented out. Logs "Position saved to database" without saving.

**Fix:** Implement the repository save call.

**Files to modify:**
- `src/execution/position_monitor.py` — In `_save_position_to_db()`:
  1. Uncomment or implement `self.position_repository.create_or_update(position)` 
  2. If `create_or_update` doesn't exist on the repository, implement it: query by `position_id`, update if exists, insert if not
  3. Fix the Position object construction — it currently uses placeholder values for `trade_id`, `expiration`, and `entry_date`. Use the actual data from `PositionStatus`
- `src/data/repositories.py` — Ensure `PositionRepository` has a `create_or_update(position)` method that does an upsert on `position_id`

**Acceptance criteria:**
- Position snapshots are actually written to database
- Log message matches reality
- Subsequent calls for the same position update rather than insert duplicate

---

### 4.4 — Position Monitor 15-Min Interval Too Slow (🟢 LOW)

**Finding:** `update_interval_minutes=15` is too slow for account health monitoring. Margin health should be checked every 1-5 minutes during market hours.

**Fix:** Separate position monitoring (15 min is fine) from account health monitoring (needs to be faster). The account health check is lightweight (single account summary API call) vs. full position update (requires market data per position).

**Files to modify:**
- `src/services/margin_checker.py` (already being created in 2.1A) — Add `async def check_account_health() -> AccountHealthResult` that polls `NetLiquidation`, `AvailableFunds`, `ExcessLiquidity`, `MaintMarginReq` and returns health status
- `src/execution/risk_governor.py` — Add `ACCOUNT_HEALTH_INTERVAL_MINUTES = 5`. In `pre_trade_check()`, if more than 5 minutes since last health check, call `margin_checker.check_account_health()` before processing. Cache the result.

**Acceptance criteria:**
- Account health polled at least every 5 minutes during active monitoring
- Position data still updates at 15 minute intervals
- Health check is a single API call, not full position refresh

---

## SECTION 5: EXIT MANAGEMENT

### 5.3 — Rolling Positions Not Implemented (🟡 MEDIUM)

**Finding:** No rolling functionality anywhere. System can only hold or close. Rolling (closing current position and opening a new one further out) provides a defensive adjustment for positions under temporary pressure.

**Fix:** Implement a `RollManager` service with roll-out and roll-down-and-out capabilities.

**Files to create:**
- `src/services/roll_manager.py`:

```
class RollManager:
    """Manage defensive position rolls for naked puts.
    
    Roll types:
    - Roll out: Same strike, later expiration (more time)
    - Roll down and out: Lower strike, later expiration (more safety + time)
    
    Rules:
    - ONLY roll for net credit (new premium > buyback cost)
    - Max 1-2 rolls per position (track in database)
    - Apply ALL entry screening criteria to the new position
    - New position must pass earnings check
    - New position must pass margin check (WhatIf)
    """
    
    def __init__(self, ibkr_client, exit_manager, risk_governor, earnings_service, margin_checker):
        self.client = ibkr_client
        self.exit_manager = exit_manager
        self.risk_governor = risk_governor
        self.earnings_service = earnings_service
        self.margin_checker = margin_checker
        self.MAX_ROLLS = 2
    
    async def evaluate_roll(self, position: PositionStatus) -> RollDecision:
        """Evaluate whether a position should be rolled instead of closed.
        
        Called when exit_manager determines a position should exit 
        (stop loss or time exit), but before actually closing it.
        
        Steps:
        1. Check roll count (max 2)
        2. Find candidate strikes/expirations for roll target
        3. Check net credit (new premium - buyback cost > 0)
        4. Run new position through screening criteria
        5. Run earnings check on new position
        6. Run WhatIf margin check on combined close+open
        7. Return RollDecision with recommendation
        """
    
    async def execute_roll(self, position_id: str, roll_target: RollTarget) -> RollResult:
        """Execute a roll: close existing + open new as combo order.
        
        Uses IBKR combo/spread order for atomic execution when possible,
        or sequential close-then-open with safeguards.
        """
```

**Files to modify:**
- `src/data/models.py` — Add `roll_count: int = 0` and `rolled_from_trade_id: Optional[str] = None` to `Trade` model
- `src/execution/exit_manager.py` — In `_evaluate_position()`, before returning a stop_loss or time_exit decision, check if rolling is preferable:
  1. If position is approaching stop loss but delta < 0.50 and roll_count < MAX_ROLLS, call `roll_manager.evaluate_roll()`
  2. If roll evaluation returns a viable roll, change the ExitDecision to a "roll" type instead of "close"
  3. Add new exit_type `"roll"` to ExitDecision
- `src/execution/exit_manager.py` — In `execute_exit()`, if `decision.exit_type == "roll"`, delegate to `roll_manager.execute_roll()`

**Database migration:** Add `roll_count` and `rolled_from_trade_id` columns to trades table.

**Acceptance criteria:**
- Rolling is offered as alternative to closing when conditions are met
- Only net credit rolls are allowed
- Max 2 rolls per position
- New rolled position passes all screening criteria including earnings check
- Roll count tracked in database
- Test: Position at -150% loss, viable roll target exists → roll offered
- Test: Position at -150% loss, already rolled twice → close, no roll offered
- Test: Roll target has earnings within DTE → roll rejected, close instead

---

### 5.4 — Assignment Detection Missing (🟠 HIGH)

**Finding:** No detection of stock positions resulting from put assignment. If a naked put is assigned, IBKR converts the options position to 100 shares of stock per contract. The system doesn't detect this.

**Fix:** Implement assignment detection in the position reconciliation workflow and add a dedicated assignment monitor.

**Files to create:**
- `src/services/assignment_detector.py`:
```
class AssignmentDetector:
    """Detect option assignments by monitoring for unexpected stock positions.
    
    Logic:
    1. Get all positions from IBKR
    2. Filter for STOCK positions (secType='STK')
    3. For each stock position, check if we have/had a matching option trade:
       - Same symbol
       - Position size is multiple of 100 (assignment always = 100 shares per contract)
    4. If match found → assignment detected
    
    Response options (configurable):
    - ALERT: Log critical alert + notification (default)
    - SELL_IMMEDIATELY: Place market sell for the stock
    - COVERED_CALL: Sell covered calls against the stock
    - HOLD: Do nothing, just track
    """
    
    def __init__(self, ibkr_client, trade_repository):
        self.client = ibkr_client
        self.trade_repo = trade_repository
        self.response_mode = "ALERT"  # Default: alert operator
    
    async def check_for_assignments(self) -> list[AssignmentEvent]:
        """Check IBKR positions for unexpected stock positions.
        
        Returns list of AssignmentEvent objects for any detected assignments.
        """
    
    async def handle_assignment(self, event: AssignmentEvent) -> AssignmentResult:
        """Handle a detected assignment based on configured response mode."""
```

**Files to modify:**
- `src/execution/position_monitor.py` — In `get_all_positions()`, after building the IBKR position map, scan for stock positions that match option trade symbols:
  1. Iterate over `ib_positions` where `contract.secType == 'STK'`
  2. For each, check if we have an open or recently-closed put trade on that symbol
  3. If stock position size is a multiple of 100 and matches a put trade → flag as potential assignment
  4. Log CRITICAL: "POSSIBLE ASSIGNMENT DETECTED: {symbol} — {shares} shares found in account"
- `src/services/order_reconciliation.py` — In `reconcile_positions()`, add detection for stock positions that don't have corresponding trades. These are likely assignments.
- `src/data/models.py` — Add `assignment_detected: bool = False` and `assignment_date: Optional[datetime] = None` to Trade model

**Acceptance criteria:**
- Stock positions matching option trades are detected
- CRITICAL log emitted on detection
- Assignment recorded in database
- Assignment handling mode is configurable (ALERT is default for safety)
- Test: Add mock stock position for symbol with open put trade → detected
- Test: Stock position for symbol with NO option trade → not flagged (user may have other trades)

---

## SECTION 6: PORTFOLIO-LEVEL RISK MANAGEMENT

### 6.1A — Weekly Loss Circuit Breaker Missing (🟡 MEDIUM)

**Finding:** Daily -2% circuit breaker exists but no weekly tracking. Repeated sub-threshold daily losses can accumulate.

**Fix:** Add weekly and drawdown circuit breakers to the risk governor.

**Files to modify:**
- `src/execution/risk_governor.py`:
  1. Add constants: `MAX_WEEKLY_LOSS_PCT = -0.05` (-5%), `MAX_DRAWDOWN_PCT = -0.10` (-10%)
  2. Add tracking state: `self._week_start_equity: float = 0`, `self._peak_equity: float = 0`
  3. Add method `_check_weekly_loss_limit()`:
     - Calculate weekly P&L: `(current_equity - week_start_equity) / week_start_equity`
     - If < -5%, trigger `emergency_halt("Weekly loss limit exceeded")`
     - Reset `_week_start_equity` every Monday (check `datetime.now().weekday() == 0`)
  4. Add method `_check_max_drawdown()`:
     - Track peak equity: `self._peak_equity = max(self._peak_equity, current_equity)`
     - Calculate drawdown: `(current_equity - peak_equity) / peak_equity`
     - If < -10%, trigger `emergency_halt("Max drawdown exceeded")`
  5. Add both checks to `pre_trade_check()` chain, after daily loss check
  6. Add to `get_risk_status()` return dict

**Acceptance criteria:**
- Weekly loss > 5% halts trading
- Peak-to-trough drawdown > 10% halts trading
- Weekly counter resets every Monday
- Peak equity tracks across sessions (load from database on startup if possible, otherwise start fresh)

---

### 6.2A — ExcessLiquidity Monitoring Missing (🟡 MEDIUM)

**Finding:** ExcessLiquidity is IBKR's key indicator of margin call proximity but is not tracked.

**Fix:** Already addressed as part of Section 2.1B (`verify_post_trade_margin` checks ExcessLiquidity) and Section 4.4 (`check_account_health` polls it). Add one more integration point:

**Files to modify:**
- `src/execution/risk_governor.py` — In `_check_margin_utilization()`, after the existing buying power check:
  1. Get `ExcessLiquidity` from `account_summary`
  2. Get `NetLiquidation` from `account_summary`
  3. `excess_ratio = ExcessLiquidity / NetLiquidation`
  4. If `excess_ratio < 0.10`: reject trade with reason "ExcessLiquidity dangerously low ({pct}% of NLV)"
  5. If `excess_ratio < 0.20`: log WARNING but approve

**Acceptance criteria:**
- ExcessLiquidity < 10% of NLV → trade rejected
- ExcessLiquidity < 20% of NLV → warning logged
- Shown in `get_risk_status()` output

---

### 6.3 — Stress Testing Not Implemented (🟢 LOW)

**Finding:** No ability to simulate portfolio impact of market drops or VIX spikes.

**Fix:** Create a stress testing module that estimates portfolio impact under adverse scenarios.

**Files to create:**
- `src/analysis/stress_test.py`:
```
class PortfolioStressTest:
    """Estimate portfolio impact under adverse market scenarios.
    
    Scenarios:
    - market_drop_5pct: All underlyings drop 5%
    - market_drop_10pct: All underlyings drop 10%
    - single_stock_crash: One underlying drops 20%
    - vix_spike_35: VIX jumps to 35+ (margin expansion)
    - correlation_crisis: All positions move against simultaneously
    
    For each scenario, estimate:
    - New P&L per position
    - New delta per position (approximated)
    - New margin requirement (approximated using 25% formula)
    - Total portfolio P&L
    - Margin call risk (yes/no)
    """
    
    def __init__(self, position_monitor, ibkr_client):
        self.monitor = position_monitor
        self.client = ibkr_client
    
    def run_scenario(self, scenario: str) -> StressTestResult:
        """Run a single stress scenario against current portfolio."""
    
    def run_all_scenarios(self) -> dict[str, StressTestResult]:
        """Run all standard scenarios and return results."""
```

**Files to modify:**
- `src/cli/commands/portfolio_commands.py` — Add `stress-test` CLI command that runs all scenarios and displays results in a Rich table

**Acceptance criteria:**
- `stress-test` CLI command shows estimated impact of 5 standard scenarios
- Output includes per-position and portfolio-level impact
- Margin call risk clearly flagged

---

### 6.4 — VIX-Aware Position Sizing Not Implemented (🟢 LOW)

**Finding:** System does not adjust position size or screening criteria based on market regime. No VIX references found in the codebase.

**Fix:** Add VIX awareness to the position sizer and screener config.

**Files to modify:**
- `src/services/position_sizer.py` (being created in 2.3A) — Add VIX-aware adjustment:
  1. Fetch current VIX from IBKR (use `Index("VIX", "CBOE")`)
  2. Apply scaling factor to position size:
     - VIX < 15 (low vol): normal sizing (1.0x)
     - VIX 15-25 (normal): reduce by 20% (0.8x)
     - VIX 25-35 (elevated): reduce by 50% (0.5x)
     - VIX > 35 (extreme): reduce by 75% (0.25x) or halt new positions
  3. Log: "VIX={vix}, sizing adjustment={factor}x"

- `src/config/naked_put_options_config.py` — Add `vix_halt_threshold: float = Field(default=40.0, description="Halt new positions when VIX exceeds this level")`

**Acceptance criteria:**
- Position size is reduced when VIX is elevated
- VIX > 40 halts new position entry
- VIX level logged with every position sizing calculation

---

## SECTION 7: SYSTEM INFRASTRUCTURE

> All items in Section 7 (connectivity, audit trail, data persistence, clock sync) are ✅ IMPLEMENTED. No fixes needed.

---

## SECTION 8: OPERATIONAL CONTROLS

### 8.2 — Kill Switch Not Persistent or Multi-Interface (🟠 HIGH)

**Finding:** `emergency_halt()` sets an in-memory `_trading_halted` flag that is lost on process restart. No CLI command, no database persistence, no signal handler.

**Fix:** Make the kill switch persistent across restarts, accessible from CLI, and wired into process signals.

**Files to create:**
- `src/services/kill_switch.py`:
```
class KillSwitch:
    """Persistent, multi-interface trading halt mechanism.
    
    Storage: Database table `system_state` with key `trading_halted`.
    
    Interfaces:
    1. Database flag — survives restarts
    2. CLI command — `emergency-stop` and `resume-trading`
    3. Signal handler — SIGTERM triggers halt
    4. In-memory flag — fast check, synced from database
    
    Check order (fast to slow):
    1. In-memory flag (cached)
    2. Database flag (every N seconds or on demand)
    """
    
    def __init__(self, db_session_factory):
        self._halted = False
        self._reason = ""
        self._db = db_session_factory
        self._load_from_db()  # Restore state on startup
        self._register_signal_handlers()
    
    def halt(self, reason: str):
        """Halt trading. Persists to database."""
        self._halted = True
        self._reason = reason
        self._save_to_db()
        logger.critical(f"TRADING HALTED: {reason}")
    
    def resume(self):
        """Resume trading. Clears database flag."""
        self._halted = False
        self._reason = ""
        self._save_to_db()
        logger.info("Trading resumed")
    
    def is_halted(self) -> tuple[bool, str]:
        """Check halt status. Returns (halted, reason)."""
        return self._halted, self._reason
    
    def _register_signal_handlers(self):
        """Register SIGTERM and SIGINT handlers."""
        import signal
        signal.signal(signal.SIGTERM, self._signal_halt)
        signal.signal(signal.SIGINT, self._signal_halt)
    
    def _signal_halt(self, signum, frame):
        self.halt(f"Signal {signum} received")
```

**Database migration:** Create `system_state` table with columns: `key VARCHAR PRIMARY KEY`, `value TEXT`, `updated_at TIMESTAMP`.

**Files to modify:**
- `src/execution/risk_governor.py` — Replace in-memory `_trading_halted` with `KillSwitch` instance:
  1. Accept `KillSwitch` in constructor
  2. `emergency_halt()` delegates to `kill_switch.halt()`
  3. `resume_trading()` delegates to `kill_switch.resume()`
  4. `is_halted()` delegates to `kill_switch.is_halted()`
- `src/cli/commands/portfolio_commands.py` — Add CLI commands:
  1. `emergency-stop [reason]` — calls `kill_switch.halt(reason)`
  2. `resume-trading` — calls `kill_switch.resume()` (requires confirmation prompt)
  3. `status` — shows current halt state

**Acceptance criteria:**
- Halt survives process restart (read from database on startup)
- CLI `emergency-stop` halts trading immediately
- CLI `resume-trading` clears halt (with confirmation)
- SIGTERM triggers halt
- Pre-trade check reads halt state from kill switch
- Test: Halt via CLI → restart process → verify still halted

---

### 8.3 — Notification System Missing (🟡 MEDIUM)

**Finding:** No email, SMS, push, or Slack integration. Critical events only appear in log files.

**Fix:** Implement a pluggable notification system starting with email (via SMTP) and optional webhook (Slack/Discord).

**Files to create:**
- `src/services/notifier.py`:
```
class Notifier:
    """Pluggable notification system for critical trading events.
    
    Channels (configured via .env):
    - EMAIL: SMTP-based email (NOTIFY_EMAIL_TO, SMTP_HOST, SMTP_PORT, etc.)
    - WEBHOOK: HTTP POST to Slack/Discord webhook URL (NOTIFY_WEBHOOK_URL)
    - LOG: Always active, writes to log file
    
    Severity levels:
    - INFO: Trade placed, profit target hit (log only)
    - WARNING: Margin approaching limit, delta elevated (log + email)
    - CRITICAL: Stop loss, circuit breaker, assignment (log + email + webhook)
    - EMERGENCY: System error, margin call risk (log + email + webhook + repeat)
    """
    
    def __init__(self):
        self.email_enabled = bool(os.getenv("NOTIFY_EMAIL_TO"))
        self.webhook_enabled = bool(os.getenv("NOTIFY_WEBHOOK_URL"))
    
    def notify(self, severity: str, title: str, message: str, data: dict = None):
        """Send notification based on severity."""
    
    def _send_email(self, subject: str, body: str): ...
    def _send_webhook(self, title: str, message: str, data: dict): ...
```

**Files to modify:**
- `src/execution/risk_governor.py` — Accept `Notifier` in constructor. Call `notifier.notify("CRITICAL", ...)` on:
  - Circuit breaker activation
  - Emergency halt
  - Margin limit rejection
- `src/execution/exit_manager.py` — Call `notifier.notify()` on:
  - Stop loss exit
  - Emergency exit all
- `src/services/assignment_detector.py` — Call `notifier.notify("CRITICAL", ...)` on assignment detection

**Acceptance criteria:**
- Email sent for CRITICAL events when `NOTIFY_EMAIL_TO` is configured
- Webhook POST for CRITICAL events when `NOTIFY_WEBHOOK_URL` is configured
- Graceful failure if notification channel is down (log error, don't crash)
- Notifications include: event type, symbol, details, timestamp

---

## SECTION 9: LEARNING & OPTIMIZATION

> All items in Section 9 (performance tracking, pattern detection, A/B experiments, what-if analysis) are ✅ IMPLEMENTED. No fixes needed.

---

## SUMMARY: Implementation Order and Dependencies

```
Section 1.2C  (DTE min → 7)           — standalone, 1 line
Section 1.2A  (OTM% filter)           — standalone
Section 1.2B  (IV Rank vs raw IV)     — standalone  
Section 1.3   (Earnings gate)         — depends on existing earnings_service.py
Section 2.1A  (WhatIf margin)         — creates margin_checker.py, modifies ibkr_client
Section 2.1B  (Post-trade verify)     — depends on 2.1A (margin_checker)
Section 2.1C  (Per-trade cap)         — depends on 2.1A (margin_checker)
Section 2.2   (Sector concentration)  — creates sector_map.py
Section 2.3A  (Position sizing)       — creates position_sizer.py
Section 3.2A  (Spread at execution)   — modifies adaptive_order_executor
Section 3.2B  (Price stability)       — modifies adaptive_order_executor
Section 3.4A  (Premium sanity)        — modifies order_executor
Section 3.4B  (Market hours guard)    — modifies order_executor
Section 4.2A  (Delta alert)           — modifies position_monitor
Section 4.2B  (Underlying drop alert) — modifies position_monitor
Section 4.3A  (Save position bug)     — modifies position_monitor
Section 4.4   (Health check interval) — depends on 2.1A (margin_checker)
Section 5.3   (Rolling)               — creates roll_manager.py, depends on 1.3, 2.1A
Section 5.4   (Assignment)            — creates assignment_detector.py
Section 6.1A  (Weekly/drawdown CB)    — modifies risk_governor
Section 6.2A  (ExcessLiquidity)       — modifies risk_governor
Section 6.3   (Stress testing)        — creates stress_test.py
Section 6.4   (VIX sizing)            — depends on 2.3A (position_sizer)
Section 8.2   (Kill switch)           — creates kill_switch.py, DB migration
Section 8.3   (Notifications)         — creates notifier.py
```

**New files to create (8):**
1. `src/services/margin_checker.py`
2. `src/data/sector_map.py`
3. `src/services/position_sizer.py`
4. `src/services/roll_manager.py`
5. `src/services/assignment_detector.py`
6. `src/analysis/stress_test.py`
7. `src/services/kill_switch.py`
8. `src/services/notifier.py`

**Existing files to modify (12):**
1. `src/config/naked_put_options_config.py`
2. `src/config/baseline_strategy.py`
3. `src/tools/barchart_csv_parser.py`
4. `src/tools/barchart_scanner.py`
5. `src/tools/ibkr_client.py`
6. `src/tools/ibkr_validator.py`
7. `src/execution/risk_governor.py` (heaviest changes)
8. `src/execution/order_executor.py`
9. `src/execution/exit_manager.py`
10. `src/execution/position_monitor.py`
11. `src/services/adaptive_order_executor.py`
12. `src/cli/commands/portfolio_commands.py`

**Database migrations (3):**
1. Add `roll_count`, `rolled_from_trade_id` to `trades` table
2. Add `assignment_detected`, `assignment_date` to `trades` table
3. Create `system_state` table for kill switch persistence
