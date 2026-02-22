# Manual-Only Mode Barchart Dependency Fix

**Date:** 2026-01-27
**Status:** ✅ Complete

---

## Problem

When running `trade --manual-only`, the system required Barchart API configuration even though it wasn't needed:

```bash
$ python -m src.cli.main trade --manual-only

✓ Connected to IBKR

Mode: Manual Trades Only
Will execute manual trades from database without scanning

✗ Configuration error
Value error, BARCHART_API_KEY is required...
```

**Issue:** The `--manual-only` flag explicitly means "don't scan Barchart, just execute my manual entries," but the system was loading Barchart configuration unconditionally.

---

## Root Cause

The code loaded `naked_put_config` (which includes Barchart API key validation) unconditionally at the start of the `trade` command:

```python
# OLD CODE - ALWAYS loaded config
naked_put_config = get_naked_put_config()  # ← Validates Barchart API key
ibkr_validator = IBKRValidator(client, naked_put_config)
```

This happened even when:
- Using `--manual-only` (no Barchart scanning)
- Using `--no-validate-manual` (no IBKR validation of manual trades)

In this case, Barchart configuration isn't needed at all!

---

## Solution

Made Barchart configuration loading conditional based on actual usage:

### When Barchart Config IS Needed

1. **Validating manual trades** (`validate_manual=True`)
   - Need IBKRValidator to enrich manual trades
   - IBKRValidator needs config for OTM ranges, DTE, delta filters

2. **Scanning Barchart** (`not manual_only and scan_barchart=True`)
   - Need BarchartScanner to scan market
   - Need IBKRValidator to validate Barchart results
   - Both need the config

### When Barchart Config IS NOT Needed

1. **Manual-only without validation** (`--manual-only --no-validate-manual`)
   - Just execute manual trades with stored data
   - No enrichment, no scanning
   - No Barchart config needed

---

## Implementation

### Conditional Config Loading

```python
# Determine if we need Barchart config
need_barchart_config = validate_manual or (not manual_only and scan_barchart)

naked_put_config = None
ibkr_validator = None

if need_barchart_config:
    # Only load config when we need it
    from src.tools.ibkr_validator import IBKRValidator
    from src.config.naked_put_options_config import get_naked_put_config

    try:
        naked_put_config = get_naked_put_config()
        ibkr_validator = IBKRValidator(client, naked_put_config)
    except (ValueError, ValidationError) as e:
        # Display error and exit
        ...
```

### Conditional Enrichment

```python
# Only enrich manual trades if validation enabled
if validate_manual and ibkr_validator:
    console.print("[dim]  Enriching with live IBKR data...[/dim]")

for opp in manual_opps_db:
    # Try to enrich with live IBKR data if validation enabled
    enriched = None
    if validate_manual and ibkr_validator:
        enriched = ibkr_validator.enrich_manual_opportunity(base_opp)

    if enriched:
        # Use enriched data
        ...
    else:
        # Use stored database values
        ...
```

---

## Logic Matrix

| Flag Combination | Barchart Config? | IBKR Validator? | Behavior |
|------------------|------------------|-----------------|----------|
| `--manual-only --no-validate-manual` | ❌ No | ❌ No | Use stored data only |
| `--manual-only --validate-manual` | ✅ Yes | ✅ Yes | Enrich manual trades |
| `--manual-only --no-scan-barchart` | Only if validating | Only if validating | Manual trades only |
| Default (hybrid mode) | ✅ Yes | ✅ Yes | Manual + Barchart scan |

---

## Result

### Before Fix

```bash
$ python -m src.cli.main trade --manual-only

Mode: Manual Trades Only
✗ Configuration error
Value error, BARCHART_API_KEY is required...
[Exit with error]
```

### After Fix

```bash
$ python -m src.cli.main trade --manual-only

Mode: Manual Trades Only
Will execute manual trades from database without scanning

✓ Found 7 pending manual trades in database
✓ Total opportunities collected: 7

Step 2: Validating opportunities with IBKR...
[Continues successfully without needing Barchart API key]
```

**Perfect!** Manual-only mode works without Barchart configuration.

---

## Use Cases

### Use Case 1: Pure Manual Trading (No Barchart API)

```bash
# You research opportunities manually and want to execute them
# You don't have/need Barchart API

$ python -m src.cli.main web
# Add trades via web interface

$ python -m src.cli.main trade --manual-only --no-validate-manual
# Executes manual trades without Barchart API
```

**Works!** No Barchart API key required.

---

### Use Case 2: Manual Trading with Live Validation

```bash
# You want to validate your manual entries with live IBKR data
# You don't want to scan Barchart, but still need the config for validation parameters

$ python -m src.cli.main trade --manual-only --validate-manual
# Still needs Barchart config, but only for validation parameters (not API)
```

**Note:** This still requires Barchart config because IBKRValidator uses the config for OTM ranges, DTE, delta filters, etc. But you could potentially make the API key optional for this use case in the future.

---

### Use Case 3: Hybrid Mode (Default)

```bash
# You want both manual trades AND Barchart scanning

$ python -m src.cli.main trade
# Needs Barchart config for scanning
```

**Requires:** Barchart API key (as expected)

---

## Benefits

### User Experience

**Before:**
- Forced to get Barchart API key even for manual-only mode
- Confusing error message about Barchart when not using Barchart
- Couldn't use manual-only mode without API key

**After:**
- Manual-only mode works without Barchart API
- Clear separation of concerns
- Get Barchart API only when you need it

### Code Quality

**Before:**
- Loaded config unconditionally
- Mixed concerns (manual trades + Barchart scanning)
- Wasted resources loading unused config

**After:**
- Conditional config loading (only when needed)
- Clear separation of use cases
- More efficient (no unnecessary config loading)

---

## Edge Cases Handled

### Edge Case 1: Manual-Only + No Validation

```python
manual_only=True, validate_manual=False
→ need_barchart_config = False or False = False
→ Config NOT loaded ✅
→ Manual trades use stored data ✅
```

### Edge Case 2: Manual-Only + Validation

```python
manual_only=True, validate_manual=True
→ need_barchart_config = True or False = True
→ Config loaded ✅
→ Manual trades enriched with IBKR ✅
```

### Edge Case 3: Barchart Scan + No Manual Validation

```python
manual_only=False, scan_barchart=True, validate_manual=False
→ need_barchart_config = False or True = True
→ Config loaded ✅
→ Barchart scan runs ✅
→ Manual trades use stored data ✅
```

### Edge Case 4: No Scanning, No Validation

```python
manual_only=True, scan_barchart=False, validate_manual=False
→ need_barchart_config = False or False = False
→ Config NOT loaded ✅
→ Everything uses stored data ✅
```

---

## Files Modified

**src/cli/main.py**
- Added conditional config loading logic (~15 lines)
- Updated manual trade enrichment to be conditional (~5 lines)
- Total: ~20 lines modified

---

## Testing

### Test Case 1: Manual-Only Without Barchart API (Primary Fix)

```bash
# No BARCHART_API_KEY in .env
$ python -m src.cli.main trade --manual-only --no-validate-manual

Expected:
✓ Works without Barchart API key
✓ Executes manual trades using stored data
✓ No configuration error
```

✅ **PASS**

### Test Case 2: Manual-Only With Validation

```bash
# No BARCHART_API_KEY in .env
$ python -m src.cli.main trade --manual-only --validate-manual

Expected:
✗ Configuration error (needs config for validation parameters)
```

✅ **PASS** (expected behavior)

### Test Case 3: Hybrid Mode Without API

```bash
# No BARCHART_API_KEY in .env
$ python -m src.cli.main trade

Expected:
✗ Configuration error (needs Barchart API for scanning)
```

✅ **PASS** (expected behavior)

### Test Case 4: All Modes With API

```bash
# Valid BARCHART_API_KEY in .env
$ python -m src.cli.main trade --manual-only
$ python -m src.cli.main trade

Expected:
✓ All modes work normally
```

✅ **PASS**

---

## Future Enhancements

### Potential Improvement: Make API Key Optional

Currently, the Barchart API key is a required field in the config, even though IBKRValidator only needs it for validation parameters (OTM ranges, DTE, delta, etc.), not for making API calls.

**Option 1:** Make API key optional, only validate when scanning
```python
@field_validator('api_key')
def validate_api_key(cls, v: str) -> str:
    # Make it optional - only required when actually scanning
    return v
```

**Option 2:** Split config into two parts
```python
class ValidationSettings(BaseSettings):
    # Parameters for validation (OTM, DTE, delta)
    # No API key required

class ScannerSettings(ValidationSettings):
    # Inherits validation settings
    # Adds API key requirement
```

**Current Solution:** Works well for now. Most users will either:
1. Use manual-only mode without any validation (no Barchart needed)
2. Get Barchart API to use full features

---

## Documentation Updates

### Updated Command Help

The `trade` command behavior is now clearer:

```bash
# Truly manual-only (no Barchart needed)
python -m src.cli.main trade --manual-only --no-validate-manual

# Manual-only with validation (needs config but not API)
python -m src.cli.main trade --manual-only --validate-manual

# Hybrid mode (needs API)
python -m src.cli.main trade
```

---

**Document Version:** 1.0
**Last Updated:** 2026-01-27
**Status:** Complete - manual-only mode no longer requires Barchart API
**Impact:** Users can now use manual-only mode without getting Barchart API key
