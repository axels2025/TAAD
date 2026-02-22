# Troubleshooting Trade Validation

**Date:** 2026-01-26

---

## Understanding Rejection Messages

When running `trade --manual-only`, the system now shows **exactly why** each trade was rejected:

```
Step 2: Evaluating opportunities...

✗ 1 opportunities rejected:

  • SLV $80.00 - OTM 0.0% below minimum 10.0%
```

This tells you:
- **Symbol & Strike:** SLV $80.00
- **Problem:** OTM percentage is 0.0%
- **Requirement:** Minimum 10.0% OTM

---

## Common Rejection Reasons

### 1. OTM Percentage Issues

**Rejection Message:**
```
OTM 0.0% below minimum 10.0%
OTM 5.5% below minimum 10.0%
OTM 35.0% above maximum 30.0%
```

**Cause:**
- OTM field was left blank (defaults to 0.0%)
- OTM too low (< 10%)
- OTM too high (> 30%)

**Solution:**

**Option A: Skip Validation (Recommended for Manual Trades)**
```bash
# Trust your own research
python -m src.cli.main trade --manual-only --no-validate-manual
```

**Option B: Fix the OTM Field**
1. Calculate OTM percentage:
   ```
   For PUTS:
   OTM % = (Stock Price - Strike) / Stock Price

   Example - SLV at $28.00, strike $25.00:
   OTM % = (28.00 - 25.00) / 28.00 = 0.107 = 10.7%
   ```

2. Re-enter trade via web interface with correct OTM %
3. Enter as decimal: 10.7% = 0.107

---

### 2. Premium Issues

**Rejection Message:**
```
Premium $0.15 below minimum $0.20
Premium $2.50 above maximum $2.00
```

**Cause:** Premium outside $0.20 - $2.00 range

**Solution:**

**Option A: Skip Validation**
```bash
python -m src.cli.main trade --manual-only --no-validate-manual
```

**Option B: Adjust Range in .env**
```bash
# Edit .env
PREMIUM_MIN=0.10   # Lower minimum
PREMIUM_MAX=5.00   # Raise maximum
```

---

### 3. DTE (Days to Expiration) Issues

**Rejection Message:**
```
DTE 35 above maximum 30
DTE -1 below minimum 0
```

**Cause:** DTE outside 0-30 day range

**Solution:**

**Option A: Skip Validation**
```bash
python -m src.cli.main trade --manual-only --no-validate-manual
```

**Option B: Adjust Range in .env**
```bash
# Edit .env
DTE_MIN=0
DTE_MAX=45   # Allow longer expiration
```

---

### 4. Trend Issues

**Rejection Message:**
```
Trend 'unknown' does not match required 'uptrend'
Trend 'downtrend' does not match required 'uptrend'
```

**Cause:** Trend field doesn't match requirement

**Solution:**

**Option A: Skip Validation**
```bash
python -m src.cli.main trade --manual-only --no-validate-manual
```

**Option B: Set Trend in Web Form**
- Go back to web interface
- Re-enter trade
- Select correct trend from dropdown

**Option C: Disable Trend Filter in .env**
```bash
# Edit .env
TREND_FILTER=any   # Accept any trend
```

---

## Default Validation Criteria

When you use `trade --manual-only` (with validation enabled), these are the default requirements:

```
OTM Range:     10% - 30%
Premium Range: $0.20 - $2.00
DTE Range:     0 - 30 days
Trend Filter:  uptrend
```

**Note:** These are LEGACY parameters, mainly used for backward compatibility. They're not used by the Barchart scan workflow.

---

## Solutions Summary

### Quick Fix: Skip Validation (Recommended)

**For manual trades, you've already done your research, so skip the automated validation:**

```bash
# Dry run
python -m src.cli.main trade --manual-only --no-validate-manual --dry-run

# Execute for real
python -m src.cli.main trade --manual-only --no-validate-manual
```

This trusts your analysis and executes your manually entered trades without checking them against legacy criteria.

---

### Proper Fix: Complete All Fields

When entering trades via web interface, fill in these **optional but important** fields:

**Critical for Validation:**
- ✅ **OTM %** - Calculate: (Stock Price - Strike) / Stock Price for puts
- ✅ **Premium** - Use bid price from IBKR
- ✅ **Trend** - Select from dropdown (uptrend/downtrend/sideways)

**Optional but Helpful:**
- Delta, Stock Price, Volume, Open Interest, IV, Notes

---

### Configuration Fix: Adjust Parameters

If you want to use validation but need different criteria:

**Edit .env:**
```bash
# Position Management (Active)
POSITION_SIZE=5
PROFIT_TARGET=0.50
STOP_LOSS=-2.00

# Legacy Validation Criteria (Optional - Defaults shown)
OTM_MIN=0.05           # Allow 5% OTM instead of 10%
OTM_MAX=0.50           # Allow up to 50% OTM
PREMIUM_MIN=0.10       # Allow lower premiums
PREMIUM_MAX=5.00       # Allow higher premiums
DTE_MIN=0              # Same day expiration OK
DTE_MAX=60             # Allow 60 days out
TREND_FILTER=any       # Accept any trend
```

**Then restart:**
```bash
python -m src.cli.main trade --manual-only --dry-run
```

---

## Example: Your SLV Trade

**Your Trade:**
```
Symbol: SLV
Strike: $80.00
Premium: $0.43
OTM %: 0.0% (blank - defaulted to 0)
```

**Rejection Reason:**
```
OTM 0.0% below minimum 10.0%
```

**Why This Failed:**
The OTM field was left blank, so it defaulted to 0.0%, which is below the minimum 10% requirement.

**Solutions:**

**Option 1: Skip Validation (Easiest)**
```bash
python -m src.cli.main trade --manual-only --no-validate-manual
```

**Option 2: Fix OTM Calculation**

First, check current SLV price (let's say it's $28.00):
```
Current Price: $28.00
Strike: $80.00

Wait - this strike is WAY out of the money!
OTM % = (28 - 80) / 28 = -185%

This is actually deep ITM (strike > price for a put)!
```

**Something's wrong here - double check:**
- Is strike really $80 or did you mean $25?
- Is this SLV (silver ETF ~$28) or a different symbol?
- Are you selling puts or calls?

**Option 3: Re-enter with Correct Data**
1. Open web interface: `python -m src.cli.main web`
2. Delete old trade (or mark as executed in database)
3. Re-enter with:
   - Correct strike price
   - Calculated OTM percentage
   - All other fields

---

## Testing Your Fix

### 1. Show Your Trades
```bash
python -m src.cli.main show-pending-trades
```

### 2. Dry Run with Validation
```bash
python -m src.cli.main trade --manual-only --dry-run
```

**Expected Output:**
- ✅ If valid: "✓ 1 opportunities qualified"
- ❌ If invalid: "✗ 1 opportunities rejected: [reason]"

### 3. Execute Without Validation (Safest)
```bash
python -m src.cli.main trade --manual-only --no-validate-manual
```

---

## Validation Modes Comparison

| Mode | Command | Use Case |
|------|---------|----------|
| **No Validation** | `--no-validate-manual` | Trust your research (Recommended) |
| **With Validation** | (default) | Double-check against criteria |
| **Barchart Scan** | Without `--manual-only` | Find additional opportunities |

---

## FAQ

**Q: Should I always use `--no-validate-manual`?**

**A:** Yes, for manual trades! You've already done your research. The validation is a legacy feature from when the system did automated scanning. For manual entries, your judgment is better than automated rules.

---

**Q: What's the difference between validation and risk checks?**

**A:**
- **Validation:** Checks if trade matches strategy criteria (OTM %, premium, etc.)
- **Risk Checks:** Enforced regardless of validation (position size limits, daily loss limits, etc.)

Risk checks always run - validation is optional.

---

**Q: Why does the system validate manual trades at all?**

**A:** Historical reasons. The `trade` command was originally designed for automated scanning + execution. For manual trades, you can (and should) skip validation with `--no-validate-manual`.

---

## Quick Reference Card

```bash
# See why trades were rejected
python -m src.cli.main trade --manual-only --dry-run

# Execute without validation (recommended)
python -m src.cli.main trade --manual-only --no-validate-manual

# Show current manual trades
python -m src.cli.main show-pending-trades

# Adjust criteria in .env, then test again
vim .env
python -m src.cli.main trade --manual-only --dry-run
```

---

**Document Version:** 1.0
**Last Updated:** 2026-01-26
**Related Docs:**
- `docs/PARAMETER_SYSTEM_EXPLAINED.md` - Parameter system details
- `docs/IBKR_FIELD_REFERENCE.md` - Field mapping guide
- `docs/CLI_COMMANDS_UPDATED.md` - All commands
