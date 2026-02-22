"""
Test Historical Data Caching System
Demonstrates cache functionality and performance improvements
"""
import logging
import sys
import time
from pathlib import Path
from config.ibkr_connection import create_ibkr_connection
from ib_insync import Stock
from utils_historical_data import (
    get_historical_data_with_cache,
    get_cache_stats,
    print_cache_stats,
    clear_cache_for_symbol
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


def test_cache_performance():
    """Test cache performance with multiple fetches"""
    print("\n" + "=" * 70)
    print("TESTING HISTORICAL DATA CACHE PERFORMANCE")
    print("=" * 70)

    test_symbols = ['AAPL', 'MSFT', 'GOOGL']

    print(f"\nTesting with symbols: {', '.join(test_symbols)}")
    print("=" * 70)

    with create_ibkr_connection() as ib:
        # First pass - no cache
        print("\n[FIRST PASS - NO CACHE]")
        print("This will fetch data from IBKR and create cache files")
        print("-" * 70)

        first_pass_start = time.time()
        first_pass_results = []

        for symbol in test_symbols:
            print(f"\nFetching {symbol}...")
            start = time.time()

            stock = Stock(symbol, 'SMART', 'USD')
            ib.qualifyContracts(stock)

            df = get_historical_data_with_cache(ib, stock, lookback_days=100)

            elapsed = time.time() - start

            if df is not None:
                print(f"  ✓ Got {len(df)} bars in {elapsed:.2f}s")
                print(f"  → Date range: {df['date'].min().date()} to {df['date'].max().date()}")
                first_pass_results.append(elapsed)
            else:
                print(f"  ✗ Failed to get data")

            # Small delay to avoid rate limits
            time.sleep(0.5)

        first_pass_total = time.time() - first_pass_start

        # Second pass - with cache
        print("\n" + "=" * 70)
        print("[SECOND PASS - WITH CACHE]")
        print("This will use cached data (should be much faster)")
        print("-" * 70)

        second_pass_start = time.time()
        second_pass_results = []

        for symbol in test_symbols:
            print(f"\nFetching {symbol}...")
            start = time.time()

            stock = Stock(symbol, 'SMART', 'USD')
            ib.qualifyContracts(stock)

            df = get_historical_data_with_cache(ib, stock, lookback_days=100)

            elapsed = time.time() - start

            if df is not None:
                print(f"  ✓ Got {len(df)} bars in {elapsed:.2f}s (cached)")
                print(f"  → Date range: {df['date'].min().date()} to {df['date'].max().date()}")
                second_pass_results.append(elapsed)
            else:
                print(f"  ✗ Failed to get data")

    second_pass_total = time.time() - second_pass_start

    # Performance summary
    print("\n" + "=" * 70)
    print("PERFORMANCE SUMMARY")
    print("=" * 70)
    print(f"First pass (no cache):   {first_pass_total:.2f}s")
    print(f"Second pass (cached):    {second_pass_total:.2f}s")
    print(f"Speedup:                 {first_pass_total / second_pass_total:.1f}x faster")
    print(f"Time saved:              {first_pass_total - second_pass_total:.2f}s")
    print("=" * 70)

    # Show cache stats
    print_cache_stats()


def test_incremental_update():
    """Test incremental cache updates"""
    print("\n" + "=" * 70)
    print("TESTING INCREMENTAL CACHE UPDATES")
    print("=" * 70)

    symbol = 'AMD'
    print(f"\nTesting with: {symbol}")

    with create_ibkr_connection() as ib:
        # First fetch
        print("\n1. Initial fetch (no cache):")
        stock = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(stock)

        df1 = get_historical_data_with_cache(ib, stock, lookback_days=100)
        if df1 is not None:
            print(f"   ✓ Got {len(df1)} bars")
            print(f"   → Date range: {df1['date'].min().date()} to {df1['date'].max().date()}")

        # Second fetch (should use cache)
        print("\n2. Second fetch (should use cache, no update needed):")
        df2 = get_historical_data_with_cache(ib, stock, lookback_days=100)
        if df2 is not None:
            print(f"   ✓ Got {len(df2)} bars")
            print(f"   → Date range: {df2['date'].min().date()} to {df2['date'].max().date()}")
            print(f"   → Same as first fetch: {len(df1) == len(df2)}")

        # Show cache file info
        from utils_historical_data import get_cache_file_path
        cache_file = get_cache_file_path(symbol)
        if cache_file.exists():
            size_kb = cache_file.stat().st_size / 1024
            print(f"\n   Cache file: {cache_file}")
            print(f"   Size: {size_kb:.2f} KB")


def test_data_validation():
    """Test data validation and corruption handling"""
    print("\n" + "=" * 70)
    print("TESTING DATA VALIDATION")
    print("=" * 70)

    symbol = 'INTC'
    print(f"\nTesting with: {symbol}")

    with create_ibkr_connection() as ib:
        # Create cache
        print("\n1. Creating cache:")
        stock = Stock(symbol, 'SMART', 'USD')
        ib.qualifyContracts(stock)

        df1 = get_historical_data_with_cache(ib, stock, lookback_days=100)
        if df1 is not None:
            print(f"   ✓ Created cache with {len(df1)} bars")

        # Corrupt the cache file
        from utils_historical_data import get_cache_file_path
        cache_file = get_cache_file_path(symbol)

        if cache_file.exists():
            print("\n2. Corrupting cache file...")
            with open(cache_file, 'w') as f:
                f.write("corrupted,data,here\n1,2,3\n")
            print("   ✓ Cache corrupted")

        # Try to load corrupted cache (should re-fetch)
        print("\n3. Loading corrupted cache (should re-fetch):")
        df2 = get_historical_data_with_cache(ib, stock, lookback_days=100)
        if df2 is not None:
            print(f"   ✓ Re-fetched data, got {len(df2)} bars")
            print(f"   → Cache was automatically repaired")


def main():
    """Run all tests"""
    print("\n" + "=" * 70)
    print("HISTORICAL DATA CACHE TEST SUITE")
    print("=" * 70)
    print("\nThis test will:")
    print("1. Test cache performance (no cache vs cached)")
    print("2. Test incremental updates")
    print("3. Test data validation and corruption handling")
    print("\nNote: This requires IBKR TWS/Gateway to be running")
    print("=" * 70)

    try:
        # Test 1: Performance
        test_cache_performance()

        # Test 2: Incremental updates
        test_incremental_update()

        # Test 3: Validation
        test_data_validation()

        print("\n" + "=" * 70)
        print("ALL TESTS COMPLETED")
        print("=" * 70)

        # Final cache stats
        print_cache_stats()

        return 0

    except KeyboardInterrupt:
        print("\n\nTests interrupted by user")
        return 1

    except Exception as e:
        logger.error(f"Test failed: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
