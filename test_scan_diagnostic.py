"""Diagnostic script to test scan functionality and see what's being scanned."""

from dotenv import load_dotenv
load_dotenv()

from src.tools.stock_universe import StockUniverseManager
from src.tools.ibkr_client import IBKRClient
from src.tools.screener import StockScreener
from src.config.base import get_config

print("=" * 70)
print("SCAN DIAGNOSTIC TEST")
print("=" * 70)

# Test 1: Check stock universe
print("\n1. Testing Stock Universe Manager:")
universe_mgr = StockUniverseManager()

tier1 = universe_mgr.get_universe("tier1")
tier2 = universe_mgr.get_universe("tier2")

print(f"   Tier 1 size: {len(tier1)} stocks")
print(f"   Tier 1 samples: {tier1[:10]}")
print(f"   Tier 2 size: {len(tier2)} stocks")
print(f"   Tier 2 samples: {tier2[:10]}")

# Test 2: Connect to IBKR
print("\n2. Connecting to IBKR:")
config = get_config()
client = IBKRClient(config.ibkr)
client.connect()
print("   ✓ Connected")

# Test 3: Create screener with universe
print("\n3. Creating screener with universe manager:")
screener = StockScreener(client, universe_manager=universe_mgr)
print("   ✓ Screener created")

# Test 4: Run small scan to test
print("\n4. Running test scan (tier1, first 10 stocks):")
try:
    stocks = screener.scan_stocks(
        trend_filter="any",  # Don't filter by trend for test
        max_results=10,
        universe_tier="tier1",
        use_cache=False,  # Don't use cache for this test
    )
    print(f"   ✓ Found {len(stocks)} stocks")
    for stock in stocks[:5]:
        print(f"      - {stock['symbol']}: ${stock['price']:.2f} (trend: {stock.get('trend', 'N/A')})")
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

# Test 5: Run scan with uptrend filter
print("\n5. Running scan with uptrend filter (tier1):")
try:
    stocks = screener.scan_stocks(
        trend_filter="uptrend",
        max_results=10,
        universe_tier="tier1",
        use_cache=False,
    )
    print(f"   ✓ Found {len(stocks)} stocks in uptrend")
    for stock in stocks:
        print(f"      - {stock['symbol']}: ${stock['price']:.2f}")
except Exception as e:
    print(f"   ✗ Error: {e}")
    import traceback
    traceback.print_exc()

client.disconnect()
print("\n✓ Diagnostic complete")
print("=" * 70)
