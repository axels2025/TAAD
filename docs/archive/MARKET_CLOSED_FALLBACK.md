# Market Closed Fallback Pricing

**Date:** 2026-01-27
**Status:** ✅ Implemented

---

## Problem: No Live Quotes When Market Closed

When running enrichment outside market hours (after 4:00 PM ET), IBKR returns:
```
bid=-1.0, ask=-1.0
```

This means "no data available" because options don't trade after hours.

**Your test was at 11:26 PM** - markets close at 4:00 PM ET, so no live quotes were available.

---

## Solution: Fallback Pricing

Added intelligent fallback when live quotes unavailable:

### Fallback Priority:

**1. Live Bid/Ask (Best - during market hours)**
```python
bid = ticker.bid  # Live bid from market
ask = ticker.ask  # Live ask from market
premium = (bid + ask) / 2
```

**2. Close Price (Good - market closed)**
```python
# Last traded price before market closed
bid = ask = ticker.close
premium = ticker.close
```

**3. Model Price (Acceptable - theoretical)**
```python
# IBKR's theoretical option price
bid = ask = ticker.modelGreeks.optPrice
premium = ticker.modelGreeks.optPrice
```

### Example from Your Output:

**AG option:**
```
close=0.31
```
With fallback, will now use: `bid=0.31, ask=0.31, premium=0.31`

**AMZN option:**
```
modelGreeks=OptionComputation(optPrice=0.2103435969293974)
```
With fallback, will use: `premium=0.21`

---

## Changes Made

### 1. Suppress Informational Messages (src/tools/ibkr_client.py)

**Filtered out:**
- Error 2104: Market data farm connection is OK
- Error 2106: HMDS data farm connection is OK
- Error 2107: HMDS data farm connection inactive
- Error 2119: Market data farm is connecting
- Error 2158: Sec-def data farm connection is OK

These are status messages, not actual errors.

### 2. Add Fallback Pricing (src/tools/ibkr_validator.py)

**Logic:**
```python
# Try live bid/ask
bid = ticker.bid if ticker.bid > 0 else None
ask = ticker.ask if ticker.ask > 0 else None

# Fallback 1: Close price
if not bid or not ask:
    if ticker.close and ticker.close > 0:
        bid = ask = ticker.close
        logger.info("Using close price (market closed)")

# Fallback 2: Model price
if not bid or not ask:
    if ticker.modelGreeks and ticker.modelGreeks.optPrice > 0:
        bid = ask = ticker.modelGreeks.optPrice
        logger.info("Using model price (market closed)")

# Still no data? Fail
if not bid or not ask:
    logger.warning("No pricing data available")
    return None
```

---

## Expected Output (Market Closed)

```bash
$ python -m src.cli.main trade --manual-only

✓ Connected to IBKR

Mode: Manual Trades Only
Will enrich manual trades with live IBKR data

Step 1: Gathering opportunities...

• Found 7 pending manual trades in database
  Enriching with live IBKR data...
2026-01-27 23:30:00 | INFO | Using close price $0.31 for IONQ $42.5 (market closed)
  ✓ Enriched IONQ $42.5: premium=$0.31, OTM=10.6%
2026-01-27 23:30:05 | INFO | Using close price $0.31 for AG $23.0 (market closed)
  ✓ Enriched AG $23.0: premium=$0.31, OTM=8.2%
2026-01-27 23:30:10 | INFO | Using model price $0.21 for AMZN $185.0 (market closed)
  ✓ Enriched AMZN $185.0: premium=$0.21, OTM=15.3%
...
  ✓ Enriched 7 opportunities with fallback pricing

✓ Total opportunities collected: 7
```

**Clean output:**
- ✅ No connection status messages
- ✅ Clear indication of fallback pricing used
- ✅ All trades enriched with available data
- ✅ Premium values filled in (not $0.00)

---

## Important Notes

### For Real Trading (Production):

**Always use live quotes during market hours:**
- Market hours: 9:30 AM - 4:00 PM ET (Monday-Friday)
- Live bid/ask spreads are critical for execution
- Close prices may be stale (from previous day/week)
- Model prices are theoretical (may not match reality)

### For Testing (Development):

**Fallback pricing is acceptable:**
- Test workflow anytime (no need to wait for market hours)
- Close/model prices good enough for validation
- Real execution still requires live quotes

---

## What This Fixes

### Before:
```
bid=-1.0, ask=-1.0
→ Enrichment failed: No bid/ask data
→ All trades use stored data with $0.00 premiums
```

### After:
```
bid=0.31, ask=0.31 (from close price)
→ Enrichment successful with fallback pricing
→ Trades have realistic premium estimates
→ Can test workflow anytime
```

---

## Test Again

Run the command now (even though market is closed):

```bash
python -m src.cli.main trade --manual-only
```

**You should see:**
- ✅ No connection status spam
- ✅ Trades enriched with close/model prices
- ✅ Real premium values (not $0.00)
- ✅ INFO messages showing fallback pricing used

---

## Files Modified

1. **src/tools/ibkr_client.py**
   - Added informational error codes to filter (2104, 2106, 2107, 2119, 2158)
   - Lines: ~10 lines changed

2. **src/tools/ibkr_validator.py**
   - Added fallback pricing logic (close price → model price → fail)
   - Added INFO logging when fallback used
   - Lines: ~15 lines changed

---

**Document Version:** 1.0
**Last Updated:** 2026-01-27
**Status:** Complete - fallback pricing implemented
**Impact:** Enrichment works 24/7 (not just during market hours)
