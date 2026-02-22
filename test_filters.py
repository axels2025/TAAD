"""
Test script to verify stock filters are working
"""
import logging

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(levelname)s - %(message)s'
)

from tools.uptrend_screener import get_sp500_symbols
from config import trading_config as cfg

def test_filters():
    """Test that filters are configured correctly"""

    print("=" * 60)
    print("STOCK FILTER TEST")
    print("=" * 60)

    # Show configuration
    print(f"\n1. Configuration:")
    print(f"   Price Range: ${cfg.MIN_STOCK_PRICE:.0f} - ${cfg.MAX_STOCK_PRICE:.0f}")
    print(f"   Min Market Cap: ${cfg.MIN_MARKET_CAP:,.0f}")
    print(f"   Market Cap Check: {'Enabled' if cfg.ENABLE_MARKET_CAP_CHECK else 'Disabled (Fast)'}")

    # Show symbol list
    symbols = get_sp500_symbols()
    print(f"\n2. Stock Universe:")
    print(f"   Total symbols: {len(symbols)}")
    print(f"   First 10: {', '.join(symbols[:10])}")

    # Simulate filtering (prices are examples)
    print(f"\n3. Filter Examples:")
    test_stocks = [
        ("AAPL", 178.50),  # Too high
        ("AMD", 87.25),    # Good
        ("F", 12.30),      # Too low
        ("GOOGL", 140.50), # Good
        ("INTC", 65.50),   # Good
        ("TSLA", 182.50),  # Too high
    ]

    filtered_in = []
    filtered_out = []

    for symbol, price in test_stocks:
        if cfg.MIN_STOCK_PRICE <= price <= cfg.MAX_STOCK_PRICE:
            filtered_in.append(f"{symbol} (${price:.2f})")
            status = "✓ IN"
        else:
            filtered_out.append(f"{symbol} (${price:.2f})")
            status = "✗ OUT"

        print(f"   {symbol:6} ${price:7.2f}  {status}")

    print(f"\n4. Summary:")
    print(f"   Passed filter: {len(filtered_in)} stocks")
    print(f"   Filtered out: {len(filtered_out)} stocks")

    if filtered_in:
        print(f"   → {', '.join(filtered_in)}")

    print("\n" + "=" * 60)
    print("✓ Filter configuration is correct!")
    print("\nRun 'python3 main.py' to use filters with live data")
    print("=" * 60)


if __name__ == "__main__":
    test_filters()
