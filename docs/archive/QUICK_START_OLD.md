# Quick Start Guide

## Prerequisites

1. **IBKR TWS or Gateway** running in paper trading mode on port 7497
2. **Python 3.13** installed at `/Library/Frameworks/Python.framework/Versions/3.13/bin/python3`
	source venv/bin/activate
3. **Anthropic API Key** for Claude

## Setup (5 minutes)

### Option 1: Automated Setup
```bash
./setup.sh
```

### Option 2: Manual Setup
```bash
# Install dependencies
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pip install -r requirements.txt

# Create .env file
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY

# Create logs directory
mkdir -p logs
```

## Configuration

Edit `.env`:
```bash
ANTHROPIC_API_KEY=sk-ant-xxxxx
IBKR_HOST=127.0.0.1
IBKR_PORT=7497
IBKR_CLIENT_ID=1
```

## Running

### Full Workflow
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 main.py
```

### Test Individual Components

#### Test IBKR Connection
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 config/ibkr_connection.py
```

#### Test Uptrend Screener
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 tools/uptrend_screener.py
```

#### Test Options Finder
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 tools/options_finder.py
```

#### Test Margin Calculator
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 tools/margin_calculator.py
```

## How It Works

The agent follows this workflow:

1. **Screen Stocks** - Finds stocks where Price > SMA20 > SMA50
2. **Find Options** - Searches for PUT options 15-20% OTM with $0.30-$0.50 premium
3. **Calculate Margin** - Computes margin requirements for 5-contract trades
4. **Rank Opportunities** - Sorts by margin efficiency (return on margin %)
5. **Present Results** - Shows top recommendations with detailed analysis

## Project Structure

```
trading_agent/
├── config/
│   └── ibkr_connection.py      # IBKR TWS/Gateway connection
├── tools/
│   ├── uptrend_screener.py     # Stock screening (SMA analysis)
│   ├── options_finder.py       # PUT options search
│   └── margin_calculator.py    # Margin calculations
├── agents/
│   └── trading_agent.py        # LangGraph workflow orchestration
├── main.py                     # Entry point
├── utils.py                    # Error handling & retries
└── logs/                       # Execution logs
```

## Customization

### Change Screening Criteria

Edit `tools/uptrend_screener.py`:
```python
# Adjust SMA periods
sma_20 = calculate_sma(df['close'], 30)  # Use 30-day instead
sma_50 = calculate_sma(df['close'], 100) # Use 100-day instead
```

### Change Options Criteria

Edit `tools/options_finder.py`:
```python
def find_put_options(
    symbol: str,
    current_price: float,
    otm_range: tuple = (0.10, 0.15),      # 10-15% OTM
    premium_range: tuple = (0.20, 0.60),  # $0.20-$0.60
    min_dte: int = 20,                    # 20-90 days
    max_dte: int = 90
):
```

### Change Contract Size

Edit `agents/trading_agent.py`:
```python
@tool
def calculate_margin_tool(
    stock_price: float,
    strike: float,
    premium: float,
    contracts: int = 10  # Change from 5 to 10
):
```

## Troubleshooting

### "Failed to connect to IBKR"
- Ensure TWS/Gateway is running
- Check it's on port 7497 (not 7496)
- Enable API connections in TWS settings
- Check client ID is not already in use

### "No option chains found"
- Some stocks don't have options
- Try larger cap stocks (AAPL, MSFT, etc.)
- Check market hours

### "ANTHROPIC_API_KEY not found"
- Make sure .env file exists
- Check the key starts with `sk-ant-`
- Verify .env is in the project root

### Rate Limiting
The system includes automatic rate limiting and retries. If you hit limits:
- Reduce `num_stocks` in main.py
- Increase delays in options_finder.py

## Logs

All execution logs are saved to `logs/trading_agent_TIMESTAMP.log`

View recent logs:
```bash
tail -f logs/trading_agent_*.log
```

## Safety Notes

- This is for **PAPER TRADING ONLY**
- Always verify opportunities before real trading
- Understand PUT options risks
- Check margin requirements match IBKR's actual requirements
- Past performance doesn't guarantee future results

## Support

For issues:
1. Check logs in `logs/` directory
2. Test individual components
3. Verify IBKR connection manually
4. Check API key is valid
