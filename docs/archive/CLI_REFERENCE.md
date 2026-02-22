# CLI Command Reference

**Status:** ✅ All commands implemented and operational
**Date:** 2026-01-27
**Version:** Trading Agent v0.2.0 (Enhanced with Manual Trade Entry & Database Integration)

---

## Table of Contents

1. [Infrastructure Commands](#infrastructure-commands)
2. [Trading Commands](#trading-commands)
3. [Manual Trade Entry](#manual-trade-entry)
4. [Scan History & Analysis](#scan-history--analysis)
5. [Quick Start Guide](#quick-start-guide)
6. [Complete Workflow Examples](#complete-workflow-examples)
7. [Command Cheat Sheet](#command-cheat-sheet)
8. [Testing & Validation Status](#testing--validation-status)

---

## Infrastructure Commands

### init - Initialize System

```bash
python -m src.cli.main init
```

**Purpose:** First-time setup - creates database, directories, and initializes logging.

**What it does:**
- Creates database schema (trades, positions, audit_log, scan_results, scan_opportunities)
- Creates required directories (data/, logs/, cache/)
- Sets up logging configuration
- Validates environment variables

**When to use:** Once during initial setup, or after `db-reset`.

**Status:** ✅ Working

---

### status - System Status

```bash
python -m src.cli.main status
```

**Purpose:** View system health and trade statistics.

**Output:**
- Database connection status
- IBKR connection status (if available)
- Total trades (open/closed)
- Current P&L
- Recent activity summary

**No IBKR required:** Works offline with database only.

**Status:** ✅ Working

---

### test-ibkr - Test IBKR Connection

```bash
python -m src.cli.main test-ibkr
```

**Purpose:** Diagnose IBKR connection issues.

**Output:**
- Connection success/failure
- Account type (paper/live)
- Available capital
- Connection diagnostics

**Requires:** TWS or IB Gateway running.

**Status:** ✅ Working

---

### version - Version Info

```bash
python -m src.cli.main version
```

**Purpose:** Show version and system information.

**Status:** ✅ Working

---

### db-reset - Reset Database

```bash
python -m src.cli.main db-reset
```

**Purpose:** Delete all data and recreate database schema.

**⚠️ Warning:** This deletes ALL trades, positions, and scan history. Asks for confirmation.

**Status:** ✅ Working

---

## Trading Commands

### scan - Scan for Opportunities (Enhanced)

```bash
# Full scan with Barchart + IBKR validation (auto-saves to DB)
python -m src.cli.main scan

# Quick Barchart scan without IBKR validation
python -m src.cli.main scan --no-validate

# Scan without saving to database
python -m src.cli.main scan --no-save-db

# Show more results
python -m src.cli.main scan --max-results 50

# Save raw results to file
python -m src.cli.main scan --save-file data/scans/my_scan.json
```

**How it works:**
1. **Barchart API** - Scans entire US options market (fast, single API call)
2. **IBKR Validation** - Validates top candidates with real-time quotes (accurate)
3. **Database Storage** - Automatically saves all results for historical tracking

**Options:**
- `--max-results INT` - Maximum opportunities to show (default: 20)
- `--validate / --no-validate` - Validate with IBKR (default: true)
- `--save-db / --no-save-db` - Save to database (default: true)
- `--save-file PATH` - Save raw Barchart results to JSON file

**Output:**
- Table of validated opportunities
- Symbol, strike, premium, delta, margin efficiency, trend
- Automatically saved to database with scan_id

**Requires:** Barchart API key, IBKR connection (for validation)

**Status:** ✅ Working

---

### trade - Autonomous Trading Cycle (Enhanced)

```bash
# Full workflow: manual trades + Barchart scan
python -m src.cli.main trade

# Only trade manual entries (no Barchart)
python -m src.cli.main trade --manual-only

# Skip validation of manual trades (trust your analysis)
python -m src.cli.main trade --no-validate-manual

# Fully autonomous mode
python -m src.cli.main trade --auto

# Limit number of trades
python -m src.cli.main trade --max-trades 3

# Dry run (no real orders)
python -m src.cli.main trade --dry-run
```

**How it works:**
1. **Import Manual Trades** - Loads pending manual trades from database
2. **Barchart Scan** - Optionally scans for additional opportunities
3. **Merge & Dedupe** - Combines manual + Barchart opportunities
4. **IBKR Validation** - Validates all opportunities with real-time data
5. **Risk Checks** - Enforces risk limits
6. **Execute** - Places orders (with confirmation unless --auto)
7. **Monitor** - Checks positions and exit signals

**Options:**
- `--auto` - Fully autonomous (no confirmations)
- `--max-trades INT` - Maximum trades to place (default: 5)
- `--dry-run` - Simulate without placing real orders
- `--manual-only` - Use only manual trades, skip Barchart
- `--scan-barchart / --no-scan-barchart` - Run Barchart scan (default: true)
- `--validate-manual / --no-validate-manual` - Validate manual trades (default: true)

**Confidence Scoring:**
- Manual trades: 0.80 confidence
- Barchart + IBKR validated: 0.75 confidence
- System executes highest confidence opportunities first

**Example Workflow:**
```
Step 1: Import 3 manual trades from database
Step 2: Run Barchart scan → find 15 candidates
Step 3: IBKR validation → 8 pass
Step 4: Merge → 11 total opportunities (manual + validated)
Step 5: Execute top 5 (within risk limits)
```

**Status:** ✅ Working

---

### execute - Execute Single Trade

```bash
# Execute a trade
python -m src.cli.main execute AAPL 180 2025-02-07 --premium 0.50 --contracts 1

# Dry run
python -m src.cli.main execute AAPL 180 2025-02-07 --premium 0.50 --dry-run
```

**Purpose:** Place a single trade with specified parameters.

**Arguments:**
- `SYMBOL` - Stock ticker (AAPL, SPY, MSFT, etc.)
- `STRIKE` - Strike price
- `EXPIRATION` - Expiration date (YYYY-MM-DD)

**Options:**
- `--premium FLOAT` - Expected premium (default: 0.50)
- `--contracts INT` - Number of contracts (default: 1)
- `--dry-run` - Simulate without placing real order

**Status:** ✅ Working

---

### monitor - Check Positions

```bash
python -m src.cli.main monitor
```

**Purpose:** Monitor all open positions with real-time P&L and exit signals.

**Output:**
- Table of open positions
- Current P&L ($ and %)
- Greeks (delta, gamma, theta, vega)
- Exit signals (profit target, stop loss, time exit)
- Alerts for positions ready to exit

**Example Output:**
```
┌─────────────────────────────────────────────────────────────┐
│ Open Positions (3)                                          │
├───────┬────────┬──────┬─────┬────────┬─────────┬──────────┤
│Symbol │ Strike │ Exp  │ DTE │  P&L   │  P&L%   │   Exit   │
├───────┼────────┼──────┼─────┼────────┼─────────┼──────────┤
│ AAPL  │ $180   │02/07 │  10 │ +$45   │ +90.0%  │ ⚠ Target│
│ MSFT  │ $350   │02/14 │  17 │ -$15   │ -15.0%  │          │
│ SPY   │ $570   │02/21 │  24 │ +$25   │ +50.0%  │          │
└───────┴────────┴──────┴─────┴────────┴─────────┴──────────┘

Total Unrealized P&L: +$55.00

⚠ 1 position ready for exit
```

**Requires:** IBKR connection

**Status:** ✅ Working

---

### analyze - Performance Analysis

```bash
# Analyze last 30 days
python -m src.cli.main analyze

# Custom time period
python -m src.cli.main analyze --days 60

# Show recent trades
python -m src.cli.main analyze --trades

# Combine options
python -m src.cli.main analyze --days 90 --trades
```

**Purpose:** Analyze trading performance and statistics.

**Output:**
- Win rate
- Total P&L
- Average P&L per trade
- Average ROI
- Optional: Recent closed trades table

**Options:**
- `--days` - Days to analyze (default: 30)
- `--trades` - Show recent closed trades

**Example Output:**
```
┌─────────────────────────────────────────────┐
│ Performance Summary (Last 30 Days)          │
├────────────────────┬────────────────────────┤
│ Metric             │ Value                  │
├────────────────────┼────────────────────────┤
│ Total Trades       │ 25                     │
│ Winning Trades     │ 22                     │
│ Win Rate           │ 88.0%                  │
│ Total P&L          │ $1,250.00              │
│ Avg P&L/Trade      │ $50.00                 │
│ Avg ROI            │ 2.5%                   │
└────────────────────┴────────────────────────┘
```

**No IBKR required:** Database only.

**Status:** ✅ Working

---

### emergency-stop - Halt Trading

```bash
# Halt trading (blocks new orders)
python -m src.cli.main emergency-stop

# Halt AND liquidate all positions
python -m src.cli.main emergency-stop --liquidate
```

**Purpose:** Emergency stop mechanism to immediately halt all trading.

**What it does:**
1. Triggers `risk_governor.emergency_halt()`
2. Blocks all new trade attempts
3. Optionally closes all open positions (with `--liquidate`)
4. Logs emergency event

**Options:**
- `--liquidate` - Close all open positions (⚠️ use carefully)

**When to use:**
- System malfunction detected
- Market conditions change dramatically
- Need to stop trading immediately
- Before maintenance/updates

**Recovery:**
- Restart application to resume trading
- Or call `risk_governor.resume_trading()` programmatically

**⚠️ Warning:** `--liquidate` places market orders to close ALL positions. Use only in true emergencies.

**Status:** ✅ Working

---

## Manual Trade Entry

### web - Web Interface

```bash
# Start web server
python -m src.cli.main web

# Custom port
python -m src.cli.main web --port 8080

# Bind to all interfaces (remote access)
python -m src.cli.main web --host 0.0.0.0
```

**Purpose:** Launch browser-based interface for manual trade entry.

**Features:**
- Clean web form with validation
- Save directly to database (no JSON files)
- View pending manual trades
- "Save & Add Another" for batch entry
- Interactive help for each field

**Access:** Open browser to http://127.0.0.1:5000

**Options:**
- `--host TEXT` - Host to bind to (default: 127.0.0.1)
- `--port INT` - Port to listen on (default: 5000)
- `--debug / --no-debug` - Enable debug mode (default: true)

**Saves to:** Database directly (source: "manual_web")

**Reference:** See `docs/IBKR_FIELD_REFERENCE.md` for field mapping guide.

**Status:** ✅ Working

---

### add-trade - CLI Manual Entry

```bash
# Interactive mode (prompts for all fields)
python -m src.cli.main add-trade

# Command-line mode (specify all fields)
python -m src.cli.main add-trade \
  --symbol AAPL \
  --strike 180.00 \
  --expiration 2025-02-14 \
  --premium 0.45 \
  --notes "Strong uptrend, support at 200"

# Create template file
python -m src.cli.main add-trade --create-template
```

**Purpose:** Add manual trades via command line.

**Modes:**
1. **Interactive** - Prompts for each field with guidance
2. **Command-line** - Specify all fields as arguments
3. **Template** - Creates JSON template for bulk editing

**Required Fields:**
- `--symbol` - Stock ticker
- `--strike` - Strike price
- `--expiration` - Expiration date (YYYY-MM-DD)

**Optional Fields:**
- `--premium` - Expected premium
- `--notes` - Your reasoning
- `--filename` - Custom filename for JSON
- Plus all Greeks, pricing, volume fields

**Saves to:** JSON file in `data/manual_trades/pending/`

**Next Step:** Files are imported when running `trade` command.

**Status:** ✅ Working

---

### show-pending-trades - Show Pending Database Trades

```bash
# Show pending web trades (default)
python -m src.cli.main show-pending-trades

# Show all manual trade sources
python -m src.cli.main show-pending-trades --all-sources

# Show more results
python -m src.cli.main show-pending-trades --limit 100
```

**Purpose:** View pending manual trades from the database that are ready for execution.

**What it shows:**
- Trades entered via web interface (source: "manual_web")
- Optionally, trades from CLI and JSON files (with --all-sources)
- All trades ready to be executed by the `trade` command

**Options:**
- `--all-sources` - Show all manual trades (web + CLI + JSON)
- `--limit INT` - Maximum trades to show (default: 50)

**Output:**
- Table with ID, symbol, strike, expiration, premium, delta, DTE, source, notes
- Summary by source (web interface, CLI/JSON)
- Next steps suggestions

**Use this command when:**
- You've entered trades via web interface and want to review them
- You want to see what will be executed by `trade --manual-only`
- You're checking the database state before trading

**Status:** ✅ Working

---

### list-manual-trade-files - List Trade JSON Files

```bash
# List pending trade files
python -m src.cli.main list-manual-trade-files

# List imported history
python -m src.cli.main list-manual-trade-files --imported
```

**Purpose:** View pending or imported manual trade JSON files on the filesystem.

**What it shows:**
- JSON files in `data/manual_trades/pending/` (not yet imported)
- JSON files in `data/manual_trades/imported/` (already processed)
- File metadata: name, modified date, trade count, notes

**Options:**
- `--imported` - Show imported files instead of pending

**Output:**
- File name
- Modified date
- Number of trades
- Notes preview

**Important Notes:**
- **Web interface trades** go directly to database and won't appear here
- Use `show-pending-trades` to see web interface entries
- This command is for file-based workflow only (JSON import method)

**Use this command when:**
- You've created JSON files manually and want to see them
- You want to check if JSON files have been imported
- You're using the file-based workflow instead of web interface

**Status:** ✅ Working

---

## Scan History & Analysis

### scan-history - View Historical Scans

```bash
# Show last 30 days of scans
python -m src.cli.main scan-history

# Filter by source
python -m src.cli.main scan-history --source barchart

# Filter by symbol
python -m src.cli.main scan-history --symbol AAPL

# Custom time range
python -m src.cli.main scan-history --days 7 --limit 100
```

**Purpose:** Query and display past scan results from database.

**Options:**
- `--days INT` - Days to look back (default: 30)
- `--source TEXT` - Filter by source (barchart, manual, manual_web)
- `--symbol TEXT` - Filter by symbol
- `--limit INT` - Maximum scans to show (default: 50)

**Output:**
- Table of scans (ID, date, source, candidates, validated count, execution time)
- Statistics summary (total scans, avg opportunities per scan)
- Breakdown by source

**Use cases:**
- Track scan effectiveness over time
- Identify patterns in opportunity discovery
- Compare manual vs automated scanning
- Analyze scan performance metrics

**Status:** ✅ Working

---

### scan-details - View Scan Details

```bash
# View scan details
python -m src.cli.main scan-details 123

# Include rejected opportunities
python -m src.cli.main scan-details 123 --show-rejected
```

**Purpose:** View detailed information about a specific scan.

**Arguments:**
- `SCAN_ID` - The scan ID to view (from scan-history)

**Options:**
- `--show-rejected` - Show rejected opportunities with rejection reasons

**Output:**
- Scan metadata (timestamp, source, counts, execution time)
- Table of all opportunities (symbol, strike, premium, delta, status)
- Execution summary
- Rejection reasons (if any)

**Use cases:**
- Investigate why certain opportunities were rejected
- Review historical opportunity details
- Audit scan quality and validation effectiveness

**Status:** ✅ Working

---

## Quick Start Guide

### First Time Setup

```bash
# 1. Initialize system
python -m src.cli.main init

# 2. Test IBKR connection
python -m src.cli.main test-ibkr

# 3. Check status
python -m src.cli.main status
```

---

### Daily Workflow Option 1: Web Interface

```bash
# 1. Start web interface
python -m src.cli.main web

# 2. Open browser: http://127.0.0.1:5000
#    - Add manual trades via web form
#    - View pending trades

# 3. Review pending trades
python -m src.cli.main show-pending-trades

# 4. Run trading cycle (imports manual + scans Barchart)
python -m src.cli.main trade

# 5. Monitor positions
python -m src.cli.main monitor

# 6. End of day analysis
python -m src.cli.main analyze --trades
```

---

### Daily Workflow Option 2: Fully Automated

```bash
# 1. Scan for opportunities (Barchart + IBKR)
python -m src.cli.main scan

# 2. Run autonomous trading
python -m src.cli.main trade --auto --max-trades 5

# 3. Monitor throughout day
python -m src.cli.main monitor

# 4. Review history
python -m src.cli.main scan-history --days 1
```

---

### Weekly Review

```bash
# 1. Performance analysis
python -m src.cli.main analyze --days 7 --trades

# 2. Scan effectiveness
python -m src.cli.main scan-history --days 7

# 3. Review specific scans
python -m src.cli.main scan-details <scan-id>

# 4. Check system status
python -m src.cli.main status
```

---

## Complete Workflow Examples

### Scenario 1: Manual Trading (Research Your Own Trades)

```bash
# Step 1: Research opportunities and add via web
python -m src.cli.main web
# Enter 3 trades you researched

# Step 2: Review pending trades
python -m src.cli.main show-pending-trades

# Step 3: Trade only your manual entries
python -m src.cli.main trade --manual-only

# Step 4: Monitor
python -m src.cli.main monitor

# Step 5: Review what you traded
python -m src.cli.main scan-history --source manual_web
```

---

### Scenario 2: Hybrid Approach (Manual + Automated)

```bash
# Step 1: Add a few high-conviction manual trades
python -m src.cli.main web
# Add 2-3 trades

# Step 2: Review before trading
python -m src.cli.main show-pending-trades

# Step 3: Run trade command (merges manual + Barchart)
python -m src.cli.main trade --max-trades 10
# System will:
# - Import your 2-3 manual trades
# - Scan Barchart for 7-8 additional opportunities
# - Execute best 10 total

# Step 4: Monitor and analyze
python -m src.cli.main monitor
python -m src.cli.main scan-history
```

---

### Scenario 3: Fully Automated

```bash
# Just run autonomous trading cycle
python -m src.cli.main trade --auto --max-trades 10

# System handles everything:
# - Scans market
# - Validates opportunities
# - Executes trades
# - Monitors positions
```

---

### Scenario 4: Research & Analysis

```bash
# Review last week's scans
python -m src.cli.main scan-history --days 7

# View specific scan details
python -m src.cli.main scan-details 42

# Check which opportunities were executed
python -m src.cli.main analyze --days 7 --trades

# Compare manual vs automated effectiveness
python -m src.cli.main scan-history --source manual_web --days 30
python -m src.cli.main scan-history --source barchart --days 30
```

---

## Command Cheat Sheet

| Task | Command |
|------|---------|
| **Setup** | |
| Initialize system | `python -m src.cli.main init` |
| Test connection | `python -m src.cli.main test-ibkr` |
| System status | `python -m src.cli.main status` |
| **Manual Entry** | |
| Web interface | `python -m src.cli.main web` |
| CLI entry | `python -m src.cli.main add-trade` |
| Show pending trades (DB) | `python -m src.cli.main show-pending-trades` |
| List trade files (JSON) | `python -m src.cli.main list-manual-trade-files` |
| **Scanning** | |
| Scan market | `python -m src.cli.main scan` |
| View scan history | `python -m src.cli.main scan-history` |
| View scan details | `python -m src.cli.main scan-details <id>` |
| **Trading** | |
| Manual trades only | `python -m src.cli.main trade --manual-only` |
| Hybrid (manual + scan) | `python -m src.cli.main trade` |
| Fully automated | `python -m src.cli.main trade --auto` |
| Single trade | `python -m src.cli.main execute AAPL 180 2025-02-07` |
| **Monitoring** | |
| Check positions | `python -m src.cli.main monitor` |
| Performance analysis | `python -m src.cli.main analyze` |
| **Emergency** | |
| Stop trading | `python -m src.cli.main emergency-stop` |

---

## Understanding Command Differences

### Manual Trade Entry: Two Workflows

#### Web Interface Workflow (Recommended)
```
1. python -m src.cli.main web
2. Enter trades in browser
3. Saves directly to database
4. python -m src.cli.main show-pending-trades  ← View DB trades
5. python -m src.cli.main trade --manual-only  ← Execute
```

#### JSON File Workflow (Advanced)
```
1. python -m src.cli.main add-trade
2. Creates JSON file in pending/
3. python -m src.cli.main list-manual-trade-files  ← View files
4. python -m src.cli.main trade  ← Imports files to DB
5. python -m src.cli.main show-pending-trades  ← View DB trades
```

**Key Points:**
- `show-pending-trades` shows **database records** (web + imported JSON)
- `list-manual-trade-files` shows **JSON files** on disk (not yet imported)
- Web interface is simpler: direct to database, no file management needed

---

## Environment Requirements

### Required Environment Variables (.env)

```bash
# Barchart API
BARCHART_API_KEY=your_api_key_here

# IBKR Connection
IBKR_HOST=127.0.0.1
IBKR_PORT=7497                    # 7497=paper, 7496=live
PAPER_TRADING=true

# Database
DATABASE_URL=sqlite:///data/databases/trades.db

# Logging
LOG_LEVEL=INFO
```

### IBKR Requirements
- TWS or IB Gateway running
- Paper trading account
- API connections enabled
- Port 7497 (paper) or 7496 (live)

### Python Requirements
- Python 3.11+
- Virtual environment activated
- All packages from requirements.txt installed

---

## Tips & Best Practices

### Manual Trade Entry
✅ Use the **web interface** for easiest entry (saves directly to database)
✅ Use **IV Last** from IBKR, not Implied Vol/Hist Vol
✅ Enter percentages as decimals (75% = 0.75)
✅ Add **notes** to remember your reasoning
✅ Leave optional fields blank if you don't have the data
✅ Review with `show-pending-trades` before executing

### Trading Strategy
✅ Start with `--manual-only` to trade only researched opportunities
✅ Use `--no-validate-manual` if you trust your analysis
✅ Always use `--dry-run` when testing
✅ Limit exposure with `--max-trades`
✅ Use interactive mode before `--auto`

### Database & History
✅ All scans auto-save to database (track everything)
✅ Use `scan-history` to analyze patterns
✅ Use `scan-details` to investigate specific scans
✅ Manual web entries go directly to database (no JSON files)

### Safety
✅ Monitor positions regularly
✅ Review scan history weekly
✅ Keep `emergency-stop` accessible
✅ Start small, increase gradually
✅ Check logs in `logs/app.log`

---

## Testing & Validation Status

### All Commands Tested ✅

**Infrastructure Commands:**
- ✅ `init` - Creates database, directories, logging
- ✅ `status` - Shows system stats, trade counts
- ✅ `test-ibkr` - Tests connection, displays account info
- ✅ `version` - Shows version information
- ✅ `db-reset` - Prompts for confirmation correctly

**Trading Commands:**
- ✅ `scan` - Finds opportunities (Barchart + IBKR validation)
- ✅ `execute` - Validates date, runs risk checks
- ✅ `trade` - Full workflow executes (manual + Barchart + execution)
- ✅ `monitor` - Displays positions with P&L
- ✅ `analyze` - Works with --trades flag
- ✅ `emergency-stop` - Halts trading, liquidates if requested

**Manual Trade Entry:**
- ✅ `web` - Web interface launches, saves to database
- ✅ `add-trade` - Interactive and CLI modes work
- ✅ `show-pending-trades` - Shows database trades
- ✅ `list-manual-trade-files` - Lists JSON files

**Scan History:**
- ✅ `scan-history` - Query and filter historical scans
- ✅ `scan-details` - View detailed scan information

### Issues Fixed

1. ✅ Wrong screener method name - Updated to use correct API
2. ✅ PositionStatus missing expiration attribute - Fixed calculation
3. ✅ Environment variables not loaded - Added dotenv loading
4. ✅ Main help command - Custom formatter implemented
5. ✅ Command naming confusion - Renamed for clarity

### Known Limitations

1. **Market Hours Dependency**
   - Commands requiring IBKR work best during market hours (9:30 AM - 4:00 PM ET)
   - Outside hours: scan may find limited results, data may be stale
   - Workaround: Run during market hours for full functionality

2. **Command-Specific Help**
   - Main help works: `python -m src.cli.main --help` ✅
   - Command help broken: `python -m src.cli.main scan --help` ❌
   - Workaround: Refer to this documentation for all command details

---

## Reference Documents

- **CLI Reference:** `docs/CLI_REFERENCE.md` (this file)
- **IBKR Field Mapping:** `docs/IBKR_FIELD_REFERENCE.md`
- **Barchart API Guide:** `docs/BARCHART_API_GUIDE.md`
- **Barchart Migration:** `docs/BARCHART_MIGRATION.md`
- **Troubleshooting:** `docs/TROUBLESHOOTING_VALIDATION.md`
- **System Specification:** `SPEC_TRADING_SYSTEM.md`
- **Development Guide:** `CLAUDE.md`

---

## Common Error Messages & Solutions

### "No such command"
**Solution:** Commands have been renamed for clarity. Use this reference document.

### "IBKR connection failed"
**Solution:**
- Start TWS/IB Gateway
- Enable API connections
- Verify port 7497 (paper) or 7496 (live)
- Run `test-ibkr` to diagnose

### "Trade rejected by risk governor"
**Solution:**
- Risk limits exceeded
- Check limits in config
- Close some positions to free up capacity
- View details in `logs/app.log`

### "No opportunities found matching criteria"
**Solution:**
- May be outside market hours
- Try adjusting scan parameters (lower min-premium, wider OTM range)
- Run `scan --no-validate` for quick Barchart-only scan

### "Database error"
**Solution:**
- Run `db-reset` to recreate database (⚠️ deletes all data)
- Check `DATABASE_URL` in .env
- Verify write permissions in data/ directory

---

**Document Version:** 3.0 (Consolidated)
**Last Updated:** 2026-01-27
**Status:** All commands documented, tested, and operational
**Changes:**
- Renamed `list-manual-trades` → `list-manual-trade-files`
- Renamed `show-manual-trades` → `show-pending-trades`
- Consolidated 3 CLI docs into single comprehensive reference
- Added "Understanding Command Differences" section
- Updated all examples and references
