# Operations Guide

## Complete Reference for Running the Trading System

**Document Version:** 1.5
**Last Updated:** February 18, 2026
**Status:** Consolidated from multiple docs

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [CLI Command Reference](#cli-command-reference)
3. [Scanning & Trade Selection](#scanning--trade-selection)
4. [Trade Execution](#trade-execution)
5. [Advanced Order Execution](#advanced-order-execution)
   - [Adaptive Strike Selection](#adaptive-strike-selection)
   - [Fill Management](#fill-management)
   - [End-to-End Logical Flow](#end-to-end-logical-flow)
6. [Position Monitoring](#position-monitoring)
7. [Learning Engine](#learning-engine)
8. [TAAD: Trade Archaeology & Alpha Discovery](#taad-trade-archaeology--alpha-discovery)
9. [NakedTrader Daily Workflow](#nakedtrader-daily-workflow)
10. [24/7 Operation](#247-operation)
11. [IBKR Client ID Assignments](#ibkr-client-id-assignments)
12. [Troubleshooting](#troubleshooting)

---

## Quick Start

### Prerequisites

1. **IBKR Gateway/TWS** running with API enabled (port 7497 for paper trading)
2. **Python environment** activated: `source venv/bin/activate`
3. **Configuration** in `.env` file (copy from `.env.example`)

### First-Time Setup

```bash
# Initialize database
python -m src.cli.main init

# Test IBKR connection
python -m src.cli.main test-ibkr

# Check system status
python -m src.cli.main status
```

### Daily Workflow

```bash
# 1. Import candidates from Barchart CSV
python -m src.cli.main scan --from-csv your_export.csv

# 2. Execute top trades (dry run first)
python -m src.cli.main trade --from-csv your_export.csv --dry-run

# 3. Execute for real
python -m src.cli.main trade --from-csv your_export.csv --max-trades 5

# 4. Monitor positions
python -m src.cli.main monitor
```

---

## CLI Command Reference

### Infrastructure Commands

| Command | Description |
|---------|-------------|
| `init` | Initialize database and directories |
| `test-ibkr` | Test IBKR connection |
| `status` | Show system status and statistics |
| `db-reset` | Reset database (WARNING: deletes all data) |
| `version` | Show version information |
| `web` | Launch web interface for manual trade entry |

### Trading Commands

| Command | Description |
|---------|-------------|
| `scan` | Scan for opportunities (Barchart API or CSV import) |
| `trade` | Execute autonomous trading cycle |
| `execute` | Execute a single specific trade |
| `monitor` | Monitor current positions |
| `auto-monitor` | Continuous monitoring loop |
| `analyze` | Analyze trading performance |
| `emergency-stop` | Halt all trading immediately |

### Manual Trade Commands

| Command | Description |
|---------|-------------|
| `add-trade` | Add manual trade opportunity |
| `list-manual-trade-files` | List pending/imported trade files |
| `show-pending-trades` | Show trades ready for execution |
| `scan-history` | View historical scan results |
| `scan-details` | View specific scan details |

### Market Data Commands

| Command | Description |
|---------|-------------|
| `quote` | Get real-time stock or option quote |
| `option-chain` | Browse available option chains |
| `market-status` | Check if market is open |

### TAAD Commands (Trade Archaeology & Alpha Discovery)

| Command | Description |
|---------|-------------|
| `taad-import` | Import trades from IBKR Flex Query |
| `taad-report` | Display matched trade lifecycles with P&L |
| `taad-status` | Show import session history and statistics |
| `taad-gaps` | Identify gaps and data quality issues |
| `taad-promote` | Promote matched trades into public.trades for enrichment |
| `taad-enrich` | Enrich trades with historical market context |
| `taad-barchart-login` | Save Barchart session for scraping |

### NakedTrader Commands

| Command | Description |
|---------|-------------|
| `nt SYMBOL` | Execute daily delta-targeted put trade (dry-run default, `--live` for paper) |
| `nt-watch` | Monitor open NakedTrader positions with live P&L and bracket status |
| `nt-status` | Show NakedTrader trade history and performance (offline) |

### Learning Commands

| Command | Description |
|---------|-------------|
| `learn --analyze` | Run weekly learning analysis |
| `learn --patterns` | View detected patterns |
| `learn --experiments` | View active experiments |
| `learn --proposals` | View parameter proposals |
| `learn --summary` | Show learning summary |
| `learning-stats` | Show data quality statistics |
| `export-learning-data` | Export learning data to CSV |
| `snapshot-positions` | Capture daily position snapshots |

---

## Scanning & Trade Selection

### Option 1: Barchart CSV Import (Recommended)

Export from Barchart.com with these columns:
- Symbol, Price, Exp Date, DTE, Strike, Moneyness
- Bid, Volume, Open Int, IV Rank, Delta, Return, Ann Rtn, Profit Prob

```bash
# Basic scan with scoring
python -m src.cli.main scan --from-csv naked-put-screener.csv

# Validate top candidates with IBKR real-time data
python -m src.cli.main scan --from-csv naked-put-screener.csv --validate

# Show top 30 candidates
python -m src.cli.main scan --from-csv naked-put-screener.csv --top 30

# Skip diversification rules
python -m src.cli.main scan --from-csv naked-put-screener.csv --no-diversify

# Show rejected candidates with reasons
python -m src.cli.main scan --from-csv naked-put-screener.csv --show-rejected
```

### Option 2: Barchart API (Requires API Key)

```bash
# Full API scan with validation
python -m src.cli.main scan --validate

# Quick scan without IBKR validation
python -m src.cli.main scan --no-validate
```

### Scoring System

Candidates are scored across 6 dimensions (0-100 each):

| Dimension | Weight | Optimal Range |
|-----------|--------|---------------|
| Risk-Adjusted Return | 25% | 30-50% annualized |
| Probability of Profit | 20% | 85%+ |
| IV Rank | 15% | 60-80% |
| Liquidity | 15% | OI 2000+, Vol 300+ |
| Capital Efficiency | 15% | 2.0%+ return |
| Safety Buffer | 10% | 12-18% OTM |

**Grades:**
- A+ (85-100): Excellent
- A (75-84): Very good
- B (65-74): Good
- C (55-64): Acceptable
- D (45-54): Below average
- F (<45): Poor

---

## Trade Execution

### Opportunity Sources (Choose ONE)

```bash
# From Barchart CSV export
python -m src.cli.main trade --from-csv opportunities.csv

# From Barchart API scan
python -m src.cli.main trade --use-api

# From manual database entries only
python -m src.cli.main trade --manual-only
```

### Execution Options

```bash
# Dry run (test without placing orders)
python -m src.cli.main trade --from-csv file.csv --dry-run

# Limit number of trades
python -m src.cli.main trade --from-csv file.csv --max-trades 5

# Auto-execute without confirmation
python -m src.cli.main trade --from-csv file.csv --auto

# Skip IBKR validation (not recommended)
python -m src.cli.main trade --from-csv file.csv --no-validate
```

### Single Trade Execution

```bash
python -m src.cli.main execute AAPL 180 2025-02-07 \
    --premium 0.50 \
    --contracts 1 \
    --dry-run
```

---

## Advanced Order Execution

### New Order Execution Features

The trading system includes advanced order execution capabilities designed to improve fill rates and pricing while maintaining safety and reliability.

#### Adaptive Algorithm Overview

The **Adaptive Algorithm** intelligently selects the best execution method based on market conditions:

- **Liquid markets** (spread < $0.10): Uses IBKR's ADAPTIVE algorithm for smart routing
- **Wide markets** (spread >= $0.10): Uses MIDPRICE algorithm to work the spread
- **Automatic detection**: System analyzes bid-ask spread in real-time and chooses optimal algo

Benefits:
- Better fill rates in illiquid options
- Improved pricing by working the spread
- Automatic fallback to limit orders if needed

#### Rapid-Fire Parallel Execution

When executing multiple trades, the system can use **parallel execution** to place orders simultaneously:

- **Sequential mode** (default): Places orders one at a time, waiting for each to fill
- **Rapid-fire mode**: Places all orders simultaneously, then monitors for fills
- **Configurable timeout**: Control how long to wait for fills before adjustment

Benefits:
- Much faster execution for multi-trade batches
- Reduced exposure to price movement during execution
- Better capital deployment timing

#### Order Reconciliation

The system includes robust order reconciliation to keep internal state synchronized with IBKR:

- **Automatic sync**: Compares local database with IBKR orders
- **Conflict detection**: Identifies discrepancies between systems
- **Manual sync commands**: Tools to inspect and fix synchronization issues
- **Position validation**: Ensures positions match across systems

#### Two-Tier Execution (Phase D)

The **Two-Tier Execution** system provides intelligent market-timing for option selling:

**Tier 1 (9:30 AM):**
- Execute all orders while pre-market research still valid
- Preserves value of overnight stock screening
- Conservative limit prices for initial submission

**Tier 2 (Condition-Based: 9:45-10:30 AM):**
- Monitors VIX and bid-ask spreads every 5 minutes
- Executes when market conditions favorable (VIX low, spreads tight)
- Slightly more aggressive limits for better fill probability
- Skips if VIX > 25 (too volatile for adjustments)

**Progressive Automation Modes:**
- **Hybrid:** Automated prep, manual execution trigger (for testing)
- **Supervised:** Automated execution, manual report review (for validation)
- **Autonomous:** Fully automated, alerts only on errors (for production)

Benefits:
- Preserves pre-market research value (no re-screening needed)
- Intelligent retry based on actual market conditions (not fixed time)
- Progressive path from testing to full automation
- FINRA-compliant clock synchronization (50ms drift limit)
- Scalable from 3-5 trades → 10-15 trades

---

### Order Reconciliation Commands

Use these commands to diagnose and fix order synchronization issues.

#### Basic Order Sync

Sync today's orders from IBKR to local database:

```bash
# Sync all orders from today
python -m src.cli.main sync-orders
```

**Example output:**
```
📋 Syncing orders from IBKR...
✓ Connected to IBKR
✓ Fetched 12 orders from IBKR
✓ Found 10 orders in local database

Synchronization Summary:
  - Orders in IBKR: 12
  - Orders in database: 10
  - New orders to add: 2
  - Orders to update: 8
  - Conflicts found: 0

✓ Added 2 new orders to database
✓ Updated 8 existing orders
✓ Synchronization complete
```

**When to use:**
- After placing trades to verify they're recorded
- When dashboard shows missing orders
- Daily reconciliation check
- After IBKR disconnect/reconnect

#### Sync Specific Date

Sync orders from a specific date (useful for historical reconciliation):

```bash
# Sync orders from a specific date
python -m src.cli.main sync-orders --date 2026-02-03

# Sync orders from yesterday
python -m src.cli.main sync-orders --date 2026-02-02

# Sync orders from last week
python -m src.cli.main sync-orders --date 2026-01-27
```

**Example output:**
```
📋 Syncing orders from IBKR for date: 2026-02-03...
✓ Connected to IBKR
✓ Fetched 8 orders from 2026-02-03
✓ Found 6 orders in local database for this date

Synchronization Summary:
  - Orders in IBKR: 8
  - Orders in database: 6
  - New orders to add: 2
  - Orders to update: 6
  - Conflicts found: 0

✓ Added 2 new orders to database
✓ Updated 6 existing orders
✓ Synchronization complete
```

**When to use:**
- Backfilling historical data
- Fixing data gaps from specific days
- Investigating past execution issues
- Audit trail verification

#### Include Filled Orders

By default, sync focuses on open/recent orders. Include filled orders for complete history:

```bash
# Include all filled orders (comprehensive sync)
python -m src.cli.main sync-orders --include-filled

# Include filled orders for specific date
python -m src.cli.main sync-orders --date 2026-02-03 --include-filled
```

**Example output:**
```
📋 Syncing orders from IBKR (including filled)...
✓ Connected to IBKR
✓ Fetched 45 orders from IBKR (including filled)
✓ Found 38 orders in local database

Synchronization Summary:
  - Orders in IBKR: 45
  - Orders in database: 38
  - New orders to add: 7
  - Orders to update: 38
  - Conflicts found: 0

Order Status Breakdown:
  - Filled: 32
  - Cancelled: 4
  - Submitted: 5
  - PreSubmitted: 4

✓ Added 7 new orders to database
✓ Updated 38 existing orders
✓ Synchronization complete
```

**When to use:**
- Complete database rebuild
- Ensuring all historical orders are captured
- Performance analysis requiring full order history
- Initial system setup with existing IBKR account

#### Position Reconciliation

Reconcile and sync positions between database and IBKR:

```bash
# Preview discrepancies only (safe, default)
python -m src.cli.main reconcile-positions

# Apply fixes to sync database with IBKR
python -m src.cli.main reconcile-positions --live
```

**What it does:**

**Dry-run mode (default - safe):**
- Analyzes discrepancies between database and IBKR
- Shows what would be fixed
- Makes NO changes to database
- Safe to run anytime

**Live mode (--live):**
- Imports positions from IBKR not in database
- Closes positions in database not in IBKR
- Updates quantity mismatches to match IBKR
- Actually modifies database to match IBKR

**Example output - No issues:**
```
Position Reconciliation & Sync

[DRY RUN - Preview Only]

Analyzing discrepancies...

Position Reconciliation Report

✓ No discrepancies found - database is in sync with IBKR!
```

**Example output - Issues detected (dry-run):**
```
Position Reconciliation & Sync

[DRY RUN - Preview Only]

Analyzing discrepancies...

⚠ Discrepancies detected:

                    Quantity Mismatches
┏━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Contract           ┃ DB Quantity ┃ IBKR Quantity ┃ Diff     ┃
┡━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ NVDA_850.0_...     │ 3           │ 5             │ +2       │
└────────────────────┴─────────────┴───────────────┴──────────┘

In IBKR but not in database:
  - GOOGL_160.0_20260214_P

In database but not in IBKR:
  - TSLA_250.0_20260214_P

════════════════════════════════════════════════════════════
DRY RUN COMPLETE - No changes made
Run with --live to apply these fixes:
  python -m src.cli.main reconcile-positions --live
════════════════════════════════════════════════════════════
```

**Example output - Applying fixes (--live):**
```
Position Reconciliation & Sync

[LIVE MODE - Will Apply Fixes]

⚠️  This will modify your database to match IBKR!
   - Import missing positions from IBKR
   - Close positions not in IBKR
   - Update quantity mismatches

Are you sure you want to proceed? [y/N]: y

Analyzing discrepancies...

[Displays discrepancy report]

Applying fixes...

Step 1: Importing 1 orphan positions from IBKR...
  ✓ Imported 1 positions

Step 2: Closing 1 positions not in IBKR...
  ✓ Closed: TSLA $250.0 exp 2026-02-14 (P&L: $150.00)
  ✓ Closed 1 positions

Step 3: Fixing 1 quantity mismatches...
  ✓ Updated: NVDA $850.0 from 3 → 5 contracts
  ✓ Fixed 1 quantity mismatches

════════════════════════════════════════════════════════════
✓ SYNC COMPLETE - Database now matches IBKR!
════════════════════════════════════════════════════════════
```

**When to use:**
- **Daily:** Run in dry-run mode to check for discrepancies
- **After issues detected:** Run with --live to apply fixes
- Before placing new trades
- After unexpected fills or cancellations
- Investigating P&L discrepancies
- After system restarts or reconnections

**Safety:** Always defaults to dry-run mode. Review the report before using --live.

#### Troubleshooting Common Reconciliation Issues

**Issue: Any discrepancies between database and IBKR**

The new reconciliation command can **automatically fix** all common issues:

```bash
# Step 1: Preview what's wrong (safe, read-only)
python -m src.cli.main reconcile-positions

# Step 2: Review the report

# Step 3: Apply fixes if they look correct
python -m src.cli.main reconcile-positions --live
```

This automatically handles:
- ✅ Positions in IBKR but not in database → Imports them
- ✅ Positions in database but not in IBKR → Closes them
- ✅ Quantity mismatches → Updates to match IBKR

**Issue: Position quantities don't match**

```bash
# Automatic fix (easiest)
python -m src.cli.main reconcile-positions --live

# Manual alternative (if you prefer)
python -m src.cli.main sync-orders --include-filled
python -m src.cli.main reconcile-positions
```

**Issue: Positions in IBKR but not in database**

This usually means:
1. Orders were placed outside the system (manually in TWS)
2. System was offline when orders filled
3. Database connection issue during execution

```bash
# Automatic fix
python -m src.cli.main reconcile-positions --live

# This imports all missing positions from IBKR
```

**Issue: Ghost positions (in DB but not IBKR)**

This usually means position was closed in IBKR but not recorded:

```bash
# Automatic fix
python -m src.cli.main reconcile-positions --live

# This marks ghost positions as closed in database

```bash
# Step 1: Sync all recent orders
python -m src.cli.main sync-orders --include-filled

# Step 2: If position was manually closed in TWS, you may need to
# manually close it in the database via monitor command
python -m src.cli.main monitor
# Look for positions showing as open but with 0 quantity in IBKR
```

**Issue: Duplicate orders in database**

```bash
# Prevention: Always use sync-orders instead of manual database edits
# Recovery: May require database inspection
python -m src.cli.main status  # Check for abnormalities

# Contact support if duplicates persist after sync
```

---

### Two-Tier Execution Commands (Phase D)

Use these commands to execute staged trades with intelligent two-tier execution and progressive automation.

#### Test with Hybrid Mode (Manual Trigger)

Start with hybrid mode during initial testing - system does all prep, you click "execute":

```bash
# Dry run with manual trigger
python -m src.cli.main execute-two-tier --mode=hybrid --dry-run
```

**What happens:**
1. System verifies clock sync (FINRA compliance)
2. Pre-market validation at 9:15 AM (stock price deviation check)
3. Quote refresh at 9:28 AM (recalculate limit prices)
4. System shows market conditions and waits for your input:

```
╔════════════════════════════════════════════════════════╗
║           READY FOR EXECUTION (Hybrid Mode)            ║
╚════════════════════════════════════════════════════════╝

Trades Ready:    5
Current Time:    09:30:15 ET

Market Conditions:
  VIX:           14.2
  SPY:           $450.10
  Avg Spread:    $0.03
  Status:        ✓ FAVORABLE
  Reason:        VIX low (14.2), spreads tight ($0.03)

────────────────────────────────────────────────────────
Type 'execute' to submit all orders now
Type 'wait' to check again in 5 minutes
Type 'abort' to cancel execution
────────────────────────────────────────────────────────
>
```

5. You type `execute` when ready
6. System executes Tier 1 + Tier 2
7. Report shows results

**When to use:**
- Initial testing (weeks 1-3)
- Learning how the system works
- Building confidence in automation
- Unusual market conditions requiring manual review

#### Supervised Mode (Auto-Execute, Review After)

Use supervised mode when you're confident in automation but want to review results:

```bash
# Automated execution in paper trading
python -m src.cli.main execute-two-tier --mode=supervised --dry-run

# Live execution with post-review
python -m src.cli.main execute-two-tier --mode=supervised --live
```

**What happens:**
1. System runs pre-market validation (9:15 AM) and quote refresh (9:28 AM) automatically
2. Tier 1 executes at 9:30:00 AM (all orders submitted in <3 seconds)
3. Tier 2 monitors conditions and adjusts unfilled orders at 10:00 AM
4. System sends execution report for your review
5. You review results and confirm correctness

**Example output:**
```
📊 Pre-market validation (9:15 AM)
  ✓ 5/5 trades passed

🔄 Quote refresh (9:28 AM)
  ✓ 5/5 limit prices recalculated

🎯 Adaptive strike selection (9:29 AM)
  ✓ AAPL: SELECTED strike 175 → 172.5 (delta=0.19, was 0.24)
  ✓ MSFT: UNCHANGED strike 380 (delta=0.21, within tolerance)
  ✓ NVDA: SELECTED strike 850 → 840 (delta=0.20, was 0.26)
  ✓ GOOGL: UNCHANGED strike 155 (delta=0.18, within tolerance)
  ✓ META: SELECTED strike 520 → 510 (delta=0.19, was 0.23)

🚀 TIER 1: Submitting 5 orders at market open (9:30:00)
  ✓ Submitted 5 orders in 1.8s

📋 Fill monitoring (9:30-9:40)
  ✓ 3 filled within 30s
  ↻ 2 orders: adjusted limits (-$0.01 x2)
  ✓ 1 more filled at 9:32
  ✓ 1 left working as DAY order

⏳ TIER 2: Monitoring conditions (9:45-10:30)
  Conditions at 09:50: VIX 15.1, Spread $0.03 - VIX low, spreads tight
  ✓ Conditions favorable - executing Tier 2 adjustments
  ✓ Adjusted 1 order (+8% more aggressive)

✓ Execution complete
  Filled: 5 | Working: 0 | Failed: 0
  Total Premium: $1,125.00
```

**When to use:**
- Weeks 4-8 of testing
- Paper trading validation
- Building track record
- Tuning parameters based on results

#### Autonomous Mode (Fully Automated)

Use autonomous mode for production after extensive testing:

```bash
# Fully autonomous execution (live trading)
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes
```

**What happens:**
1. System runs completely autonomous
2. No manual intervention required
3. Report sent after completion
4. You wake up to execution results

**Typical workflow:**
```bash
# Sunday night: Stage trades for Monday
python -m src.cli.main sunday-session

# Monday morning: System executes autonomously (you sleep)
# (Would be scheduled via cron/systemd timer)
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes

# Later: Review execution report
```

**When to use:**
- Production trading (weeks 9+)
- After 20+ successful supervised runs
- When sleeping during US market hours
- Full automation achieved

**Safety notes:**
- Always test extensively in paper trading first
- Use supervised mode for at least 6 weeks
- Monitor weekly results before going autonomous
- Keep circuit breakers configured (VIX > 30 aborts)

#### Dry Run vs Live Execution

All modes support dry-run for testing:

```bash
# Test without real orders (default)
python -m src.cli.main execute-two-tier --mode=hybrid --dry-run

# Execute real orders in paper trading
python -m src.cli.main execute-two-tier --mode=hybrid --live

# Autonomous production execution
python -m src.cli.main execute-two-tier --mode=autonomous --live --yes
```

#### Troubleshooting Two-Tier Execution

**Issue: Clock sync failure**

```
✗ Clock sync failed: Clock drift 75ms exceeds 50ms limit
```

Solution:
```bash
# Synchronize system time with NTP
sudo sntp time.nist.gov  # Linux/Mac
# Or use Windows time sync

# Verify sync
python -m src.cli.main execute-two-tier --mode=hybrid --dry-run
# Should show: ✓ Clock synced: 12.5ms drift
```

**Issue: No trades pass Stage 1**

```
Stage 1 complete: 0/5 passed
Warning: No trades passed Stage 1 validation
```

Causes:
- Stock prices moved significantly from weekend
- Pre-market volatility invalidated candidates

Solution:
```bash
# Check stock prices vs staged prices
python -m src.cli.main show-staged

# If needed, re-run Sunday session with current prices
python -m src.cli.main sunday-session
```

**Issue: Tier 2 never executes**

```
⏳ TIER 2: Monitoring conditions (9:45-10:30)
  Conditions at 09:50: VIX 28.0 - VIX too high: 28.0 (threshold: 25)
  Tier 2 window expired without favorable conditions
```

This is expected behavior - Tier 2 only executes when market conditions are good.

If Tier 2 consistently skips:
- Consider raising `TIER2_VIX_HIGH` threshold (e.g., from 25 to 28)
- Or accept that some days have unfavorable conditions
- Tier 1 fills are still valid even without Tier 2

**Issue: Orders stuck in "Working" status**

Check order status:
```bash
# Sync orders from IBKR
python -m src.cli.main sync-orders

# Monitor current positions
python -m src.cli.main monitor
```

---

### Adaptive Strike Selection

The system supports **delta-based strike selection** at execution time. Instead of using the static strike from overnight Barchart screening, the system queries the live IBKR option chain just before Tier 1 execution and selects the strike closest to a target delta.

#### Why Delta-Based Selection?

Professional options traders parameterize by **delta, not strike**. When a stock moves overnight, the original strike's delta drifts. A strike that was 20-delta on Sunday may be 30-delta or 12-delta by Monday morning. Adaptive strike selection resolves the actual strike at execution time, preserving the intended risk profile.

#### How It Works

1. **Get live stock price** from IBKR
2. **Fetch option chain** via `reqSecDefOptParams` for the target expiration
3. **Filter to OTM put strikes** in the relevant range (centered around the original strike)
4. **Fetch live Greeks** (delta, IV, gamma, theta) for up to 5 candidate strikes
5. **Select best strike**: closest to target delta (default 0.20), passing all boundary checks:
   - Premium >= floor ($0.20)
   - OTM% >= minimum (10%)
   - Bid-ask spread <= max (30%)
   - Volume >= minimum (10)
   - Open interest >= minimum (50)
6. **Update the staged opportunity** with new strike, premium, delta, and Greeks
7. **Recalculate limit price** for the new strike

#### Selection Outcomes

| Outcome | Meaning |
|---------|---------|
| **SELECTED** | New strike found via delta matching, opportunity updated |
| **UNCHANGED** | No better strike found, keeping original |
| **ABANDONED** | No valid strikes available (all failed boundary checks) |

#### Configuration

```bash
# Master switch (disable to skip strike selection entirely)
ADAPTIVE_STRIKE_ENABLED=true

# Delta targeting
STRIKE_TARGET_DELTA=0.20        # Target absolute delta
STRIKE_DELTA_TOLERANCE=0.05     # Acceptable +/- range (0.15 to 0.25)

# Boundary checks
MIN_OTM_PCT=0.10                # Minimum OTM%
PREMIUM_FLOOR=0.20              # Minimum bid premium
MAX_EXECUTION_SPREAD_PCT=0.30   # Maximum bid-ask spread %
STRIKE_MIN_VOLUME=10            # Minimum option volume
STRIKE_MIN_OI=50                # Minimum open interest

# Behavior
STRIKE_MAX_CANDIDATES=5         # Max strikes to evaluate per symbol
STRIKE_FALLBACK_TO_OTM=true     # Fall back to OTM% if no delta match
```

#### Disabling Strike Selection

Set `ADAPTIVE_STRIKE_ENABLED=false` in `.env` to skip delta-based selection entirely. The system will use the original strikes from overnight screening (existing behavior).

---

### Fill Management

After Tier 1 submits all orders, the **Fill Manager** monitors fills for a configurable time window (default 10 minutes) with progressive limit adjustment and partial fill handling.

#### How It Works

1. **Monitor loop** (every 2 seconds): Check order status for all pending orders
2. **Partial fill detection**: If an order is >50% filled, cancel the remainder and place a new order for the unfilled quantity at a fresh limit price
3. **Progressive limit adjustment**: Every 60 seconds, lower the limit price by $0.01 to improve fill probability
4. **Floor protection**: Never adjusts below the premium floor ($0.20)
5. **Timeout handling**: After the monitoring window expires, leave unfilled orders working as DAY orders (they'll fill or expire at market close)

#### Configuration

```bash
# Monitoring window
FILL_MONITOR_WINDOW_SECONDS=600    # 10 minutes (default)
FILL_CHECK_INTERVAL=2.0            # Check every 2 seconds

# Progressive adjustment
FILL_MAX_ADJUSTMENTS=5             # Max limit adjustments per order
FILL_ADJUSTMENT_INCREMENT=0.01     # $ decrement per adjustment
FILL_ADJUSTMENT_INTERVAL=60        # Seconds between adjustments

# Partial fill handling
FILL_PARTIAL_THRESHOLD=0.5         # >50% filled triggers remainder handling

# Timeout behavior
FILL_LEAVE_WORKING=true            # Leave unfilled as DAY orders
PREMIUM_FLOOR=0.20                 # Never adjust below this
```

#### Fill Report

After the monitoring window completes, a FillReport is generated showing:
- Orders monitored, fully filled, partially filled
- Orders left working as DAY orders
- Total limit adjustments made
- Elapsed time

#### Troubleshooting

**Issue: No fills during monitoring window**

Possible causes:
- Limit prices too aggressive (too low premium)
- Wide bid-ask spreads at market open
- Low liquidity in the option

Solutions:
- Increase `FILL_MAX_ADJUSTMENTS` to allow more price adjustments
- Increase `FILL_ADJUSTMENT_INCREMENT` to $0.02 for faster convergence
- Reduce `FILL_ADJUSTMENT_INTERVAL` to 30 seconds
- Check that Tier 2 will pick up unfilled orders afterward

**Issue: Partial fills not handled**

Check that `FILL_PARTIAL_THRESHOLD` is set appropriately. The default (0.5) means the remainder is only handled when >50% of the order has filled. Lower this to 0.3 if you want earlier partial fill handling.

---

### End-to-End Logical Flow

This is the complete flow from finding trade candidates to monitoring open positions:

```
BARCHART SCREENING (Manual — Sunday Evening)
│   1. Go to Barchart.com, run saved naked put screener
│   2. Export results as CSV (Symbol, Price, Strike, DTE, Bid, Delta, OI, etc.)
│   3. Save CSV to project directory
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
REVIEW (Manual — Sunday Evening)
│   show-staged          → view all staged opportunities with details
│   validate-staged      → re-validate with fresh market data
│   cancel-staged --id N → remove unwanted candidates
│
▼
EXECUTE — execute-two-tier --mode=autonomous --live --yes  (Monday)
│
├─ 9:15 AM  Stage 1: Pre-Market Validation
│    • Re-check stock prices against staged values
│    • Auto-adjust if stock moved <5%, mark stale if >10%
│
├─ 9:28 AM  Stage 2: Quote Refresh
│    • Fetch live option quotes from IBKR
│    • Recalculate limit prices from fresh bid/ask
│
├─ ~9:29 AM  Adaptive Strike Selection
│    • For each trade: pull live option chain from IBKR
│    • Fetch Greeks for candidate strikes near original strike
│    • Select strike closest to target delta (0.20)
│    • Validate boundaries (premium, OTM%, spread, liquidity)
│    • Update staged opportunity with new strike + Greeks
│    • Outcome: SELECTED, UNCHANGED, or ABANDONED
│
├─ 9:30:00  Tier 1: Rapid-Fire Execution
│    • Submit ALL orders in parallel (<3 seconds)
│    • IBKR Adaptive algorithm for smart routing
│
├─ 9:30-9:40  Fill Monitoring (10-minute window)
│    • Check order fills every 2 seconds
│    • Detect partial fills → cancel + replace remainder
│    • Progressive limit adjustment: -$0.01/min, max 5 steps
│    • Never adjusts below premium floor
│    • On timeout: leave unfilled as DAY orders
│
├─ At Fill Time  Entry Snapshot Capture
│    • 86+ fields: Greeks, IV, technicals, market data
│    • + strike_selection_method, original_strike, live_delta
│    • Saved to TradeEntrySnapshot in database
│
├─ 9:45-10:30  Tier 2: Condition-Based Retry
│    • Monitor VIX and bid-ask spreads every 5 minutes
│    • Execute when conditions favorable (VIX low, spreads tight)
│    • Adjust limits more aggressively for unfilled orders
│
└─ 10:30 AM  Final Reconciliation
     • Generate execution report
     • Save all results to database
     • Log outcomes
│
▼
POST-EXECUTION VERIFICATION (Monday ~10:35 AM)
│   list-trades --open-only    → verify filled positions
│   reconcile-positions        → sync database with IBKR
│   sync-orders                → update order status and commissions
│
▼
DAILY MONITORING (Ongoing)
    monitor                    → real-time position P&L
    snapshot-positions         → EOD snapshot for path analysis
    auto-monitor --auto-exit   → continuous with automated exits
    reconcile-positions        → daily database/IBKR sync
```

---

### Configuration for Order Execution

Control order execution behavior through environment variables in `.env`:

#### USE_ADAPTIVE_ALGO

Enable/disable the adaptive algorithm for intelligent order routing.

```bash
# Enable adaptive algorithm (recommended)
USE_ADAPTIVE_ALGO=true

# Disable adaptive algorithm (use simple limit orders)
USE_ADAPTIVE_ALGO=false
```

**When enabled:**
- System analyzes bid-ask spread for each order
- Narrow spreads (< $0.10): Uses IBKR ADAPTIVE algorithm
- Wide spreads (>= $0.10): Uses MIDPRICE algorithm
- Better fill rates and pricing

**When disabled:**
- All orders placed as simple limit orders at mid-price
- More predictable but potentially worse fills
- Useful for debugging or conservative execution

**Default:** `true`

**When to change:**
- Set to `false` if experiencing unexpected order behavior
- Set to `false` for very liquid options where simple limit works
- Keep `true` for best overall performance

**Example scenarios:**

```bash
# Scenario 1: Liquid blue-chip options (AAPL, MSFT)
# Both work well, ADAPTIVE may get slight improvement
USE_ADAPTIVE_ALGO=true

# Scenario 2: Illiquid small-cap options
# ADAPTIVE is crucial for fills
USE_ADAPTIVE_ALGO=true

# Scenario 3: Debugging order issues
# Simplify execution to isolate problems
USE_ADAPTIVE_ALGO=false
```

#### QUOTE_FETCH_TIMEOUT_SECONDS

How long to wait for market data quotes before timing out.

```bash
# Default: 10 seconds
QUOTE_FETCH_TIMEOUT_SECONDS=10

# Aggressive (faster but may miss quotes)
QUOTE_FETCH_TIMEOUT_SECONDS=5

# Conservative (slower but more reliable)
QUOTE_FETCH_TIMEOUT_SECONDS=15
```

**Trade-offs:**
- **Lower values (5s):** Faster execution, risk of timeout errors on slow quotes
- **Higher values (15s):** More reliable quotes, slower overall execution
- **Default (10s):** Balanced approach suitable for most conditions

**When to adjust:**
- Increase if seeing frequent quote timeout errors
- Increase during high-volatility periods (market open, news events)
- Decrease if execution speed is critical and quotes are reliable
- Decrease during quiet market periods with stable quotes

**Example scenarios:**

```bash
# Market open (9:30-10:00 AM ET) - quotes may be slow
QUOTE_FETCH_TIMEOUT_SECONDS=15

# Mid-day (11:00 AM - 2:00 PM ET) - stable quotes
QUOTE_FETCH_TIMEOUT_SECONDS=10

# Testing/development - want fast feedback
QUOTE_FETCH_TIMEOUT_SECONDS=5
```

#### USE_RAPID_FIRE

Enable parallel order execution for multiple trades.

```bash
# Enable rapid-fire parallel execution (faster)
USE_RAPID_FIRE=true

# Disable rapid-fire (sequential execution)
USE_RAPID_FIRE=false
```

**When enabled:**
- Places all orders simultaneously
- Monitors all fills in parallel
- Much faster for batches of trades
- May adjust prices simultaneously if needed

**When disabled:**
- Places orders one at a time
- Waits for each to fill before next
- Slower but more conservative
- Easier to debug issues

**Default:** `false` (conservative)

**When to change:**
- Set to `true` when executing 3+ trades simultaneously
- Set to `true` for time-sensitive batch execution
- Keep `false` for single trades or debugging
- Keep `false` if IBKR account has low order rate limits

**Example scenarios:**

```bash
# Scenario 1: Executing 10 trades from morning scan
# Want fast deployment of capital
USE_RAPID_FIRE=true

# Scenario 2: Executing 1-2 trades
# No benefit to parallel execution
USE_RAPID_FIRE=false

# Scenario 3: Debugging execution issues
# Simplify to sequential for easier troubleshooting
USE_RAPID_FIRE=false
```

#### RAPID_FIRE_MAX_WAIT_SECONDS

When using rapid-fire mode, how long to wait for orders to fill before adjusting prices.

```bash
# Default: 30 seconds
RAPID_FIRE_MAX_WAIT_SECONDS=30

# Aggressive (quick adjustments)
RAPID_FIRE_MAX_WAIT_SECONDS=15

# Patient (wait longer for fills)
RAPID_FIRE_MAX_WAIT_SECONDS=60
```

**Behavior:**
1. Place all orders at initial prices
2. Wait `RAPID_FIRE_MAX_WAIT_SECONDS`
3. Check which orders filled
4. Adjust unfilled orders closer to ask
5. Repeat until all filled or max attempts reached

**Trade-offs:**
- **Lower values (15s):** Faster execution, may pay higher prices
- **Higher values (60s):** Better prices, slower execution
- **Default (30s):** Balanced approach

**When to adjust:**
- Increase in quiet markets where orders may fill slowly
- Increase for very illiquid options
- Decrease when execution speed is critical
- Decrease in fast-moving markets where prices change quickly

**Example scenarios:**

```bash
# Illiquid small-cap options - be patient for fills
RAPID_FIRE_MAX_WAIT_SECONDS=60

# Liquid blue-chip options - fills come quickly
RAPID_FIRE_MAX_WAIT_SECONDS=20

# High volatility - prices moving fast
RAPID_FIRE_MAX_WAIT_SECONDS=15
```

#### ADJUSTMENT_THRESHOLD

How much to adjust unfilled order prices toward the ask (as fraction of spread).

```bash
# Default: 0.25 (move 25% closer to ask)
ADJUSTMENT_THRESHOLD=0.25

# Conservative (small adjustments)
ADJUSTMENT_THRESHOLD=0.15

# Aggressive (large adjustments)
ADJUSTMENT_THRESHOLD=0.40
```

**How it works:**

If mid-price is $0.40 and ask is $0.50 (spread = $0.10):
- `ADJUSTMENT_THRESHOLD=0.25`: Adjust to $0.40 + (0.25 × $0.10) = $0.425
- `ADJUSTMENT_THRESHOLD=0.15`: Adjust to $0.40 + (0.15 × $0.10) = $0.415
- `ADJUSTMENT_THRESHOLD=0.40`: Adjust to $0.40 + (0.40 × $0.10) = $0.440

**Trade-offs:**
- **Lower values (0.15):** Better prices, may not fill as quickly
- **Higher values (0.40):** Faster fills, paying more
- **Default (0.25):** Balanced approach

**When to adjust:**
- Increase if orders frequently not filling after several attempts
- Decrease if paying too much (losing edge)
- Adjust based on backtesting results
- Consider market conditions (liquid vs illiquid)

**Example scenarios:**

```bash
# Liquid options - small adjustments sufficient
ADJUSTMENT_THRESHOLD=0.15

# Moderately liquid - balanced approach
ADJUSTMENT_THRESHOLD=0.25

# Illiquid options - need aggressive adjustments
ADJUSTMENT_THRESHOLD=0.40

# Testing fill rates - try different values
ADJUSTMENT_THRESHOLD=0.20  # Test and measure fill rate
ADJUSTMENT_THRESHOLD=0.30  # Compare results
```

---

### Complete Configuration Example

Here's a complete `.env` configuration with recommended settings for different scenarios:

**Scenario 1: Conservative (beginners, debugging)**
```bash
# Conservative execution
USE_ADAPTIVE_ALGO=false
USE_RAPID_FIRE=false
QUOTE_FETCH_TIMEOUT_SECONDS=15
RAPID_FIRE_MAX_WAIT_SECONDS=30
ADJUSTMENT_THRESHOLD=0.25
ADAPTIVE_STRIKE_ENABLED=false
FILL_MONITOR_WINDOW_SECONDS=600
FILL_MAX_ADJUSTMENTS=3
```

**Scenario 2: Balanced (recommended default)**
```bash
# Balanced execution
USE_ADAPTIVE_ALGO=true
USE_RAPID_FIRE=false
QUOTE_FETCH_TIMEOUT_SECONDS=10
RAPID_FIRE_MAX_WAIT_SECONDS=30
ADJUSTMENT_THRESHOLD=0.25
ADAPTIVE_STRIKE_ENABLED=true
STRIKE_TARGET_DELTA=0.20
FILL_MONITOR_WINDOW_SECONDS=600
FILL_MAX_ADJUSTMENTS=5
```

**Scenario 3: Aggressive (experienced, liquid options)**
```bash
# Aggressive execution
USE_ADAPTIVE_ALGO=true
USE_RAPID_FIRE=true
QUOTE_FETCH_TIMEOUT_SECONDS=10
RAPID_FIRE_MAX_WAIT_SECONDS=20
ADJUSTMENT_THRESHOLD=0.30
ADAPTIVE_STRIKE_ENABLED=true
STRIKE_TARGET_DELTA=0.20
FILL_MONITOR_WINDOW_SECONDS=300
FILL_MAX_ADJUSTMENTS=5
FILL_ADJUSTMENT_INCREMENT=0.02
```

**Scenario 4: Illiquid options (small caps)**
```bash
# Optimized for illiquid options
USE_ADAPTIVE_ALGO=true
USE_RAPID_FIRE=true
QUOTE_FETCH_TIMEOUT_SECONDS=15
RAPID_FIRE_MAX_WAIT_SECONDS=60
ADJUSTMENT_THRESHOLD=0.35
ADAPTIVE_STRIKE_ENABLED=true
STRIKE_TARGET_DELTA=0.20
STRIKE_MIN_VOLUME=5
STRIKE_MIN_OI=20
FILL_MONITOR_WINDOW_SECONDS=600
FILL_MAX_ADJUSTMENTS=5
FILL_ADJUSTMENT_INCREMENT=0.01
```

---

### Two-Tier Execution Configuration (Phase D)

Control two-tier execution behavior through environment variables in `.env`:

#### AUTOMATION_MODE

Choose the level of automation:

```bash
# Test with manual trigger (weeks 1-3)
AUTOMATION_MODE=hybrid

# Automated execution with review (weeks 4-8)
AUTOMATION_MODE=supervised

# Fully autonomous (weeks 9+ production)
AUTOMATION_MODE=autonomous
```

**Default:** `hybrid`

**Progressive path:**
1. Start with `hybrid` for 2-3 weeks (manual trigger builds confidence)
2. Move to `supervised` for 4-6 weeks (validate automation)
3. Graduate to `autonomous` after 20+ successful runs

#### TIER2_ENABLED

Enable/disable Tier 2 condition-based retry logic:

```bash
# Enable Tier 2 (recommended)
TIER2_ENABLED=true

# Disable Tier 2 (Tier 1 only)
TIER2_ENABLED=false
```

**Default:** `true`

**When to disable:**
- Testing Tier 1 execution only
- Market conditions consistently unfavorable
- Debugging execution issues

#### TIER2_VIX_LOW / TIER2_VIX_HIGH

VIX thresholds for Tier 2 execution timing:

```bash
# Very favorable threshold (execute early)
TIER2_VIX_LOW=18

# Unfavorable threshold (skip Tier 2)
TIER2_VIX_HIGH=25
```

**Logic:**
- VIX < 18: Very favorable → execute at 9:50
- VIX 18-25: Moderate → execute at 10:00-10:20
- VIX > 25: Unfavorable → skip Tier 2

**Tuning guidance:**
- If Tier 2 executes too rarely: Raise `TIER2_VIX_HIGH` to 28-30
- If Tier 2 executes during volatility: Lower `TIER2_VIX_LOW` to 15-16
- Track VIX at execution time to optimize thresholds

#### TIER2_MAX_SPREAD

Maximum acceptable bid-ask spread for Tier 2 execution:

```bash
# Default: $0.08
TIER2_MAX_SPREAD=0.08

# More conservative (only very tight spreads)
TIER2_MAX_SPREAD=0.05

# More aggressive (allow wider spreads)
TIER2_MAX_SPREAD=0.10
```

**Default:** `0.08`

**Tuning guidance:**
- If Tier 2 skips due to spreads: Raise to 0.10-0.12
- If Tier 2 fills are poor quality: Lower to 0.05-0.06
- Monitor average spread at execution time

#### TIER2_LIMIT_ADJUSTMENT

Tier 2 limit price aggressiveness multiplier:

```bash
# Default: 10% more aggressive than Tier 1
TIER2_LIMIT_ADJUSTMENT=1.1

# More conservative (5% more)
TIER2_LIMIT_ADJUSTMENT=1.05

# More aggressive (20% more)
TIER2_LIMIT_ADJUSTMENT=1.2
```

**Default:** `1.1` (10% more aggressive)

**How it works:**
- Base limit calculated from current bid/ask
- Tier 2 limit = base limit × adjustment factor
- Example: base=$0.45 → Tier 2=$0.45×1.1=$0.495
- Capped at ask-$0.01 (sanity check)

**Tuning guidance:**
- If Tier 2 doesn't fill: Increase to 1.15-1.2
- If Tier 2 fills at poor prices: Decrease to 1.05
- Start conservative (1.05), increase gradually

#### TIER2_WINDOW_START / TIER2_WINDOW_END

Tier 2 monitoring time window:

```bash
# Default window: 9:45 AM - 10:30 AM
TIER2_WINDOW_START=09:45
TIER2_WINDOW_END=10:30

# Earlier window (9:40-10:15)
TIER2_WINDOW_START=09:40
TIER2_WINDOW_END=10:15

# Later window (10:00-11:00)
TIER2_WINDOW_START=10:00
TIER2_WINDOW_END=11:00
```

**Default:** 9:45-10:30

**Considerations:**
- Earlier window: Better for very liquid options
- Later window: Better if spreads take longer to tighten
- Wider window: More chances for favorable conditions

#### TIER2_CHECK_INTERVAL

How often to check market conditions during Tier 2 window:

```bash
# Default: Every 5 minutes (300 seconds)
TIER2_CHECK_INTERVAL=300

# More frequent: Every 3 minutes
TIER2_CHECK_INTERVAL=180

# Less frequent: Every 10 minutes
TIER2_CHECK_INTERVAL=600
```

**Default:** `300` (5 minutes)

**Trade-offs:**
- More frequent: Faster response to condition changes
- Less frequent: Lower API load, fewer logs

#### Example Configurations

**Conservative (start here):**
```bash
AUTOMATION_MODE=hybrid
TIER2_ENABLED=true
TIER2_VIX_LOW=15
TIER2_VIX_HIGH=22
TIER2_MAX_SPREAD=0.06
TIER2_LIMIT_ADJUSTMENT=1.05
```

**Balanced (after tuning):**
```bash
AUTOMATION_MODE=supervised
TIER2_ENABLED=true
TIER2_VIX_LOW=18
TIER2_VIX_HIGH=25
TIER2_MAX_SPREAD=0.08
TIER2_LIMIT_ADJUSTMENT=1.1
```

**Aggressive (high fill priority):**
```bash
AUTOMATION_MODE=autonomous
TIER2_ENABLED=true
TIER2_VIX_LOW=20
TIER2_VIX_HIGH=30
TIER2_MAX_SPREAD=0.10
TIER2_LIMIT_ADJUSTMENT=1.15
```

---

## Position Monitoring

### Manual Check

```bash
# Show all open positions with P&L
python -m src.cli.main monitor
```

### Continuous Monitoring

```bash
# Monitor only (show alerts)
python -m src.cli.main auto-monitor

# Monitor and auto-exit (DRY RUN)
python -m src.cli.main auto-monitor --auto-exit

# Monitor and auto-exit (LIVE)
python -m src.cli.main auto-monitor --auto-exit --no-dry-run

# Custom check interval (30 seconds)
python -m src.cli.main auto-monitor --check-interval 30 --auto-exit
```

### Exit Triggers

| Trigger | Action |
|---------|--------|
| 50% profit | Close position |
| -200% loss | Stop loss exit |
| 3 DTE remaining | Time-based exit |
| Emergency stop | Close all positions |

---

## Learning Engine

### Weekly Analysis

```bash
# Run full learning cycle
python -m src.cli.main learn --analyze
```

This will:
1. Detect patterns from trade history (35+ dimensions)
2. Validate statistical significance (p < 0.05)
3. Run A/B experiments (80/20 split)
4. Propose parameter optimizations
5. Auto-apply high-confidence changes (>90%)

### View Results

```bash
# See detected patterns
python -m src.cli.main learn --patterns

# See active experiments
python -m src.cli.main learn --experiments

# See parameter proposals
python -m src.cli.main learn --proposals

# See learning summary
python -m src.cli.main learn --summary --days 30
```

### Data Quality

```bash
# Check data quality and coverage
python -m src.cli.main learning-stats

# Export learning data for external analysis
python -m src.cli.main export-learning-data --output data/learning.csv
```

### Daily Snapshots (for Path Analysis)

Schedule this at market close (4 PM ET):

```bash
python -m src.cli.main snapshot-positions
```

---

## TAAD: Trade Archaeology & Alpha Discovery

TAAD imports your actual IBKR trade history for analysis and verification. It uses the Flex Query Web Service to fetch execution records, matches STO/BTC trade pairs, and provides detailed P&L reporting.

### Setup

#### 1. Configure Flex Query in IBKR

1. Log into IBKR Account Management
2. Go to **Reports/Tax Docs** → **Flex Queries**
3. Create a **Trade Confirmation Flex Query**:
   - Sections: Trade Confirmations
   - Delivery: XML
   - Level of Detail: **Execution**
4. Note the **Query ID**
5. Go to **Settings** → **FlexWeb Service** for your **Token**

#### 2. Add Credentials to `.env`

```bash
# Account 1
IBKR_FLEX_TOKEN_1=your_flex_token
IBKR_FLEX_QUERY_ID_1=your_query_id
IBKR_FLEX_ACCOUNT_1=YOUR_ACCOUNT

# Account 2 (optional)
IBKR_FLEX_TOKEN_2=second_token
IBKR_FLEX_QUERY_ID_2=second_query_id
IBKR_FLEX_ACCOUNT_2=U9876543
```

Up to 3 accounts are supported.

### Import Workflow

#### Step 1: Import Trade History

```bash
# Fetch from IBKR Flex Query API
python -m src.cli.main taad-import --account YOUR_ACCOUNT

# Or from a saved XML file
python -m src.cli.main taad-import -a YOUR_ACCOUNT -f flex_report.xml

# Preview without saving (dry run)
python -m src.cli.main taad-import --dry-run
```

**Import details:**
- Parses only **EXECUTION-level** records (not ORDER or SYMBOL_SUMMARY)
- Handles Australian DD/MM/YYYY date format
- Deduplicates by IBKR `execID` (safe to re-run)
- Maps `code` field: `O` → Opening, `C` → Closing
- Maps action: `SELL+O` → entry (STO), `BUY+C` → exit (BTC)
- Stores complete raw XML attributes as JSONB

#### Step 2: Verify Import

```bash
# Check import session details
python -m src.cli.main taad-status
```

Example output:
```
               Recent TAAD Import Sessions
┏━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┓
┃ ID ┃ Account   ┃ Status    ┃ Date Range    ┃ Total ┃ Imported ┃
┡━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━┩
│  2 │ YOUR_ACCOUNT  │ completed │ 2026-01-05 →  │   142 │      142 │
│    │           │           │ 2026-01-30    │       │          │
└────┴───────────┴───────────┴───────────────┴───────┴──────────┘

Database totals:
  Raw imports: 142
  Matched imports: 141
  Trade matches: 131
```

#### Step 3: Review Matched Trades

```bash
# Full report with P&L
python -m src.cli.main taad-report

# Filter by symbol
python -m src.cli.main taad-report --symbol SLV

# Sort by P&L (worst first)
python -m src.cli.main taad-report --sort pnl

# Group by symbol with subtotals
python -m src.cli.main taad-report --sort symbol
```

The report shows:
- **Matched trade table**: Entry/exit dates, symbol, strike, expiry, quantity, premiums, exit type, days held, gross/net P&L
- **Summary panel**: Total trades, winners/losers, win rate, avg days held, gross P&L, commissions, net P&L
- **Per-symbol breakdown**: Trade count, win rate, P&L per symbol

#### Step 4: Check for Gaps

```bash
python -m src.cli.main taad-gaps
```

Example output:
```
Import Coverage
────────────────────────────────────────────────────────────
  Session 2: 2026-01-05 → 2026-01-30  (26 days, 142 records)

No coverage gaps detected

Unmatched Option Records: 0
  All option records matched

Non-Option Unmatched: 1
  2026-01-28 [CASH]  BUY x85727 @0.6999

Match Quality
────────────────────────────────────────────────────────────
  sell_to_open+buy_to_close: 22
  sell_to_open+expiration: 109
  Average confidence: 0.95
  Total matched lifecycles: 131
```

### Trade Matching Logic

TAAD matches trades using this key: `(account_id, underlying_symbol, strike, expiry, put_call)`

**Match types:**
| Type | Description | Confidence |
|------|-------------|------------|
| `sell_to_open+buy_to_close` | STO matched with BTC | 1.0 (perfect), 0.9 (partial) |
| `sell_to_open+expiration` | STO with no close, past expiry | 0.95 |
| `sell_to_open+assignment` | STO with stock assignment | 0.85 |

**Partial closes:** When a BTC covers fewer contracts than the STO, TAAD tracks remaining quantities and matches subsequent BTCs to the same STO.

### Promoting Matched Trades for Enrichment

After importing and verifying matched trades (Steps 1-4 above), you can **promote** them into the `public.trades` table. This makes them visible to the enrichment engine and learning pipeline.

#### Why Promote?

The import pipeline stores raw data in the `import` schema. The enrichment engine and learning pipeline read from `public.trades`. Promotion bridges this gap by creating Trade records with `trade_source='ibkr_import'` for each matched trade lifecycle.

#### Step 5: Promote Matched Trades

```bash
# Preview what would be promoted (no changes)
python -m src.cli.main taad-promote --dry-run

# Promote all matched trades
python -m src.cli.main taad-promote

# Promote only a specific account
python -m src.cli.main taad-promote --account YOUR_ACCOUNT
```

Example output:
```
TAAD Promote — Matched Trades → public.trades
───────────────────────────────────────────────────────

Promotion Complete
────────────────────────────────────────
  Promoted:          3814
  Already promoted:  0
  Skipped (no exit): 0
  Errors:            0
  Total processed:   3814
```

**Key properties:**
- **Idempotent:** Safe to re-run. Already-promoted matches are skipped (dedup by `ibkr_execution_id`).
- **All promoted trades are closed:** Only matched lifecycles (with exit dates) are promoted. They never appear in open-position queries.
- **`trade_source='ibkr_import'`:** Distinguishes promoted historical trades from live trades (`trade_source='live'`).

#### Step 6: Enrich Promoted Trades

```bash
# Enrich a sample first
python -m src.cli.main taad-enrich --limit 10

# Enrich all promoted trades
python -m src.cli.main taad-enrich

# With Barchart option data (requires login first)
python -m src.cli.main taad-barchart-login
python -m src.cli.main taad-enrich --with-scrape

# With IBKR historical data (requires TWS connection)
python -m src.cli.main taad-enrich --with-ibkr
```

#### Filtering by Trade Source

After promotion, the repository supports filtering by `trade_source`:
- Live/paper trades only: `trade_source=["live"]`
- Imports only: `trade_source=["ibkr_import"]`
- All trades (default): no filter

This is used internally by the learning engine to include or exclude historical imports as needed.

### Troubleshooting TAAD

**Issue: Flex Query returns empty XML**
- Verify Query ID is correct in `.env`
- Check the Flex Query date range covers your trades
- Ensure the query is set to **Execution** level of detail

**Issue: Unmatched STOs past expiry**
- Re-run matching: `python -m src.cli.main taad-import --account YOUR_ACCOUNT`
- Check if the closing trade is in a different import period
- Import a wider date range to capture the closing trade

**Issue: Date parsing errors**
- TAAD supports DD/MM/YYYY (Australian), YYYY-MM-DD (ISO), and MM/DD/YYYY formats
- Check your Flex Query locale settings in IBKR

**Issue: Duplicate imports**
- Safe to re-run — deduplication by `execID` prevents double-counting
- Check `taad-status` for `Skipped (dupes)` count

### TAAD Data Model

Data is stored in the PostgreSQL `import` schema, separate from the main trading tables:

| Table | Purpose | Key Fields |
|-------|---------|------------|
| `import.import_sessions` | Import batch tracking | source_type, date_range, record counts |
| `import.ibkr_raw_imports` | Immutable raw records | all IBKR fields + raw_data JSONB |
| `import.trade_matching_log` | Match audit trail | open/close import IDs, match_type, confidence |

The `public.trades` table has been extended with TAAD columns:
- `trade_source` — Origin of the trade (`live`, `paper`, `ibkr_import`)
- `account_id` — IBKR account identifier
- `assignment_status` — none, partial, full
- `ibkr_execution_id` — For deduplication
- `enrichment_status` — pending, partial, complete
- `enrichment_quality` — 0.0-1.0 data quality score

**Data flow:**

```
import.ibkr_raw_imports (raw executions)
    ↓ trade_matcher.py
import.trade_matching_log (matched lifecycles)
    ↓ taad-promote (trade_promoter.py)
public.trades (trade_source='ibkr_import')
    ↓ taad-enrich (engine.py)
public.trade_entry_snapshots + public.trade_exit_snapshots
```

After promotion, `trade_matching_log.matched_trade_id` links back to `public.trades.trade_id`, completing the FK relationship between schemas.

---

## NakedTrader Daily Workflow

NakedTrader is a mechanical daily put selling strategy for SPX, XSP, or SPY index options. It operates independently of the weekly Barchart screening pipeline.

### Daily Routine

**Morning (after market open):**

```bash
# 1. Execute the daily trade
nakedtrader sell XSP --live --yes

# 2. Monitor bracket orders
nakedtrader sell-watch
```

The `sell` command handles the full workflow: fetch underlying price, retrieve option chain with Greeks, select the best strike by delta, display the trade plan, place bracket orders (parent SELL + profit-take BUY + optional stop-loss BUY), wait for parent fill, and record the trade.

**During the day:** Leave `sell-watch` running to track profit-take and stop-loss fills.

**End of day / any time:** Check performance with `nakedtrader sell-status`.

### Config File Setup

All parameters live in `config/daily_spx_options.yaml`. Key sections:

```yaml
instrument:
  default_symbol: XSP       # SPX, XSP, or SPY
  contracts: 1               # Contracts per trade

strike:
  delta_target: 0.065        # Target delta (6.5)
  delta_min: 0.05
  delta_max: 0.12

dte:
  min: 1                     # Never 0 DTE
  max: 4

exit:
  profit_target_pct: 0.70    # Close at 70% profit
  stop_loss_enabled: false   # No stop-loss by default
  stop_loss_multiplier: 3.0  # If enabled: stop at 3x premium
```

Edit the YAML file directly for persistent changes. Use CLI flags for per-run overrides.

### NakedTrader CLI Reference

#### `sell` -- Execute Daily Trade

```bash
nakedtrader sell SYMBOL [OPTIONS]
```

**Argument:**
- `SYMBOL` -- Underlying: `XSP`, `SPX`, or `SPY` (required)

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--dry-run / --live` | Dry run or live paper trading | `--dry-run` |
| `--yes, -y` | Skip confirmation prompt | off |
| `--contracts, -c N` | Override contract count | from config |
| `--delta, -d FLOAT` | Override target delta | from config |
| `--dte N` | Override max DTE | from config |
| `--stop-loss / --no-stop` | Override stop-loss toggle | from config |
| `--no-wait` | Skip waiting for market open | off |
| `--config PATH` | Path to YAML config file | `config/daily_spx_options.yaml` |

**Examples:**

```bash
nakedtrader sell XSP --dry-run                    # Simulate trade
nakedtrader sell XSP --live --yes                 # Execute without confirmation
nakedtrader sell SPX --contracts 2 --delta 0.08 --dry-run
nakedtrader sell SPY --live --stop-loss --yes     # Enable stop-loss for this run
```

#### `sell-watch` -- Monitor Positions

```bash
nakedtrader sell-watch [OPTIONS]
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--interval, -i SECONDS` | Refresh interval | from config (120s) |
| `--once` | Check once and exit | off |
| `--config PATH` | Path to YAML config file | `config/daily_spx_options.yaml` |

**Examples:**

```bash
nakedtrader sell-watch                  # Continuous monitoring (Ctrl+C to stop)
nakedtrader sell-watch --once           # Single snapshot
nakedtrader sell-watch --interval 60    # Refresh every 60s
```

Displays: symbol, strike, expiration, DTE, entry premium, current mid, P&L ($/%),  delta, bracket status.

#### `sell-status` -- Trade History & Performance

```bash
nakedtrader sell-status [OPTIONS]
```

**Options:**

| Flag | Description | Default |
|------|-------------|---------|
| `--history, -n N` | Number of recent trades to show | 20 |

**Examples:**

```bash
nakedtrader sell-status                # Last 20 trades + summary
nakedtrader sell-status --history 50   # Last 50 trades
```

No IBKR connection required. Shows win rate, total P&L, average premium, and recent trade history from the database.

### Troubleshooting NakedTrader

**Issue: "No option chains found for SPX"**

Index options require explicit market data subscriptions in IBKR. Go to Account Management > Market Data Subscriptions and ensure you have index options data enabled.

**Issue: No Greeks returned for any strikes**

IBKR's `modelGreeks` can be empty outside market hours or if the data subscription does not cover computed Greeks. Run `nt` during regular market hours (9:30-16:00 ET).

**Issue: "Could not qualify option contract"**

SPX uses trading class `SPXW` for weekly/daily expirations, XSP uses `XSPW`, SPY uses `SPY`. The system handles this automatically. If qualification fails, verify the expiration date is valid.

**Issue: Bracket order profit-take not triggering**

IBKR bracket children only activate after the parent SELL fills. If the parent is still working, child orders remain inactive. Use `nt-watch` to see bracket status.

**Issue: Both profit-take and stop-loss filled**

IBKR bracket children are linked via OCA (One-Cancels-All) grouping. If one fills, the other should cancel. Run `reconcile-positions` if you see both filled.

---

## 24/7 Operation

### Using systemd (Linux)

Create `/etc/systemd/system/trading-agent.service`:

```ini
[Unit]
Description=Trading Agent Auto-Monitor
After=network.target

[Service]
Type=simple
User=your_user
WorkingDirectory=/path/to/trading_agent
Environment=PATH=/path/to/trading_agent/venv/bin
ExecStart=/path/to/trading_agent/venv/bin/python -m src.cli.main auto-monitor --auto-exit --no-dry-run
Restart=always
RestartSec=60

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable trading-agent
sudo systemctl start trading-agent
sudo systemctl status trading-agent
```

### Using cron

```bash
# Edit crontab
crontab -e

# Add scheduled tasks
# Run scan at market open (9:30 AM ET)
30 9 * * 1-5 cd /path/to/trading_agent && ./venv/bin/python -m src.cli.main scan --from-csv /path/to/daily_export.csv >> logs/scan.log 2>&1

# Snapshot positions at market close (4:00 PM ET)
0 16 * * 1-5 cd /path/to/trading_agent && ./venv/bin/python -m src.cli.main snapshot-positions >> logs/snapshot.log 2>&1

# Weekly learning analysis (Saturday 9 AM)
0 9 * * 6 cd /path/to/trading_agent && ./venv/bin/python -m src.cli.main learn --analyze >> logs/learn.log 2>&1
```

### Using screen/tmux

```bash
# Start in screen session
screen -S trading
python -m src.cli.main auto-monitor --auto-exit --no-dry-run
# Detach: Ctrl+A, D
# Reattach: screen -r trading
```

---

## Troubleshooting

### IBKR Connection Issues

**Error: Cannot connect to IB Gateway/TWS**

1. Verify Gateway/TWS is running
2. Check API is enabled: Configure → API → Settings
3. Verify port: 7497 (paper) or 7496 (live)
4. Whitelist 127.0.0.1 in Trusted IPs
5. Test: `python -m src.cli.main test-ibkr`

**Error: Market data subscription**

- You may not have options market data subscription
- Contact IBKR to enable options data

### Market Data Issues

**No quote data returned**

1. Market may be closed: `python -m src.cli.main market-status`
2. Symbol may be invalid: `python -m src.cli.main option-chain SYMBOL`
3. May need market data subscription

### Database Issues

**Database locked**

1. Close all other connections
2. Check for hung processes: `ps aux | grep python`
3. Reset if needed: `python -m src.cli.main db-reset`

### Common Errors

| Error | Solution |
|-------|----------|
| `No module named 'src'` | Activate venv: `source venv/bin/activate` |
| `BARCHART_API_KEY not set` | Add to `.env` file |
| `Pacing violation` | IBKR rate limit - wait and retry |
| `Contract not found` | Verify strike/expiration are valid |

### Emergency Procedures

```bash
# Halt all trading immediately
python -m src.cli.main emergency-stop

# Close all positions
python -m src.cli.main emergency-stop --liquidate
```

---

## IBKR Client ID Assignments

Each IBKR API connection requires a unique `clientId`. Running two commands with the same client ID will cause one to disconnect the other. The system assigns fixed client IDs so commands can run concurrently without conflicts.

**Important:** Client ID 3 is reserved as the TWS **Master API Client ID**. Do not assign it to any command — TWS allows only one connection on the master ID and it blocks until released.

| Client ID | Commands | Purpose |
|-----------|----------|---------|
| **1** | `stage`, `execute`, `execute-one`, `trade`, `scan`, `monitor`, `quote`, `chain`, `market`, `reconcile`, `sync` | Default — primary trading operations |
| **2** | `watch` | Autonomous position monitoring (runs alongside execute) |
| **3** | *(reserved)* | TWS Master API Client ID — do not use |
| **4** | `nt-watch` | NakedTrader position monitoring |
| **5** | `nt` | NakedTrader trade entry |

**Safe concurrent combinations:**
- `stage` (1) + `nt` (5) + `watch` (2) + `nt-watch` (4)
- `execute-two-tier` (1) + `watch` (2)
- `nt` (5) + `nt-watch` (4)

**Will conflict (same client ID):**
- `stage` + `execute` (both 1) — run sequentially
- `stage` + `monitor` (both 1) — run sequentially

**Overriding:** The `.env` variable `IBKR_CLIENT_ID` sets the default (client ID 1). Commands with explicit overrides ignore this value.

---

## Configuration Reference

### Environment Variables (.env)

```bash
# IBKR Connection
IBKR_HOST=127.0.0.1
IBKR_PORT=7497                    # 7497=paper, 7496=live
IBKR_CLIENT_ID=1

# Database
DATABASE_URL=sqlite:///data/databases/trades.db

# Logging
LOG_LEVEL=INFO
LOG_FILE=logs/app.log

# Trading
PAPER_TRADING=true
MAX_DAILY_LOSS=-0.02
MAX_POSITION_SIZE=5000

# Barchart API (optional)
BARCHART_API_KEY=your_key_here

# Learning
LEARNING_ENABLED=true
MIN_TRADES_FOR_LEARNING=30
```

### Baseline Strategy Parameters

```python
BASELINE_STRATEGY = {
    "otm_range": (0.15, 0.20),      # 15-20% OTM
    "premium_range": (0.30, 0.50),   # $0.30-$0.50
    "dte_range": (7, 14),            # 7-14 days
    "position_size": 5,              # 5 contracts
    "max_positions": 10,             # Max concurrent
    "exit_rules": {
        "profit_target": 0.50,       # 50% profit
        "stop_loss": -2.00,          # -200% loss
        "time_exit": 3               # 3 DTE
    }
}
```

---

## Document History

This guide consolidates:
- 24_7_OPERATION_GUIDE.md
- CLI_REFERENCE.md
- CLI_COMMAND_RENAME_CHANGELOG.md
- TROUBLESHOOTING_VALIDATION.md
- PARAMETER_SYSTEM_EXPLAINED.md
- Parts of various phase completion docs

**Version History:**
| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-01-31 | Initial consolidated version |
| 1.1 | 2026-02-03 | Added Advanced Order Execution section with reconciliation commands and configuration guide |
| 1.2 | 2026-02-11 | Added TAAD (Trade Archaeology & Alpha Discovery) section: import, report, status, gaps commands |
| 1.3 | 2026-02-12 | Added taad-promote command, trade_source filtering, enrichment workflow, data flow diagram |
| 1.4 | 2026-02-16 | Added Adaptive Strike Selection, Fill Management, end-to-end logical flow |
| 1.5 | 2026-02-18 | Added IBKR Client ID Assignments table; fixed nt/nt-watch client IDs to avoid conflicts |
