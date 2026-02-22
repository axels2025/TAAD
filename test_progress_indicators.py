"""
Quick test to verify progress indicators work correctly
Tests with a small subset of stocks
"""
import logging
import sys
from tools.uptrend_screener import screen_uptrend_stocks

# Set up logging to see what's happening
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

def main():
    """Test progress indicators with a small stock list"""
    print("\n" + "=" * 70)
    print("Testing Progress Indicators")
    print("=" * 70)
    print("\nThis will screen 5 stocks to verify progress indicators work.\n")

    # Test with just 5 stocks
    test_symbols = ['AMD', 'INTC', 'QCOM', 'CSCO', 'ORCL']

    try:
        results = screen_uptrend_stocks(
            symbols=test_symbols,
            lookback_days=100
        )

        print("\n" + "=" * 70)
        print("Test Complete!")
        print("=" * 70)
        print(f"\nFound {len(results)} stocks in uptrend:")
        for stock in results:
            print(f"  - {stock['symbol']}: ${stock['price']:.2f}")

        return 0

    except Exception as e:
        logger.error(f"Test failed: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
