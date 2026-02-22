# Parameter System Explained

**Date:** 2026-01-26
**Status:** Two parameter systems in use (by design)

---

## Overview

The system uses **two different parameter configurations** depending on the workflow:

1. **Barchart + IBKR Config** - For automated scanning (`scan` command)
2. **BaselineStrategy Config** - For validation and position management (`trade` command)

This is intentional! Here's why:

---

## 1. Barchart + IBKR Parameters (NEW - For Scanning)

**File:** `src/config/naked_put_options_config.py`
**Environment Variables:** `BARCHART_*` in `.env`
**Used By:** `scan` command

### Purpose
These parameters control the **automated market scanning** workflow:
- Barchart API screens entire US options market
- IBKR validates top candidates with real-time data

### Configuration (.env)
```bash
# Barchart Scan Parameters
BARCHART_API_KEY=your_key_here
BARCHART_DELTA_MIN=-0.50
BARCHART_DELTA_MAX=-0.10
BARCHART_DTE_MIN=5
BARCHART_DTE_MAX=45
BARCHART_BID_PRICE_MIN=0.20
BARCHART_OPTION_TYPE=put
BARCHART_MAX_RESULTS=50

# IBKR Validation Parameters
MAX_SPREAD_PCT=0.10           # 10% max bid-ask spread
MIN_MARGIN_EFFICIENCY=0.015   # 1.5% min return on margin
REQUIRE_UPTREND=true
```

### Commands That Use This
```bash
# These use Barchart config
python -m src.cli.main scan
python -m src.cli.main trade  # (when scanning for opportunities)
```

---

## 2. BaselineStrategy Parameters (LEGACY - For Validation)

**File:** `src/config/baseline_strategy.py`
**Environment Variables:** Direct fields in `.env`
**Used By:** `trade` command validation, position management

### Purpose
These parameters control **position management and validation**:
- Position sizing
- Exit rules (profit target, stop loss)
- Risk limits
- **DEPRECATED:** Old scanning parameters (kept for backward compatibility)

### Configuration (.env)
```bash
# Position Management (ACTIVE - Used)
POSITION_SIZE=5                  # Number of contracts per trade
PROFIT_TARGET=0.50               # 50% profit target
STOP_LOSS=-2.00                  # -200% stop loss
MAX_POSITION_SIZE=5000           # Max $ per position
MAX_RISK_PER_TRADE=0.02          # 2% max risk per trade

# Legacy Scan Parameters (DEPRECATED - Not used for scanning)
# These are HARDCODED defaults, only used for validation
OTM_MIN=0.10                     # DEPRECATED
OTM_MAX=0.30                     # DEPRECATED
PREMIUM_MIN=0.20                 # DEPRECATED
PREMIUM_MAX=2.00                 # DEPRECATED
DTE_MIN=0                        # DEPRECATED
DTE_MAX=30                       # DEPRECATED
```

### Hardcoded Defaults (If Not in .env)
```python
otm_range: (0.10, 0.30)          # 10-30% OTM
premium_range: (0.20, 2.00)      # $0.20-$2.00
dte_range: (0, 30)               # 0-30 days
trend_filter: "uptrend"
```

**Note:** These legacy parameters are NOT used for scanning. They're only used by the `validate_opportunity()` method for basic validation.

### Commands That Use This
```bash
# These use BaselineStrategy for validation
python -m src.cli.main trade --manual-only
python -m src.cli.main execute
```

---

## Why Two Parameter Systems?

### Historical Context
1. **Originally:** Trade command used BaselineStrategy for everything (scanning + validation)
2. **Barchart Migration:** Added new Barchart-based scanning (10-20x faster)
3. **Now:** Barchart for scanning, BaselineStrategy for validation/exits

### Design Rationale

**Barchart Parameters = Market Scanning**
- Fast, efficient, entire market coverage
- Tuned for finding opportunities at scale
- Configurable via Barchart API capabilities
- Updated frequently as market changes

**BaselineStrategy Parameters = Risk Management**
- Position sizing rules
- Exit criteria (profit target, stop loss)
- Risk limits enforcement
- Stable, rarely changed

---

## Which Parameters Apply When?

### Scenario 1: Manual Trade Entry (--manual-only)
```bash
python -m src.cli.main trade --manual-only
```

**Parameters Used:**
- ✅ BaselineStrategy validation (profit target, stop loss, position size)
- ❌ NO scanning parameters used (you already entered the trade)

**Display:**
```
Mode: Manual Trades Only
Will execute manual trades from database without scanning

Validation:
  • Strategy validation: 50% profit target
  • Stop loss: 200%
  • Max position size: $5,000
```

---

### Scenario 2: Automated Scan Only
```bash
python -m src.cli.main scan
```

**Parameters Used:**
- ✅ Barchart + IBKR config (delta, DTE, bid price, spread, margin efficiency)
- ❌ BaselineStrategy NOT used (just finding opportunities)

**Display:**
```
Scan Parameters (from Barchart + IBKR config)
Delta Range:          -0.50 to -0.10
DTE Range:            5 - 45 days
Min Bid Price:        $0.20
Max Spread:           10%
Min Margin Efficiency: 1.5%
Require Uptrend:      Yes
```

---

### Scenario 3: Hybrid Mode (Manual + Scan)
```bash
python -m src.cli.main trade
```

**Parameters Used:**
- ✅ Barchart + IBKR config (for scanning additional opportunities)
- ✅ BaselineStrategy validation (for all opportunities before execution)

**Display:**
```
Mode: Hybrid (Manual + Barchart Scan)
Will import manual trades and scan for additional opportunities

Validation:
  • Strategy validation: 50% profit target
  • Stop loss: 200%
  • Max position size: $5,000
```

---

## Common Confusion

### "The parameters are hardcoded!"

**Answer:** Sort of, but it's okay!

The **BaselineStrategy** defaults (0.10-0.30 OTM, $0.20-$2.00 premium, etc.) are hardcoded **fallbacks**. They're only used if you don't set them in `.env`.

**But here's the key:** These parameters are **DEPRECATED for scanning**. They're only used for basic validation in `validate_opportunity()`, which is a simple sanity check.

**For actual scanning:** Use the Barchart parameters in `.env` (BARCHART_DELTA_MIN, etc.).

### "Why show 'from .env file' when they're defaults?"

**Fixed!** The display now shows:
- Manual-only mode: Shows validation parameters only
- Scan mode: Shows Barchart parameters
- No more misleading "from .env file" label

---

## What Should You Configure?

### Essential (For Scanning)
```bash
# .env - Barchart parameters
BARCHART_API_KEY=your_key_here
BARCHART_DELTA_MIN=-0.50
BARCHART_DELTA_MAX=-0.10
BARCHART_DTE_MIN=5
BARCHART_DTE_MAX=45
BARCHART_BID_PRICE_MIN=0.20

# IBKR validation
MAX_SPREAD_PCT=0.10
MIN_MARGIN_EFFICIENCY=0.015
REQUIRE_UPTREND=true
```

### Important (For Risk Management)
```bash
# .env - Position management
POSITION_SIZE=5
PROFIT_TARGET=0.50
STOP_LOSS=-2.00
MAX_POSITION_SIZE=5000
MAX_RISK_PER_TRADE=0.02
```

### Optional (Legacy - Can Ignore)
```bash
# .env - Legacy parameters (not needed, have defaults)
# OTM_MIN=0.10
# OTM_MAX=0.30
# PREMIUM_MIN=0.20
# PREMIUM_MAX=2.00
```

---

## Summary

**For Manual Trading:**
- Enter trades via web interface
- Run `trade --manual-only`
- System validates with BaselineStrategy (profit target, stop loss, size)
- **No scanning parameters involved**

**For Automated Scanning:**
- Configure Barchart parameters in `.env`
- Run `scan` to find opportunities
- System uses Barchart + IBKR config
- **BaselineStrategy not involved until execution**

**For Hybrid:**
- Manual trades + automated scanning
- Both parameter sets used appropriately
- Manual trades get high confidence (0.80)
- Scanned trades get lower confidence (0.75)

---

## Configuration Files Reference

| File | Purpose | Used By |
|------|---------|---------|
| `src/config/naked_put_options_config.py` | Barchart + IBKR scanning | `scan`, `trade` (scanning) |
| `src/config/baseline_strategy.py` | Position management & validation | `trade` (validation), `execute`, `monitor` |
| `.env` | Environment variables | Both (different variables) |

---

## Recommendations

1. **For manual trading:** Ignore the legacy BaselineStrategy scan parameters
2. **For automated scanning:** Configure Barchart parameters in `.env`
3. **For position management:** Set profit target, stop loss, position size
4. **Don't worry about:** The hardcoded defaults - they're fine for validation

The system is designed to work well with sensible defaults. Focus on configuring the Barchart parameters for scanning!
