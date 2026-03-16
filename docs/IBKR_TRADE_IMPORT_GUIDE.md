# IBKR Historical Trade Import Guide

## Purpose

This guide provides step-by-step instructions for importing your historical options trades from Interactive Brokers (IBKR) into the TAAD trading system. Importing past trades enables the self-improvement engine to analyse years of trading data, detect statistically significant patterns, generate hypotheses via Claude AI, and ultimately improve future trading decisions.

**When to use this guide:**
- Initial setup: backfilling the system with your complete trade history
- Periodic maintenance: importing recent trades not captured by the daily job
- After gaps: recovering missed data from system downtime

## Summary

The import process uses IBKR's **Flex Query Web Service** — a one-time setup in Account Management that provides years of historical execution data via HTTP API. The pipeline has 6 steps:

1. **Setup** — Create Flex Queries in IBKR and configure credentials
2. **Import** — Fetch execution records from IBKR into raw storage
3. **Verify** — Check import completeness and statistics
4. **Review** — Inspect matched trade lifecycles and P&L accuracy
5. **Promote** — Move matched trades into the main trades table
6. **Enrich** — Reconstruct historical market context (price, IV, Greeks, technicals)

All commands are **idempotent** — safe to re-run without creating duplicates. The system deduplicates by IBKR's unique execution ID.

---

## Step 1: IBKR Flex Query Setup (One-Time)

Flex Queries are IBKR's mechanism for programmatic access to trade history. You configure them once in Account Management, then the system fetches data automatically via HTTP API.

### 1a. Create Flex Queries

1. Log into **IBKR Account Management** (portal.interactivebrokers.com)
2. Navigate to **Reports/Tax Docs** → **Flex Queries**
3. Click **Create** to create a new Flex Query for each time period:

**Trade Confirmation Query (for daily imports):**
- Query Name: `Daily Trade Confirmations`
- Sections: **Trade Confirmations**
- Delivery Configuration: **XML**
- Level of Detail: **Execution** (critical — must be Execution, not Order or Summary)

**Activity Flex Queries (for historical backfill):**
Create additional queries for longer periods:
- `Last Month Activity` — covers ~30 days
- `Last Quarter Activity` — covers ~90 days
- `Last Year Activity` — covers ~365 days

Each activity query should use:
- Sections: **Trades** (under Activity)
- Delivery: **XML**
- Level of Detail: **Execution**

4. After saving each query, note its **Query ID** (displayed in the query list)

### 1b. Get Your Flex Web Service Token

1. In Account Management, go to **Settings** → **FlexWeb Service**
2. Generate or view your **Token** (a long alphanumeric string)
3. This token authenticates all Flex Query API requests

### 1c. Configure Environment Variables

Add the credentials to your `.env` file:

```bash
# Primary account
IBKR_FLEX_TOKEN_1=your_flex_token_here
IBKR_FLEX_QUERY_ID_1=123456                              # Daily query ID
IBKR_FLEX_ACCOUNT_1=U1234567                              # Your IBKR account ID

# Activity queries for historical backfill
IBKR_FLEX_ACTIVITY_QUERY_LAST_MONTH_1=234567              # Last month query ID
IBKR_FLEX_ACTIVITY_QUERY_LAST_QUARTER_1=345678            # Last quarter query ID
IBKR_FLEX_ACTIVITY_QUERY_LAST_YEAR_1=456789               # Last year query ID

# Second account (optional)
# IBKR_FLEX_TOKEN_2=second_token
# IBKR_FLEX_QUERY_ID_2=567890
# IBKR_FLEX_ACCOUNT_2=U9876543
```

Up to 3 accounts are supported (`_1`, `_2`, `_3` suffixes).

**What happens behind the scenes:** The system calls IBKR's Flex Web Service in two steps: (1) sends a request with your token and query ID, receiving a reference code; (2) polls until the XML report is ready (typically 5-30 seconds). The XML is then parsed, stored, and matched.

---

## Step 2: Import Trade History

This step fetches execution records from IBKR and stores them as raw imports in the database.

### Preview First (Dry Run)

Before committing data, preview what would be imported:

```bash
nakedtrader taad-import --query last_year --dry-run
```

This fetches the XML from IBKR, parses it, and displays a table of execution records — without saving anything to the database. Use this to verify your credentials work and the data looks correct.

### Run the Import

```bash
# Import last year of trades (recommended for initial backfill)
nakedtrader taad-import --query last_year

# Import last quarter
nakedtrader taad-import --query last_quarter

# Import last month
nakedtrader taad-import --query last_month

# Import today's trades (this is what the daily job runs)
nakedtrader taad-import --query daily
```

### Import from a Saved XML File

If you've downloaded a Flex Query report manually from Account Management (useful for data older than 1 year):

```bash
nakedtrader taad-import -f /path/to/flex_report.xml
```

### Specify an Account

If you have multiple accounts configured:

```bash
nakedtrader taad-import --query last_year --account U1234567
```

### Skip Trade Matching

If you only want to store raw records without matching STO/BTC pairs:

```bash
nakedtrader taad-import --query last_year --no-match
```

**What happens behind the scenes:**
1. The Flex Query client calls IBKR's API and downloads the XML report
2. The parser extracts **EXECUTION-level** records (ignores ORDER and SYMBOL_SUMMARY rows)
3. Each execution is stored in the `import.ibkr_raw_imports` table with:
   - All trade details (symbol, strike, expiry, price, quantity, commission)
   - The complete raw XML attributes as JSONB (for future reprocessing)
   - IBKR's unique `execID` as deduplication key
4. The trade matcher runs automatically (unless `--no-match`), pairing STO entries with BTC exits
5. Date formats are handled automatically (DD/MM/YYYY for Australian accounts, ISO for others)

**IBKR Limitation:** Each Flex Query covers a maximum of ~365 days. To import more than 1 year of history, either:
- Download XML exports manually from Account Management for older periods, then import with `-f`
- Create multiple Activity Flex Queries with custom date ranges

---

## Step 3: Verify Import

After importing, verify the data landed correctly:

```bash
nakedtrader taad-status
```

This displays:
- **Recent import sessions** — each with date range, record counts, and status
- **Database totals** — total raw imports, matched imports, and trade matches

Example output:
```
               Recent TAAD Import Sessions
┏━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┓
┃ ID ┃ Account   ┃ Status    ┃ Date Range           ┃ Total ┃ Imported ┃ Dupes ┃ Errors ┃
┡━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━━━╇━━━━━━━╇━━━━━━━━┩
│  1 │ U1234567  │ completed │ 2025-03-15 → 2026-03 │   284 │      284 │     0 │      0 │
└────┴───────────┴───────────┴──────────────────────┴───────┴──────────┴───────┴────────┘

Database totals:
  Raw imports: 284
  Matched imports: 280
  Trade matches: 142
```

**What to check:**
- Status should be `completed` (not `failed`)
- Imported count should match Total (zero errors)
- If you re-run, the "Dupes" column shows how many were already imported (this is normal)

---

## Step 4: Review Matched Trades

Inspect the matched trade lifecycles to verify P&L accuracy against your IBKR statements:

```bash
# Full report with all matched trades
nakedtrader taad-report

# Filter by symbol
nakedtrader taad-report --symbol AAPL

# Sort by P&L (worst trades first — useful for reviewing losses)
nakedtrader taad-report --sort pnl

# Group by symbol with subtotals
nakedtrader taad-report --sort symbol

# Show unmatched records (STOs without matching BTCs)
nakedtrader taad-report --unmatched

# Show all raw records
nakedtrader taad-report --raw
```

The report shows:
- **Matched trade table** — entry/exit dates, symbol, strike, expiry, quantity, entry/exit premiums, exit type (buy_to_close or expiration), days held, gross and net P&L
- **Summary panel** — total trades, winners/losers, win rate, average days held, gross P&L, commissions, net P&L (per currency if multi-currency)
- **Per-symbol breakdown** — trade count, win rate, and P&L for each underlying

**What happens behind the scenes:** The trade matcher pairs records using the key `(account_id, underlying_symbol, strike, expiry, put_call)`:

| Match Type | Description | Confidence |
|-----------|-------------|------------|
| `sell_to_open+buy_to_close` | STO matched with BTC | 1.0 (perfect) or 0.9 (partial close) |
| `sell_to_open+expiration` | STO with no close, past expiry date | 0.95 |
| `sell_to_open+assignment` | STO with stock assignment detected | 0.85 |

Partial closes are handled automatically — if you sold 5 contracts and closed 3, TAAD tracks the remaining 2 and matches subsequent BTCs.

---

## Step 5: Check for Gaps

Before promoting, verify there are no data quality issues:

```bash
nakedtrader taad-gaps
```

This shows:
- **Import coverage** — date ranges covered by each import session
- **Calendar gaps** — missing date ranges between sessions
- **Unmatched records** — open STOs with no closing trade (may be still open or missing BTC data)
- **Match quality** — breakdown by match type and average confidence score

Example output:
```
Import Coverage
────────────────────────────────────────────────────────────
  Session 1: 2025-03-15 → 2026-03-12  (363 days, 284 records)

No coverage gaps detected

Unmatched Option Records: 3
  Open STOs (no close found): 3
    2026-03-10 AAPL 210.0P exp=2026-03-14 x2 @0.4500 (still open)
    2026-03-11 MSFT 380.0P exp=2026-03-14 x1 @0.3200 (still open)

Match Quality
────────────────────────────────────────────────────────────
  sell_to_open+buy_to_close: 34
  sell_to_open+expiration: 108
  Average confidence: 0.96
  Total matched lifecycles: 142
```

**What to look for:**
- **Coverage gaps**: If you see gaps, run additional imports for the missing date ranges
- **Unmatched STOs marked "(still open)"**: These are positions currently open — expected and normal
- **Unmatched STOs marked "(past expiry)"**: These may need investigation — the close/expiration data might be missing
- **Orphan BTCs**: Buy-to-close records without a matching STO — the opening trade may be outside the import date range

---

## Step 6: Promote to Main Trades Table

The import pipeline stores raw data in the `import` database schema, separate from the main `public.trades` table used by the learning engine and enrichment pipeline. This step bridges that gap.

### Preview First

```bash
nakedtrader taad-promote --dry-run
```

### Promote All Matched Trades

```bash
nakedtrader taad-promote
```

### Promote Specific Account

```bash
nakedtrader taad-promote --account U1234567
```

Example output:
```
TAAD Promote — Matched Trades → public.trades
───────────────────────────────────────────────────────

Promotion Complete
────────────────────────────────────
  Promoted:          139
  Already promoted:  0
  Skipped (no exit): 3
  Errors:            0
  Total processed:   142

  Run nakedtrader taad-enrich to enrich the promoted trades.
```

**What happens behind the scenes:**
- Each matched trade lifecycle (STO+BTC pair or STO+expiration) creates a `Trade` record in `public.trades`
- Trades are marked with `trade_source='ibkr_import'` to distinguish them from daemon-placed trades
- Deduplication uses the IBKR execution ID — re-running promotion is safe
- Only closed trades (with an exit date) are promoted; still-open positions are skipped
- P&L is calculated from actual execution prices and commissions

---

## Step 7: Enrich with Market Context

Enrichment reconstructs what the market looked like when each trade was opened and closed. This data powers the learning engine's 23-dimension pattern detection.

### Preview First

```bash
nakedtrader taad-enrich --dry-run
```

### Enrich All Promoted Trades

```bash
nakedtrader taad-enrich
```

### Enrich with IBKR Historical Data

For richer data (requires TWS connection):

```bash
nakedtrader taad-enrich --with-ibkr
```

### Enrich Specific Trades

```bash
# Specific symbol
nakedtrader taad-enrich --symbol AAPL

# Limit batch size (useful for testing)
nakedtrader taad-enrich --limit 10

# Re-enrich already-enriched trades
nakedtrader taad-enrich --force

# Specific account
nakedtrader taad-enrich --account U1234567
```

Example output:
```
TAAD Enrichment — Historical Trade Context Reconstruction
────────────────────────────────────────────────────────────
  Trades to enrich: 139
  Force re-enrich:  No
  IBKR data:        No

  Starting enrichment...

Enrichment Complete
────────────────────────────────────
  Total:       139
  Enriched:    135
  Merged:      0
  Skipped:     0
  Failed:      4
  Avg quality: 0.847
```

**What happens behind the scenes:** For each trade, the enrichment engine creates entry and exit snapshots containing:

| Data Point | Source | Description |
|-----------|--------|-------------|
| Stock price | yfinance | Underlying price at entry/exit |
| SMA (20/50/200) | yfinance | Simple moving averages |
| Historical volatility | yfinance | 20-day realized volatility |
| VIX level | yfinance | CBOE Volatility Index |
| Implied volatility | Black-Scholes | Approximated from option price |
| Delta/Gamma/Theta | Black-Scholes | Approximated Greeks |
| RSI | Calculated | 14-day relative strength index |
| Beta | Calculated | 60-day beta vs SPY |
| Sector | Lookup | Stock sector classification |
| FOMC proximity | Calendar | Days to nearest Fed meeting |
| Earnings proximity | Calendar | Days to nearest earnings date |

The `--with-ibkr` flag adds IBKR historical bars (more accurate than yfinance for options data) but requires an active TWS connection.

---

## Quick Reference

### Complete Backfill Workflow (Copy-Paste)

```bash
# 1. Import last year of trades
nakedtrader taad-import --query last_year

# 2. Check import status
nakedtrader taad-status

# 3. Review matched trades
nakedtrader taad-report

# 4. Check for gaps
nakedtrader taad-gaps

# 5. Promote to main trades table
nakedtrader taad-promote

# 6. Enrich with market context
nakedtrader taad-enrich
```

### Daily Maintenance (Automated)

The daily import job runs automatically and covers today's trades:

```bash
nakedtrader taad-import --query daily
```

### Command Reference

| Command | Description |
|---------|-------------|
| `nakedtrader taad-import` | Import trades from IBKR Flex Query |
| `nakedtrader taad-status` | Show import sessions and database stats |
| `nakedtrader taad-report` | Display matched trades with P&L |
| `nakedtrader taad-gaps` | Check data coverage and quality |
| `nakedtrader taad-promote` | Move matched trades to main trades table |
| `nakedtrader taad-enrich` | Add historical market context |

### Common Flags

| Flag | Available On | Description |
|------|-------------|-------------|
| `--account, -a` | All commands | Filter by IBKR account ID |
| `--dry-run` | import, promote, enrich | Preview without making changes |
| `--query, -q` | import | Query type: `daily`, `last_month`, `last_quarter`, `last_year` |
| `--xml-file, -f` | import | Import from local XML file |
| `--symbol, -s` | report, enrich | Filter by underlying symbol |
| `--sort` | report | Sort by: `date`, `symbol`, `pnl` |
| `--unmatched, -u` | report | Show unmatched records |
| `--force` | enrich | Re-enrich already-enriched trades |
| `--with-ibkr` | enrich | Use IBKR historical bars (needs TWS) |
| `--limit, -n` | status, enrich | Limit number of records |
| `--no-match` | import | Skip automatic trade matching |

---

## Troubleshooting

### "No Flex Query credentials configured"
Verify `.env` has `IBKR_FLEX_TOKEN_1`, `IBKR_FLEX_QUERY_ID_1`, and `IBKR_FLEX_ACCOUNT_1` set. For activity queries, also set the corresponding `IBKR_FLEX_ACTIVITY_QUERY_LAST_YEAR_1` etc.

### Import shows 0 records
- Check that the Flex Query's Level of Detail is set to **Execution** (not Order or Summary)
- Verify the date range of the query covers the period you're importing
- Try `--dry-run` to see the raw XML parsing output

### Unmatched records after import
- **Open STOs (still open):** Normal for current positions — they'll match when the position closes
- **Past-expiry STOs:** The BTC execution might be in a different import period — import a broader date range
- **Orphan BTCs:** The opening STO is outside your import window — extend the date range backward

### Enrichment failures
- Some symbols may have been delisted or renamed — these fail gracefully
- yfinance has rate limits — large batches may need `--limit` to process in chunks
- Use `--with-ibkr` for more reliable data when TWS is connected

### More than 1 year of history
IBKR Flex Queries max at ~365 days per query. For older data:
1. In Account Management, download Activity Statements as XML for older periods
2. Import each file: `nakedtrader taad-import -f statement_2024.xml`
3. The system deduplicates automatically, so overlapping periods are safe

---

*Last updated: 2026-03-13*
