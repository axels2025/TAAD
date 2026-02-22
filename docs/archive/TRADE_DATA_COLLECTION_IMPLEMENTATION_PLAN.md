# Trade Data Collection Implementation Plan
## Phase 2.6: Building the Foundation for Self-Learning

**Document Version:** 1.0  
**Created:** January 27, 2026  
**Purpose:** Phased implementation plan for comprehensive trade data collection  
**Reference:** `TRADE_DATA_COLLECTION_SPEC.md` for field definitions  
**Estimated Effort:** 4-5 weeks  

---

## Executive Summary

This plan implements the data collection infrastructure defined in `TRADE_DATA_COLLECTION_SPEC.md`. It captures ~125 data points per trade across five dimensions: Option, Underlying, Market, Events, and Position Path.

```
┌─────────────────────────────────────────────────────────────────────────────┐
│  PHASE 2.6: TRADE DATA COLLECTION FOR LEARNING                              │
│                                                                             │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐ │
│  │  2.6A    │──▶│  2.6B    │──▶│  2.6C    │──▶│  2.6D    │──▶│  2.6E    │ │
│  │ Critical │   │Technical │   │ Market & │   │ Position │   │  Exit &  │ │
│  │  Fields  │   │Indicators│   │  Events  │   │Monitoring│   │  Learning│ │
│  │ (5 days) │   │ (4 days) │   │ (4 days) │   │ (4 days) │   │  (4 days)│ │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘ │
│                                                                             │
│  Total: 21 days (4-5 weeks with testing and buffer)                        │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Phase 2.6A: Critical Fields Infrastructure (5 days)

### Objective
Implement the 8 highest-value data fields that drive ~80% of predictive power.

### Critical Fields (Priority 1)

| Field | Source | Learning Value |
|-------|--------|----------------|
| `delta` | IBKR Greeks | Probability of profit proxy |
| `iv` | IBKR Greeks | Premium richness |
| `iv_rank` | Calculated | IV relative to 52-week range |
| `vix` | IBKR | Market fear gauge |
| `dte` | Calculated | Time decay driver |
| `trend_direction` | Calculated | Stock health |
| `days_to_earnings` | External API | Event risk |
| `margin_efficiency_pct` | Calculated | Capital efficiency (your 1:10-1:20 ratio) |

### Deliverables

#### 2.6A.1: Database Schema

**File:** `migrations/005_trade_entry_snapshots.sql`

```sql
CREATE TABLE IF NOT EXISTS trade_entry_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    opportunity_id INTEGER,
    
    -- Option Data
    symbol TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration DATE NOT NULL,
    option_type TEXT DEFAULT 'PUT',
    bid REAL,
    ask REAL,
    mid REAL,
    entry_premium REAL NOT NULL,
    spread_pct REAL,
    
    -- Greeks (Critical)
    delta REAL,
    gamma REAL,
    theta REAL,
    vega REAL,
    rho REAL,
    
    -- Volatility (Critical)
    iv REAL,
    iv_rank REAL,
    iv_percentile REAL,
    hv_20 REAL,
    iv_hv_ratio REAL,
    
    -- Liquidity
    option_volume INTEGER,
    open_interest INTEGER,
    volume_oi_ratio REAL,
    
    -- Underlying Data
    stock_price REAL NOT NULL,
    stock_open REAL,
    stock_high REAL,
    stock_low REAL,
    stock_prev_close REAL,
    stock_change_pct REAL,
    
    -- Calculated Metrics (Critical)
    otm_pct REAL,
    otm_dollars REAL,
    dte INTEGER,
    margin_requirement REAL,
    margin_efficiency_pct REAL,
    
    -- Trend (Critical)
    sma_20 REAL,
    sma_50 REAL,
    trend_direction TEXT,
    trend_strength REAL,
    price_vs_sma20_pct REAL,
    price_vs_sma50_pct REAL,
    
    -- Market Data (Critical)
    spy_price REAL,
    spy_change_pct REAL,
    vix REAL,
    vix_change_pct REAL,
    
    -- Event Data (Critical)
    earnings_date DATE,
    days_to_earnings INTEGER,
    earnings_in_dte BOOLEAN,
    
    -- Metadata
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    data_quality_score REAL,
    source TEXT,
    
    FOREIGN KEY (trade_id) REFERENCES trades(id),
    FOREIGN KEY (opportunity_id) REFERENCES opportunities(id)
);

-- Indexes for learning queries
CREATE INDEX idx_entry_trade_id ON trade_entry_snapshots(trade_id);
CREATE INDEX idx_entry_delta ON trade_entry_snapshots(delta);
CREATE INDEX idx_entry_iv_rank ON trade_entry_snapshots(iv_rank);
CREATE INDEX idx_entry_vix ON trade_entry_snapshots(vix);
CREATE INDEX idx_entry_trend ON trade_entry_snapshots(trend_direction);
CREATE INDEX idx_entry_dte ON trade_entry_snapshots(dte);
CREATE INDEX idx_entry_days_to_earnings ON trade_entry_snapshots(days_to_earnings);
```

#### 2.6A.2: Data Models

**File:** `src/data/models/entry_snapshot.py`

Key classes:
- `OptionEntryData` - Option pricing, Greeks, IV
- `UnderlyingEntryData` - Stock price, trend, metrics
- `MarketEntryData` - VIX, SPY, broad market
- `EventEntryData` - Earnings, dividends
- `TradeEntrySnapshot` - Combined snapshot

#### 2.6A.3: Entry Snapshot Service

**File:** `src/services/entry_snapshot_service.py`

```python
class EntrySnapshotService:
    """Captures comprehensive data at trade entry."""
    
    def capture_entry_snapshot(
        self,
        trade_id: int,
        opportunity: TradeOpportunity,
    ) -> TradeEntrySnapshot:
        """
        Capture complete entry snapshot.
        
        Gathers data from:
        - IBKR: Real-time prices, Greeks, margin
        - Calculations: SMAs, trend, IV rank
        - External APIs: Earnings dates
        """
        pass
    
    def _capture_option_data(self, opportunity) -> OptionEntryData:
        """Get Greeks, IV, pricing from IBKR."""
        pass
    
    def _capture_underlying_data(self, opportunity) -> UnderlyingEntryData:
        """Get stock price, calculate trend."""
        pass
    
    def _calculate_margin_efficiency(self, snapshot, opportunity) -> None:
        """Calculate your key metric: premium / margin ratio."""
        pass
```

#### 2.6A.4: Repository

**File:** `src/data/repositories/entry_snapshot_repository.py`

#### 2.6A.5: Trade Command Integration

Update `src/cli/main.py` to capture snapshot after successful execution:

```python
# After order fills
if result.success:
    snapshot_service = EntrySnapshotService(client)
    snapshot = snapshot_service.capture_entry_snapshot(
        trade_id=result.trade_id,
        opportunity=opportunity,
    )
    snapshot_repo.save(snapshot)
```

### Success Criteria

| Criterion | Test |
|-----------|------|
| All 8 critical fields captured | Check snapshot after trade |
| Delta matches IBKR | Compare to TWS display |
| Margin efficiency calculated | Verify formula |
| Data quality scored | Missing fields reduce score |

---

## Phase 2.6B: Technical Indicators (4 days)

### Objective
Add momentum and volatility indicators for pattern detection.

### Fields to Add

| Indicator | Fields | Learning Use |
|-----------|--------|--------------|
| RSI | `rsi_14`, `rsi_7` | Overbought/oversold |
| MACD | `macd`, `macd_signal`, `macd_histogram` | Momentum |
| ADX | `adx`, `plus_di`, `minus_di` | Trend strength |
| ATR | `atr_14`, `atr_pct` | Volatility |
| Bollinger | `bb_upper`, `bb_lower`, `bb_position` | Mean reversion |
| Support/Resistance | `support_1`, `support_2`, `resistance_1`, `resistance_2` | Strike placement |

### Deliverables

#### 2.6B.1: Technical Indicator Calculator

**File:** `src/analysis/technical_indicators.py`

```python
class TechnicalIndicatorCalculator:
    """Calculates all technical indicators from OHLCV data."""
    
    def calculate_all(
        self,
        symbol: str,
        closes: List[float],
        highs: List[float],
        lows: List[float],
    ) -> TechnicalIndicators:
        """Calculate complete indicator set."""
        pass
    
    def _calculate_rsi(self, closes: np.ndarray, period: int) -> float:
        """Relative Strength Index."""
        pass
    
    def _calculate_macd(self, closes: np.ndarray) -> Tuple[float, float, float]:
        """MACD, Signal, Histogram."""
        pass
    
    def _calculate_adx(self, highs, lows, closes, period: int) -> Tuple[float, float, float]:
        """ADX, +DI, -DI."""
        pass
    
    def _calculate_bollinger(self, closes: np.ndarray, period: int, std: float) -> Tuple[float, float, float]:
        """Upper, Lower, Middle bands."""
        pass
    
    def _calculate_support_resistance(self, highs, lows, closes) -> Tuple[float, float, float, float]:
        """S1, S2, R1, R2."""
        pass
```

#### 2.6B.2: Schema Update

**File:** `migrations/006_technical_indicators.sql`

```sql
ALTER TABLE trade_entry_snapshots ADD COLUMN rsi_14 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN rsi_7 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN macd REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN macd_signal REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN macd_histogram REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN adx REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN plus_di REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN minus_di REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN atr_14 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN atr_pct REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN bollinger_upper REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN bollinger_lower REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN bollinger_position REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN support_1 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN support_2 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN resistance_1 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN resistance_2 REAL;
ALTER TABLE trade_entry_snapshots ADD COLUMN distance_to_support_pct REAL;
```

### Success Criteria

| Criterion | Test |
|-----------|------|
| RSI range 0-100 | Boundary check |
| MACD sign matches trend | Visual verification |
| Support below current price | Logic check |
| Indicators match TradingView | External validation |

---

## Phase 2.6C: Market & Event Data (4 days)

### Objective
Add broad market context and event catalysts.

### Fields to Add

| Category | Fields |
|----------|--------|
| **Indices** | `qqq_price`, `iwm_price`, `dia_price`, changes |
| **Volatility** | `vix_term_structure`, `vol_regime` |
| **Breadth** | `advance_decline_ratio`, `put_call_ratio` |
| **Sector** | `sector`, `sector_etf`, `sector_change_*` |
| **Calendar** | `day_of_week`, `is_opex_week`, `days_to_fomc` |
| **Events** | `earnings_timing`, `ex_dividend_date` |

### Deliverables

#### 2.6C.1: Market Context Service

**File:** `src/services/market_context_service.py`

```python
class MarketContextService:
    """Captures broad market context."""
    
    SECTOR_ETFS = {
        "Technology": "XLK",
        "Healthcare": "XLV",
        "Financials": "XLF",
        # ... etc
    }
    
    FOMC_DATES_2026 = [
        date(2026, 1, 28),
        date(2026, 3, 18),
        # ... etc
    ]
    
    def capture_full_context(self, symbol: str) -> FullMarketContext:
        """Capture complete market snapshot."""
        pass
    
    def _classify_market_regime(self, context) -> str:
        """Classify: bullish, bearish, neutral, volatile."""
        pass
    
    def _classify_vol_regime(self, vix: float) -> str:
        """Classify: low, normal, elevated, extreme."""
        pass
```

#### 2.6C.2: Earnings Service

**File:** `src/services/earnings_service.py`

```python
class EarningsService:
    """Fetches earnings dates from external sources."""
    
    def get_earnings_info(self, symbol: str) -> EarningsInfo:
        """Get next earnings date and history."""
        pass
    
    def calculate_days_to_earnings(
        self, 
        symbol: str, 
        expiration: date,
    ) -> Tuple[int, bool]:
        """Returns (days_to_earnings, earnings_in_dte)."""
        pass
```

#### 2.6C.3: Schema Update

**File:** `migrations/007_market_event_data.sql`

### Success Criteria

| Criterion | Test |
|-----------|------|
| Sector identified | Check populated |
| Calendar flags accurate | Verify against calendar |
| Earnings dates match Yahoo | External validation |
| Market regime sensible | VIX=25, SPY down = "bearish" |

---

## Phase 2.6D: Position Monitoring (4 days)

### Objective
Capture daily snapshots while positions are open for path analysis.

### Fields per Daily Snapshot

| Field | Purpose |
|-------|---------|
| `current_premium` | Track option price |
| `current_pnl` | Track profit/loss |
| `delta` | Track Greek evolution |
| `theta` | Track time decay |
| `iv` | Track IV changes |
| `stock_price` | Track underlying |
| `vix` | Track market |
| `distance_to_strike_pct` | Track safety buffer |

### Deliverables

#### 2.6D.1: Position Snapshot Schema

**File:** `migrations/008_position_snapshots.sql`

```sql
CREATE TABLE IF NOT EXISTS position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL,
    snapshot_date DATE NOT NULL,
    
    current_premium REAL,
    current_pnl REAL,
    current_pnl_pct REAL,
    dte_remaining INTEGER,
    
    delta REAL,
    theta REAL,
    gamma REAL,
    vega REAL,
    iv REAL,
    
    stock_price REAL,
    distance_to_strike_pct REAL,
    
    vix REAL,
    spy_price REAL,
    
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (trade_id) REFERENCES trades(id),
    UNIQUE(trade_id, snapshot_date)
);
```

#### 2.6D.2: Position Snapshot Service

**File:** `src/services/position_snapshot_service.py`

```python
class PositionSnapshotService:
    """Captures daily position snapshots."""
    
    def capture_all_positions(self) -> List[PositionSnapshot]:
        """Capture snapshots for all open positions."""
        pass
```

#### 2.6D.3: Snapshot Scheduler

**File:** `src/services/snapshot_scheduler.py`

```python
class SnapshotScheduler:
    """Schedules daily snapshots at market close."""
    
    SNAPSHOT_TIME = time(16, 0)  # 4 PM ET
    
    def run_daily_snapshot(self) -> int:
        """Execute daily capture. Returns count."""
        pass
```

### Success Criteria

| Criterion | Test |
|-----------|------|
| One snapshot per position per day | UNIQUE constraint |
| P&L matches IBKR | Compare values |
| Greeks tracked over time | Query history |

---

## Phase 2.6E: Exit Snapshots & Learning Prep (4 days)

### Objective
Capture exit data and prepare complete dataset for learning.

### Exit Fields

| Category | Fields |
|----------|--------|
| **Outcome** | `roi_pct`, `roi_on_margin`, `win`, `days_held` |
| **Context** | `exit_iv`, `iv_crush`, `stock_change_during_trade` |
| **Path** | `closest_to_strike_pct`, `max_drawdown_pct` |
| **Quality** | `trade_quality_score`, `risk_adjusted_return` |

### Deliverables

#### 2.6E.1: Exit Snapshot Schema

**File:** `migrations/009_exit_snapshots.sql`

```sql
CREATE TABLE IF NOT EXISTS trade_exit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER NOT NULL UNIQUE,
    
    -- Exit details
    exit_date TIMESTAMP NOT NULL,
    exit_premium REAL NOT NULL,
    exit_reason TEXT NOT NULL,
    
    -- Outcome
    days_held INTEGER,
    gross_profit REAL,
    net_profit REAL,
    roi_pct REAL,
    roi_on_margin REAL,
    win BOOLEAN,
    max_profit_captured_pct REAL,
    
    -- Context changes
    exit_iv REAL,
    iv_change_during_trade REAL,
    stock_change_during_trade_pct REAL,
    vix_change_during_trade REAL,
    
    -- Path analysis
    closest_to_strike_pct REAL,
    stock_max_drawdown_pct REAL,
    
    -- Learning features
    trade_quality_score REAL,
    risk_adjusted_return REAL,
    
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);
```

#### 2.6E.2: Exit Snapshot Service

**File:** `src/services/exit_snapshot_service.py`

```python
class ExitSnapshotService:
    """Captures comprehensive exit data."""
    
    def capture_exit_snapshot(
        self,
        trade_id: int,
        exit_premium: float,
        exit_reason: str,
    ) -> ExitSnapshot:
        """
        Capture complete exit snapshot.
        
        Combines:
        - Exit execution data
        - Entry snapshot for comparison
        - Position snapshots for path analysis
        """
        pass
    
    def _calculate_outcomes(self, snapshot, entry, exit_premium) -> None:
        """Calculate ROI, win/loss, etc."""
        pass
    
    def _analyze_position_path(self, snapshot, position_history, entry) -> None:
        """Analyze min distance to strike, max drawdown."""
        pass
    
    def _calculate_learning_features(self, snapshot) -> None:
        """Calculate quality score, risk-adjusted return."""
        pass
```

#### 2.6E.3: Learning Data View

**File:** `migrations/010_learning_views.sql`

```sql
CREATE VIEW IF NOT EXISTS trade_learning_data AS
SELECT 
    t.id as trade_id,
    t.symbol,
    
    -- Entry features (predictors)
    e.delta as entry_delta,
    e.iv as entry_iv,
    e.iv_rank as entry_iv_rank,
    e.dte as entry_dte,
    e.otm_pct as entry_otm_pct,
    e.margin_efficiency_pct,
    e.trend_direction,
    e.rsi_14,
    e.adx,
    e.vix as entry_vix,
    e.vol_regime,
    e.market_regime,
    e.days_to_earnings,
    e.earnings_in_dte,
    e.sector,
    e.is_opex_week,
    e.day_of_week,
    
    -- Outcome (target variables)
    x.win,
    x.roi_pct,
    x.roi_on_margin,
    x.days_held,
    x.exit_reason,
    x.trade_quality_score,
    x.iv_change_during_trade as iv_crush,
    x.closest_to_strike_pct as min_buffer,
    x.stock_max_drawdown_pct as max_drawdown
    
FROM trades t
JOIN trade_entry_snapshots e ON t.id = e.trade_id
JOIN trade_exit_snapshots x ON t.id = x.trade_id
WHERE e.data_quality_score >= 0.7;
```

#### 2.6E.4: Learning Data Export

**File:** `src/learning/data_export.py`

```python
class LearningDataExporter:
    """Export trade data for learning analysis."""
    
    def export_to_dataframe(self, min_quality: float = 0.7) -> pd.DataFrame:
        """Export to pandas DataFrame."""
        pass
    
    def export_to_csv(self, path: Path) -> int:
        """Export to CSV file."""
        pass
    
    def get_feature_statistics(self) -> dict:
        """Get coverage stats for all features."""
        pass
```

### Success Criteria

| Criterion | Test |
|-----------|------|
| Exit snapshots captured | Check after closing trade |
| ROI calculations correct | Manual verification |
| Learning view populated | Query returns data |
| CSV export works | File created |

---

## Configuration

### New `.env` Parameters

```bash
# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2.6: DATA COLLECTION
# ═══════════════════════════════════════════════════════════════════════════

# Data Quality
MIN_DATA_QUALITY_FOR_LEARNING=0.70

# Feature Capture
CAPTURE_TECHNICAL_INDICATORS=true
CAPTURE_MARKET_CONTEXT=true
CAPTURE_EVENT_DATA=true

# Position Monitoring
DAILY_SNAPSHOT_ENABLED=true
DAILY_SNAPSHOT_TIME=16:00

# External Data
EARNINGS_DATA_SOURCE=yahoo
```

---

## File Summary

### New Files (15)

| Phase | Files |
|-------|-------|
| 2.6A | 4 files: migration, models, service, repository |
| 2.6B | 2 files: calculator, migration |
| 2.6C | 3 files: market service, earnings service, migration |
| 2.6D | 3 files: snapshot service, scheduler, migration |
| 2.6E | 3 files: exit service, learning view, data export |

### Estimated Lines of Code

| Phase | Lines |
|-------|-------|
| 2.6A | ~800 |
| 2.6B | ~400 |
| 2.6C | ~500 |
| 2.6D | ~300 |
| 2.6E | ~500 |
| **Total** | **~2,500** |

---

## Schedule

| Week | Phase | Focus |
|------|-------|-------|
| 1 | 2.6A | Critical fields, entry snapshots |
| 2 | 2.6B | Technical indicators |
| 3 | 2.6C | Market context, events |
| 4 | 2.6D + 2.6E | Position monitoring, exit snapshots, learning prep |

---

## Learning Engine Integration

This data collection enables Phase 3 (Learning Engine) to:

| Analysis | Enabled By |
|----------|------------|
| Delta bucket patterns | `entry_delta` |
| VIX regime analysis | `entry_vix`, `vol_regime` |
| Sector performance | `sector`, `sector_change` |
| Earnings timing impact | `days_to_earnings`, `earnings_in_dte` |
| Momentum patterns | RSI, MACD, ADX |
| IV crush analysis | `entry_iv`, `exit_iv`, `iv_crush` |
| Position path analysis | Daily snapshots |

The learning engine queries `trade_learning_data` view to find statistically significant patterns with p < 0.05.

---

## Claude Code Prompt

To implement this plan, provide Claude Code with:

```
I have an implementation plan in TRADE_DATA_COLLECTION_IMPLEMENTATION_PLAN.md
and field definitions in TRADE_DATA_COLLECTION_SPEC.md.

Please implement Phase 2.6 (Trade Data Collection) following these guidelines:

1. Read both documents completely first
2. Follow development standards in CLAUDE.md  
3. Implement phases in order: 2.6A → 2.6B → 2.6C → 2.6D → 2.6E
4. Use subagents for parallel work:
   - Main: implementation code
   - Subagent 1: unit tests
   - Subagent 2: migrations
5. Run quality gates after each sub-phase
6. Request approval at phase boundaries

Start with Phase 2.6A (Critical Fields Infrastructure).
Work autonomously without asking permission for file operations.
```

---

**End of Implementation Plan**
