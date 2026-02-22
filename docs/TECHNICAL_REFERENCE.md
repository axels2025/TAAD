# Technical Reference

## Architecture, Specifications, and Implementation Details

**Document Version:** 1.2
**Last Updated:** February 16, 2026
**Status:** Consolidated from multiple docs

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Scoring Algorithm](#scoring-algorithm)
3. [Trade Data Collection](#trade-data-collection)
4. [Learning Engine Patterns](#learning-engine-patterns)
5. [IBKR Field Reference](#ibkr-field-reference)
6. [Barchart Integration](#barchart-integration)
7. [Database Schema](#database-schema)
8. [Order Execution Architecture](#order-execution-architecture)
9. [Adaptive Strike Selection Algorithm](#adaptive-strike-selection-algorithm)
10. [Fill Management System](#fill-management-system)
11. [NakedTrader Architecture](#nakedtrader-architecture)

---

## System Architecture

### Core Components

```
trading_agent/
├── src/
│   ├── cli/                    # Command-line interface
│   │   └── main.py             # 20+ commands
│   ├── core/                   # Core trading logic
│   │   ├── naked_put_strategy.py
│   │   ├── order_executor.py
│   │   ├── position_monitor.py
│   │   ├── exit_manager.py
│   │   └── risk_governor.py
│   ├── learning/               # Phase 3: Learning engine
│   │   ├── pattern_detector.py
│   │   ├── path_analyzer.py
│   │   ├── pattern_combiner.py
│   │   ├── parameter_optimizer.py
│   │   ├── experiment_engine.py
│   │   └── learning_orchestrator.py
│   ├── data/                   # Data layer
│   │   ├── models.py           # SQLAlchemy models
│   │   ├── repositories/       # Database operations
│   │   └── services/           # Snapshot services
│   ├── integrations/           # External APIs
│   │   ├── ibkr_client.py
│   │   ├── barchart_scanner.py
│   │   └── barchart_csv_parser.py
│   ├── nakedtrader/            # Daily SPX/XSP/SPY put selling
│   │   ├── config.py           # YAML + Pydantic config
│   │   ├── chain.py            # Index option chain + Greeks
│   │   ├── strike_selector.py  # Delta-based strike selection
│   │   ├── order_manager.py    # Bracket order placement
│   │   ├── trade_recorder.py   # Trade DB recording
│   │   ├── watcher.py          # Position monitoring
│   │   └── workflow.py         # Daily workflow orchestrator
│   └── utils/                  # Helpers
│       ├── scoring.py
│       ├── logging_config.py
│       └── validators.py
├── data/                       # Runtime data
│   ├── databases/
│   └── exports/
├── logs/                       # Application logs
├── tests/                      # Test suite (87%+ coverage)
└── docs/                       # Documentation
```

### Data Flow

```
[Barchart CSV] → [CSV Parser] → [Scoring Engine] → [IBKR Validator]
                                      ↓
                              [Ranked Candidates]
                                      ↓
                              [Sunday Session] → [Staged Opportunities DB]
                                      ↓
                    ┌─────── [TwoTierExecutionScheduler] ───────┐
                    │                                           │
                    │  Stage 1: PremarketValidator (9:15 AM)    │
                    │  Stage 2: Quote Refresh (9:28 AM)         │
                    │  LiveStrikeSelector (9:29 AM)             │
                    │  Tier 1: RapidFireExecutor (9:30 AM)      │
                    │  FillManager (9:30-9:40 AM)               │
                    │  EntrySnapshotService (at fill time)      │
                    │  Tier 2: Condition-Based Retry (9:45 AM)  │
                    │  Reconciliation (10:30 AM)                │
                    │                                           │
                    └───────────────────────────────────────────┘
                                      ↓
                              [Position Monitor]
                                      ↓
                              [Exit Manager] ← [Risk Governor]
                                      ↓
                              [Snapshot Service]
                                      ↓
                              [Learning Engine]
```

---

## Scoring Algorithm

### Six-Dimension Composite Score

Each candidate is scored 0-100 across six dimensions:

#### 1. Risk-Adjusted Return (25% weight)

```python
def score_risk_adjusted_return(annualized_return: float) -> float:
    """
    Optimal: 30-50% annualized
    - <10%: 0-30 points
    - 10-30%: 30-70 points
    - 30-50%: 70-100 points (optimal)
    - >50%: 85-100 points (diminishing returns)
    """
    if annualized_return < 0.10:
        return annualized_return / 0.10 * 30
    elif annualized_return < 0.30:
        return 30 + (annualized_return - 0.10) / 0.20 * 40
    elif annualized_return < 0.50:
        return 70 + (annualized_return - 0.30) / 0.20 * 30
    else:
        return 85 + min(15, (annualized_return - 0.50) / 0.50 * 15)
```

#### 2. Probability of Profit (20% weight)

```python
def score_profit_probability(prob: float) -> float:
    """
    Based on delta (1 - |delta| ≈ probability of profit)
    - <70%: 0-30 points
    - 70-80%: 30-60 points
    - 80-90%: 60-85 points
    - >90%: 85-100 points
    """
    if prob < 0.70:
        return prob / 0.70 * 30
    elif prob < 0.80:
        return 30 + (prob - 0.70) / 0.10 * 30
    elif prob < 0.90:
        return 60 + (prob - 0.80) / 0.10 * 25
    else:
        return 85 + min(15, (prob - 0.90) / 0.10 * 15)
```

#### 3. IV Rank (15% weight)

```python
def score_iv_rank(iv_rank: float) -> float:
    """
    Optimal: 60-80% IV Rank (sell high IV)
    - <30%: 0-30 points (low IV, poor premium)
    - 30-60%: 30-70 points
    - 60-80%: 70-100 points (optimal)
    - >80%: 80-100 points (very high IV, could be risky)
    """
    if iv_rank < 30:
        return iv_rank / 30 * 30
    elif iv_rank < 60:
        return 30 + (iv_rank - 30) / 30 * 40
    elif iv_rank < 80:
        return 70 + (iv_rank - 60) / 20 * 30
    else:
        return 80 + min(20, (iv_rank - 80) / 20 * 20)
```

#### 4. Liquidity (15% weight)

```python
def score_liquidity(open_interest: int, volume: int) -> float:
    """
    Optimal: OI >= 2000, Volume >= 300
    Combined score based on both metrics
    """
    oi_score = min(50, open_interest / 2000 * 50)
    vol_score = min(50, volume / 300 * 50)
    return oi_score + vol_score
```

#### 5. Capital Efficiency (15% weight)

```python
def score_capital_efficiency(return_on_strike: float) -> float:
    """
    Premium / Strike price as %
    Optimal: 2.0%+ (corresponds to ~50% margin efficiency)
    """
    if return_on_strike < 0.01:
        return return_on_strike / 0.01 * 30
    elif return_on_strike < 0.015:
        return 30 + (return_on_strike - 0.01) / 0.005 * 30
    elif return_on_strike < 0.02:
        return 60 + (return_on_strike - 0.015) / 0.005 * 25
    else:
        return 85 + min(15, (return_on_strike - 0.02) / 0.02 * 15)
```

#### 6. Safety Buffer (10% weight)

```python
def score_safety_buffer(otm_percent: float) -> float:
    """
    Distance from current price to strike
    Optimal: 12-18% OTM
    """
    if otm_percent < 0.08:
        return otm_percent / 0.08 * 40  # Too close
    elif otm_percent < 0.12:
        return 40 + (otm_percent - 0.08) / 0.04 * 30
    elif otm_percent < 0.18:
        return 70 + (otm_percent - 0.12) / 0.06 * 30  # Optimal
    elif otm_percent < 0.25:
        return 85 + (0.25 - otm_percent) / 0.07 * 15
    else:
        return 70 - min(40, (otm_percent - 0.25) / 0.10 * 40)  # Too far
```

### Composite Score Calculation

```python
def calculate_composite_score(candidate: Dict) -> float:
    weights = {
        "risk_adjusted_return": 0.25,
        "profit_probability": 0.20,
        "iv_rank": 0.15,
        "liquidity": 0.15,
        "capital_efficiency": 0.15,
        "safety_buffer": 0.10
    }
    
    scores = {
        "risk_adjusted_return": score_risk_adjusted_return(candidate["ann_return"]),
        "profit_probability": score_profit_probability(candidate["profit_prob"]),
        "iv_rank": score_iv_rank(candidate["iv_rank"]),
        "liquidity": score_liquidity(candidate["oi"], candidate["volume"]),
        "capital_efficiency": score_capital_efficiency(candidate["return_on_strike"]),
        "safety_buffer": score_safety_buffer(candidate["otm_percent"])
    }
    
    return sum(scores[k] * weights[k] for k in weights)
```

### Grade Assignment

| Score | Grade | Description |
|-------|-------|-------------|
| 85-100 | A+ | Excellent candidate |
| 75-84 | A | Very good |
| 65-74 | B | Good |
| 55-64 | C | Acceptable |
| 45-54 | D | Below average |
| <45 | F | Poor |

---

## Trade Data Collection

### 125+ Fields Captured

#### Entry Snapshot (66 fields)

**Option Data (12 fields)**
- symbol, strike, expiration, contracts
- entry_bid, entry_ask, entry_mid, entry_premium
- entry_iv, entry_delta, entry_theta, entry_gamma

**Underlying Data (8 fields)**
- underlying_price, underlying_bid, underlying_ask
- underlying_volume, underlying_50d_avg_volume
- underlying_52w_high, underlying_52w_low
- distance_from_52w_high

**Market Context (7 fields)**
- vix, vix_term_structure (contango/backwardation)
- spy_price, spy_trend_20d
- sector_etf_price, sector_trend_20d
- market_regime (bull/bear/neutral)

**Technical Indicators (12 fields)**
- sma_20, sma_50, sma_200
- ema_20, ema_50
- rsi_14, macd, macd_signal, macd_histogram
- bb_upper, bb_middle, bb_lower

**Fundamental Data (5 fields)**
- days_to_earnings, earnings_date
- market_cap, avg_analyst_rating
- short_interest_percent

**Position Context (6 fields)**
- position_id, entry_timestamp
- account_margin_before, account_margin_after
- margin_impact, portfolio_delta_before

**Trade Metadata (6 fields)**
- strategy_version, pattern_source
- score_composite, score_breakdown
- validation_source (manual/api/csv)
- trade_rationale

**Strike Selection (3 fields)**
- strike_selection_method ("delta", "otm_pct", "unchanged")
- original_strike (strike from overnight screening before delta adjustment)
- live_delta_at_selection (actual delta at time of strike selection)

#### Daily Position Snapshot (15 fields)

- current_bid, current_ask, current_mid
- current_iv, current_delta, current_theta
- current_underlying_price
- days_in_trade, dte_remaining
- unrealized_pnl, unrealized_pnl_percent
- max_profit_realized_percent
- vix_at_snapshot, underlying_change_percent

#### Exit Snapshot (20 fields)

- exit_timestamp, exit_price, exit_premium
- exit_type (profit_target/stop_loss/time_exit/manual)
- realized_pnl, realized_pnl_percent
- holding_days, max_drawdown
- max_profit_during_trade
- underlying_at_exit, underlying_move_percent
- vix_at_exit, vix_change
- exit_delta, exit_iv
- assigned (bool)

---

## Learning Engine Patterns

### Pattern Categories (35+ patterns)

#### 1. Technical Indicator Patterns (12)

| Pattern | Description |
|---------|-------------|
| `trend_sma_alignment` | All SMAs aligned (20 > 50 > 200) |
| `trend_ema_momentum` | EMA crossover signals |
| `rsi_oversold_entry` | RSI < 30 at entry |
| `rsi_overbought_entry` | RSI > 70 at entry |
| `macd_bullish_cross` | MACD crossed above signal |
| `macd_bearish_cross` | MACD crossed below signal |
| `bb_squeeze` | Bollinger Bands narrowing |
| `bb_upper_touch` | Price at upper band |
| `bb_lower_touch` | Price at lower band |
| `volume_surge` | Volume > 2x average |
| `price_near_52w_high` | Within 5% of 52-week high |
| `price_near_52w_low` | Within 5% of 52-week low |

#### 2. Market Context Patterns (7)

| Pattern | Description |
|---------|-------------|
| `vix_low` | VIX < 15 |
| `vix_medium` | VIX 15-25 |
| `vix_high` | VIX > 25 |
| `vix_contango` | Term structure normal |
| `vix_backwardation` | Term structure inverted |
| `spy_uptrend` | SPY above 20-day SMA |
| `spy_downtrend` | SPY below 20-day SMA |

#### 3. Trade Trajectory Patterns (9)

| Pattern | Description |
|---------|-------------|
| `immediate_winner` | Profitable within 24 hours |
| `slow_grind_winner` | Gradual profit accumulation |
| `early_scare` | Initial drawdown, then recovery |
| `steady_loser` | Consistent decline |
| `volatility_spike` | Large intraday swings |
| `theta_decay_clean` | Smooth time decay |
| `delta_blowout` | Delta exceeded 0.50 |
| `iv_crush` | IV dropped significantly |
| `gamma_risk_realized` | Large gamma-driven moves |

#### 4. Exit Quality Patterns (7)

| Pattern | Description |
|---------|-------------|
| `early_profit_take` | Exited < 3 days at profit |
| `optimal_exit` | Captured 70%+ of max profit |
| `premature_exit` | Left 30%+ profit on table |
| `stop_loss_necessary` | Stop prevented larger loss |
| `stop_loss_premature` | Would have recovered |
| `held_to_expiry` | Let option expire |
| `assignment_risk` | Delta > 0.50 at any point |

#### 5. Combination Patterns (10)

| Pattern | Description |
|---------|-------------|
| `golden_setup` | Multiple bullish indicators aligned |
| `high_risk_high_reward` | High IV + high delta |
| `safe_income` | Low IV + low delta + uptrend |
| `earnings_play` | Entry near earnings |
| `sector_momentum` | Sector outperforming SPY |
| `contrarian_entry` | Against short-term trend |
| `momentum_continuation` | With established trend |
| `mean_reversion` | Expecting bounce from oversold |
| `volatility_expansion` | Entering before IV spike |
| `volatility_contraction` | Entering after IV spike |

### Statistical Validation

```python
SIGNIFICANCE_THRESHOLDS = {
    "p_value": 0.05,           # Maximum p-value
    "effect_size": 0.005,      # Minimum ROI improvement
    "min_samples": 30,         # Minimum trades per pattern
    "confidence_interval": 0.95
}
```

---

## IBKR Field Reference

### Quote Fields

| Field | IBKR Constant | Description |
|-------|--------------|-------------|
| Bid | 1 | Current bid price |
| Ask | 2 | Current ask price |
| Last | 4 | Last traded price |
| High | 6 | Day high |
| Low | 7 | Day low |
| Close | 9 | Previous close |
| Volume | 8 | Day volume |
| Open Interest | 86 | Option open interest |

### Greek Fields

| Field | IBKR Constant | Description |
|-------|--------------|-------------|
| Model Opt Implied Vol | 30 | Implied volatility |
| Model Option Delta | 29 | Delta |
| Model Option Gamma | 31 | Gamma |
| Model Option Theta | 32 | Theta |
| Model Option Vega | 33 | Vega |

### Order Fields

| Field | Description |
|-------|-------------|
| whatIfOrder | Margin impact calculation |
| initMarginChange | Initial margin required |
| maintMarginChange | Maintenance margin required |
| commission | Estimated commission |

---

## Barchart Integration

### Required CSV Columns

```
Symbol, Price, Exp Date, DTE, Strike, Moneyness,
Bid, Volume, Open Int, IV Rank, Delta, Return, Ann Rtn, Profit Prob
```

### CSV Parser Processing

```python
def parse_barchart_csv(filepath: str) -> List[Candidate]:
    """
    1. Read CSV with pandas
    2. Normalize column names
    3. Parse dates (MM/DD/YYYY format)
    4. Convert percentages (remove % sign)
    5. Calculate derived fields:
       - OTM percent = (Price - Strike) / Price
       - Return on strike = Premium / Strike
    6. Apply minimum filters:
       - OI >= 100
       - Volume >= 10
       - DTE >= 1
    7. Return validated candidates
    """
```

### Barchart API Configuration

```python
BARCHART_CONFIG = {
    "base_url": "https://api.barchart.com/v1",
    "endpoints": {
        "options_screener": "/options/screener",
        "quote": "/quote/{symbol}",
        "options_chain": "/options/chain/{symbol}"
    },
    "rate_limits": {
        "requests_per_minute": 60,
        "requests_per_day": 10000
    }
}
```

---

## Database Schema

### Core Tables

```sql
-- Trades table
CREATE TABLE trades (
    id INTEGER PRIMARY KEY,
    symbol VARCHAR(10) NOT NULL,
    strike DECIMAL(10, 2) NOT NULL,
    expiration DATE NOT NULL,
    contracts INTEGER NOT NULL,
    entry_premium DECIMAL(10, 4),
    entry_timestamp TIMESTAMP,
    exit_premium DECIMAL(10, 4),
    exit_timestamp TIMESTAMP,
    exit_type VARCHAR(20),
    realized_pnl DECIMAL(12, 2),
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Entry snapshots
CREATE TABLE entry_snapshots (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER REFERENCES trades(id),
    snapshot_data JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Position snapshots (daily)
CREATE TABLE position_snapshots (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER REFERENCES trades(id),
    snapshot_timestamp TIMESTAMP NOT NULL,
    snapshot_data JSON NOT NULL
);

-- Exit snapshots
CREATE TABLE exit_snapshots (
    id INTEGER PRIMARY KEY,
    trade_id INTEGER REFERENCES trades(id),
    snapshot_data JSON NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Patterns detected
CREATE TABLE patterns (
    id INTEGER PRIMARY KEY,
    pattern_type VARCHAR(50) NOT NULL,
    pattern_name VARCHAR(100) NOT NULL,
    parameters JSON,
    trades_analyzed INTEGER,
    win_rate DECIMAL(5, 4),
    avg_return DECIMAL(8, 4),
    p_value DECIMAL(8, 6),
    effect_size DECIMAL(8, 6),
    status VARCHAR(20) DEFAULT 'active',
    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Experiments
CREATE TABLE experiments (
    id INTEGER PRIMARY KEY,
    experiment_name VARCHAR(100) NOT NULL,
    hypothesis TEXT,
    control_parameters JSON,
    test_parameters JSON,
    start_date DATE,
    end_date DATE,
    control_trades INTEGER DEFAULT 0,
    test_trades INTEGER DEFAULT 0,
    control_avg_return DECIMAL(8, 4),
    test_avg_return DECIMAL(8, 4),
    status VARCHAR(20) DEFAULT 'running'
);

-- Learning history
CREATE TABLE learning_history (
    id INTEGER PRIMARY KEY,
    cycle_date DATE NOT NULL,
    patterns_detected INTEGER,
    experiments_completed INTEGER,
    parameters_updated JSON,
    summary TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Scans
CREATE TABLE scans (
    id INTEGER PRIMARY KEY,
    scan_timestamp TIMESTAMP NOT NULL,
    source VARCHAR(20),
    candidates_found INTEGER,
    candidates_validated INTEGER,
    parameters_used JSON,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

---

## Order Execution Architecture

### Overview

The order execution system implements institutional-grade trade execution with four core layers:

```
┌─────────────────────────────────────────────────────────────────┐
│                    IBKRClient (Wrapper Layer)                    │
│  - Automatic reconnection & retry logic                         │
│  - Event-driven quote fetching (not polling)                    │
│  - Audit logging for all orders                                 │
│  - Direct access to orders(), trades(), executions(), fills()   │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│             LiveStrikeSelector (Delta Selection Layer)           │
│  - Fetch live option chains (reqSecDefOptParams)                │
│  - Greeks retrieval via reqMktData + modelGreeks                │
│  - Delta-based strike matching with boundary validation         │
│  - Fallback to original OTM%-based strike                       │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│              AdaptiveOrderExecutor (Strategy Layer)              │
│  - Primary: IBKR Adaptive Algo (dynamic spread navigation)      │
│  - Fallback: Standard LIMIT orders                              │
│  - Live quote validation before submission                      │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│           RapidFireExecutor (Parallel Execution Layer)           │
│  - Submit ALL orders in <3 seconds (not sequential)             │
│  - Event-driven fill monitoring (not polling)                   │
│  - Condition-based adjustment (only when needed)                │
│  - Async monitoring of all orders simultaneously                │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│              FillManager (Fill Monitoring Layer)                 │
│  - 10-minute time-boxed monitoring window                       │
│  - Partial fill detection + cancel/replace for remainder        │
│  - Progressive limit adjustment (-$0.01/min, max 5 steps)       │
│  - Floor protection (never below premium floor)                 │
└────────────────────────┬────────────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────────────┐
│          OrderReconciliation (Verification Layer)                │
│  - Sync database with TWS reality                               │
│  - Fill price, status, commission tracking                      │
│  - Discrepancy detection & auto-resolution                      │
│  - End-of-day position validation                               │
└──────────────────────────────────────────────────────────────────┘
```

### Data Flow

```
[Staged Trades]
    │
    ├──> [LiveStrikeSelector]              # Delta-based strike resolution
    │        ├─> reqSecDefOptParams (chain)
    │        ├─> reqMktData + modelGreeks
    │        ├─> Select best delta match
    │        └─> Update StagedOpportunity
    │
    ├──> [Batch Qualify Contracts]         # Parallel, <500ms
    │
    ├──> [Fetch Live Quotes (All)]         # Parallel, event-driven
    │
    ├──> [RAPID FIRE Submit (All)]         # <3 seconds total
    │        ├─> Adaptive Algo (primary)
    │        └─> LIMIT fallback
    │
    ├──> [FillManager]                     # 10-minute window
    │        ├─> 2s check loop
    │        ├─> Partial fill → cancel/replace
    │        ├─> Progressive limit adjustment
    │        └─> Leave unfilled as DAY
    │
    ├──> [EntrySnapshotService]            # At fill time
    │        ├─> 86+ fields
    │        └─> strike_selection_method, original_strike, live_delta
    │
    └──> [Reconciliation]                   # End-of-day
             ├─> Compare DB vs TWS
             ├─> Update fill prices
             ├─> Track commissions
             └─> Validate positions
```

### Architecture Components

#### 1. IBKRClient (Wrapper Layer)

**Purpose:** Robust wrapper around `ib_insync` with retry logic and comprehensive error handling.

**Key Features:**
- Automatic reconnection with exponential backoff
- Event-driven quote fetching (returns immediately when valid quote arrives)
- Comprehensive audit logging for all order operations
- Direct access to IBKR methods: `orders()`, `trades()`, `executions()`, `fills()`, `positions()`

**Location:** `/src/tools/ibkr_client.py`

**Example:**
```python
client = IBKRClient(config)
client.connect()

# Event-driven quote (not polling)
quote = await client.get_quote(contract, timeout=0.5)
if quote.is_valid:
    print(f"Bid: ${quote.bid:.2f}, Ask: ${quote.ask:.2f}")

# Place order with audit trail
trade = await client.place_order(
    contract,
    order,
    reason="Staged trade AAPL"
)
```

#### 2. AdaptiveOrderExecutor (Strategy Layer)

**Purpose:** Execute orders using IBKR Adaptive Algorithm with automatic fallback to LIMIT.

**Strategy:**
1. **Primary:** Adaptive Algo with "Urgent" priority
   - IBKR dynamically navigates the bid-ask spread
   - Limit price becomes the floor (will try to improve)
   - Better fills than static LIMIT orders

2. **Fallback:** Standard LIMIT orders
   - Used when Adaptive is rejected (some option classes don't support it)
   - Automatic detection and retry with LIMIT

**Location:** `/src/services/adaptive_order_executor.py`

**Example:**
```python
executor = AdaptiveOrderExecutor(ibkr_client, limit_calculator)

# Get live quote with tradeability check
quote = await executor.get_live_quote(contract)

if quote.is_tradeable:
    result = await executor.place_order(staged_trade, contract, quote)
    print(f"Order {result.order_id} placed using {result.order_type}")
```

#### 3. RapidFireExecutor (Parallel Execution Layer)

**Purpose:** Submit ALL orders in parallel (<3 seconds) with event-driven monitoring.

**Timeline:**
```
T+0:    Pre-qualify all contracts (batch)
T+1:    Request live quotes for all (parallel)
T+2:    Submit all orders (rapid-fire, ~100ms each)
T+3:    Begin async fill monitoring
T+??:   Condition-based adjustments (when limit > $0.02 outside spread)
T+120:  Final status, leave unfilled as DAY orders
```

**Location:** `/src/services/rapid_fire_executor.py`

**Example:**
```python
executor = RapidFireExecutor(ibkr_client, adaptive_executor)

# Execute all in parallel
report = await executor.execute_all(staged_trades)

print(f"Submitted {report.total_submitted} in {report.submission_time:.2f}s")
print(f"Fill rate: {report.fill_rate:.1%}")
```

#### 4. OrderReconciliation (Verification Layer)

**Purpose:** Sync database records with TWS reality, catching any discrepancies.

**What it Syncs:**
- Order status (Submitted → Filled/Cancelled)
- Fill prices (actual execution prices)
- Fill times (when orders were filled)
- Commissions (from IBKR fill reports)
- Positions (database vs IBKR quantities)

**Location:** `/src/services/order_reconciliation.py`

**Example:**
```python
reconciler = OrderReconciliation(ibkr_client, trade_repository)

# Sync all orders for today
report = await reconciler.sync_all_orders()

print(f"Reconciled {report.total_reconciled} trades")
print(f"Found {report.total_discrepancies} discrepancies")

# End-of-day position check
pos_report = await reconciler.reconcile_positions()
if pos_report.has_discrepancies:
    print("Position mismatches detected!")
```

---

## Adaptive Algorithm Execution

### How Adaptive Algo Works

The IBKR Adaptive Algorithm is an intelligent order execution strategy that:

1. **Dynamically Navigates the Spread**
   - Doesn't sit static at your limit price
   - Actively works to get fills between bid and ask
   - Adapts to market conditions in real-time

2. **Uses Limit as Floor**
   - Your limit price = minimum acceptable
   - Algo tries to improve on this
   - Won't fill below your limit

3. **Three Priority Levels**
   - **Urgent:** Faster fills, less price improvement (we use this)
   - **Normal:** Balanced approach
   - **Patient:** More price improvement, slower fills

### Fallback to LIMIT Orders

Some option classes don't support Adaptive Algo. The system handles this automatically:

```python
# Step 1: Try Adaptive
order = create_adaptive_order(contracts=5, floor_price=0.45)
trade = await client.place_order(contract, order)

# Step 2: Check for rejection
await asyncio.sleep(0.3)
if trade.orderStatus.status == "Inactive":
    # Step 3: Automatic fallback
    logger.warning("Adaptive rejected, falling back to LIMIT")

    await client.cancel_order(trade.order.orderId)

    order = create_limit_order(contracts=5, limit_price=0.45)
    trade = await client.place_order(contract, order)
```

### Configuration Options

Environment variables control behavior:

```bash
# Enable/disable Adaptive Algo
USE_ADAPTIVE_ALGO=true          # Use Adaptive as primary (default)
USE_ADAPTIVE_ALGO=false         # Skip Adaptive, go straight to LIMIT

# Quote fetching
QUOTE_FETCH_TIMEOUT_SECONDS=0.5 # Event-driven timeout (default)

# Minimum premium threshold
PREMIUM_MIN=0.30                # Skip if premium < $0.30
```

### Code Example: Creating Adaptive Order

```python
from ib_insync import LimitOrder, TagValue

def create_adaptive_order(contracts: int, floor_price: float) -> LimitOrder:
    """Create an Adaptive Algo order with Urgent priority."""
    order = LimitOrder(
        action="SELL",
        totalQuantity=contracts,
        lmtPrice=floor_price,  # This becomes your floor
        tif="DAY",
    )

    # Enable Adaptive Algo
    order.algoStrategy = "Adaptive"
    order.algoParams = [
        TagValue("adaptivePriority", "Urgent")
    ]

    return order
```

---

## Rapid-Fire Parallel Execution

### Parallel Submission Architecture

**Old Approach (Sequential):**
```
Submit order 1 → Wait 30s → Submit order 2 → Wait 30s → ...
Total time: N × 30 seconds
```

**New Approach (Parallel):**
```
Qualify all contracts (batch)      → 0.5s
Fetch all quotes (parallel)        → 1.0s
Submit ALL orders (rapid-fire)     → 1-3s
Monitor ALL (event-driven)         → async
Total time: ~3 seconds to submit all
```

### Event-Driven Monitoring

Instead of polling every 30 seconds, we use IBKR's event system:

```python
class RapidFireExecutor:
    def __init__(self, ibkr_client, adaptive_executor):
        self.client = ibkr_client
        self.pending_orders = {}

        # Register event callback
        self.client.order_status_event += self._on_order_status

    def _on_order_status(self, trade):
        """Called AUTOMATICALLY when order status changes."""
        order_id = trade.order.orderId

        if order_id in self.pending_orders:
            pending = self.pending_orders[order_id]
            pending.last_status = trade.orderStatus.status

            if trade.orderStatus.status == "Filled":
                pending.fill_price = trade.orderStatus.avgFillPrice
                logger.info(f"✓ {pending.symbol} FILLED @ ${pending.fill_price:.2f}")
```

**Benefits:**
- Zero polling overhead
- Instant notification when fills occur
- Simultaneous monitoring of all orders
- Natural async concurrency

### Condition-Based Adjustment Logic

Instead of time-based adjustments ("adjust after 30s"), we use condition-based:

**Condition:** Only adjust if current limit is > $0.02 outside the spread.

```python
async def _adjust_if_outside_spread(self):
    """Adjust ONLY if limit is > $0.02 outside current spread."""
    for order_id, pending in self.pending_orders.items():
        # Get fresh quote
        quote = await self.client.get_quote(pending.contract, timeout=0.3)

        current_bid = quote.bid
        current_ask = quote.ask
        current_limit = pending.current_limit

        # CONDITION: Is our limit > $0.02 ABOVE the current ask?
        spread_distance = current_limit - current_ask

        if spread_distance > 0.02:
            # We're too far above the ask — need to lower our limit
            new_limit = calculate_sell_limit(current_bid, current_ask)
            new_limit = max(new_limit, min_premium)

            if new_limit < pending.current_limit:
                await self._modify_order_price(pending, new_limit)
                logger.info(
                    f"📉 Adjusted ${pending.current_limit:.2f} → ${new_limit:.2f} "
                    f"(was ${spread_distance:.2f} above ask)"
                )
```

**Why This is Better:**
- Market-driven, not clock-driven
- Only adjust when actually needed
- Preserves limit when market hasn't moved
- Reduces unnecessary order modifications

### Timeline Breakdown

```
T+0.0s:  Start execution
         ├─> Batch qualify all contracts (ib.qualifyContractsAsync)

T+0.5s:  Contracts qualified
         ├─> Request live quotes for ALL (parallel)

T+1.5s:  Quotes received (event-driven, not fixed wait)
         ├─> Begin rapid-fire submission

T+1.6s:  Order 1 submitted (Adaptive)
T+1.7s:  Order 2 submitted (Adaptive)
T+1.8s:  Order 3 submitted (Adaptive)
T+1.9s:  Order 4 submitted (LIMIT fallback - Adaptive rejected)
T+2.0s:  Order 5 submitted (Adaptive)
         ...
T+2.5s:  All orders submitted
         ├─> Begin event-driven monitoring

T+3.0s:  Order 2 FILLED (event notification)
T+5.0s:  Order 1 FILLED (event notification)
T+8.0s:  Check spread distances (condition-based)
T+10.0s: Order 3 FILLED (event notification)
T+15.0s: Adjust Order 4 (was > $0.02 outside spread)
         ...
T+120s:  Monitoring complete
         ├─> Orders 2, 1, 3 filled
         ├─> Orders 4, 5 left working (TIF=DAY)
         └─> Generate execution report
```

### Code Example: Execute All

```python
async def execute_all(self, staged_trades: list) -> ExecutionReport:
    """Execute all staged trades using rapid-fire parallel submission."""
    report = ExecutionReport()

    # Step 1: Pre-qualify contracts in batch
    contracts = [self._create_contract(s) for s in staged_trades]
    qualified = await self.client.qualify_contracts_async(*contracts)

    # Step 2: Fetch live quotes (parallel)
    quote_tasks = [
        self.adaptive_executor.get_live_quote(c) for c in qualified
    ]
    quotes = await asyncio.gather(*quote_tasks)

    # Step 3: RAPID FIRE - Submit all orders
    for staged, contract, quote in zip(staged_trades, qualified, quotes):
        if quote.is_tradeable:
            result = await self.adaptive_executor.place_order(
                staged, contract, quote
            )
            if result.success:
                self.pending_orders[result.order_id] = PendingOrder(...)
                report.add_submitted(staged, result.order_id, quote.limit)

    # Step 4: Async monitoring with condition-based adjustments
    await self._monitor_and_adjust(report)

    return report
```

---

## Order Reconciliation

### Database/TWS Sync Process

**Problem:** Database may diverge from TWS reality:
- Orders filled but database shows "submitted"
- Orders cancelled but database doesn't know
- Missing fill prices, fill times, commissions

**Solution:** Comprehensive reconciliation using multiple IBKR methods:

```python
# Get comprehensive data from IBKR
ib_orders = client.get_orders()         # Open orders
ib_trades = client.get_trades()         # All trades this session
ib_executions = client.get_executions() # Execution details
ib_fills = client.get_fills()           # Fill details with commissions
ib_positions = client.get_positions()   # Current positions

# Match to database records by order_id
for ib_trade in ib_trades:
    order_id = ib_trade.order.orderId

    if order_id in db_by_order_id:
        db_trade = db_by_order_id[order_id]

        # Get execution and fill details
        executions = executions_by_order.get(order_id, [])
        fills = fills_by_order.get(order_id, [])

        # Reconcile and update database
        discrepancy = self._reconcile_single(
            db_trade, ib_trade, executions, fills
        )
```

### IBKR Methods Used

| Method | Returns | Purpose |
|--------|---------|---------|
| `ib.orders()` | Open orders | Active order tracking |
| `ib.trades()` | All trades this session | Complete trade history |
| `ib.executions()` | Execution details | Fill time tracking |
| `ib.fills()` | Fill details with commissions | Commission tracking |
| `ib.positions()` | Current positions | Position validation |

**Code Example:**
```python
# Get all data sources
orders = self.client.get_orders()
trades = self.client.get_trades()
executions = self.client.get_executions()
fills = self.client.get_fills()
positions = self.client.get_positions()

# Process each trade
for trade in trades:
    order_id = trade.order.orderId
    status = trade.orderStatus.status
    avg_fill_price = trade.orderStatus.avgFillPrice
    filled_qty = trade.orderStatus.filled

    # Get associated fills for this order
    order_fills = [f for f in fills if f.execution.orderId == order_id]
    total_commission = sum(
        f.commissionReport.commission
        for f in order_fills
        if f.commissionReport
    )
```

### Discrepancy Detection

The reconciliation system detects and resolves:

**1. Status Mismatches**
```python
if tws_status == "Filled" and db_status != "filled":
    updates = {
        "status": "filled",
        "fill_price": tws_fill_price,
        "filled_quantity": tws_filled_qty,
        "fill_time": get_fill_time(executions),
        "commission": total_commission,
        "tws_status": tws_status,
        "reconciled_at": datetime.now()
    }

    discrepancy = Discrepancy(
        type="STATUS_MISMATCH",
        field="status",
        db_value=db_status,
        tws_value="Filled",
        resolved=True,
        resolution=f"Updated to Filled @ ${tws_fill_price:.2f}"
    )
```

**2. Fill Price Mismatches**
```python
if tws_fill_price and db_trade.fill_price:
    price_diff = abs(tws_fill_price - db_trade.fill_price)

    if price_diff > 0.01:  # More than $0.01 difference
        updates = {
            "fill_price": tws_fill_price,
            "fill_price_discrepancy": price_diff,
            "reconciled_at": datetime.now()
        }

        discrepancy = Discrepancy(
            type="FILL_PRICE_MISMATCH",
            field="fill_price",
            db_value=db_trade.fill_price,
            tws_value=tws_fill_price,
            resolved=True
        )
```

**3. Orphan Orders**
- Orders in TWS but not in database
- Logged as warnings for manual review

**4. Missing in TWS**
- Orders in database but not in TWS
- May indicate old/stale data

### Commission Tracking

Commissions are extracted from IBKR fill reports:

```python
# Calculate total commission from fills
total_commission = sum(
    f.commissionReport.commission
    for f in fills
    if f.commissionReport
)

# Add to database if missing
if total_commission and (not db_trade.commission or db_trade.commission == 0):
    updates["commission"] = total_commission
    updates["reconciled_at"] = datetime.now()

    logger.info(f"Commission added: Order {db_trade.order_id} - ${total_commission:.2f}")
```

**Multiple Fills:**
If an order is filled in multiple executions, commissions are summed:

```python
# Example: Order filled in 3 parts
# Fill 1: 2 contracts @ $0.45, commission $1.25
# Fill 2: 2 contracts @ $0.44, commission $1.25
# Fill 3: 1 contract @ $0.45, commission $1.00
# Total commission: $3.50

fills = [
    create_fill(order_id=123, commission=1.25),
    create_fill(order_id=123, commission=1.25),
    create_fill(order_id=123, commission=1.00),
]

total = sum(f.commissionReport.commission for f in fills if f.commissionReport)
# total = 3.50
```

### Code Example: Full Reconciliation

```python
async def sync_all_orders(self, sync_date: date = None) -> ReconciliationReport:
    """Sync all orders from a given date (default: today)."""
    report = ReconciliationReport(date=sync_date or date.today())

    # Get comprehensive data from IBKR
    ib_trades = self.client.get_trades()
    ib_executions = self.client.get_executions()
    ib_fills = self.client.get_fills()

    # Build lookup maps
    executions_by_order = self._group_by_order(ib_executions)
    fills_by_order = self._group_by_order(ib_fills)

    # Get database trades
    db_trades = self.trade_repo.get_trades_by_date(sync_date)
    db_by_order_id = {t.order_id: t for t in db_trades}

    # Reconcile each trade
    for ib_trade in ib_trades:
        order_id = ib_trade.order.orderId

        if order_id in db_by_order_id:
            db_trade = db_by_order_id[order_id]
            executions = executions_by_order.get(order_id, [])
            fills = fills_by_order.get(order_id, [])

            discrepancy = self._reconcile_single(
                db_trade, ib_trade, executions, fills
            )
            report.add_reconciled(db_trade, ib_trade, discrepancy)
        else:
            report.add_orphan(ib_trade)

    return report
```

---

## Database Schema Updates

### New Reconciliation Columns

The `trades` table includes these columns for order reconciliation:

```python
# In models.py (SQLAlchemy)
class Trade(Base):
    __tablename__ = 'trades'

    # ... existing columns ...

    # Order execution tracking
    order_id = Column(Integer, nullable=True)
    status = Column(String(50), nullable=True)  # submitted, filled, cancelled
    fill_price = Column(Float, nullable=True)

    # NEW: Reconciliation columns
    reconciled_at = Column(DateTime, nullable=True)
    tws_status = Column(String(50), nullable=True)
    commission = Column(Float, nullable=True)
    fill_time = Column(DateTime, nullable=True)
    fill_price_discrepancy = Column(Float, nullable=True)
```

### Column Descriptions

| Column | Type | Description | Example |
|--------|------|-------------|---------|
| `reconciled_at` | DateTime | When reconciliation last ran | `2026-02-03 16:05:00` |
| `tws_status` | String(50) | Actual status from TWS | `"Filled"`, `"Cancelled"`, `"Submitted"` |
| `commission` | Float | Total commission charged | `2.50` |
| `fill_time` | DateTime | Exact time of fill | `2026-02-03 10:30:15` |
| `fill_price_discrepancy` | Float | Absolute difference if fill price changed | `0.02` |

### Migration

```python
# alembic/versions/xxx_add_reconciliation_columns.py
"""Add order reconciliation columns

Revision ID: xxx
Revises: yyy
Create Date: 2026-02-03
"""

from alembic import op
import sqlalchemy as sa

def upgrade():
    op.add_column('trades', sa.Column('reconciled_at', sa.DateTime, nullable=True))
    op.add_column('trades', sa.Column('tws_status', sa.String(50), nullable=True))
    op.add_column('trades', sa.Column('commission', sa.Float, nullable=True))
    op.add_column('trades', sa.Column('fill_time', sa.DateTime, nullable=True))
    op.add_column('trades', sa.Column('fill_price_discrepancy', sa.Float, nullable=True))

def downgrade():
    op.drop_column('trades', 'fill_price_discrepancy')
    op.drop_column('trades', 'fill_time')
    op.drop_column('trades', 'commission')
    op.drop_column('trades', 'tws_status')
    op.drop_column('trades', 'reconciled_at')
```

---

## Order Execution API Reference

### IBKRClient

**Location:** `/src/tools/ibkr_client.py`

#### Connection Methods

```python
def connect(retry: bool = True) -> bool
def disconnect() -> None
def is_connected() -> bool
def ensure_connected() -> None
```

#### Order Operations

```python
async def place_order(
    contract: Contract,
    order: Order,
    reason: str = ""
) -> Trade
    """Place order with audit logging."""

async def cancel_order(order_id: int, reason: str = "") -> bool
    """Cancel order with retry logic."""

async def modify_order(
    trade: Trade,
    new_limit: float,
    reason: str = ""
) -> Trade
    """Modify order limit price."""
```

#### Quote Fetching

```python
async def get_quote(
    contract: Contract,
    timeout: float = 0.5
) -> Quote
    """Event-driven quote fetching.

    Returns immediately when valid quote arrives,
    up to timeout seconds.
    """
```

#### Reconciliation Methods

```python
def get_trades() -> list
    """Get all trades for this session."""

def get_orders() -> list
    """Get all open orders."""

def get_positions() -> list
    """Get all current positions."""

def get_executions() -> list
    """Get all executions (fills) for this session."""

def get_fills() -> list
    """Get all fills with commission details."""
```

### AdaptiveOrderExecutor

**Location:** `/src/services/adaptive_order_executor.py`

```python
class AdaptiveOrderExecutor:
    def __init__(
        self,
        ibkr_client: IBKRClient,
        limit_calc: LimitPriceCalculator
    )

    def create_adaptive_order(
        contracts: int,
        floor_price: float
    ) -> LimitOrder
        """Create Adaptive Algo order with Urgent priority."""

    def create_limit_order(
        contracts: int,
        limit_price: float
    ) -> LimitOrder
        """Create standard LIMIT order (fallback)."""

    async def get_live_quote(contract: Contract) -> LiveQuote
        """Fetch live bid/ask for limit calculation."""

    async def place_order(
        staged: StagedOpportunity,
        contract: Contract,
        quote: LiveQuote
    ) -> OrderResult
        """Place order using Adaptive, fallback to LIMIT if needed."""
```

### RapidFireExecutor

**Location:** `/src/services/rapid_fire_executor.py`

```python
class RapidFireExecutor:
    def __init__(
        self,
        ibkr_client: IBKRClient,
        adaptive_executor: AdaptiveOrderExecutor
    )

    async def execute_all(
        staged_trades: list[StagedOpportunity]
    ) -> ExecutionReport
        """Execute all staged trades using rapid-fire parallel submission.

        Timeline:
            T+0:    Pre-qualify all contracts (batch)
            T+1:    Request live quotes for all (parallel)
            T+2:    Submit all orders (rapid-fire)
            T+3:    Begin async fill monitoring
            T+??:   Condition-based adjustments
            T+120:  Final status, leave unfilled as DAY orders
        """
```

### OrderReconciliation

**Location:** `/src/services/order_reconciliation.py`

```python
class OrderReconciliation:
    def __init__(
        self,
        ibkr_client: IBKRClient,
        trade_repository = None
    )

    async def sync_all_orders(
        sync_date: date = None,
        include_filled: bool = True
    ) -> ReconciliationReport
        """Sync all orders from a given date.

        Steps:
        1. Query TWS for orders, trades, executions, fills
        2. Match to database records by order_id
        3. Update database with actual status, fill price, commission
        4. Generate discrepancy report
        """

    async def reconcile_positions() -> PositionReconciliationReport
        """End-of-day position reconciliation.

        Compares IBKR positions with database positions.
        """
```

---

## Adaptive Strike Selection Algorithm

### Overview

**Location:** `/src/services/live_strike_selector.py`

The LiveStrikeSelector resolves option strikes at execution time using live delta from the IBKR option chain, rather than relying on static strikes from overnight Barchart screening. This preserves the intended risk profile even when stocks move overnight.

### Algorithm

```python
async def _select_for_symbol(opp: StagedOpportunity) -> StrikeSelectionResult:
    """
    1. Get live stock price (IBKR or staged fallback)
    2. Get option chain strikes via reqSecDefOptParams
    3. Filter to OTM put candidates in range
    4. Fetch Greeks for candidates (reqMktData + modelGreeks)
    5. Select strike closest to target delta
    6. Validate boundaries
    7. Return SELECTED, UNCHANGED, or ABANDONED
    """
```

### Chain Retrieval

Uses IBKR's `reqSecDefOptParams` to get available strikes for a given expiration:

```python
async def _get_chain_strikes(symbol, expiration) -> list[float]:
    """
    1. Create Stock contract for symbol
    2. Call ib.reqSecDefOptParams(symbol, '', 'STK', conId)
    3. Filter chain_list to matching exchange and expiration
    4. Return sorted list of available strikes
    """
```

### Candidate Filtering

```python
def _get_candidate_strikes(chain_strikes, stock_price, current_strike) -> list[float]:
    """
    1. Filter to OTM puts only (strike < stock_price)
    2. Calculate OTM% for each: (stock_price - strike) / stock_price
    3. Keep strikes where OTM% >= min_otm_pct (default 10%)
    4. Sort by distance from current_strike (closest first)
    5. Limit to max_candidates (default 5)
    """
```

### Greeks Retrieval

Uses the same pattern as `EntrySnapshotService._capture_greeks()`:

```python
async def _get_greeks_for_strikes(symbol, expiration, strikes) -> dict:
    """
    For each candidate strike:
    1. Create Option contract (symbol, expiration, strike, 'P', 'SMART')
    2. Qualify contract
    3. reqMktData with snapshot=False
    4. Wait for modelGreeks to populate (event-driven, 5s timeout)
    5. Read: delta, iv, gamma, theta from ticker.modelGreeks
    6. Read: bid, ask, volume, open_interest from ticker
    7. Cancel market data subscription

    Returns: {strike: {delta, iv, gamma, theta, bid, ask, volume, oi}}
    """
```

### Delta Matching

```python
def _select_best_strike(candidates, stock_price) -> tuple[float, dict] | None:
    """
    1. Filter candidates with valid delta (not None)
    2. Filter by boundaries:
       - bid >= min_premium (default $0.20)
       - otm_pct >= min_otm_pct (default 10%)
       - spread_pct <= max_spread_pct (default 30%)
       - volume >= min_volume (default 10)
       - open_interest >= min_oi (default 50)
    3. Sort by abs(delta - target_delta) ascending
    4. Select closest within tolerance (default ±0.05)
    5. Return (strike, greeks_dict) or None
    """
```

### Rate Limit Strategy

- **Across symbols:** Sequential processing (not parallel)
- **Within a symbol:** Candidate strikes fetched in parallel via `get_quotes_batch()`
- **Max concurrent streams:** 5 candidates x 1 symbol = 5 (well within IBKR's 100 limit)
- **Cleanup:** All market data subscriptions cancelled after reading

### Configuration

```python
@dataclass
class StrikeSelectionConfig:
    target_delta: float = 0.20        # STRIKE_TARGET_DELTA
    delta_tolerance: float = 0.05     # STRIKE_DELTA_TOLERANCE
    min_otm_pct: float = 0.10        # MIN_OTM_PCT
    min_premium: float = 0.20        # PREMIUM_FLOOR
    max_spread_pct: float = 0.30     # MAX_EXECUTION_SPREAD_PCT
    min_volume: int = 10             # STRIKE_MIN_VOLUME
    min_open_interest: int = 50      # STRIKE_MIN_OI
    max_candidates: int = 5          # STRIKE_MAX_CANDIDATES
    fallback_to_otm: bool = True     # STRIKE_FALLBACK_TO_OTM
    enabled: bool = True             # ADAPTIVE_STRIKE_ENABLED
```

### Result Dataclass

```python
@dataclass
class StrikeSelectionResult:
    opportunity: StagedOpportunity
    status: str                       # "SELECTED", "UNCHANGED", "ABANDONED"
    original_strike: float
    selected_strike: float | None
    selected_delta: float | None
    selected_bid: float | None
    selected_ask: float | None
    selected_otm_pct: float | None
    selected_volume: int | None
    selected_open_interest: int | None
    new_limit_price: float | None
    reason: str
    candidates_evaluated: int
    selection_time_ms: int
```

### Integration Point

Called in `TwoTierExecutionScheduler._execute_tier1_and_tier2()` between Stage 2 (quote refresh) and Tier 1 (rapid-fire execution). Results update `StagedOpportunity` fields in-place:

```python
# Fields updated on StagedOpportunity when status == "SELECTED":
opp.strike = result.selected_strike
opp.execution_limit_price = result.new_limit_price
opp.live_delta = result.selected_delta
opp.live_iv = greeks.get("iv")
opp.live_gamma = greeks.get("gamma")
opp.live_theta = greeks.get("theta")
opp.live_volume = result.selected_volume
opp.live_open_interest = result.selected_open_interest
opp.strike_selection_method = "delta"
```

---

## Fill Management System

### Overview

**Location:** `/src/services/fill_manager.py`

The FillManager replaces the legacy 5-minute sleep after Tier 1 submission with active, time-boxed fill monitoring. It detects partial fills, performs progressive limit adjustment, and leaves unfilled orders working as DAY orders.

### Architecture

```
Tier 1 Submission
    │
    ▼
FillManager.monitor_fills(pending_orders)
    │
    ├─ Every 2 seconds: Check order status
    │   └─ Detect: Filled, Cancelled, Partial
    │
    ├─ Partial fill detected (>50% filled):
    │   └─ Cancel remaining → Place new order for remainder
    │
    ├─ Every 60 seconds: Progressive adjustment
    │   └─ Lower limit by $0.01 (max 5 times)
    │   └─ Never below premium floor ($0.20)
    │
    └─ After 10 minutes: Timeout
        └─ Leave unfilled as DAY orders
        └─ Generate FillReport
```

### Monitoring Loop

```python
async def monitor_fills(pending_orders: list[PendingOrder]) -> FillReport:
    """
    Main monitoring loop:

    1. Initialize tracking for each pending order
    2. Start timer (10-minute window)
    3. Loop until all resolved or timeout:
       a. Check each order status via IBKR
       b. If Filled → mark complete, record fill price
       c. If Cancelled → mark complete, record reason
       d. If Partial (>threshold) → cancel + replace remainder
       e. Every adjustment_interval → progressive_adjust
    4. On timeout: leave unfilled as DAY, generate report
    """
```

### Partial Fill Handling

When `filled_qty / total_qty >= partial_fill_threshold` (default 0.5):

```python
async def _adjust_for_remainder(pending, remaining_qty) -> bool:
    """
    1. Cancel the existing order
    2. Wait for cancellation confirmation
    3. Calculate fresh limit price for remainder
    4. Place new LimitOrder for remaining_qty
    5. Track new order in pending list
    """
```

### Progressive Limit Adjustment

```python
async def _progressive_adjust(pending, adjustment_number) -> bool:
    """
    1. Calculate new limit: current_limit - adjustment_increment ($0.01)
    2. Check floor: if new_limit < premium_floor → skip
    3. Cancel existing order
    4. Place new order at new_limit
    5. Increment adjustments_made counter

    Example progression:
      Original:  $0.45
      Adjust 1:  $0.44  (after 60s)
      Adjust 2:  $0.43  (after 120s)
      Adjust 3:  $0.42  (after 180s)
      Adjust 4:  $0.41  (after 240s)
      Adjust 5:  $0.40  (after 300s — max reached)
    """
```

### Configuration

```python
@dataclass
class FillManagerConfig:
    monitoring_window_seconds: int = 600    # FILL_MONITOR_WINDOW_SECONDS
    check_interval_seconds: float = 2.0     # FILL_CHECK_INTERVAL
    max_adjustments: int = 5                # FILL_MAX_ADJUSTMENTS
    adjustment_increment: float = 0.01      # FILL_ADJUSTMENT_INCREMENT
    adjustment_interval_seconds: int = 60   # FILL_ADJUSTMENT_INTERVAL
    partial_fill_threshold: float = 0.5     # FILL_PARTIAL_THRESHOLD
    leave_working_on_timeout: bool = True   # FILL_LEAVE_WORKING
    min_premium_floor: float = 0.20         # PREMIUM_FLOOR
```

### Result Dataclasses

```python
@dataclass
class FillStatus:
    order_id: int
    symbol: str
    total_qty: int
    filled_qty: int
    remaining_qty: int
    fill_price: float | None
    current_limit: float
    initial_limit: float
    adjustments_made: int
    status: str              # "filled", "partial", "working", "cancelled"
    elapsed_seconds: float
    reason: str

@dataclass
class FillReport:
    started_at: datetime
    completed_at: datetime
    monitoring_window: int
    orders_monitored: int
    fully_filled: int
    partially_filled: int
    left_working: int
    cancelled: int
    total_adjustments: int
```

### Integration Point

Called in `TwoTierExecutionScheduler._execute_tier1_and_tier2()` immediately after Tier 1 `RapidFireExecutor.execute_all()`:

```python
# After Tier 1 submission
if self.fill_manager and pending_orders:
    fill_report = await self.fill_manager.monitor_fills(pending_orders)
    logger.info(f"Fill monitoring: {fill_report.fully_filled} filled, "
                f"{fill_report.left_working} left working")
```

### Database Impact

New fields on `TradeEntrySnapshot` (captured at fill time):
- `strike_selection_method` — "delta", "otm_pct", or "unchanged"
- `original_strike` — strike from overnight screening
- `live_delta_at_selection` — actual delta when strike was selected

These are populated by `EntrySnapshotService.capture_entry_snapshot()` and stored via Alembic migration `d5e6f7g8h9i0`.

---

## NakedTrader Architecture

### Overview

NakedTrader is a standalone daily trading module that sells short-dated index puts using a mechanical delta-targeting approach. It is implemented in `src/nakedtrader/` as a self-contained package, separate from the weekly Barchart-based pipeline.

### Module Structure

```
src/nakedtrader/
├── __init__.py           # Package docstring
├── config.py             # Pydantic config loaded from YAML
├── chain.py              # Index option chain retrieval with Greeks
├── strike_selector.py    # Delta-based strike selection algorithm
├── order_manager.py      # Bracket order placement (parent + children)
├── trade_recorder.py     # Trade database recording
├── watcher.py            # Position monitoring and bracket status checking
└── workflow.py           # Orchestrator: ties all modules into daily flow
```

**Configuration:** `config/daily_spx_options.yaml` loaded into `NakedTraderConfig` (Pydantic model). CLI flags override config values per-run via `with_overrides()`.

### Index Option Chain Retrieval

Index options differ from equity options in several ways the chain module handles:

| Underlying | Contract Type | Trading Class | Settlement | Assignment |
|-----------|--------------|---------------|------------|------------|
| SPX | IND | SPXW (weeklies/dailies) | Cash | European |
| XSP | IND | XSPW (weeklies/dailies) | Cash | European |
| SPY | STK | SPY | Physical | American |

**Chain retrieval flow:**

1. Create underlying contract (`get_index_contract` for SPX/XSP, `get_stock_contract` for SPY)
2. `reqSecDefOptParams` to discover available expirations and strikes for the target trading class
3. Filter expirations to configured DTE range (default 1-4 days)
4. For the selected expiration, filter OTM put strikes to the 90-99% range of underlying price
5. Build and qualify option contracts for candidate strikes (up to 15)
6. `reqMktData` with `modelGreeks` for all candidates (parallel, 3s timeout)
7. Read delta, IV, gamma, theta, bid, ask from tickers
8. Return `ChainResult` with `OptionQuote` list sorted by strike descending

### Delta-Based Strike Selection

The `select_strike()` function implements the mechanical selection:

1. **Filter** quotes where `delta_min <= abs(delta) <= delta_max` AND `bid >= premium.min`
2. **Sort** by distance from `delta_target` (closest first)
3. **Widen** delta range by 0.02 once if no candidates pass the strict filter
4. **Calculate** profit-take price: `max(bid * (1 - profit_target_pct), profit_target_floor)`
5. **Calculate** stop-loss price (if enabled): `bid * stop_loss_multiplier`
6. Return `StrikeSelection` with the best quote and exit prices

### Bracket Order Implementation

The `place_bracket_order()` function creates IBKR native parent-child bracket orders:

```
Order Group (atomic submission via transmit flag):
│
├── Parent: SELL PUT (LMT, DAY, transmit=False)
│   orderId = N
│
├── Child 1: BUY (profit-take, LMT, GTC, parentId=N)
│   transmit = True if no stop-loss, else False
│
└── Child 2: BUY (stop-loss, LMT, GTC, parentId=N)  [optional]
    transmit = True (sends entire group)
```

**Key behaviors:**

- Parent SELL uses DAY time-in-force; children use GTC
- Children activate only after parent fills
- When both profit-take and stop-loss are present, IBKR creates an OCA (One-Cancels-All) group: whichever fills first cancels the other
- The `transmit=False` / `transmit=True` pattern ensures the entire bracket is submitted atomically
- After submission, `wait_for_fill()` polls order status every 2 seconds until the parent fills or times out

### Database Schema Additions

The `trades` table has four NakedTrader-specific columns (added via Alembic migration):

```sql
trade_strategy    VARCHAR(20)   -- 'nakedtrader' for NT trades (indexed)
exit_order_id     INTEGER       -- IBKR order ID for profit-take child
stop_order_id     INTEGER       -- IBKR order ID for stop-loss child
bracket_status    VARCHAR(20)   -- 'active', 'profit_taken', 'stopped', 'expired'
```

The `trade_recorder.py` module sets `trade_strategy='nakedtrader'` and `trade_source='paper'` on all NT trades. The `watcher.py` module queries for open NT trades by filtering on `trade_strategy='nakedtrader'` and `exit_date IS NULL`, then checks IBKR order status to detect bracket fills and update `bracket_status`.

---

## Document History

This reference consolidates:
- TRADE_DATA_COLLECTION_SPEC.md
- naked_put_ranking_rules_v2.md
- scoring_implementation_approach.md
- IBKR_FIELD_REFERENCE.md
- BARCHART_API_GUIDE.md
- Implementation_Plan_Modifications_for_Self_Learning_Goal.md
- Parts of SPEC_TRADING_SYSTEM.md
- ORDER_EXECUTION_IMPROVEMENTS_PLAN.md (new)

**Version History:**
| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-31 | Initial consolidated version |
| 1.1 | 2026-02-03 | Added order execution architecture documentation |
| 1.2 | 2026-02-16 | Added Adaptive Strike Selection Algorithm, Fill Management System, updated data flow diagrams |
| 1.3 | 2026-02-18 | Added NakedTrader Architecture section |
