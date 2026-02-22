# Trade Data Collection Specification
## Comprehensive Data Capture for Self-Learning Trading System

**Document Version:** 1.0  
**Created:** January 27, 2026  
**Purpose:** Define all data points to capture at entry, during hold, and at exit for pattern detection and learning  
**Status:** Ready for implementation

---

## Executive Summary

To enable effective pattern detection and learning, we need to capture data across **five dimensions**:

| Dimension | Purpose | When Captured |
|-----------|---------|---------------|
| **Option Data** | Core trade parameters | Entry |
| **Underlying Data** | Stock-level context | Entry + Daily |
| **Market Data** | Broad market context | Entry + Daily |
| **Fundamental Data** | Company health signals | Entry (weekly refresh) |
| **Event Data** | Catalysts and timing | Entry + As occurs |

The learning engine will analyze correlations between these inputs and trade outcomes (win/loss, ROI, hold time) to discover profitable patterns.

---

## Data Collection Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         TRADE ENTRY POINT                               │
│                                                                         │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │   Option    │  │ Underlying  │  │   Market    │  │ Fundamental │   │
│  │    Data     │  │    Data     │  │    Data     │  │    Data     │   │
│  │  (17 fields)│  │ (25 fields) │  │ (18 fields) │  │ (12 fields) │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│         │                │                │                │           │
│         └────────────────┴────────────────┴────────────────┘           │
│                                    │                                    │
│                          ┌─────────▼─────────┐                         │
│                          │  ENTRY SNAPSHOT   │                         │
│                          │   (~72 fields)    │                         │
│                          └─────────┬─────────┘                         │
│                                    │                                    │
└────────────────────────────────────┼────────────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    │                │                │
            ┌───────▼───────┐ ┌─────▼─────┐ ┌───────▼───────┐
            │  DAILY SNAP   │ │   EVENT   │ │  EXIT SNAP    │
            │  (position    │ │  CAPTURES │ │  (outcome +   │
            │   updates)    │ │ (earnings,│ │   context)    │
            └───────────────┘ │  news)    │ └───────────────┘
                              └───────────┘
```

---

## 1. Option Data (Capture at Entry)

These are the core parameters of the option contract itself.

### 1.1 Contract Identification

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `symbol` | str | User/Barchart | "AAPL" | Sector/stock patterns |
| `strike` | float | User/Barchart | 180.00 | Strike selection optimization |
| `expiration` | date | User/Barchart | 2025-02-14 | DTE analysis |
| `option_type` | str | Fixed | "PUT" | Strategy type |
| `contract_id` | str | IBKR | "265598" | Unique identifier |

### 1.2 Pricing Data

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `bid` | float | IBKR | 0.44 | Spread analysis |
| `ask` | float | IBKR | 0.48 | Spread analysis |
| `mid` | float | Calculated | 0.46 | Reference price |
| `last` | float | IBKR | 0.45 | Execution comparison |
| `entry_premium` | float | Execution | 0.45 | ROI calculation |
| `spread_pct` | float | Calculated | 0.087 | Liquidity indicator |
| `spread_dollars` | float | Calculated | 0.04 | Cost analysis |

### 1.3 Greeks (Critical for Learning)

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `delta` | float | IBKR | -0.15 | **Primary OTM proxy** |
| `gamma` | float | IBKR | 0.02 | Price sensitivity |
| `theta` | float | IBKR | -0.03 | Time decay rate |
| `vega` | float | IBKR | 0.08 | IV sensitivity |
| `rho` | float | IBKR | -0.01 | Interest rate sensitivity |

### 1.4 Volatility Data

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `iv` | float | IBKR | 0.45 | **Key predictor** |
| `iv_rank` | float | Calculated/Barchart | 0.72 | IV relative to history |
| `iv_percentile` | float | Calculated | 0.68 | IV distribution position |
| `hv_20` | float | IBKR/Calculated | 0.38 | Historical vol baseline |
| `iv_hv_ratio` | float | Calculated | 1.18 | Premium indicator |

### 1.5 Liquidity Metrics

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `volume` | int | IBKR | 1250 | Trade activity |
| `open_interest` | int | IBKR | 8500 | Position buildup |
| `volume_oi_ratio` | float | Calculated | 0.15 | Activity vs positions |

---

## 2. Underlying (Stock) Data (Capture at Entry + Daily)

### 2.1 Price Data

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `stock_price` | float | IBKR | 204.50 | Reference price |
| `stock_open` | float | IBKR | 203.80 | Intraday context |
| `stock_high` | float | IBKR | 206.20 | Daily range |
| `stock_low` | float | IBKR | 202.90 | Daily range |
| `stock_close_prev` | float | IBKR | 203.50 | Previous close |
| `stock_change_pct` | float | Calculated | 0.0049 | Daily momentum |

### 2.2 Calculated Trade Metrics

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `otm_pct` | float | Calculated | 0.12 | **Distance from strike** |
| `otm_dollars` | float | Calculated | 24.50 | Absolute buffer |
| `dte` | int | Calculated | 14 | **Time to expiration** |
| `margin_requirement` | float | IBKR | 3800.00 | Capital efficiency |
| `margin_efficiency_pct` | float | Calculated | 0.059 | Premium/Margin ratio |

### 2.3 Trend Indicators

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `sma_5` | float | Calculated | 205.20 | Short-term trend |
| `sma_10` | float | Calculated | 204.80 | Short-term trend |
| `sma_20` | float | Calculated | 202.50 | **Primary trend** |
| `sma_50` | float | Calculated | 198.30 | **Primary trend** |
| `sma_200` | float | Calculated | 185.40 | Long-term trend |
| `ema_9` | float | Calculated | 204.90 | Fast EMA |
| `ema_21` | float | Calculated | 203.20 | Slow EMA |
| `trend_direction` | str | Derived | "uptrend" | Trend classification |
| `trend_strength` | float | Derived | 0.75 | Trend confidence |
| `price_vs_sma20_pct` | float | Calculated | 0.0099 | Distance from trend |
| `price_vs_sma50_pct` | float | Calculated | 0.0313 | Distance from trend |

### 2.4 Momentum Indicators

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `rsi_14` | float | Calculated | 58.5 | Overbought/oversold |
| `rsi_7` | float | Calculated | 62.3 | Short-term RSI |
| `macd` | float | Calculated | 1.25 | Momentum |
| `macd_signal` | float | Calculated | 0.95 | Signal line |
| `macd_histogram` | float | Calculated | 0.30 | Momentum direction |
| `adx` | float | Calculated | 28.5 | Trend strength |
| `plus_di` | float | Calculated | 32.1 | Directional indicator |
| `minus_di` | float | Calculated | 18.4 | Directional indicator |

### 2.5 Volatility Indicators

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `atr_14` | float | Calculated | 3.25 | Average true range |
| `atr_pct` | float | Calculated | 0.016 | ATR as % of price |
| `bollinger_upper` | float | Calculated | 212.50 | Upper band |
| `bollinger_lower` | float | Calculated | 196.50 | Lower band |
| `bollinger_width` | float | Calculated | 0.078 | Band width |
| `bollinger_position` | float | Calculated | 0.65 | Price position in bands |

### 2.6 Volume Analysis

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `stock_volume` | int | IBKR | 45000000 | Today's volume |
| `avg_volume_20` | int | Calculated | 52000000 | Average volume |
| `relative_volume` | float | Calculated | 0.87 | Volume ratio |
| `volume_trend` | str | Derived | "declining" | Volume direction |

### 2.7 Support/Resistance

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `support_1` | float | Calculated | 200.00 | Nearest support |
| `support_2` | float | Calculated | 195.50 | Secondary support |
| `resistance_1` | float | Calculated | 210.00 | Nearest resistance |
| `resistance_2` | float | Calculated | 215.00 | Secondary resistance |
| `distance_to_support_pct` | float | Calculated | 0.022 | Buffer to support |
| `strike_vs_support` | str | Derived | "below" | Strike position vs support |

---

## 3. Market Data (Broad Context)

### 3.1 Index Levels

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `spy_price` | float | IBKR | 502.30 | S&P 500 level |
| `spy_change_pct` | float | Calculated | 0.0045 | Market direction |
| `qqq_price` | float | IBKR | 432.50 | Nasdaq level |
| `qqq_change_pct` | float | Calculated | 0.0062 | Tech sentiment |
| `iwm_price` | float | IBKR | 198.20 | Small cap level |
| `iwm_change_pct` | float | Calculated | 0.0028 | Small cap sentiment |
| `dia_price` | float | IBKR | 425.80 | Dow level |

### 3.2 Volatility Measures

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `vix` | float | IBKR | 18.50 | **Fear gauge** |
| `vix_change_pct` | float | Calculated | -0.035 | VIX momentum |
| `vix_term_structure` | str | Derived | "contango" | VIX curve shape |
| `vix_9d` | float | IBKR (if available) | 16.80 | Short-term VIX |
| `vix_3m` | float | IBKR (if available) | 19.20 | 3-month VIX |
| `vvix` | float | IBKR (if available) | 95.30 | Vol of VIX |

### 3.3 Market Breadth

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `advance_decline_ratio` | float | External API | 1.45 | Market breadth |
| `new_highs` | int | External API | 125 | Strength indicator |
| `new_lows` | int | External API | 38 | Weakness indicator |
| `pct_above_sma200` | float | External API | 0.68 | Broad trend health |

### 3.4 Sector Context

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `sector` | str | Lookup | "Technology" | Sector classification |
| `sector_etf` | str | Lookup | "XLK" | Sector ETF symbol |
| `sector_performance_1d` | float | Calculated | 0.0082 | Sector momentum |
| `sector_performance_5d` | float | Calculated | 0.0235 | Sector trend |
| `sector_rank` | int | Calculated | 2 | Sector relative strength |

### 3.5 Calendar/Timing

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `day_of_week` | int | System | 1 | Monday=0, Friday=4 |
| `week_of_month` | int | Calculated | 2 | Week position |
| `month` | int | System | 2 | Month number |
| `is_options_expiry_week` | bool | Calendar | True | OpEx week flag |
| `is_quad_witching` | bool | Calendar | False | Quad witching flag |
| `days_to_fomc` | int | Calendar | 12 | Days until FOMC |
| `market_session` | str | Clock | "regular" | Session type |
| `time_of_day` | str | Clock | "morning" | AM/Midday/PM |

---

## 4. Fundamental Data (Company Health)

### 4.1 Valuation Metrics

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `market_cap` | float | IBKR/External | 3.2e12 | Company size |
| `market_cap_category` | str | Derived | "mega" | Size bucket |
| `pe_ratio` | float | External | 28.5 | Earnings valuation |
| `forward_pe` | float | External | 24.2 | Forward valuation |
| `peg_ratio` | float | External | 1.8 | Growth-adjusted PE |
| `ps_ratio` | float | External | 7.2 | Price to sales |
| `pb_ratio` | float | External | 45.3 | Price to book |

### 4.2 Financial Health

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `debt_to_equity` | float | External | 1.45 | Leverage |
| `current_ratio` | float | External | 1.02 | Liquidity |
| `quick_ratio` | float | External | 0.95 | Short-term liquidity |
| `roe` | float | External | 0.145 | Return on equity |
| `profit_margin` | float | External | 0.25 | Profitability |

### 4.3 Growth Metrics

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `revenue_growth_yoy` | float | External | 0.08 | Revenue trend |
| `earnings_growth_yoy` | float | External | 0.12 | Earnings trend |
| `eps_surprise_last` | float | External | 0.05 | Last EPS beat/miss |
| `revenue_surprise_last` | float | External | 0.02 | Last revenue beat/miss |

### 4.4 Dividend Info

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `dividend_yield` | float | External | 0.005 | Yield |
| `ex_dividend_date` | date | External | 2025-02-10 | Ex-div date |
| `days_to_ex_dividend` | int | Calculated | 8 | **Important for puts** |

---

## 5. Event Data (Catalysts)

### 5.1 Earnings

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `earnings_date` | date | External | 2025-02-01 | Next earnings |
| `days_to_earnings` | int | Calculated | 5 | **Critical for IV** |
| `earnings_timing` | str | External | "AMC" | Before/After market |
| `earnings_in_dte` | bool | Calculated | True | Earnings before expiry |
| `expected_move_earnings` | float | Calculated | 0.045 | Implied move |

### 5.2 Corporate Events

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `has_upcoming_dividend` | bool | Calculated | True | Dividend flag |
| `has_stock_split` | bool | External | False | Split flag |
| `has_special_event` | bool | External | False | Other event flag |

### 5.3 News Sentiment (Optional but Valuable)

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `news_sentiment_score` | float | NLP/API | 0.65 | Sentiment (-1 to 1) |
| `news_volume_24h` | int | API | 25 | News activity |
| `social_sentiment` | float | API | 0.58 | Social media sentiment |

---

## 6. Position Monitoring Data (Daily Updates)

While the position is open, capture daily snapshots:

### 6.1 Daily Position Snapshot

| Field | Type | Frequency | Learning Use |
|-------|------|-----------|--------------|
| `date` | date | Daily | Time series |
| `current_premium` | float | Daily | P&L tracking |
| `current_pnl` | float | Daily | Profit path |
| `current_pnl_pct` | float | Daily | Return path |
| `current_delta` | float | Daily | Greek evolution |
| `current_theta` | float | Daily | Decay rate |
| `current_iv` | float | Daily | IV evolution |
| `stock_price` | float | Daily | Underlying path |
| `dte_remaining` | int | Daily | Time decay |
| `vix` | float | Daily | Market context |
| `spy_change` | float | Daily | Market moves |

This creates a time series for each trade showing how conditions evolved.

---

## 7. Exit Data (Capture at Close)

### 7.1 Exit Details

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `exit_date` | datetime | System | 2025-02-10 | Exit timing |
| `exit_premium` | float | Execution | 0.22 | Close price |
| `exit_reason` | str | System | "profit_target" | Exit trigger |
| `exit_type` | str | System | "LIMIT" | Order type used |
| `days_held` | int | Calculated | 8 | Hold duration |
| `exit_delta` | float | IBKR | -0.08 | Delta at exit |
| `exit_iv` | float | IBKR | 0.38 | IV at exit |

### 7.2 Outcome Metrics

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `gross_profit` | float | Calculated | 115.00 | Raw profit |
| `commission` | float | IBKR | 2.60 | Trading costs |
| `net_profit` | float | Calculated | 112.40 | After costs |
| `roi_pct` | float | Calculated | 0.51 | Return on premium |
| `roi_annualized` | float | Calculated | 2.32 | Annualized return |
| `roi_on_margin` | float | Calculated | 0.030 | Return on capital |
| `win` | bool | Calculated | True | Win/loss flag |
| `max_profit_captured` | float | Calculated | 0.51 | % of max profit |

### 7.3 Exit Context

| Field | Type | Source | Example | Learning Use |
|-------|------|--------|---------|--------------|
| `stock_price_at_exit` | float | IBKR | 208.50 | Underlying at exit |
| `stock_change_during_trade` | float | Calculated | 0.020 | Stock moved +2% |
| `vix_at_exit` | float | IBKR | 16.80 | VIX at exit |
| `vix_change_during_trade` | float | Calculated | -0.092 | VIX dropped 9.2% |
| `spy_change_during_trade` | float | Calculated | 0.015 | Market moved +1.5% |
| `iv_crush_pct` | float | Calculated | 0.156 | IV drop during trade |

---

## 8. Derived Features for Learning

These are calculated features that the learning engine will analyze:

### 8.1 Trade Quality Scores

| Feature | Calculation | Learning Use |
|---------|-------------|--------------|
| `premium_per_day` | premium / dte | Daily decay value |
| `theta_premium_ratio` | theta * dte / premium | Theoretical decay vs actual |
| `risk_reward_ratio` | max_profit / max_loss | Risk efficiency |
| `probability_otm` | 1 - abs(delta) | Probability of profit |
| `expected_value` | (prob_win * avg_win) - (prob_loss * avg_loss) | EV calculation |

### 8.2 Market Regime Classification

| Feature | Derivation | Learning Use |
|---------|------------|--------------|
| `market_regime` | VIX + trend + breadth | "bullish", "bearish", "neutral", "volatile" |
| `vol_regime` | VIX percentile | "low", "normal", "elevated", "extreme" |
| `trend_regime` | SPY vs SMAs | "strong_up", "weak_up", "sideways", "weak_down", "strong_down" |

### 8.3 Relative Metrics

| Feature | Calculation | Learning Use |
|---------|-------------|--------------|
| `iv_vs_sector_avg` | stock_iv / sector_avg_iv | Relative IV |
| `volume_vs_avg` | volume / avg_volume | Activity level |
| `premium_vs_historical` | premium / avg_premium_similar_delta | Premium richness |

---

## 9. Database Schema Additions

### 9.1 Entry Snapshot Table

```sql
CREATE TABLE trade_entry_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trades(id),
    
    -- Option data (JSON for flexibility)
    option_data JSON NOT NULL,
    
    -- Underlying data
    underlying_data JSON NOT NULL,
    
    -- Market data
    market_data JSON NOT NULL,
    
    -- Fundamental data
    fundamental_data JSON,
    
    -- Event data
    event_data JSON,
    
    -- Derived features
    derived_features JSON,
    
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- Indexes for common queries
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

CREATE INDEX idx_entry_snapshot_trade ON trade_entry_snapshots(trade_id);
```

### 9.2 Position Snapshots Table

```sql
CREATE TABLE position_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trades(id),
    snapshot_date DATE NOT NULL,
    
    -- Position state
    current_premium REAL,
    current_pnl REAL,
    current_pnl_pct REAL,
    
    -- Greeks
    delta REAL,
    theta REAL,
    gamma REAL,
    vega REAL,
    
    -- Context
    stock_price REAL,
    iv REAL,
    dte_remaining INTEGER,
    vix REAL,
    spy_price REAL,
    
    -- Full snapshot (JSON for extra fields)
    full_snapshot JSON,
    
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    UNIQUE(trade_id, snapshot_date)
);

CREATE INDEX idx_position_snapshot_trade ON position_snapshots(trade_id);
CREATE INDEX idx_position_snapshot_date ON position_snapshots(snapshot_date);
```

### 9.3 Exit Snapshot Table

```sql
CREATE TABLE trade_exit_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trades(id),
    
    -- Exit details
    exit_date TIMESTAMP,
    exit_premium REAL,
    exit_reason TEXT,
    exit_type TEXT,
    
    -- Outcome
    gross_profit REAL,
    net_profit REAL,
    roi_pct REAL,
    roi_on_margin REAL,
    win BOOLEAN,
    days_held INTEGER,
    
    -- Exit context
    exit_context JSON,
    
    -- Full snapshot
    full_snapshot JSON,
    
    captured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    FOREIGN KEY (trade_id) REFERENCES trades(id)
);

CREATE INDEX idx_exit_snapshot_trade ON trade_exit_snapshots(trade_id);
```

---

## 10. Data Sources Summary

| Data Category | Primary Source | Backup Source | Refresh Rate |
|---------------|----------------|---------------|--------------|
| Option pricing | IBKR API | Barchart | Real-time at entry |
| Greeks | IBKR API | Calculate locally | Real-time at entry |
| IV | IBKR API | Barchart | Real-time |
| Stock price | IBKR API | - | Real-time |
| Technical indicators | Calculate locally | - | Daily |
| Market indices | IBKR API | Yahoo Finance | Real-time |
| VIX | IBKR API | CBOE | Real-time |
| Fundamentals | Yahoo Finance / FMP | Barchart | Weekly |
| Earnings dates | Yahoo Finance / Earnings Whispers | - | Daily |
| Sector data | Calculate from ETFs | - | Daily |

---

## 11. Implementation Priority

### Phase 1: Critical (Implement Immediately)

These fields have the highest predictive value:

| Field | Reason |
|-------|--------|
| `delta` | Primary OTM measure |
| `dte` | Time decay driver |
| `iv` | Premium richness |
| `iv_rank` | IV context |
| `vix` | Market fear |
| `trend_direction` | Stock health |
| `days_to_earnings` | Event risk |
| `margin_efficiency_pct` | Capital efficiency |

### Phase 2: High Value (Next Sprint)

| Field | Reason |
|-------|--------|
| Technical indicators (RSI, MACD, ADX) | Momentum signals |
| Support/resistance | Strike placement |
| Sector performance | Rotation patterns |
| Market breadth | Market health |
| Bollinger position | Mean reversion |

### Phase 3: Enhancement (Future)

| Field | Reason |
|-------|--------|
| Fundamentals | Long-term patterns |
| News sentiment | Catalyst detection |
| Social sentiment | Momentum signals |
| Inter-market correlations | Macro patterns |

---

## 12. Learning Engine Queries

The learning engine will run queries like:

```sql
-- Find patterns by delta bucket
SELECT 
    CASE 
        WHEN delta > -0.15 THEN '0-15%'
        WHEN delta > -0.20 THEN '15-20%'
        WHEN delta > -0.25 THEN '20-25%'
        ELSE '25%+'
    END as delta_bucket,
    COUNT(*) as trades,
    AVG(CASE WHEN win THEN 1.0 ELSE 0.0 END) as win_rate,
    AVG(roi_pct) as avg_roi
FROM trades t
JOIN trade_entry_snapshots e ON t.id = e.trade_id
JOIN trade_exit_snapshots x ON t.id = x.trade_id
GROUP BY delta_bucket
HAVING COUNT(*) >= 30;

-- Find patterns by VIX regime
SELECT 
    CASE 
        WHEN vix < 15 THEN 'low'
        WHEN vix < 20 THEN 'normal'
        WHEN vix < 25 THEN 'elevated'
        ELSE 'high'
    END as vix_regime,
    COUNT(*) as trades,
    AVG(CASE WHEN win THEN 1.0 ELSE 0.0 END) as win_rate,
    AVG(roi_pct) as avg_roi
FROM trades t
JOIN trade_entry_snapshots e ON t.id = e.trade_id
JOIN trade_exit_snapshots x ON t.id = x.trade_id
GROUP BY vix_regime;

-- Earnings proximity analysis
SELECT 
    CASE 
        WHEN days_to_earnings <= 0 THEN 'post_earnings'
        WHEN days_to_earnings <= 7 THEN 'earnings_week'
        WHEN days_to_earnings <= 14 THEN '1-2_weeks'
        ELSE 'far_from_earnings'
    END as earnings_proximity,
    COUNT(*) as trades,
    AVG(CASE WHEN win THEN 1.0 ELSE 0.0 END) as win_rate,
    AVG(iv_crush_pct) as avg_iv_crush
FROM trades t
JOIN trade_entry_snapshots e ON t.id = e.trade_id
JOIN trade_exit_snapshots x ON t.id = x.trade_id
GROUP BY earnings_proximity;
```

---

## 13. Field Count Summary

| Category | Field Count |
|----------|-------------|
| Option Data | 17 |
| Underlying Data | 35 |
| Market Data | 18 |
| Fundamental Data | 15 |
| Event Data | 8 |
| Position Monitoring | 12/day |
| Exit Data | 18 |
| Derived Features | 12 |
| **Total Entry Fields** | **~95** |
| **Total with Monitoring** | **~125+** |

---

## Document Control

| Version | Date | Author | Changes |
|---------|------|--------|---------|
| 1.0 | 2026-01-27 | Claude | Initial specification |

---

**End of Data Collection Specification**
