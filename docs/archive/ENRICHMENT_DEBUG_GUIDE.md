# Enrichment Debugging - Testing Guide

**Date:** 2026-01-27
**Status:** 🔧 Debugging in progress

---

## Changes Made

### 1. Added Exception Handling in Trade Command
- Added try/except around enrichment calls to catch and display any exceptions
- Will show full traceback if enrichment throws an error

### 2. Enhanced Logging in Enrichment Method
- Changed `logger.debug()` to `logger.info()` so messages appear in console
- Added detailed step-by-step logging:
  - Step 1: Stock price lookup
  - Step 2: Option contract qualification
  - Step 3: Bid/ask quotes retrieval
- Each failure point now logs which step failed and why

### 3. Enabled Logging in Trade Command
- Added `setup_logging()` call at start of trade command
- Logging warnings and info messages will now appear in console

---

## Testing Instructions

Run the trade command again with `--manual-only`:

```bash
python -m src.cli.main trade --manual-only
```

### Expected Output

You should now see **detailed logging** showing why enrichment is failing:

#### Example 1: Stock Price Lookup Failure
```
• Found 7 pending manual trades in database
  Enriching with live IBKR data...
2026-01-27 10:30:15 | INFO     | ibkr_validator:enrich_manual_opportunity:495 - Enriching manual opportunity: IONQ $42.5 PUT exp 2026-01-30
2026-01-27 10:30:16 | WARNING  | ibkr_validator:enrich_manual_opportunity:500 - Could not get stock price for IONQ
2026-01-27 10:30:16 | WARNING  | ibkr_validator:enrich_manual_opportunity:501 -   → Enrichment failed at Step 1: Stock price lookup
  ⚠ Could not enrich IONQ $42.5 - using stored data
```

#### Example 2: Option Contract Qualification Failure
```
2026-01-27 10:30:15 | INFO     | ibkr_validator:enrich_manual_opportunity:495 - Enriching manual opportunity: IONQ $42.5 PUT exp 2026-01-30
2026-01-27 10:30:17 | WARNING  | ibkr_validator:enrich_manual_opportunity:519 - Could not qualify option contract: IONQ $42.5 PUT
2026-01-27 10:30:17 | WARNING  | ibkr_validator:enrich_manual_opportunity:520 -   → Enrichment failed at Step 2: Option contract qualification
2026-01-27 10:30:17 | WARNING  | ibkr_validator:enrich_manual_opportunity:521 -   → Contract details: symbol=IONQ, exp=20260130, strike=42.5, right=P
  ⚠ Could not enrich IONQ $42.5 - using stored data
```

#### Example 3: Bid/Ask Data Unavailable
```
2026-01-27 10:30:15 | INFO     | ibkr_validator:enrich_manual_opportunity:495 - Enriching manual opportunity: IONQ $42.5 PUT exp 2026-01-30
2026-01-27 10:30:19 | WARNING  | ibkr_validator:enrich_manual_opportunity:535 - No bid/ask data for IONQ $42.5
2026-01-27 10:30:19 | WARNING  | ibkr_validator:enrich_manual_opportunity:536 -   → Enrichment failed at Step 3: Bid/ask quotes (bid=None, ask=None)
2026-01-27 10:30:19 | WARNING  | ibkr_validator:enrich_manual_opportunity:537 -   → Ticker data: <Ticker object>
  ⚠ Could not enrich IONQ $42.5 - using stored data
```

#### Example 4: Exception During Enrichment
```
2026-01-27 10:30:15 | INFO     | ibkr_validator:enrich_manual_opportunity:495 - Enriching manual opportunity: IONQ $42.5 PUT exp 2026-01-30
  ✗ Error enriching IONQ $42.5: [error message]
Traceback (most recent call last):
  ...
  [Full stack trace]
```

---

## What This Tells Us

Based on which step fails, we'll know:

1. **Step 1 Failure (Stock price):**
   - IBKR can't find the stock symbol
   - Symbol might be misspelled
   - Stock might not be available in IBKR
   - Market might be closed

2. **Step 2 Failure (Contract qualification):**
   - Option contract doesn't exist with those parameters
   - Expiration date might be wrong format or past
   - Strike price might not exist for that expiration
   - Exchange might not have that contract

3. **Step 3 Failure (Bid/ask quotes):**
   - Option exists but has no current quotes
   - Market is closed
   - Option is illiquid (no bids/offers)
   - IBKR market data subscription issue

---

## Common Issues & Solutions

### Issue: Market is Closed
**Symptoms:** All enrichments fail at Step 3 (no bid/ask)
**Solution:** Run during market hours (9:30 AM - 4:00 PM ET)

### Issue: Wrong Expiration Format
**Symptoms:** Fails at Step 2 (contract qualification)
**Solution:** Check expiration dates in database - should be YYYY-MM-DD format

### Issue: Symbol Not Found
**Symptoms:** Fails at Step 1 (stock price)
**Solution:** Verify symbol is correct and available in IBKR

### Issue: IBKR Market Data Subscription
**Symptoms:** Fails at Step 3 even during market hours
**Solution:** Check if you have appropriate market data subscriptions in IBKR

---

## Next Steps

After running the test:

1. **Share the output** showing which step fails and the detailed error messages
2. **Check expiration dates** in your manual trades - are they in the future and formatted correctly?
3. **Verify symbols** - are they spelled correctly and available in IBKR?
4. **Check market hours** - are you testing during trading hours?

---

## Upcoming Architecture Fix

The separate issue about config architecture (Barchart search parameters vs validation criteria) will be addressed next. The validation settings (spread %, margin efficiency, uptrend requirement) should be risk criteria that apply to ALL trades, not bundled with Barchart search parameters.

**Proposed Solution:**
- Move `IBKRValidationSettings` out of `NakedPutScreenerConfig`
- Make validation settings part of `BaselineStrategy` config
- Apply validation criteria to all trades (manual and Barchart)
- Only require Barchart API key when actually scanning Barchart

---

**Document Version:** 1.0
**Last Updated:** 2026-01-27
**Status:** Debug changes deployed, waiting for test results
