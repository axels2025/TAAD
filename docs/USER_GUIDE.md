# Trading Agent - User Guide

**Version:** 1.2
**Last Updated:** February 16, 2026

---

## Table of Contents

1. [Introduction](#introduction)
2. [Quick Start](#quick-start)
3. [Complete Workflow](#complete-workflow)
4. [Step-by-Step Guides](#step-by-step-guides)
5. [TAAD: Trade Archaeology & Alpha Discovery](#taad-trade-archaeology--alpha-discovery)
6. [NakedTrader: Daily SPX/XSP/SPY Options](#nakedtrader-daily-spxxspsspy-options)
7. [Command Reference](#command-reference)
8. [Examples](#examples)
9. [Troubleshooting](#troubleshooting)
10. [Best Practices](#best-practices)
11. [End-to-End Logical Flow](#end-to-end-logical-flow)
12. [Configuration Reference](#configuration-reference)

---

## Introduction

This trading agent automates the execution of a naked put options selling strategy. The system follows a Sunday-Monday workflow:

- **Sunday Evening**: Screen candidates, validate opportunities, calculate optimal prices
- **Monday Morning**: Execute trades at market open with intelligent two-tier execution

### Key Features

- **Barchart Integration**: Import trade candidates from CSV files
- **Live IBKR Validation**: Verify all opportunities with real-time market data
- **Intelligent Limit Pricing**: Calculate optimal entry prices using bid-mid ratios
- **Adaptive Strike Selection**: Delta-based strike resolution from live option chains at execution time
- **Two-Tier Execution**: Fast 9:30 AM execution with condition-based 10:00 AM retry
- **Fill Management**: Time-boxed fill monitoring with partial fill handling and progressive limit adjustment
- **Position Reconciliation**: Automatic sync between database and IBKR
- **Risk Management**: Margin checks, sector limits, OTM validation

### Prerequisites

- Interactive Brokers account (paper or live)
- TWS or IB Gateway running on localhost
- Python 3.11+ with all dependencies installed
- Barchart account for trade screening (CSV export)

---

## Quick Start

### 1. Initialize System

```bash
# First time setup
python -m src.cli.main init

# Test IBKR connection
python -m src.cli.main test-ibkr
```

### 2. Sunday Preparation

```bash
# Process Barchart candidates
python -m src.cli.main sunday-session --file barchart-export.csv

# Review staged opportunities
python -m src.cli.main show-staged
```

### 3. Monday Execution

```bash
# Execute with full automation (Phase D)
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes
```

### 4. Post-Execution Verification

```bash
# List open positions
python -m src.cli.main list-trades --open-only

# Reconcile with IBKR
python -m src.cli.main reconcile-positions
```

---

## Complete Workflow

### Phase 1: Sunday Evening (Preparation)

**Time Required:** 15-30 minutes
**Objective:** Screen candidates, validate opportunities, stage for Monday execution

#### Step 1: Export from Barchart

1. Go to Barchart.com
2. Run your saved naked put screener
3. Export results as CSV
4. Save to your project directory

#### Step 2: Run Sunday Session

```bash
python -m src.cli.main sunday-session --file "naked-put-screener-2026-02-03.csv"
```

**What This Does:**
- Imports opportunities from Barchart CSV
- Fetches live stock prices from IBKR
- Validates against your strategy criteria (OTM%, premium, DTE)
- Calculates optimal limit prices using bid-mid ratio
- Determines contract quantities based on margin budget
- Saves validated opportunities to database

**Output:**
```
Sunday Session - Barchart Import & Validation

✓ Loaded 45 opportunities from Barchart
✓ Connected to IBKR
✓ Fetched live quotes for 45 symbols
✓ Validated 27 candidates meet criteria
✓ Calculated limit prices and quantities
✓ Saved 27 staged opportunities

Summary:
  Total from Barchart: 45
  Passed validation: 27
  Rejected: 18
  Total margin required: $45,230
  Account margin available: $50,000
```

#### Step 3: Review Staged Trades

```bash
python -m src.cli.main show-staged
```

This shows all opportunities ready for Monday execution with details on strike, premium, OTM%, margin, etc.

#### Step 4: Validate Specific Trades (Optional)

```bash
# Validate all staged trades
python -m src.cli.main validate-staged

# Validate specific symbol
python -m src.cli.main validate-staged --symbol AAPL
```

### Phase 2: Monday Morning (Execution)

**Time Required:** Automatic (9:15 AM - 10:30 AM)
**Objective:** Execute validated opportunities at optimal timing

#### Execution Timeline (Phase D Two-Tier)

**9:15 AM - Stage 1: Pre-Market Validation**
- Re-check stock prices against staged values
- Auto-adjust limit prices if stock moved <5%
- Mark stale if stock moved >10%

**9:28 AM - Stage 2: Quote Refresh**
- Fetch live option quotes
- Recalculate limit prices from fresh bid/ask
- Update execution_limit_price in database
- No validation gate - trades proceed to execution

**~9:29 AM - Adaptive Strike Selection**
- For each confirmed trade, pull the live IBKR option chain
- Fetch Greeks (delta, IV, gamma, theta) for candidate strikes
- Select the strike closest to target delta (default 0.20 +/- 0.05)
- Validate: premium >= floor, OTM% >= minimum, spread OK, liquidity OK
- Update the staged opportunity with the new strike, premium, and Greeks
- Falls back to the original OTM%-based strike if no delta match found

**9:30:00 AM - Tier 1: Rapid-Fire Execution**
- Submit ALL orders in parallel (<3 seconds)
- Use IBKR Adaptive algorithm for smart execution

**9:30-9:40 AM - Fill Monitoring (10 min window)**
- Monitor order fills every 2 seconds
- Detect partial fills and cancel/replace for remainder
- Progressive limit adjustment every 60 seconds (-$0.01 per step, max 5 steps)
- Never adjusts below the premium floor
- After window expires, leaves unfilled orders working as DAY orders

**9:45-10:30 AM - Tier 2: Condition-Based Retry**
- Monitor unfilled orders
- Check VIX and spread conditions
- Adjust limit prices when favorable
- Retry execution intelligently

**10:30 AM - Reconciliation**
- Generate execution report
- Update database with fills
- Log all outcomes

#### Execution Modes

**Autonomous Mode** (Full Automation)
```bash
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes
```
- No human intervention required
- Executes entire workflow automatically
- Best for: Production trading after testing

**Supervised Mode** (Auto-Execute, Manual Review)
```bash
python -m src.cli.main execute-two-tier --mode=supervised --live
```
- Executes automatically
- Pauses for report review before reconciliation
- Best for: Confidence building phase

**Hybrid Mode** (Manual Approval)
```bash
python -m src.cli.main execute-two-tier --mode=hybrid --live
```
- Requires approval at each stage
- Full control over execution
- Best for: Initial testing, uncertain conditions

**Dry-Run Mode** (Simulation)
```bash
python -m src.cli.main execute-two-tier --mode=autonomous --dry-run
```
- Simulates entire workflow without real orders
- Best for: Testing, development

### Phase 3: Post-Execution Management

#### Immediate Verification

```bash
# Check executed trades
python -m src.cli.main list-trades --open-only

# Reconcile positions with IBKR
python -m src.cli.main reconcile-positions
```

#### Import Orphan Positions (If Needed)

If you manually entered positions or reconciliation shows discrepancies:

```bash
# Preview what would be imported
python -m src.cli.main import-positions --dry-run

# Actually import
python -m src.cli.main import-positions --live
```

#### Daily Monitoring

```bash
# Monitor open positions
python -m src.cli.main monitor

# Snapshot positions for analysis
python -m src.cli.main snapshot-positions
```

---

## Step-by-Step Guides

### Guide 1: First-Time Setup

```bash
# 1. Initialize database and config
python -m src.cli.main init

# 2. Verify IBKR connection
python -m src.cli.main test-ibkr

# 3. Check market status
python -m src.cli.main market-status

# 4. Get a test quote
python -m src.cli.main quote AAPL

# 5. View system status
python -m src.cli.main status
```

### Guide 2: Importing Barchart Candidates

```bash
# Basic import
python -m src.cli.main sunday-session --file candidates.csv

# Import with custom validation
python -m src.cli.main sunday-session \
  --file candidates.csv \
  --min-premium 0.40 \
  --max-dte 14

# Dry run (don't save to database)
python -m src.cli.main sunday-session \
  --file candidates.csv \
  --dry-run
```

### Guide 3: Managing Staged Trades

```bash
# View all staged trades
python -m src.cli.main show-staged

# View staged trades for specific symbols
python -m src.cli.main show-staged --symbols AAPL MSFT GOOGL

# Validate staged trades
python -m src.cli.main validate-staged

# Cancel specific staged trade
python -m src.cli.main cancel-staged --id 1

# Cancel all staged trades
python -m src.cli.main cancel-staged --all
```

### Guide 4: Executing Trades

```bash
# Test with dry-run first
python -m src.cli.main execute-two-tier --mode=hybrid --dry-run

# Execute with manual approval
python -m src.cli.main execute-two-tier --mode=hybrid --live

# Execute with supervision
python -m src.cli.main execute-two-tier --mode=supervised --live

# Full automation
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes
```

### Guide 5: Position Management

```bash
# List all trades
python -m src.cli.main list-trades

# List open positions only
python -m src.cli.main list-trades --open-only

# List closed trades
python -m src.cli.main list-trades --closed-only

# Filter by symbol
python -m src.cli.main list-trades --symbol AAPL

# Show recent trades (last 7 days)
python -m src.cli.main list-trades --days 7

# Limit results
python -m src.cli.main list-trades --limit 10
```

### Guide 6: Reconciliation

```bash
# Reconcile positions
python -m src.cli.main reconcile-positions

# Sync orders with IBKR
python -m src.cli.main sync-orders

# Sync orders for specific date
python -m src.cli.main sync-orders --date 2026-02-03

# Import orphan positions
python -m src.cli.main import-positions --dry-run  # Preview
python -m src.cli.main import-positions --live      # Import
```

### Guide 7: Monitoring and Analysis

```bash
# Real-time position monitoring
python -m src.cli.main monitor

# Snapshot current positions
python -m src.cli.main snapshot-positions

# Analyze performance
python -m src.cli.main analyze

# View learning statistics
python -m src.cli.main learning-stats
```

---

## TAAD: Trade Archaeology & Alpha Discovery

TAAD is the historical trade import and analysis system. It imports your actual IBKR trade history, matches entry/exit pairs, and provides reports for verification and analysis.

### Overview

TAAD uses the IBKR Flex Query Web Service to fetch your trade execution records. It:

1. **Imports** raw execution records from IBKR (EXECUTION-level detail)
2. **Matches** Sell-to-Open (STO) entries with their corresponding Buy-to-Close (BTC) or expiration exits
3. **Reports** matched trade lifecycles with P&L for verification
4. **Analyzes** gaps in coverage and unmatched records

### Prerequisites

- IBKR Flex Query configured (see [Flex Query Setup](#flex-query-setup) below)
- Flex Query credentials in `.env`:
  ```bash
  IBKR_FLEX_TOKEN_1=your_flex_token
  IBKR_FLEX_QUERY_ID_1=your_query_id
  IBKR_FLEX_ACCOUNT_1=YOUR_ACCOUNT
  ```

### Flex Query Setup

1. Log into IBKR Account Management
2. Go to **Reports/Tax Docs** → **Flex Queries**
3. Create a new **Trade Confirmation Flex Query** with:
   - **Sections:** Trade Confirmations
   - **Delivery Configuration:** XML
   - **Date Period:** Last 30 days (or custom)
   - **Level of Detail:** Execution
4. Note the **Query ID** from the query list
5. Go to **Settings** → **FlexWeb Service** to get your **Token**
6. Add both to your `.env` file

### Guide 8: Importing Trade History

```bash
# Import from IBKR Flex Query (auto-fetches report)
python -m src.cli.main taad-import --account YOUR_ACCOUNT

# Import from a saved XML file
python -m src.cli.main taad-import --account YOUR_ACCOUNT --xml-file flex_report.xml

# Dry run (parse and display without saving)
python -m src.cli.main taad-import --account YOUR_ACCOUNT --dry-run

# Import without running trade matching
python -m src.cli.main taad-import --account YOUR_ACCOUNT --no-match
```

**What happens during import:**
1. Fetches the Flex Query XML report from IBKR (or reads local file)
2. Parses EXECUTION-level records only (Australian DD/MM/YYYY date format)
3. Deduplicates by IBKR execution ID (safe to re-run)
4. Stores all raw data as JSONB for future analysis
5. Matches STO/BTC pairs and expired positions into trade lifecycles

### Guide 9: Reviewing Imported Trades

```bash
# View all matched trade lifecycles with P&L
python -m src.cli.main taad-report

# Filter by symbol
python -m src.cli.main taad-report --symbol SLV

# Filter by account
python -m src.cli.main taad-report --account YOUR_ACCOUNT

# Sort by P&L (worst first)
python -m src.cli.main taad-report --sort pnl

# Sort grouped by symbol with subtotals
python -m src.cli.main taad-report --sort symbol

# Show unmatched records
python -m src.cli.main taad-report --unmatched

# Show all raw import records
python -m src.cli.main taad-report --raw
```

**Report output includes:**
- Entry/exit dates, symbols, strikes, expiries
- Entry and exit premiums
- Exit type (expiration or buy_to_close)
- Days held
- Gross P&L, commissions, and net P&L
- Per-symbol breakdown with win rates
- Summary panel with totals

### Guide 10: Checking Import Status and Gaps

```bash
# View import session history
python -m src.cli.main taad-status

# Filter by account
python -m src.cli.main taad-status --account YOUR_ACCOUNT

# Show last 20 sessions
python -m src.cli.main taad-status --limit 20

# Identify coverage gaps and data quality issues
python -m src.cli.main taad-gaps

# Gaps for specific account
python -m src.cli.main taad-gaps --account YOUR_ACCOUNT
```

**Gap analysis shows:**
- Date coverage from each import session
- Calendar gaps between sessions (missing periods)
- Unmatched option records (open STOs, orphan BTCs)
- Non-option unmatched records (CASH entries, stock trades)
- Match quality summary (confidence scores, match types)

### TAAD Workflow Example

```bash
# 1. First import: fetch last 30 days of history
python -m src.cli.main taad-import --account YOUR_ACCOUNT

# 2. Verify the import
python -m src.cli.main taad-status

# 3. Review matched trades against your IBKR statements
python -m src.cli.main taad-report

# 4. Check for any issues
python -m src.cli.main taad-gaps

# 5. Filter to check specific symbols
python -m src.cli.main taad-report --symbol CRWV --sort symbol

# 6. Re-import is safe (deduplicates by execution ID)
python -m src.cli.main taad-import --account YOUR_ACCOUNT

# 7. Promote matched trades into public.trades for enrichment
python -m src.cli.main taad-promote --dry-run   # preview first
python -m src.cli.main taad-promote              # promote for real

# 8. Enrich promoted trades with market context
python -m src.cli.main taad-enrich --limit 10    # test with a few
python -m src.cli.main taad-enrich               # enrich all
```

### Trade Promotion

After importing and verifying your trade history, the **promote** step converts matched trade lifecycles from the import schema into `public.trades` records. This is required before enrichment or learning analysis can use the data.

```bash
# Preview (no database changes)
python -m src.cli.main taad-promote --dry-run

# Promote all matched trades
python -m src.cli.main taad-promote

# Promote only a specific account
python -m src.cli.main taad-promote --account YOUR_ACCOUNT
```

**Key points:**
- Promoted trades are tagged with `trade_source='ibkr_import'` to distinguish them from live trades
- All promoted trades are closed (they have exit dates) — they never appear as open positions
- Idempotent: safe to re-run; already-promoted trades are skipped
- After promotion, run `taad-enrich` to populate entry/exit snapshots with market context

### TAAD Configuration Reference

```bash
# .env settings for TAAD (up to 3 accounts supported)

# Account 1
IBKR_FLEX_TOKEN_1=your_token_here
IBKR_FLEX_QUERY_ID_1=your_query_id
IBKR_FLEX_ACCOUNT_1=YOUR_ACCOUNT

# Account 2 (optional)
IBKR_FLEX_TOKEN_2=another_token
IBKR_FLEX_QUERY_ID_2=another_query_id
IBKR_FLEX_ACCOUNT_2=U9876543

# Account 3 (optional)
IBKR_FLEX_TOKEN_3=third_token
IBKR_FLEX_QUERY_ID_3=third_query_id
IBKR_FLEX_ACCOUNT_3=U1122334
```

### TAAD Database Schema

TAAD stores data in a separate PostgreSQL `import` schema:

| Table | Purpose |
|-------|---------|
| `import.import_sessions` | Tracks each import batch with metadata |
| `import.ibkr_raw_imports` | Immutable raw IBKR execution records (JSONB) |
| `import.trade_matching_log` | Audit trail of STO/BTC pair matching |

After promotion (`taad-promote`), records flow into the public schema:

| Table | Purpose |
|-------|---------|
| `public.trades` | Promoted trades with `trade_source='ibkr_import'` |
| `public.trade_entry_snapshots` | Market context at entry (populated by `taad-enrich`) |
| `public.trade_exit_snapshots` | Market context at exit (populated by `taad-enrich`) |

---

## NakedTrader: Daily SPX/XSP/SPY Options

NakedTrader is a mechanical delta-targeted put selling strategy for index options, based on the WealthyOption/BigERN system (see `docs/research/spx-options-trading-rulebook-2026-02-17.md` for the full rulebook). It operates independently from the weekly Barchart-based workflow described above.

**Core approach:** Sell 1-4 DTE puts at delta 5.5-7.5 (targeting 6.5) on SPX, XSP, or SPY. Collect premium, set a 70% profit-take GTC bracket order, repeat daily.

### Quick Start

```bash
# Dry run (simulate, no orders)
nakedtrader sell XSP --dry-run

# Live paper trade with confirmation prompt
nakedtrader sell XSP --live

# Live paper trade, skip confirmation
nakedtrader sell XSP --live --yes

# Override contracts and delta target
nakedtrader sell SPX --contracts 2 --delta 0.08 --dry-run
```

### Configuration

All NakedTrader parameters are defined in `config/daily_spx_options.yaml`:

- **instrument** -- symbol (SPX/XSP/SPY), contracts, max contracts
- **strike** -- delta_min, delta_max, delta_target
- **dte** -- min/max DTE, prefer shortest expiration
- **premium** -- minimum premium to accept
- **exit** -- profit target %, profit target floor, stop-loss toggle and multiplier
- **execution** -- wait for open, open delay, order type, fill timeout, latest entry time
- **watch** -- refresh interval, show Greeks toggle

CLI flags override config values per-run (e.g. `--delta 0.08`, `--contracts 2`, `--stop-loss`).

### Commands

| Command | Description |
|---------|-------------|
| `nakedtrader sell SYMBOL` | Execute daily put trade (dry-run by default, `--live` to place orders) |
| `nakedtrader sell-watch` | Monitor open naked put positions with live P&L and bracket status |
| `nakedtrader sell-status` | Show trade history and performance summary (offline, no IBKR needed) |

### Workflow

1. **Morning:** Run `nakedtrader sell XSP --live --yes` after market open (or let `wait_for_open` handle timing)
2. **During day:** Run `nakedtrader sell-watch` to monitor bracket order fills
3. **Review:** Run `nakedtrader sell-status` to check win rate and P&L history

---

## Command Reference

### System Commands

#### `init`
Initialize the trading system (database, config, directories).

```bash
python -m src.cli.main init
```

**Options:** None

**Use Case:** First-time setup, database reset recovery

---

#### `test-ibkr`
Test connection to Interactive Brokers TWS/Gateway.

```bash
python -m src.cli.main test-ibkr
```

**Options:** None

**Returns:** Connection status, account info, positions count

---

#### `version`
Display application version and system information.

```bash
python -m src.cli.main version
```

**Options:** None

---

#### `status`
Show system status including database, IBKR connection, open positions.

```bash
python -m src.cli.main status
```

**Options:** None

---

### Market Data Commands

#### `market-status`
Check if US markets are currently open.

```bash
python -m src.cli.main market-status
```

**Options:** None

**Returns:** Market open/closed status with next open/close time

---

#### `quote`
Get real-time quote for a stock.

```bash
python -m src.cli.main quote SYMBOL
```

**Arguments:**
- `symbol` - Stock ticker (required)

**Example:**
```bash
python -m src.cli.main quote AAPL
```

---

#### `option-chain`
Fetch and display option chain for a symbol.

```bash
python -m src.cli.main option-chain SYMBOL [OPTIONS]
```

**Arguments:**
- `symbol` - Stock ticker (required)

**Options:**
- `--expiration, -e DATE` - Specific expiration date (YYYY-MM-DD)
- `--days, -d NUM` - Days to expiration (default: 7-21)
- `--strike, -s PRICE` - Specific strike price
- `--put-only` - Show only puts (default)
- `--call-only` - Show only calls

**Examples:**
```bash
# Show puts expiring in 7-21 days
python -m src.cli.main option-chain AAPL

# Specific expiration
python -m src.cli.main option-chain AAPL -e 2026-02-14

# Specific strike
python -m src.cli.main option-chain AAPL -s 150
```

---

### Sunday Session Commands

#### `sunday-session`
Process Barchart CSV file, validate candidates, stage opportunities.

```bash
python -m src.cli.main sunday-session --file FILE [OPTIONS]
```

**Required Options:**
- `--file, -f PATH` - Path to Barchart CSV file

**Optional Flags:**
- `--dry-run` - Simulate without saving to database (default: False)
- `--validate-only` - Only validate, don't stage (default: False)

**Optional Filters:**
- `--min-premium MIN` - Override minimum premium (default: from .env)
- `--max-premium MAX` - Override maximum premium (default: from .env)
- `--min-otm PCT` - Override minimum OTM% (default: from .env)
- `--max-otm PCT` - Override maximum OTM% (default: from .env)
- `--min-dte DAYS` - Override minimum DTE (default: from .env)
- `--max-dte DAYS` - Override maximum DTE (default: from .env)

**Examples:**
```bash
# Basic usage
python -m src.cli.main sunday-session --file barchart.csv

# Dry run (test without saving)
python -m src.cli.main sunday-session --file barchart.csv --dry-run

# Custom filters
python -m src.cli.main sunday-session \
  --file barchart.csv \
  --min-premium 0.40 \
  --max-dte 14 \
  --min-otm 0.15
```

---

#### `show-staged`
Display all staged opportunities ready for execution.

```bash
python -m src.cli.main show-staged [OPTIONS]
```

**Options:**
- `--symbols, -s SYM1 SYM2...` - Filter by symbols
- `--sort-by FIELD` - Sort by: margin, premium, otm, dte (default: margin)
- `--limit, -l NUM` - Limit results (default: 50)

**Examples:**
```bash
# Show all staged
python -m src.cli.main show-staged

# Filter by symbols
python -m src.cli.main show-staged --symbols AAPL MSFT

# Sort by premium
python -m src.cli.main show-staged --sort-by premium
```

---

#### `validate-staged`
Re-validate staged opportunities with live market data.

```bash
python -m src.cli.main validate-staged [OPTIONS]
```

**Options:**
- `--symbol, -s SYMBOL` - Validate specific symbol only
- `--update-prices` - Update limit prices based on current quotes

**Examples:**
```bash
# Validate all
python -m src.cli.main validate-staged

# Validate one symbol
python -m src.cli.main validate-staged --symbol AAPL

# Validate and update prices
python -m src.cli.main validate-staged --update-prices
```

---

#### `cancel-staged`
Cancel/remove staged opportunities.

```bash
python -m src.cli.main cancel-staged [OPTIONS]
```

**Options:**
- `--id, -i ID` - Cancel specific opportunity by ID
- `--symbol, -s SYMBOL` - Cancel all for symbol
- `--all` - Cancel all staged opportunities
- `--yes, -y` - Skip confirmation

**Examples:**
```bash
# Cancel by ID
python -m src.cli.main cancel-staged --id 1

# Cancel by symbol
python -m src.cli.main cancel-staged --symbol AAPL

# Cancel all (with confirmation)
python -m src.cli.main cancel-staged --all

# Cancel all (skip confirmation)
python -m src.cli.main cancel-staged --all --yes
```

---

### Execution Commands

#### `execute-two-tier`
Execute staged trades with Phase D two-tier workflow.

```bash
python -m src.cli.main execute-two-tier [OPTIONS]
```

**Required Options:**
- `--mode, -m MODE` - Execution mode: `hybrid`, `supervised`, or `autonomous`

**Execution Flags:**
- `--dry-run` - Simulate execution (default)
- `--live` - Execute real orders
- `--yes, -y` - Skip confirmations (autonomous mode)

**Tier 2 Options:**
- `--tier2-enabled / --no-tier2` - Enable/disable Tier 2 retry (default: enabled)

**Examples:**
```bash
# Dry run with manual approval
python -m src.cli.main execute-two-tier --mode=hybrid --dry-run

# Live execution with supervision
python -m src.cli.main execute-two-tier --mode=supervised --live

# Full automation
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes

# Autonomous without Tier 2
python -m src.cli.main execute-two-tier \
  --mode=autonomous \
  --live \
  --yes \
  --no-tier2
```

**Automation Modes:**

- **`hybrid`**: Manual approval at each stage (9:15, 9:28, 9:30, Tier 2)
- **`supervised`**: Auto-execute, pause for report review
- **`autonomous`**: Fully automated, alerts only on errors

---

#### `execute-staged`
Legacy command: Execute specific staged trades (pre-Phase D).

```bash
python -m src.cli.main execute-staged [OPTIONS]
```

**Options:**
- `--ids, -i ID1 ID2...` - Specific opportunity IDs
- `--symbols, -s SYM1 SYM2...` - Specific symbols
- `--all` - Execute all staged
- `--dry-run` - Simulate without real orders
- `--yes, -y` - Skip confirmation

**Note:** Prefer `execute-two-tier` for Phase D workflow.

---

### Trade Management Commands

#### `list-trades`
List trades from database with filtering.

```bash
python -m src.cli.main list-trades [OPTIONS]
```

**Options:**
- `--open-only` - Show only open positions
- `--closed-only` - Show only closed trades
- `--days, -d NUM` - Show trades from last N days
- `--symbol, -s SYMBOL` - Filter by symbol
- `--limit, -l NUM` - Maximum results (default: 50)

**Examples:**
```bash
# List all trades
python -m src.cli.main list-trades

# Open positions only
python -m src.cli.main list-trades --open-only

# Closed trades
python -m src.cli.main list-trades --closed-only

# Last 7 days
python -m src.cli.main list-trades --days 7

# Specific symbol
python -m src.cli.main list-trades --symbol AAPL

# Limit results
python -m src.cli.main list-trades --open-only --limit 10
```

---

#### `add-trade`
Manually add a trade to the database.

```bash
python -m src.cli.main add-trade [OPTIONS]
```

**Options:**
- `--symbol, -s SYMBOL` - Stock ticker (required)
- `--strike PRICE` - Strike price (required)
- `--expiration, -e DATE` - Expiration date YYYY-MM-DD (required)
- `--contracts, -c NUM` - Number of contracts (required)
- `--premium PRICE` - Entry premium (required)
- `--option-type TYPE` - PUT or CALL (default: PUT)

**Example:**
```bash
python -m src.cli.main add-trade \
  --symbol AAPL \
  --strike 150 \
  --expiration 2026-02-14 \
  --contracts 5 \
  --premium 0.45
```

---

#### `monitor`
Real-time monitoring of open positions.

```bash
python -m src.cli.main monitor [OPTIONS]
```

**Options:**
- `--refresh, -r SECONDS` - Refresh interval (default: 30)
- `--once` - Run once without loop

**Example:**
```bash
# Continuous monitoring (refresh every 30s)
python -m src.cli.main monitor

# Custom refresh interval
python -m src.cli.main monitor --refresh 60

# One-time snapshot
python -m src.cli.main monitor --once
```

---

#### `snapshot-positions`
Take a snapshot of current positions for historical analysis.

```bash
python -m src.cli.main snapshot-positions
```

**Options:** None

**Use Case:** Daily EOD snapshots for performance tracking

---

### Reconciliation Commands

#### `reconcile-positions`
Reconcile and sync positions between database and IBKR.

**Default behavior:** Preview only (dry-run mode, safe, read-only)
**Live mode:** Actually applies fixes to sync database with IBKR

```bash
# Preview discrepancies (default - safe, read-only)
python -m src.cli.main reconcile-positions

# Apply fixes to sync database with IBKR
python -m src.cli.main reconcile-positions --live
```

**Options:**
- `--dry-run` - Preview only, no changes (default)
- `--live` - Apply fixes to database

**What it does:**
1. **Reports discrepancies** between database and IBKR:
   - Quantity mismatches (e.g., DB shows 5 contracts, IBKR shows 9)
   - Positions in IBKR not in database
   - Positions in database not in IBKR

2. **In --live mode, automatically fixes**:
   - ✅ Imports positions from IBKR that aren't in database
   - ✅ Closes positions in database that aren't in IBKR
   - ✅ Updates quantity mismatches to match IBKR

**Example workflow:**
```bash
# 1. First, preview what needs fixing
python -m src.cli.main reconcile-positions

# 2. Review the report

# 3. If it looks correct, apply the fixes
python -m src.cli.main reconcile-positions --live
```

**Safety:** Always defaults to dry-run mode. You must explicitly use `--live` to apply changes.

---

#### `sync-orders`
Sync order status, fills, and commissions with IBKR.

```bash
python -m src.cli.main sync-orders [OPTIONS]
```

**Options:**
- `--date, -d DATE` - Sync specific date YYYY-MM-DD (default: today)
- `--include-filled` - Include filled orders (default: True)
- `--import-orphans` - Import orphan orders found in IBKR

**Examples:**
```bash
# Sync today's orders
python -m src.cli.main sync-orders

# Sync specific date
python -m src.cli.main sync-orders --date 2026-02-03

# Sync and import orphans
python -m src.cli.main sync-orders --import-orphans
```

---

#### `import-positions`
Import orphan positions from IBKR into database.

```bash
python -m src.cli.main import-positions [OPTIONS]
```

**Options:**
- `--dry-run` - Preview import without saving (default)
- `--live` - Actually import positions

**Examples:**
```bash
# Preview what would be imported
python -m src.cli.main import-positions --dry-run

# Import positions
python -m src.cli.main import-positions --live
```

**Use Case:** Import manually entered positions or recover from database issues

---

### Analysis Commands

#### `analyze`
Analyze trading performance with detailed statistics.

```bash
python -m src.cli.main analyze [OPTIONS]
```

**Options:**
- `--days, -d NUM` - Analyze last N days (default: 30)
- `--symbol, -s SYMBOL` - Analyze specific symbol
- `--export, -e FILE` - Export results to CSV

**Example:**
```bash
# Analyze last 30 days
python -m src.cli.main analyze

# Analyze last 90 days
python -m src.cli.main analyze --days 90

# Analyze specific symbol
python -m src.cli.main analyze --symbol AAPL

# Export to CSV
python -m src.cli.main analyze --export results.csv
```

---

#### `learning-stats`
View learning engine statistics and patterns detected.

```bash
python -m src.cli.main learning-stats
```

**Options:** None

**Shows:**
- Patterns detected
- Confidence scores
- Learning events
- Parameter adjustments

---

### TAAD Commands (Trade Archaeology & Alpha Discovery)

#### `taad-import`
Import trades from IBKR Flex Query into the TAAD database.

```bash
python -m src.cli.main taad-import [OPTIONS]
```

**Options:**
- `--account, -a ACCOUNT` - IBKR account ID (uses first configured if omitted)
- `--xml-file, -f FILE` - Import from local XML file instead of Flex Query API
- `--no-match` - Skip trade matching after import
- `--dry-run` - Parse and display records without saving to database

**Examples:**
```bash
# Auto-fetch from IBKR
python -m src.cli.main taad-import --account YOUR_ACCOUNT

# Import from local XML file
python -m src.cli.main taad-import -a YOUR_ACCOUNT -f flex_report.xml

# Preview without saving
python -m src.cli.main taad-import --dry-run
```

---

#### `taad-report`
Display matched trade lifecycles with P&L for verification.

```bash
python -m src.cli.main taad-report [OPTIONS]
```

**Options:**
- `--account, -a ACCOUNT` - Filter by account ID
- `--symbol, -s SYMBOL` - Filter by underlying symbol (e.g., AAPL)
- `--unmatched, -u` - Show unmatched records
- `--raw` - Show all raw import records
- `--sort FIELD` - Sort by: date (default), symbol, pnl

**Examples:**
```bash
# View all matched trades
python -m src.cli.main taad-report

# Filter by symbol
python -m src.cli.main taad-report --symbol SLV

# Sort by P&L
python -m src.cli.main taad-report --sort pnl

# Show grouped by symbol
python -m src.cli.main taad-report --sort symbol

# Include unmatched records
python -m src.cli.main taad-report --unmatched
```

**Output includes:**
- Matched trade lifecycles table (entry date, symbol, strike, expiry, qty, premiums, exit type, days held, P&L)
- Summary panel (total trades, win rate, gross P&L, commissions, net P&L)
- Per-symbol breakdown (trades, win rate, P&L per symbol)

---

#### `taad-status`
Show recent TAAD import sessions and database statistics.

```bash
python -m src.cli.main taad-status [OPTIONS]
```

**Options:**
- `--account, -a ACCOUNT` - Filter by account ID
- `--limit, -n NUM` - Number of recent sessions to show (default: 10)

**Examples:**
```bash
# Show recent sessions
python -m src.cli.main taad-status

# Filter by account
python -m src.cli.main taad-status --account YOUR_ACCOUNT
```

**Returns:** Session table (ID, account, status, source, date range, record counts) plus database totals

---

#### `taad-gaps`
Identify gaps and data quality issues in imported TAAD data.

```bash
python -m src.cli.main taad-gaps [OPTIONS]
```

**Options:**
- `--account, -a ACCOUNT` - Filter by account ID

**Examples:**
```bash
# Full gap analysis
python -m src.cli.main taad-gaps

# For specific account
python -m src.cli.main taad-gaps --account YOUR_ACCOUNT
```

**Shows:**
- Import coverage (date ranges per session)
- Calendar gaps between sessions
- Unmatched option records (open STOs, orphan BTCs)
- Non-option unmatched records
- Match quality summary (types, confidence scores)

---

#### `taad-promote`
Promote matched trade lifecycles from import schema into `public.trades` for enrichment and learning.

```bash
python -m src.cli.main taad-promote [OPTIONS]
```

**Options:**
- `--account, -a ACCOUNT` - Filter by account ID
- `--dry-run` - Preview without making changes

**Examples:**
```bash
# Preview what would be promoted
python -m src.cli.main taad-promote --dry-run

# Promote all matched trades
python -m src.cli.main taad-promote

# Promote only a specific account
python -m src.cli.main taad-promote --account YOUR_ACCOUNT
```

**Key behavior:**
- Creates Trade records with `trade_source='ibkr_import'`
- Idempotent: already-promoted trades are skipped (dedup by `ibkr_execution_id`)
- All promoted trades are closed — they never appear as open positions
- Links `trade_matching_log.matched_trade_id` back to the promoted trade

---

#### `taad-enrich`
Enrich trades with historical market context (stock prices, technicals, VIX, sector data, IV).

```bash
python -m src.cli.main taad-enrich [OPTIONS]
```

**Options:**
- `--account, -a ACCOUNT` - Filter by account ID
- `--symbol, -s SYMBOL` - Filter by underlying symbol
- `--force` - Re-enrich already-enriched trades
- `--dry-run` - Preview without making changes
- `--with-ibkr` - Include IBKR historical data (requires TWS)
- `--with-scrape` - Include Barchart Premier scraping
- `--limit, -n NUM` - Max trades to enrich (0 = all)

**Examples:**
```bash
# Enrich a sample
python -m src.cli.main taad-enrich --limit 10

# Enrich all trades for a symbol
python -m src.cli.main taad-enrich --symbol AAPL

# Full enrichment with Barchart data
python -m src.cli.main taad-enrich --with-scrape
```

---

### Utility Commands

#### `scan`
Run option scanner (legacy - use Barchart instead).

```bash
python -m src.cli.main scan [OPTIONS]
```

**Options:**
- `--symbols, -s SYM1 SYM2...` - Scan specific symbols
- `--max-results, -m NUM` - Limit results

**Note:** Barchart screening is recommended over this command.

---

#### `db-reset`
Reset database (DANGEROUS - deletes all data).

```bash
python -m src.cli.main db-reset [OPTIONS]
```

**Options:**
- `--yes, -y` - Skip confirmation

**WARNING:** This deletes all trades, positions, and learning data.

---

#### `emergency-stop`
Emergency stop - cancel all pending orders.

```bash
python -m src.cli.main emergency-stop
```

**Options:** None

**Use Case:** Market emergency, stop all activity immediately

---

#### `web`
Launch web interface (if available).

```bash
python -m src.cli.main web [OPTIONS]
```

**Options:**
- `--port, -p PORT` - Port number (default: 8000)
- `--host, -h HOST` - Host address (default: 127.0.0.1)

---

## Examples

### Example 1: Complete Sunday-Monday Workflow

**Sunday 8:00 PM:**
```bash
# 1. Download Barchart CSV
# (manually from website)

# 2. Process candidates
python -m src.cli.main sunday-session \
  --file "naked-put-screener-2026-02-02.csv"

# 3. Review staged trades
python -m src.cli.main show-staged

# 4. Validate one more time before bed
python -m src.cli.main validate-staged

# Output shows: 27 trades staged, $45,230 margin required
```

**Monday 9:00 AM:**
```bash
# 1. Check market status
python -m src.cli.main market-status

# 2. Execute with full automation
python -m src.cli.main execute-two-tier \
  --mode=autonomous \
  --live \
  --yes

# System runs automatically:
# - 9:15 AM: Stage 1 validation
# - 9:28 AM: Stage 2 quote refresh
# - 9:29 AM: Adaptive strike selection (delta-based)
# - 9:30 AM: Tier 1 execution (all 27 orders in 2.3 seconds)
# - 9:30-9:40: Fill monitoring (partial fills, progressive adjustments)
# - 9:45-10:30: Tier 2 condition-based retry (4 unfilled orders)
# - 10:30 AM: Final report (23 filled, 4 working DAY orders)
```

**Monday 10:35 AM:**
```bash
# 3. Check results
python -m src.cli.main list-trades --open-only

# 4. Reconcile
python -m src.cli.main reconcile-positions

# All positions match! ✓
```

---

### Example 2: Testing Before Going Live

```bash
# 1. Sunday dry run
python -m src.cli.main sunday-session \
  --file candidates.csv \
  --dry-run

# 2. Monday dry run (simulate execution)
python -m src.cli.main execute-two-tier \
  --mode=hybrid \
  --dry-run

# 3. Review logs
tail -f logs/app.log

# 4. Once confident, go live
python -m src.cli.main execute-two-tier \
  --mode=supervised \
  --live
```

---

### Example 3: Manual Trade Entry

```bash
# Manually entered a trade in TWS, now add to database
python -m src.cli.main add-trade \
  --symbol NVDA \
  --strike 500 \
  --expiration 2026-02-14 \
  --contracts 3 \
  --premium 1.25

# Or import from IBKR
python -m src.cli.main import-positions --live

# Verify it's there
python -m src.cli.main list-trades --symbol NVDA
```

---

### Example 4: Recovering from Issues

```bash
# Database and IBKR don't match
python -m src.cli.main reconcile-positions

# Shows: 4 positions in IBKR not in database

# Import them
python -m src.cli.main import-positions --dry-run  # Preview
python -m src.cli.main import-positions --live      # Import

# Reconcile again
python -m src.cli.main reconcile-positions

# All positions match! ✓
```

---

### Example 5: Advanced Filtering

```bash
# Show high-premium trades only
python -m src.cli.main show-staged --sort-by premium

# Cancel low-OTM trades
python -m src.cli.main show-staged  # Note IDs of <15% OTM
python -m src.cli.main cancel-staged --id 1 --id 5 --id 12

# Re-validate remaining
python -m src.cli.main validate-staged

# Execute only tech stocks
python -m src.cli.main execute-staged \
  --symbols AAPL MSFT GOOGL NVDA \
  --live
```

---

### Example 6: Daily Monitoring

```bash
# Morning: Check positions
python -m src.cli.main monitor --once

# Afternoon: Update snapshot
python -m src.cli.main snapshot-positions

# EOD: Reconcile
python -m src.cli.main reconcile-positions
python -m src.cli.main sync-orders
```

---

## Troubleshooting

### Issue: IBKR Connection Failed

**Error:** `Connection failed: Connection refused`

**Solutions:**
```bash
# 1. Check TWS/Gateway is running
# 2. Verify port in .env matches TWS (7497 paper, 7496 live)
# 3. Enable API in TWS: Configure > Settings > API > Enable ActiveX
# 4. Test connection
python -m src.cli.main test-ibkr
```

---

### Issue: No Staged Trades After Sunday Session

**Possible Causes:**
- All candidates rejected by validation
- Stock prices moved significantly
- IBKR quotes unavailable

**Solutions:**
```bash
# 1. Check validation criteria in .env
grep -E "OTM_|PREMIUM_|DTE_" .env

# 2. Re-run with relaxed filters
python -m src.cli.main sunday-session \
  --file candidates.csv \
  --min-premium 0.25 \
  --min-otm 0.10

# 3. Check IBKR market data permissions
python -m src.cli.main market-status
```

---

### Issue: Positions in IBKR Not in Database

**Error:** `reconcile-positions` shows discrepancies

**Solution:**
```bash
# Import orphan positions
python -m src.cli.main import-positions --dry-run  # Preview
python -m src.cli.main import-positions --live      # Import

# Verify
python -m src.cli.main reconcile-positions
```

---

### Issue: Order Execution Failed

**Error:** Order rejected or not filled

**Troubleshooting:**
```bash
# 1. Check order status
python -m src.cli.main sync-orders

# 2. Verify margin available
python -m src.cli.main status

# 3. Check market hours
python -m src.cli.main market-status

# 4. Review logs
tail -50 logs/app.log
```

---

### Issue: Database Corruption

**Symptoms:** SQL errors, missing data

**Recovery:**
```bash
# 1. Backup current database
cp data/databases/trades.db data/databases/trades.db.backup

# 2. Reset database
python -m src.cli.main db-reset --yes

# 3. Re-import from IBKR
python -m src.cli.main import-positions --live

# 4. Sync orders
python -m src.cli.main sync-orders
```

---

### Issue: Stale Limit Prices

**Symptom:** All orders unfilled in Tier 1

**Solution:**
```bash
# Before execution, validate and update prices
python -m src.cli.main validate-staged --update-prices

# Or rely on Tier 2 automatic adjustment
# (VIX drops, spreads tighten → limits adjusted)
```

---

## Best Practices

### Sunday Session

1. **Run Early (6-8 PM):** Gives time to review before bed
2. **Always Dry-Run First:** Test with `--dry-run` flag
3. **Review Staged Trades:** Use `show-staged` to verify
4. **Check Margin:** Ensure sufficient margin available
5. **Validate Before Sleep:** Run `validate-staged` at 10 PM

### Monday Execution

1. **Start Conservative:** Use `--mode=hybrid` or `--mode=supervised` initially
2. **Monitor First 5 Mondays:** Watch Tier 1 fill rates
3. **Trust Tier 2:** Don't manually intervene during 9:45-10:30 window
4. **Review Reports:** Always review execution reports
5. **Reconcile Daily:** Run `reconcile-positions` every afternoon

### Risk Management

1. **Respect Margin Limits:** Don't override calculated quantities
2. **Diversify Sectors:** Max 2-3 trades per sector
3. **OTM Range:** Stick to 15-25% OTM (sweet spot)
4. **DTE Range:** 7-14 days (optimal decay)
5. **Emergency Stop:** Know how to use `emergency-stop` command

### Database Hygiene

1. **Backup Weekly:** Copy `trades.db` to safe location
2. **Reconcile Daily:** Catch discrepancies early
3. **Snapshot Positions:** EOD snapshots for analysis
4. **Import Manually Entered:** Use `import-positions` immediately

### Performance Optimization

1. **Use Phase D Two-Tier:** Much better fill rates than legacy execution
2. **Trust Adaptive Algorithm:** IBKR's algo is smart
3. **Don't Penny-Pinch:** $0.01 difference is noise
4. **Let Tier 2 Work:** Condition-based retry > manual adjustment
5. **Review Learning Stats:** Check `learning-stats` monthly

### Automation Progression

**Week 1-2:** Hybrid mode (manual approval)
```bash
python -m src.cli.main execute-two-tier --mode=hybrid --live
```

**Week 3-4:** Supervised mode (auto-execute, review report)
```bash
python -m src.cli.main execute-two-tier --mode=supervised --live
```

**Week 5+:** Autonomous mode (full automation)
```bash
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes
```

### Monitoring Schedule

**Daily (10 AM):** Post-execution check
```bash
python -m src.cli.main list-trades --open-only
python -m src.cli.main reconcile-positions
```

**Daily (4 PM):** EOD snapshot
```bash
python -m src.cli.main snapshot-positions
```

**Weekly (Sunday 8 PM):** Prepare next week
```bash
python -m src.cli.main sunday-session --file new-candidates.csv
python -m src.cli.main show-staged
```

**Monthly:** Performance review
```bash
python -m src.cli.main analyze --days 30
python -m src.cli.main learning-stats
```

---

## End-to-End Logical Flow

This section describes the complete flow from finding trade candidates to monitoring open positions.

```
BARCHART SCREENING (Manual)
│   Export naked put screener results as CSV from Barchart.com
│   Columns: Symbol, Price, Strike, Exp Date, DTE, Bid, Delta, OI, etc.
│
▼
SUNDAY SESSION — sunday-session --file screener.csv
│   1. Parse Barchart CSV, extract candidate opportunities
│   2. Connect to IBKR, fetch live stock prices
│   3. Validate: OTM% range, premium floor, DTE range, liquidity
│   4. Calculate limit prices using bid-mid ratio
│   5. Determine contract quantities from margin budget
│   6. Save validated opportunities to database as STAGED
│
▼
REVIEW (Manual)
│   show-staged          → view all staged opportunities
│   validate-staged      → re-validate with fresh market data
│   cancel-staged --id N → remove unwanted candidates
│
▼
EXECUTE — execute-two-tier --mode=autonomous --live --yes
│
├─ 9:15 AM  Stage 1: Pre-Market Validation
│    Check stock prices vs staged values, adjust or mark stale
│
├─ 9:28 AM  Stage 2: Quote Refresh
│    Fetch live option quotes, recalculate limit prices
│
├─ ~9:29 AM  Adaptive Strike Selection (NEW)
│    For each trade: pull live option chain, fetch Greeks,
│    select strike closest to target delta (0.20), validate
│    boundaries, update staged opportunity with new strike
│
├─ 9:30 AM  Tier 1: Rapid-Fire Execution
│    Submit all orders in parallel (<3 seconds)
│
├─ 9:30-9:40  Fill Monitoring (NEW)
│    10-minute window: check fills every 2s, detect partial
│    fills, progressive limit adjustment (-$0.01/min, max 5),
│    leave unfilled as DAY orders after window
│
├─ 9:30 AM  Entry Snapshot Capture (at fill time)
│    86+ fields: Greeks, IV, technicals, market context
│    + strike_selection_method, original_strike, live_delta
│
├─ 9:45-10:30  Tier 2: Condition-Based Retry
│    Monitor VIX/spreads, adjust limits when favorable,
│    retry unfilled orders intelligently
│
└─ 10:30 AM  Final Reconciliation
     Generate execution report, save to database
│
▼
POST-EXECUTION
│   list-trades --open-only    → verify filled positions
│   reconcile-positions        → sync database with IBKR
│   sync-orders                → update order status/commissions
│
▼
DAILY MONITORING
    monitor                    → real-time position P&L
    snapshot-positions         → EOD snapshot for analysis
    auto-monitor --auto-exit   → continuous monitoring with exits
```

---

## Configuration Reference

Key settings in `.env`:

```bash
# Strategy Parameters
OTM_MIN=0.15                    # 15% minimum out-of-the-money
OTM_TARGET_PCT=0.20             # 20% target OTM
PREMIUM_MIN=0.30                # $0.30 minimum premium
PREMIUM_TARGET=0.40             # $0.40 target premium
DTE_MIN=5                       # 5 days minimum
DTE_TARGET=7                    # 7 days target
DTE_MAX=21                      # 21 days maximum

# Execution
LIMIT_BID_MID_RATIO=0.3         # 30% from bid to mid
USE_ADAPTIVE_ALGO=true          # IBKR Adaptive algorithm
USE_RAPID_FIRE=true             # Parallel execution

# Adaptive Strike Selection
ADAPTIVE_STRIKE_ENABLED=true    # Master switch for delta-based selection
STRIKE_TARGET_DELTA=0.20        # Target absolute delta (0.20 = 20-delta)
STRIKE_DELTA_TOLERANCE=0.05     # Acceptable +/- range around target
STRIKE_MIN_VOLUME=10            # Minimum option volume for candidates
STRIKE_MIN_OI=50                # Minimum open interest
STRIKE_MAX_CANDIDATES=5         # Max strikes to evaluate per symbol
STRIKE_FALLBACK_TO_OTM=true     # Fall back to OTM% if no delta match
MAX_EXECUTION_SPREAD_PCT=0.30   # Max bid-ask spread % for strike selection

# Fill Management
FILL_MONITOR_WINDOW_SECONDS=600 # 10-minute monitoring window
FILL_CHECK_INTERVAL=2.0         # Check fill status every 2 seconds
FILL_MAX_ADJUSTMENTS=5          # Max limit price adjustments
FILL_ADJUSTMENT_INCREMENT=0.01  # $ decrement per adjustment
FILL_ADJUSTMENT_INTERVAL=60     # Seconds between adjustments
FILL_PARTIAL_THRESHOLD=0.5      # >50% filled triggers remainder handling
FILL_LEAVE_WORKING=true         # Leave unfilled as DAY orders on timeout
PREMIUM_FLOOR=0.20              # Never adjust limit below this

# Phase D Two-Tier
AUTOMATION_MODE=autonomous      # hybrid, supervised, autonomous
TIER2_ENABLED=true              # Enable condition-based retry
TIER2_VIX_LOW=18                # VIX threshold for favorable
TIER2_VIX_HIGH=25               # VIX threshold for unfavorable
TIER2_MAX_SPREAD=0.08           # Maximum acceptable spread
TIER2_LIMIT_ADJUSTMENT=1.1      # 10% more aggressive in Tier 2

# Risk Management
ACCOUNT_USAGE_PCT=0.20          # Use 20% of account for margin
MAX_SECTOR_CONCENTRATION=10     # Max trades per sector
```

---

## Support & Resources

- **Documentation:** `/docs` directory
- **Logs:** `logs/app.log`
- **Database:** `data/databases/trades.db`
- **Issues:** GitHub Issues
- **CLI Help:** `python -m src.cli.main COMMAND --help`

---

**End of User Guide**
