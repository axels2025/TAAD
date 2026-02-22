"""
Trading Strategy Configuration
Centralized place to modify all trading parameters
"""

# =============================================================================
# OPTIONS CRITERIA
# =============================================================================

# Out-of-the-Money Range (as decimal percentages)
# Example: (0.12, 0.25) = 12% to 25% OTM
OTM_MIN = 0.08  # 10% OTM minimum
OTM_MAX = 0.50  # 25% OTM maximum

# Premium Range (in dollars per share)
PREMIUM_MIN = 0.20  # $0.25 minimum premium
PREMIUM_MAX = 1.75  # $0.50 maximum premium

# Days to Expiration (DTE) Range
# For weekly options: 3-14 days
# For monthly options: 30-60 days
DTE_MIN = 3   # Minimum days to expiration
DTE_MAX = 40  # Maximum days to expiration

# =============================================================================
# STOCK SCREENING
# =============================================================================

# Number of stocks to screen per batch
# This limits how many stocks from the selected index will be scanned
NUM_STOCKS_TO_SCREEN = 100  # Scan 10 stocks at a time

# Stock price range filter
MIN_STOCK_PRICE = 40.0   # Minimum stock price ($50)
MAX_STOCK_PRICE = 250.0  # Maximum stock price ($150)

# Market capitalization filter (in USD)
MIN_MARKET_CAP = 500_000_000  # Minimum market cap ($500M)
ENABLE_MARKET_CAP_CHECK = False  # Enable detailed market cap verification (slower)

# Moving average periods for trend detection
SMA_SHORT = 20  # Short-term SMA
SMA_LONG = 50   # Long-term SMA

# Historical data lookback (in days)
LOOKBACK_DAYS = 100

# Trend filtering (can be overridden by command-line arguments)
# Options: ['uptrend'], ['downtrend'], ['sideways'], ['all'], or combinations
# Example: ['uptrend', 'sideways'] for uptrend OR sideways
TREND_FILTER = ['uptrend']  # Default: only uptrend stocks

# Sideways trend detection parameters
# Sideways/consolidation detected when SMA20 and SMA50 are very close
SIDEWAYS_SMA_THRESHOLD = 0.02  # 2% - SMAs within 2% of each other = sideways

# =============================================================================
# MARGIN & POSITION SIZING
# =============================================================================

# Number of contracts per trade
CONTRACTS_PER_TRADE = 5

# =============================================================================
# IBKR CONNECTION
# =============================================================================

# Connection timeout in seconds
CONNECTION_TIMEOUT = 20

# Rate limiting (seconds between requests)
RATE_LIMIT_DELAY = 0.5

# =============================================================================
# DISPLAY SETTINGS
# =============================================================================

# Number of top opportunities to show
TOP_OPPORTUNITIES = 10


def get_config_summary():
    """Get a summary of current configuration"""
    return f"""
Trading Configuration:
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Options Criteria:
  • OTM Range:      {OTM_MIN*100:.0f}% - {OTM_MAX*100:.0f}%
  • Premium Range:  ${PREMIUM_MIN:.2f} - ${PREMIUM_MAX:.2f}
  • DTE Range:      {DTE_MIN} - {DTE_MAX} days
  • Type:           {'Weekly' if DTE_MAX <= 14 else 'Monthly'} options

Stock Screening:
  • Stocks:         {NUM_STOCKS_TO_SCREEN}
  • Price Range:    ${MIN_STOCK_PRICE:.0f} - ${MAX_STOCK_PRICE:.0f}
  • Min Market Cap: ${MIN_MARKET_CAP:,.0f}
  • Trend Filter:   {', '.join(TREND_FILTER)}
  • SMAs:           {SMA_SHORT} / {SMA_LONG}
  • Lookback:       {LOOKBACK_DAYS} days

Position Sizing:
  • Contracts:      {CONTRACTS_PER_TRADE} per trade

Display:
  • Show Top:       {TOP_OPPORTUNITIES} opportunities
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""


if __name__ == "__main__":
    print(get_config_summary())
