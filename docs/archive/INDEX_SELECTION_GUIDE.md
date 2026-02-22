# Index Selection Guide

## Overview

The trading agent now supports interactive selection from 11 major stock indices. This allows you to scan specific market segments instead of a fixed stock list.

## Features

### 1. **Interactive Menu**
At startup, you'll see a menu with 11 index options:
```
  STOCK INDEX SELECTION
======================================================================

Available Indices:

  [ 1] S&P 100 (OEX)                    - 100 largest US companies
  [ 2] S&P 500 (SPX)                    - 500 largest US companies
  [ 3] Nasdaq 100 (NDX)                 - 100 largest non-financial Nasdaq stocks
  [ 4] Dow Jones Industrial Average     - 30 prominent US companies
  [ 5] Russell 2000 (RUT)               - 2000 small-cap US stocks
  [ 6] S&P MidCap 400 (MID)             - 400 mid-cap US companies
  [ 7] S&P SmallCap 600 (SML)           - 600 small-cap US companies
  [ 8] Nasdaq Composite (COMP)          - All Nasdaq-listed stocks
  [ 9] Russell 1000 (RUI)               - 1000 largest US companies
  [10] FTSE 100 (UKX)                   - 100 largest UK companies
  [11] Custom/Manual List                - User-defined stock list

Select an index (1-11) or 'q' to quit:
```

### 2. **File Caching System**

Index constituents are cached in `data/indices/` directory:

```
data/
└── indices/
    ├── sp100.txt
    ├── sp500.txt
    ├── nasdaq100.txt
    ├── djia.txt
    ├── russell2000.txt
    ├── sp400.txt
    ├── sp600.txt
    ├── nasdaq_composite.txt
    ├── russell1000.txt
    ├── ftse100.txt
    └── custom.txt
```

**File Format:**
- One stock symbol per line
- Symbols are automatically converted to uppercase
- Empty lines are ignored

Example (`sp100.txt`):
```
AAPL
MSFT
GOOGL
AMZN
NVDA
...
```

### 3. **Configurable Stock Limit**

The number of stocks scanned is controlled by `config/trading_config.py`:

```python
# Number of stocks to screen per batch
NUM_STOCKS_TO_SCREEN = 10  # Scan 10 stocks at a time
```

This limits scanning to the first N stocks from the selected index, making the workflow faster.

## How It Works

### Workflow

1. **Check Cache**
   - Looks for cached file in `data/indices/`
   - If file exists, loads symbols from it

2. **Fallback to Defaults**
   - If no cache file exists, uses built-in default lists
   - Saves defaults to cache for future use

3. **Limit Stocks**
   - Limits to `NUM_STOCKS_TO_SCREEN` from config
   - Displays how many stocks will be scanned

4. **Run Workflow**
   - Screens selected stocks for uptrend
   - Finds PUT options
   - Calculates margin efficiency

### Cache Management

**When cache is created:**
- First time an index is selected (if no cache exists)
- When using Custom index option (creates sample file)
- Built-in defaults are saved automatically

**Updating cached symbols:**
1. Navigate to `data/indices/`
2. Edit the appropriate `.txt` file
3. Add/remove symbols (one per line)
4. Save the file
5. Next run will use updated list

## Usage Examples

### Example 1: Quick Start (Dow Jones 30)

```bash
python3 main.py
```

At the menu, select `4` for Dow Jones:
```
Select an index (1-11) or 'q' to quit: 4

→ Selected: Dow Jones Industrial Average (INDU)
→ Description: 30 prominent US companies
→ Loading stock symbols...
→ Loaded 30 symbols
→ Will scan: 10 stocks
→ Sample symbols: AAPL, MSFT, UNH, GS, HD, CAT, BA, HON, IBM, CRM
```

### Example 2: Scan More Stocks

Edit `config/trading_config.py`:
```python
NUM_STOCKS_TO_SCREEN = 25  # Scan 25 instead of 10
```

Now when you select an index, it will scan 25 stocks.

### Example 3: Custom Stock List

1. Select option `11` (Custom/Manual List)
2. Edit `data/indices/custom.txt`:
   ```
   AAPL
   MSFT
   TSLA
   NVDA
   AMD
   ```
3. Run the program again and select `11`
4. Your custom stocks will be scanned

### Example 4: Update S&P 500 List

To use the latest S&P 500 constituents:

1. Get latest S&P 500 list from a data provider
2. Save to `data/indices/sp500.txt` (one symbol per line)
3. Next time you select S&P 500, it uses your updated list

## Configuration

### Stock Limit

**File:** `config/trading_config.py`

```python
# Scan 10 stocks at a time (faster)
NUM_STOCKS_TO_SCREEN = 10

# Scan 25 stocks at a time (more opportunities)
NUM_STOCKS_TO_SCREEN = 25

# Scan 50 stocks at a time (comprehensive)
NUM_STOCKS_TO_SCREEN = 50
```

**Trade-offs:**
- **10 stocks**: ~30-60 seconds, good for testing
- **25 stocks**: ~2-3 minutes, balanced approach
- **50 stocks**: ~5-8 minutes, comprehensive scan

### Price Range Filter

The price range filter is applied AFTER index selection:

```python
MIN_STOCK_PRICE = 50.0   # Skip stocks below $50
MAX_STOCK_PRICE = 150.0  # Skip stocks above $150
```

If you select Dow Jones (30 stocks) but only 15 stocks are in the $50-$150 range, only those 15 will be analyzed for uptrend.

## Testing

### Test the Menu System

```bash
python3 test_index_menu.py
```

This tests the menu without running the full trading workflow.

### Test with Small Index

For quick testing, use Dow Jones (30 stocks) with `NUM_STOCKS_TO_SCREEN = 5`:

```python
# config/trading_config.py
NUM_STOCKS_TO_SCREEN = 5
```

```bash
python3 main.py
# Select option 4 (Dow Jones)
```

This will complete in ~15-30 seconds.

## Default Symbol Lists

Each index has a built-in default list used when no cache exists:

- **S&P 100**: 100 large-cap stocks (full list)
- **S&P 500**: First 100 stocks (sample)
- **Nasdaq 100**: 60 tech stocks (sample)
- **Dow Jones**: All 30 stocks
- **Russell 2000**: 40 small-cap stocks (sample)
- **S&P MidCap 400**: 20 stocks (sample)
- **S&P SmallCap 600**: 10 stocks (sample)
- **FTSE 100**: 10 UK stocks (sample)

For production use, update the cache files with complete index constituent lists from a data provider.

## Error Handling

### File I/O Errors

If a cache file is corrupted:
```
ERROR - Error reading cache file data/indices/sp500.txt: ...
```

**Solution:** Delete the corrupted file and run again. Defaults will be used and saved.

### Empty Index

If all stocks are filtered out (e.g., none in price range):
```
WARNING - No stocks found in uptrend out of 25
```

**Solution:**
- Adjust `MIN_STOCK_PRICE` / `MAX_STOCK_PRICE` in config
- Select a different index
- Check that IBKR connection is working

### Missing Symbols

If IBKR can't find a symbol:
```
ERROR - Error screening XYZ: ...
```

**Solution:** Remove invalid symbols from cache file.

## Advanced Usage

### Multiple Scans

To scan multiple indices in one session:

```bash
# Scan Dow Jones
python3 main.py  # Select 4

# Scan Nasdaq 100
python3 main.py  # Select 3

# Scan S&P 100
python3 main.py  # Select 1
```

Each run is independent and logs to a separate file in `logs/`.

### Filtering by Sector

Create custom index files for specific sectors:

```bash
# data/indices/tech_stocks.txt
AAPL
MSFT
GOOGL
AMZN
NVDA
AMD
INTC
...

# data/indices/finance_stocks.txt
JPM
BAC
GS
MS
WFC
...
```

Then select "Custom/Manual List" and swap the `custom.txt` file.

### Batch Processing

To scan all indices automatically (advanced):

```python
# batch_scan.py
from utils_indices import get_index_symbols, AVAILABLE_INDICES
from agents.trading_agent import run_trading_workflow

for key in ["1", "2", "3", "4"]:  # S&P 100, 500, Nasdaq 100, Dow
    index_info = AVAILABLE_INDICES[key]
    symbols = get_index_symbols(key, max_stocks=10)

    print(f"\nScanning {index_info['name']}...")
    result = run_trading_workflow(
        num_stocks=10,
        symbols=symbols
    )
    # Process result...
```

## Troubleshooting

### "No selection made"

You entered an invalid option. Valid options: 1-11 or 'q'.

### "No symbols found for [index]"

The cache file is empty or missing, and defaults failed to load. Check logs for details.

### Slow Performance

- Reduce `NUM_STOCKS_TO_SCREEN` to 10 or less
- Reduce `LOOKBACK_DAYS` to 60
- Select smaller indices (Dow Jones has only 30 stocks)

### IBKR Rate Limits

```
IBKR pacing violation - consider reducing request rate
```

- Wait 10 minutes before running again
- Reduce `NUM_STOCKS_TO_SCREEN`
- Increase `RATE_LIMIT_DELAY` in config

## Summary

The index selection system provides:

✅ **Flexibility** - Choose from 11 major indices or create custom lists
✅ **Speed** - Configurable stock limit for faster scans
✅ **Caching** - Reuse index constituent lists
✅ **Simplicity** - Interactive menu, no code changes needed
✅ **Extensibility** - Easy to add new indices or update existing ones

For most users:
1. Select an index from the menu
2. Wait for the scan to complete
3. Review recommendations
4. Repeat with different indices as needed
