# Stock Filtering Guide

## Active Filters

Your trading system now includes advanced stock filtering:

### 1. Price Range Filter (Active)

```python
MIN_STOCK_PRICE = 50.0   # $50
MAX_STOCK_PRICE = 150.0  # $150
```

**Why?**
- Eliminates penny stocks (< $50) that have illiquid options
- Excludes very expensive stocks (> $150) like GOOGL, AMZN, etc.
- Focuses on mid-price stocks with liquid option markets

**How it works:**
- Checks current stock price before SMA calculation
- Filters out stocks outside your range immediately
- Saves time by not processing unsuitable stocks

### 2. Market Cap Filter (Reference Only)

```python
MIN_MARKET_CAP = 500_000_000  # $500M
ENABLE_MARKET_CAP_CHECK = False  # Disabled by default
```

**Why disabled?**
- Getting market cap from IBKR is slow and unreliable
- Most S&P 500 stocks already exceed $500M market cap
- Price filter + S&P 500 list provides sufficient filtering

**To enable (not recommended):**
```python
ENABLE_MARKET_CAP_CHECK = True
```
This will verify market cap but will make screening much slower.

## Filter Flow

```
Start with 75+ S&P 500 stocks
    ↓
[1] Price Range Filter ($50-$150)
    ↓ Remaining: ~40-50 stocks
[2] Technical Pattern (Price > SMA20 > SMA50)
    ↓ Remaining: ~10-15 stocks
[3] Options Filter (12-25% OTM, $0.25-$0.50)
    ↓ Final: ~3-8 opportunities
```

## Updated Stock Universe

The symbol list now includes 75+ stocks typically in the $50-$150 range:

**Tech**: AMD, INTC, QCOM, CSCO, ORCL, CRM, etc.
**Financials**: JPM, BAC, WFC, C, GS, MS, etc.
**Healthcare**: JNJ, UNH, PFE, ABBV, MRK, etc.
**Consumer**: WMT, HD, NKE, SBUX, TGT, etc.
**Industrials**: BA, CAT, GE, HON, UPS, etc.
**Energy**: XOM, CVX, COP, SLB, etc.

## Customization Examples

### Example 1: Focus on Tech Stocks Only
Edit `tools/uptrend_screener.py`:
```python
def get_sp500_symbols() -> List[str]:
    return ['AMD', 'INTC', 'QCOM', 'CSCO', 'ORCL', 'CRM', 'NOW', 'ADBE']
```

### Example 2: Higher Price Range
Edit `config/trading_config.py`:
```python
MIN_STOCK_PRICE = 100.0
MAX_STOCK_PRICE = 300.0
```

### Example 3: Lower Price Stocks
```python
MIN_STOCK_PRICE = 25.0
MAX_STOCK_PRICE = 75.0
```

### Example 4: All Stocks (No Filter)
```python
MIN_STOCK_PRICE = 1.0
MAX_STOCK_PRICE = 100000.0
```

## Filter Impact on Results

**Before Filters:**
- Screened all stocks regardless of price
- Got expensive stocks like GOOGL ($140+), NVDA ($140+)
- Many stocks had no suitable options

**After Filters:**
- Only stocks in $50-$150 range
- More consistent option availability
- Better margin efficiency on positions
- Cleaner results with actionable opportunities

## Verifying Filters Work

Run with logging to see filtering in action:

```bash
source ../venv/bin/activate
python3 main.py
```

Look for log messages like:
```
AAPL: Price $178.50 outside range $50-$150
AMD: UPTREND - Price: $87.25, SMA20: $85.10, SMA50: $82.30
```

## Market Cap Notes

**Why $500M minimum?**
- Ensures sufficient liquidity
- Reduces penny stock risk
- Standard threshold for "mid-cap" or larger

**Real market caps of filtered stocks:**
- AMD: ~$140B
- INTC: ~$200B
- BAC: ~$320B
- WMT: ~$650B

All well above the $500M threshold!

## Performance Impact

**Price Filter:**
- Very fast (no API calls)
- Filters ~40% of stocks immediately
- Minimal overhead

**Market Cap Filter (if enabled):**
- Slow (requires API call per stock)
- Adds 1-2 seconds per stock
- Not recommended unless critical

## Quick Reference

| Filter | Default | Speed | Recommended |
|--------|---------|-------|-------------|
| Price Range | $50-$150 | Fast ✓ | Yes ✓ |
| Market Cap | $500M | Slow ✗ | No (disabled) |
| Pattern (SMA) | Price > 20 > 50 | Medium | Yes ✓ |
| Options | 12-25% OTM | Medium | Yes ✓ |

## Troubleshooting

**"No stocks found after filtering"**
- Widen price range: `MIN_STOCK_PRICE = 30, MAX_STOCK_PRICE = 200`
- Check market conditions (bear market may have no uptrends)

**"Too few results"**
- Increase `NUM_STOCKS_TO_SCREEN` to 50
- Widen price range
- Widen OTM range in options

**"Results have wrong prices"**
- Verify TWS/Gateway is connected
- Check you're using paper trading account
- Refresh market data in TWS

## Configuration File Location

All filters are in: `config/trading_config.py`

Edit this file to adjust any filter parameters.
