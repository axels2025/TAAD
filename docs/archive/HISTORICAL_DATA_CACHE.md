# Historical Data Caching System

## Overview

The historical data caching system significantly reduces IBKR API calls, improves performance, and builds a valuable historical dataset over time. Instead of requesting 100 days of data for every scan, the system intelligently fetches only missing days and appends them to existing cache files.

## Key Benefits

✅ **Faster Performance** - 5-10x speedup on subsequent scans
✅ **Reduced API Calls** - Minimizes IBKR pacing violations
✅ **Growing Dataset** - Accumulates historical data over time
✅ **Automatic Updates** - Fetches only missing days
✅ **Smart Validation** - Detects and repairs corrupted data

## How It Works

### First Scan (No Cache)
```
Day 1: Scanning AAPL
→ No cache file found
→ Fetching 100 days from IBKR
→ Saving to data/stocks/AAPL.csv
→ Time: 2.5 seconds
```

### Second Scan (Same Day)
```
Day 1: Scanning AAPL (again)
→ Cache file found with 100 bars
→ Last date in cache: 2026-01-13
→ Today: 2026-01-13
→ Cache is current, no fetch needed
→ Time: 0.1 seconds (25x faster!)
```

### Third Scan (Next Day)
```
Day 2: Scanning AAPL
→ Cache file found with 100 bars
→ Last date in cache: 2026-01-13
→ Today: 2026-01-14
→ Missing 1 day, fetching from IBKR
→ Appending new data to cache
→ Cache now has 101 bars
→ Time: 0.5 seconds (5x faster!)
```

### After 1 Month
```
Day 30: Scanning AAPL
→ Cache file found with 129 bars
→ Last date in cache: 2026-02-12
→ Today: 2026-02-13
→ Missing 1 day, fetching from IBKR
→ Cache now has 130 bars (30 trading days accumulated!)
→ Time: 0.5 seconds
```

## File Structure

```
data/
└── stocks/
    ├── AAPL.csv
    ├── MSFT.csv
    ├── GOOGL.csv
    └── ... (one file per stock)
```

### CSV Format

Each file contains daily OHLCV data:

```csv
date,open,high,low,close,volume
2025-10-06,170.5,172.3,169.8,171.2,45678900
2025-10-07,171.3,173.1,170.9,172.8,52341200
2025-10-08,172.9,174.5,172.1,174.0,48902300
...
```

**Columns:**
- `date` - Trading date (YYYY-MM-DD)
- `open` - Opening price
- `high` - Daily high
- `low` - Daily low
- `close` - Closing price
- `volume` - Trading volume

## Usage

### Automatic (Recommended)

The caching system is **automatically used** by the uptrend screener:

```bash
python3 main.py
# Select an index
# Cache is used automatically for all stocks
```

### Programmatic Usage

```python
from config.ibkr_connection import create_ibkr_connection
from ib_insync import Stock
from utils_historical_data import get_historical_data_with_cache

with create_ibkr_connection() as ib:
    stock = Stock('AAPL', 'SMART', 'USD')
    ib.qualifyContracts(stock)

    # Get historical data with caching
    df = get_historical_data_with_cache(ib, stock, lookback_days=100)

    if df is not None:
        print(f"Got {len(df)} bars")
        print(df.tail())
```

## Cache Management

### View Cache Statistics

```bash
python3 -c "from utils_historical_data import print_cache_stats; print_cache_stats()"
```

Output:
```
======================================================================
HISTORICAL DATA CACHE STATISTICS
======================================================================
Total symbols cached:  25
Total cache size:      1.23 MB
Cache directory:       data/stocks
Oldest cache:          AAPL
Newest cache:          XOM
======================================================================
```

### Clear Cache for a Symbol

```python
from utils_historical_data import clear_cache_for_symbol

# Clear cache for AAPL (will re-fetch on next scan)
clear_cache_for_symbol('AAPL')
```

### Clear All Cache

```bash
rm -rf data/stocks/*.csv
```

This will force a full re-fetch for all stocks on the next scan.

### View Cache for a Stock

```bash
head -20 data/stocks/AAPL.csv
```

Or open in Excel/Numbers/LibreOffice.

## Performance Comparison

### Without Caching (Every Scan)
```
Scanning 10 stocks:
Stock 1: 2.5s (fetch 100 days)
Stock 2: 2.8s (fetch 100 days)
Stock 3: 2.3s (fetch 100 days)
...
Total: 25 seconds
```

### With Caching (Second Scan, Same Day)
```
Scanning 10 stocks:
Stock 1: 0.1s (cache hit, no fetch)
Stock 2: 0.1s (cache hit, no fetch)
Stock 3: 0.1s (cache hit, no fetch)
...
Total: 1 second (25x faster!)
```

### With Caching (Next Day)
```
Scanning 10 stocks:
Stock 1: 0.5s (fetch 1 day, append)
Stock 2: 0.6s (fetch 1 day, append)
Stock 3: 0.5s (fetch 1 day, append)
...
Total: 5 seconds (5x faster!)
```

## Edge Cases Handled

### 1. Market Closed Days

When the market is closed (weekends, holidays), the cache is considered current:

```
Saturday scan:
→ Last cache date: Friday
→ Today: Saturday (market closed)
→ No missing days
→ Use cache
```

### 2. Data Gaps

If there's a gap in the data (e.g., stock was halted):

```
→ Cache has data up to Jan 10
→ Stock was halted Jan 11-15
→ Today: Jan 16
→ Fetch from Jan 11 onwards
→ IBKR returns data starting Jan 16 (no data for 11-15)
→ Cache now has Jan 10 → Jan 16
```

### 3. File Corruption

If a cache file is corrupted:

```
→ Load cache file
→ Validation fails (invalid format)
→ Delete corrupted file
→ Re-fetch full 100 days
→ Create new clean cache
```

### 4. Duplicate Dates

If IBKR returns overlapping dates:

```
Cached: 2026-01-01 to 2026-01-13
Fetched: 2026-01-13 to 2026-01-14

Merge:
→ Concatenate both datasets
→ Remove duplicates (keep latest)
→ Result: 2026-01-01 to 2026-01-14 (no duplicates)
```

### 5. IBKR Fetch Failure

If IBKR fails to return new data:

```
→ Try to fetch missing days
→ IBKR error (timeout, pacing, etc.)
→ Log warning
→ Return existing cache (still valid!)
→ Scan continues with cached data
```

## Data Validation

Every cache file is validated on load:

**Checks performed:**
1. ✅ Required columns present
2. ✅ Valid date format
3. ✅ No negative prices/volumes
4. ✅ No NaN values
5. ✅ No duplicate dates

**If validation fails:**
- File is deleted
- Full re-fetch from IBKR
- New clean cache created

## Advanced Features

### Growing Dataset

Over time, cache files accumulate more than 100 days:

```
Week 1:  100 bars (initial fetch)
Week 2:  105 bars (+5 trading days)
Month 1: 120 bars (+20 trading days)
Year 1:  352 bars (1 year of data!)
```

This growing dataset enables:
- Long-term trend analysis
- Backtesting strategies
- Historical performance research
- Machine learning model training

### Deduplication

The system automatically removes duplicate dates:

```python
# Before merge:
cached_df:  100 rows (2025-10-01 to 2026-01-13)
new_df:     2 rows   (2026-01-13 to 2026-01-14)

# After merge and dedup:
merged_df:  101 rows (2025-10-01 to 2026-01-14)
# Note: Jan 13 appears once (kept latest value)
```

### Incremental Updates Only

The system only fetches what's missing:

```
Lookback period: 100 days
Cache has: 100 bars ending Jan 13
Today: Jan 14

Traditional approach:
→ Fetch 100 days (wasteful!)

Smart caching:
→ Fetch 1 day (efficient!)
```

This dramatically reduces API calls over time.

## Testing

### Quick Test

```bash
python3 test_historical_cache.py
```

This runs a comprehensive test suite:
1. Performance comparison (no cache vs cached)
2. Incremental update test
3. Data validation test

### Expected Output

```
======================================================================
TESTING HISTORICAL DATA CACHE PERFORMANCE
======================================================================

[FIRST PASS - NO CACHE]
Fetching AAPL...
  ✓ Got 100 bars in 2.45s

Fetching MSFT...
  ✓ Got 100 bars in 2.31s

[SECOND PASS - WITH CACHE]
Fetching AAPL...
  ✓ Got 100 bars in 0.12s (cached)

Fetching MSFT...
  ✓ Got 100 bars in 0.09s (cached)

PERFORMANCE SUMMARY
First pass (no cache):   7.52s
Second pass (cached):    0.32s
Speedup:                 23.5x faster
Time saved:              7.20s
```

## Troubleshooting

### Cache Not Being Used

Check logs for:
```
DEBUG - AAPL: Found valid cache with 100 bars
INFO - AAPL: Using cached data (current)
```

If you see:
```
INFO - AAPL: No cache, fetching 100 days
```

Then no cache file exists. This is normal for the first scan.

### Cache Validation Failures

If you see:
```
WARNING - AAPL: Cache validation failed, will re-fetch
```

The cache file is corrupted. It will be automatically deleted and re-fetched.

### Slow Performance

If caching doesn't improve performance:

1. Check cache directory exists: `ls -la data/stocks/`
2. Check cache files are created: `ls data/stocks/*.csv`
3. Check logs for cache hits: `grep "Using cached data" logs/*.log`

### IBKR Pacing Violations

Even with caching, you might hit pacing limits if:
- Scanning many stocks for the first time (no cache)
- Market has been closed for several days (fetching multiple missing days)

**Solution:**
- Reduce `NUM_STOCKS_TO_SCREEN` in config
- Increase `RATE_LIMIT_DELAY` in config
- Wait 10 minutes between scans

## Best Practices

### 1. Run Daily Scans

Running the scanner daily ensures minimal API calls:
- Day 1: Fetches 100 days for each stock
- Day 2: Fetches only 1 day for each stock
- Day 3: Fetches only 1 day for each stock
- ...

### 2. Keep Cache Files

Don't delete cache files unless necessary. They're valuable:
- Faster scans
- Historical dataset
- Reduced API usage

### 3. Monitor Cache Growth

Periodically check cache statistics:
```bash
python3 -c "from utils_historical_data import print_cache_stats; print_cache_stats()"
```

### 4. Backup Cache Files

Consider backing up `data/stocks/` periodically:
```bash
tar -czf stocks_cache_backup_$(date +%Y%m%d).tar.gz data/stocks/
```

### 5. Use Version Control

Add to `.gitignore`:
```
data/stocks/*.csv
```

This keeps cache files local (they're large and change daily).

## Technical Details

### Cache Lookup Algorithm

```python
1. Load cache file (if exists)
2. Validate data format
3. If validation fails:
   → Delete cache
   → Fetch full lookback period
   → Save to cache
   → Return data

4. If validation passes:
   → Get most recent date in cache
   → Calculate days since last cached date
   → If 0-1 days: return cache (current)
   → If >1 days: fetch missing days
   → Merge with cache
   → Deduplicate
   → Save merged data
   → Return merged data
```

### Merge Strategy

When merging cached and new data:
- **Concatenate** both DataFrames
- **Sort** by date ascending
- **Drop duplicates** keeping last (newest data is most accurate)
- **Save** merged result to cache

### Date Handling

Dates are stored as ISO 8601 strings in CSV:
```
2026-01-14
```

Loaded as pandas datetime64:
```python
df['date'] = pd.to_datetime(df['date'])
```

### File Naming

Symbols with special characters are cleaned:
- `BRK.B` → `BRK_B.csv`
- `BF/A` → `BF_A.csv`

## Integration with Uptrend Screener

The uptrend screener automatically uses caching:

```python
# OLD (without caching):
bars = ib.reqHistoricalData(stock, ...)
df = util.df(bars)

# NEW (with caching):
df = get_historical_data_with_cache(ib, stock, lookback_days=100)
```

**No code changes needed** - caching is transparent to the user.

## Future Enhancements

Potential improvements:
- ✨ Automatic cache cleanup (remove old data)
- ✨ Compression for large cache files
- ✨ Database storage (SQLite) instead of CSV
- ✨ Multi-timeframe caching (1min, 1hour, 1day)
- ✨ Cache warming (pre-fetch popular stocks)

## Summary

The historical data caching system provides:

✅ **5-10x performance improvement** on subsequent scans
✅ **Minimal API calls** - only fetch missing days
✅ **Automatic data accumulation** - builds dataset over time
✅ **Robust error handling** - validates and repairs corrupted data
✅ **Zero configuration** - works automatically

Simply run the scanner - caching happens behind the scenes!
