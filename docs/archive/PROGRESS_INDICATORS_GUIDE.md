# Progress Indicators Guide

## Understanding the Progress Bar Behavior

### Why the Progress Bar Appears "Stuck"

When you see the progress bar at "4% - Processing AMD", this is **normal and expected behavior**. Here's why:

1. **Progress Updates Once Per Stock**
   - The progress bar shows 1/25 (4%) for the first stock
   - It will show 2/25 (8%) for the second stock, etc.
   - Each stock takes time to process, so the bar stays at each percentage while working

2. **What Takes Time**
   - **Contract qualification**: ~0.5 seconds (fast)
   - **Historical data fetch**: 1-15 seconds per stock (slow!)
   - **SMA calculations**: <0.1 seconds (fast)

   The historical data request is the bottleneck.

3. **IBKR Rate Limits**
   - IBKR limits historical data requests to ~60 per 10 minutes
   - If you hit the limit, requests can be delayed or fail
   - This is why the progress bar might sit on one stock for a long time

### Example Timeline

```
00:00 - Progress bar shows: 1/25 (4%) Processing AMD
00:01 - Still fetching AMD historical data... (bar unchanged)
00:02 - Still fetching AMD historical data... (bar unchanged)
00:03 - AMD complete, moving to INTC
00:03 - Progress bar shows: 2/25 (8%) Processing INTC
00:04 - Still fetching INTC historical data... (bar unchanged)
...
```

## Improvements Made

### 1. Added Timeout Protection
- Historical data requests now timeout after 15 seconds
- Prevents the app from hanging indefinitely on slow requests
- If a stock times out, it's skipped and the app moves to the next one

### 2. Better Logging
- INFO level logs show: `[1/25] Processing AMD...`
- INFO level logs show: `AMD: Requesting 100 days of historical data...`
- INFO level logs show: `AMD: Received 252 bars`
- You can monitor progress in the log even if the visual bar doesn't update

### 3. Error Handling
- Catches IBKR pacing violations
- Shows clear error messages if data fetch fails
- Continues processing remaining stocks

### 4. Rate Limiting
- Small delay (0.1s) between stocks to avoid overwhelming IBKR
- Helps prevent pacing violations

### 5. Visual Progress Indicators
- Progress bar shows which stock is being processed
- Spinner for long operations (options qualification)
- Success/warning messages with summaries

## How to Test

### Quick Test (5 Stocks)
```bash
source ../venv/bin/activate
python3 test_progress_indicators.py
```

This tests with just 5 stocks so you can see if the progress indicators are working correctly. Expected time: 15-30 seconds.

### Full Test
```bash
python3 main.py
```

This runs the full workflow with 25 stocks. Expected time: 2-5 minutes (depending on IBKR response time).

## Speeding Things Up

If the progress bar is too slow, you can:

### 1. Reduce Number of Stocks
Edit `config/trading_config.py`:
```python
NUM_STOCKS_TO_SCREEN = 10  # Instead of 25
```

### 2. Reduce Lookback Period
Edit `config/trading_config.py`:
```python
LOOKBACK_DAYS = 60  # Instead of 100
```

This reduces the amount of historical data requested, speeding up each stock.

### 3. Use a Smaller Stock Universe
Edit `tools/uptrend_screener.py` in the agent workflow call to pass a smaller list of symbols.

## Monitoring Progress

### Watch the Log File
The log file in `logs/` shows detailed progress:
```bash
tail -f logs/trading_agent_*.log
```

You'll see:
```
2026-01-13 23:05:00 - tools.uptrend_screener - INFO - [1/25] Processing AMD...
2026-01-13 23:05:00 - tools.uptrend_screener - INFO - AMD: Requesting 100 days of historical data...
2026-01-13 23:05:02 - tools.uptrend_screener - INFO - AMD: Received 252 bars
2026-01-13 23:05:02 - tools.uptrend_screener - INFO - AMD: UPTREND - Price: $87.25, SMA20: $85.10, SMA50: $82.30
2026-01-13 23:05:02 - tools.uptrend_screener - INFO - [2/25] Processing INTC...
```

### Visual Indicators
- **Progress Bar**: Shows X/Y stocks complete
- **Spinner**: Rotates during long operations
- **Section Headers**: Shows which major step is running
- **Status Messages**: ✓ Success, ⚠ Warning, ✗ Error

## Troubleshooting

### Progress Bar Stuck for More Than 20 Seconds
1. Check the log file for errors
2. Verify IBKR TWS/Gateway is running and connected
3. Check if you're hitting IBKR rate limits
4. Try reducing `NUM_STOCKS_TO_SCREEN` or `LOOKBACK_DAYS`

### "Pacing Violation" Errors
```
IBKR pacing violation - consider reducing request rate
```

Solution:
- Reduce `NUM_STOCKS_TO_SCREEN` to 10 or less
- Increase `RATE_LIMIT_DELAY` in `trading_config.py` to 1.0 seconds
- Wait a few minutes before running again

### Timeout Errors
```
Error fetching historical data: timeout
```

This is normal if IBKR is slow. The app will skip that stock and continue.

If many stocks timeout:
- Check IBKR connection quality
- Increase timeout in `uptrend_screener.py` from 15 to 30 seconds
- Reduce number of stocks being screened

## Expected Performance

### Normal Operation
- **Time per stock**: 1-3 seconds
- **Total time (25 stocks)**: 30-75 seconds
- **Progress bar updates**: Every 1-3 seconds

### Slow Operation (Need to Optimize)
- **Time per stock**: 10-15 seconds
- **Total time (25 stocks)**: 4-6 minutes
- **Progress bar updates**: Every 10-15 seconds

If you're experiencing slow operation:
1. Reduce `NUM_STOCKS_TO_SCREEN` to 10
2. Reduce `LOOKBACK_DAYS` to 60
3. Monitor IBKR Gateway logs for errors
4. Check your internet connection

## Summary

The progress bar behavior you're seeing is **normal** - it updates once per stock, and each stock can take 1-15 seconds to process. The improvements I've added ensure:

1. ✅ The app doesn't hang indefinitely (timeout protection)
2. ✅ You can monitor progress via log messages
3. ✅ Errors are handled gracefully
4. ✅ Rate limits are respected

To see faster progress, reduce the number of stocks or lookback period in the config file.
