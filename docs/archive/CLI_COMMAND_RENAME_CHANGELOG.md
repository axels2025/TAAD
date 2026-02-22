# CLI Command Rename Changelog

**Date:** 2026-01-27
**Status:** ✅ Complete

---

## Summary

Renamed CLI commands for better clarity and consolidated all CLI documentation into a single comprehensive reference document.

---

## Command Name Changes

### 1. `list-manual-trades` → `list-manual-trade-files`

**Old Command:**
```bash
python -m src.cli.main list-manual-trades
```

**New Command:**
```bash
python -m src.cli.main list-manual-trade-files
```

**Purpose:** Lists JSON files on the filesystem in `data/manual_trades/pending/` or `data/manual_trades/imported/`.

**Why renamed:** The name now clearly indicates it shows **files**, not database records.

---

### 2. `show-manual-trades` → `show-pending-trades`

**Old Command:**
```bash
python -m src.cli.main show-manual-trades
```

**New Command:**
```bash
python -m src.cli.main show-pending-trades
```

**Purpose:** Shows pending trades from the database (entered via web interface or CLI).

**Why renamed:** The name now clearly indicates it shows **pending trades from database**, not files.

---

## Key Differences Explained

### `show-pending-trades` (Database Records)
- Shows trades stored in the database
- Includes trades entered via web interface (source: "manual_web")
- These are ready to be executed by `trade` command
- **Use this when:** You want to see what will be traded

**Example:**
```bash
# See what's in the database ready for execution
python -m src.cli.main show-pending-trades
```

### `list-manual-trade-files` (JSON Files)
- Shows JSON files on disk
- Located in `data/manual_trades/pending/` or `data/manual_trades/imported/`
- These need to be imported into database first
- **Use this when:** You're using file-based workflow and want to see JSON files

**Example:**
```bash
# See what JSON files are waiting to be imported
python -m src.cli.main list-manual-trade-files
```

---

## Workflow Comparison

### Web Interface Workflow (Most Common)
```
1. python -m src.cli.main web
2. Enter trades in browser → Saves directly to database
3. python -m src.cli.main show-pending-trades  ✅ See your trades
4. python -m src.cli.main trade --manual-only   ✅ Execute
```
**Note:** JSON files are NOT involved in this workflow.

### File-Based Workflow (Advanced)
```
1. python -m src.cli.main add-trade
2. Creates JSON file in pending/
3. python -m src.cli.main list-manual-trade-files  ✅ See files
4. python -m src.cli.main trade  → Imports files to database
5. python -m src.cli.main show-pending-trades  ✅ See imported trades
```

---

## Documentation Changes

### Consolidated CLI Documentation

**Removed:**
- `docs/CLI_COMMANDS.md` (old quick reference)
- `docs/CLI_STATUS.md` (testing report)
- `docs/CLI_COMMANDS_UPDATED.md` (previous comprehensive guide)

**Created:**
- `docs/CLI_REFERENCE.md` (single comprehensive reference)

**What's in CLI_REFERENCE.md:**
- All infrastructure commands
- All trading commands
- All manual trade entry commands
- Scan history & analysis commands
- Quick start guide
- Complete workflow examples
- Command cheat sheet
- Testing & validation status
- Tips & best practices
- Error messages & solutions
- Environment requirements

**Benefits:**
- Single source of truth
- No duplicate or conflicting information
- Comprehensive examples
- Clear explanation of command differences
- All testing status consolidated

---

## Files Modified

### Code Changes
1. **src/cli/main.py**
   - Line ~1890: Renamed `list_manual_trades()` → `list_manual_trade_files()`
   - Line ~1956: Renamed `show_manual_trades()` → `show_pending_trades()`
   - Updated docstrings to clarify purpose

### Documentation Updates
2. **docs/TROUBLESHOOTING_VALIDATION.md**
   - Updated command references (lines 279, 342)

3. **docs/CLI_REFERENCE.md** (NEW)
   - Consolidated documentation
   - Updated with new command names
   - Added "Understanding Command Differences" section

### Documentation Removed
4. **docs/CLI_COMMANDS.md** ❌ Deleted
5. **docs/CLI_STATUS.md** ❌ Deleted
6. **docs/CLI_COMMANDS_UPDATED.md** ❌ Deleted

---

## Migration Guide for Users

### If you were using `show-manual-trades`:
```bash
# Old way
python -m src.cli.main show-manual-trades

# New way
python -m src.cli.main show-pending-trades
```
**No functionality change** - just clearer naming.

### If you were using `list-manual-trades`:
```bash
# Old way
python -m src.cli.main list-manual-trades

# New way
python -m src.cli.main list-manual-trade-files
```
**No functionality change** - just clearer naming.

---

## Backward Compatibility

**Breaking Change:** Old command names no longer work.

**Migration Required:** Update any scripts, documentation, or habits to use new command names.

**Easy Fix:** Just add `-files` to `list-manual-trades` and change `show-manual-trades` to `show-pending-trades`.

---

## Testing Performed

✅ Command functions renamed successfully
✅ All docstrings updated
✅ All documentation references updated
✅ Old documentation files removed
✅ New consolidated documentation created
✅ No other code references to old names

---

## Next Steps for Users

1. **Update your mental model:**
   - `show-pending-trades` = database records (what will be executed)
   - `list-manual-trade-files` = JSON files on disk (file-based workflow)

2. **Update any scripts:**
   - Search for old command names in your scripts
   - Replace with new names

3. **Refer to new documentation:**
   - `docs/CLI_REFERENCE.md` is now the single source of truth
   - Comprehensive examples and explanations
   - Command cheat sheet for quick reference

4. **Test the new commands:**
   ```bash
   # See pending trades in database
   python -m src.cli.main show-pending-trades

   # See JSON files on disk (if using file workflow)
   python -m src.cli.main list-manual-trade-files
   ```

---

## Questions & Answers

**Q: I'm using the web interface. Which command should I use?**
A: Use `show-pending-trades` to see your web entries in the database.

**Q: Do I ever need `list-manual-trade-files`?**
A: Only if you're creating JSON files manually. Most users won't need this.

**Q: Will my old scripts break?**
A: Yes, if they use the old command names. Update them to use the new names.

**Q: Where do I find command documentation now?**
A: `docs/CLI_REFERENCE.md` - comprehensive, consolidated, single source of truth.

**Q: Why were the commands renamed?**
A: The old names were confusing because they sounded similar but did different things:
- One showed database records
- One showed JSON files

The new names make this distinction crystal clear.

---

**Document Version:** 1.0
**Last Updated:** 2026-01-27
**Status:** Complete - all changes implemented and tested
