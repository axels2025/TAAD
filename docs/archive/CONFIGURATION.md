# Trading System Configuration Guide

## Overview

All trading strategy parameters are configured through the **`.env` file** in the project root directory. This allows easy modification without changing code, and supports **unbounded parameters** for maximum flexibility.

## Quick Start

### 1. Edit Your Configuration

```bash
nano .env
```

### 2. Set Your Parameters

```bash
# Example: Look for premiums >= $0.50, no upper limit
PREMIUM_MIN=0.50
PREMIUM_MAX=        # Empty = unlimited!
```

### 3. Run the Scanner or Trade

```bash
python -m src.cli.main scan
python -m src.cli.main trade
```

## 🎯 New Feature: Unbounded Parameters

Leave any `MAX` parameter **empty** to make it unbounded (no upper limit).

### Examples

**Accept ANY premium above $0.50:**
```bash
PREMIUM_MIN=0.50
PREMIUM_MAX=        # Empty means unlimited
```

**Accept ANY OTM percentage above 10%:**
```bash
OTM_MIN=0.10
OTM_MAX=            # Empty means unlimited
```

**Accept ANY expiration beyond 5 days:**
```bash
DTE_MIN=5
DTE_MAX=            # Empty means unlimited
```

## Configuration Parameters

### Out-of-the-Money (OTM) Range

```bash
OTM_MIN=0.15        # Minimum 15% OTM (required)
OTM_MAX=0.30        # Maximum 30% OTM (optional)
# OTM_MAX=          # Leave empty for unlimited
```

**What it means:**
- Stock at $100, `OTM_MIN=0.15` → strike at $85 or lower
- `OTM_MAX=` (empty) → accepts $85, $75, $50, etc.

### Premium Range

```bash
PREMIUM_MIN=0.30    # Minimum $0.30 per share (required)
PREMIUM_MAX=2.00    # Maximum $2.00 per share (optional)
# PREMIUM_MAX=      # Leave empty for unlimited
```

### Days to Expiration (DTE)

```bash
DTE_MIN=5           # Minimum 5 days (required)
DTE_MAX=21          # Maximum 21 days (optional)
# DTE_MAX=          # Leave empty for unlimited
```

### Stock Price Range

```bash
MIN_STOCK_PRICE=20.0     # Minimum $20 per share
MAX_STOCK_PRICE=500.0    # Maximum $500 per share
# MAX_STOCK_PRICE=       # Leave empty for unlimited
```

### Trend Filter

```bash
TREND_FILTER=uptrend
```

**Options:**
- `uptrend` - Only stocks where price > SMA20 > SMA50
- `downtrend` - Only stocks in downtrend
- `sideways` - Only consolidating stocks
- `any` - No trend filter

### Position Sizing

```bash
POSITION_SIZE=5         # Contracts per trade
MAX_POSITIONS=10        # Maximum concurrent positions
```

### Exit Rules

```bash
PROFIT_TARGET=0.50      # Exit at 50% of max profit
STOP_LOSS=-2.00         # Stop loss at -200% of premium
TIME_EXIT_DTE=3         # Exit 3 days before expiration
```

## Common Configuration Scenarios

### Scenario 1: High Premium Hunting (Unbounded)

```bash
# Find any premium above $1.00, regardless of how high
PREMIUM_MIN=1.00
PREMIUM_MAX=

OTM_MIN=0.15
OTM_MAX=0.30
DTE_MIN=5
DTE_MAX=21
```

**Result:** Finds opportunities with $1.00, $2.50, $5.00+ premiums

### Scenario 2: Long-Dated Options (Unbounded DTE)

```bash
# Find any option 30+ days out, including LEAPs
PREMIUM_MIN=0.50
PREMIUM_MAX=3.00
OTM_MIN=0.15
OTM_MAX=0.25
DTE_MIN=30
DTE_MAX=            # Unlimited - finds 30, 60, 90, 180+ day options
```

### Scenario 3: Deep OTM Safety (Unbounded OTM)

```bash
# At least 30% OTM, but 50%+ is fine too
PREMIUM_MIN=0.30
PREMIUM_MAX=1.00
OTM_MIN=0.30
OTM_MAX=            # Unlimited - accepts 30%, 40%, 50%+ OTM
DTE_MIN=7
DTE_MAX=21
```

### Scenario 4: Conservative Bounded Strategy

```bash
# Tight, controlled parameters
PREMIUM_MIN=0.30
PREMIUM_MAX=0.50
OTM_MIN=0.15
OTM_MAX=0.20
DTE_MIN=7
DTE_MAX=14
```

### Scenario 5: Your Current Settings (from scan)

```bash
# Settings that found 20 opportunities
PREMIUM_MIN=0.30
PREMIUM_MAX=2.00
OTM_MIN=0.15
OTM_MAX=0.30
DTE_MIN=5
DTE_MAX=21
TREND_FILTER=uptrend
```

## How to Apply Changes

1. **Edit .env:**
   ```bash
   nano .env
   ```

2. **Save and exit** (Ctrl+X, Y, Enter)

3. **Run commands:**
   ```bash
   # Test with scan
   python -m src.cli.main scan

   # Use in trading
   python -m src.cli.main trade
   ```

**No restart needed!** Configuration loads on each command.

## Testing Your Configuration

```bash
# Quick validation
python -c "
from src.config.baseline_strategy import get_baseline_strategy
s = get_baseline_strategy()
print(f'OTM: {s.otm_range[0]:.0%} - {s.otm_range[1]:.0% if s.otm_range[1] else \"unlimited\"}')
print(f'Premium: \${s.premium_range[0]:.2f} - \${s.premium_range[1]:.2f if s.premium_range[1] else \"unlimited\"}')
print(f'DTE: {s.dte_range[0]} - {s.dte_range[1] if s.dte_range[1] else \"unlimited\"} days')
"
```

## Troubleshooting

### Problem: "No opportunities found" (trade command)

**Cause:** Parameters in `.env` are too restrictive

**Solution:** Widen ranges or make some unbounded
```bash
# Before (too narrow)
PREMIUM_MAX=0.50
OTM_MAX=0.20
DTE_MAX=14

# After (more flexible)
PREMIUM_MAX=2.00
OTM_MAX=0.30
DTE_MAX=21

# Or unbounded
PREMIUM_MAX=
OTM_MAX=
DTE_MAX=
```

### Problem: Too many low-quality opportunities

**Solution:** Raise minimum requirements
```bash
# Before
OTM_MIN=0.10
PREMIUM_MIN=0.20

# After (higher quality)
OTM_MIN=0.15
PREMIUM_MIN=0.50
```

### Problem: Scan finds opportunities but trade doesn't

**Cause:** `scan` command uses CLI arguments, `trade` command uses `.env`

**Solution:** Update `.env` to match your successful scan parameters

**Example:**
```bash
# Your successful scan command
python -m src.cli.main scan --max-premium 2.00 --max-otm 0.30

# Update .env to match
PREMIUM_MAX=2.00
OTM_MAX=0.30
```

## Advanced Tips

### Gradual Evolution Strategy

Start conservative, gradually loosen:

**Week 1:**
```bash
PREMIUM_MIN=0.30
PREMIUM_MAX=0.50
OTM_MIN=0.15
OTM_MAX=0.20
```

**Week 4:**
```bash
PREMIUM_MIN=0.30
PREMIUM_MAX=1.00
OTM_MIN=0.15
OTM_MAX=0.30
```

**Week 8:**
```bash
PREMIUM_MIN=0.50
PREMIUM_MAX=        # Unbounded!
OTM_MIN=0.15
OTM_MAX=0.35
```

### Mix Bounded and Unbounded

```bash
# Tight OTM control, but accept any premium
OTM_MIN=0.15
OTM_MAX=0.25        # Bounded
PREMIUM_MIN=0.50
PREMIUM_MAX=        # Unbounded
```

## Files Reference

```
trading_agent/
├── .env                    ← EDIT THIS! (your configuration)
├── .env.example            ← Template with all options
├── CONFIGURATION.md        ← This guide
├── src/
│   └── config/
│       └── baseline_strategy.py  (Loads from .env automatically)
└── config/
    └── trading_config.py   ← DEPRECATED (old system)
```

## Migration from Old System

If you previously used `config/trading_config.py`, those values are **ignored now**.

All configuration comes from `.env`.

---

**Last Updated:** 2026-01-22
**New Feature:** Unbounded max parameters support
