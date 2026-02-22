"""
Test to verify thread-local connections work with reqHistoricalData
This simulates the actual LangGraph workflow behavior
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from ib_insync import Stock
from config.ibkr_connection import create_ibkr_connection, get_connection_pool

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def fetch_historical_data_in_thread(thread_id: int, symbol: str):
    """
    Test fetching historical data in a worker thread
    This simulates what happens in the LangGraph tool
    """
    logger.info(f"Thread {thread_id} ({symbol}): Starting...")
    start_time = time.time()

    try:
        with create_ibkr_connection() as ib:
            logger.info(f"Thread {thread_id} ({symbol}): Got connection")

            # Create and qualify stock contract
            stock = Stock(symbol, 'SMART', 'USD')
            ib.qualifyContracts(stock)
            logger.info(f"Thread {thread_id} ({symbol}): Contract qualified")

            # Fetch historical data - THIS IS THE CRITICAL TEST
            # This is what hangs in the LangGraph workflow
            bars = ib.reqHistoricalData(
                stock,
                endDateTime=datetime.now(),
                durationStr='5 D',  # Just 5 days for quick test
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1,
                timeout=15
            )

            if bars:
                elapsed = time.time() - start_time
                logger.info(
                    f"Thread {thread_id} ({symbol}): ✓ SUCCESS - "
                    f"Fetched {len(bars)} bars in {elapsed:.2f}s"
                )
                return {
                    'thread_id': thread_id,
                    'symbol': symbol,
                    'success': True,
                    'bars_count': len(bars),
                    'elapsed': elapsed,
                    'connection_id': id(ib)
                }
            else:
                logger.warning(f"Thread {thread_id} ({symbol}): No bars returned")
                return {
                    'thread_id': thread_id,
                    'symbol': symbol,
                    'success': False,
                    'error': 'No bars returned'
                }

    except Exception as e:
        elapsed = time.time() - start_time
        logger.error(f"Thread {thread_id} ({symbol}): ✗ FAILED after {elapsed:.2f}s - {e}")
        return {
            'thread_id': thread_id,
            'symbol': symbol,
            'success': False,
            'error': str(e),
            'elapsed': elapsed
        }


def test_sequential():
    """Test sequential data fetching"""
    print("\n" + "="*70)
    print("TEST 1: Sequential Historical Data Fetching")
    print("="*70)

    symbols = ['AAPL', 'MSFT', 'GOOGL']
    results = []

    for i, symbol in enumerate(symbols):
        result = fetch_historical_data_in_thread(i, symbol)
        results.append(result)
        time.sleep(0.5)  # Small delay between requests

    # Analyze results
    successful = sum(1 for r in results if r['success'])
    total_time = sum(r.get('elapsed', 0) for r in results if 'elapsed' in r)

    print("\nResults:")
    print(f"  Total requests: {len(results)}")
    print(f"  Successful: {successful}")
    print(f"  Failed: {len(results) - successful}")
    print(f"  Total time: {total_time:.2f}s")

    if successful == len(results):
        print("  ✓ All requests succeeded!")
        return True
    else:
        print(f"  ✗ {len(results) - successful} requests failed")
        for r in results:
            if not r['success']:
                print(f"    - {r['symbol']}: {r.get('error', 'Unknown error')}")
        return False


def test_parallel():
    """
    Test parallel data fetching (simulates LangGraph ToolNode)
    THIS IS THE KEY TEST - this is what hangs in the actual workflow
    """
    print("\n" + "="*70)
    print("TEST 2: Parallel Historical Data Fetching (LangGraph Simulation)")
    print("="*70)
    print("This simulates what LangGraph does when calling screen_stocks_tool")
    print()

    symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA']
    results = []

    start_time = time.time()

    # Use ThreadPoolExecutor like LangGraph does
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [
            executor.submit(fetch_historical_data_in_thread, i, symbol)
            for i, symbol in enumerate(symbols)
        ]

        for future in as_completed(futures):
            results.append(future.result())

    total_time = time.time() - start_time

    # Analyze results
    successful = sum(1 for r in results if r['success'])
    connection_ids = [r['connection_id'] for r in results if r['success']]
    unique_connections = len(set(connection_ids))

    print("\nResults:")
    print(f"  Total requests: {len(results)}")
    print(f"  Successful: {successful}")
    print(f"  Failed: {len(results) - successful}")
    print(f"  Unique connections: {unique_connections}")
    print(f"  Total time: {total_time:.2f}s")

    # Show individual results
    for r in results:
        if r['success']:
            print(f"    ✓ {r['symbol']}: {r['bars_count']} bars in {r['elapsed']:.2f}s")
        else:
            print(f"    ✗ {r['symbol']}: {r.get('error', 'Unknown error')}")

    if successful == len(results):
        print(f"\n  ✓ All parallel requests succeeded!")
        print(f"  ✓ Used {unique_connections} connections (one per thread)")
        return True
    else:
        print(f"\n  ✗ {len(results) - successful} requests failed")
        return False


def test_stress():
    """Stress test with more parallel requests"""
    print("\n" + "="*70)
    print("TEST 3: Stress Test (10 Symbols, 5 Workers)")
    print("="*70)

    symbols = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'TSLA',
               'META', 'NVDA', 'NFLX', 'AMD', 'INTC']
    results = []

    start_time = time.time()

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(fetch_historical_data_in_thread, i, symbol)
            for i, symbol in enumerate(symbols)
        ]

        for future in as_completed(futures):
            results.append(future.result())

    total_time = time.time() - start_time

    # Analyze results
    successful = sum(1 for r in results if r['success'])
    connection_ids = [r['connection_id'] for r in results if r['success']]
    unique_connections = len(set(connection_ids))
    total_bars = sum(r.get('bars_count', 0) for r in results if r['success'])

    print("\nResults:")
    print(f"  Total requests: {len(results)}")
    print(f"  Successful: {successful}")
    print(f"  Failed: {len(results) - successful}")
    print(f"  Total bars fetched: {total_bars}")
    print(f"  Unique connections: {unique_connections}")
    print(f"  Total time: {total_time:.2f}s")
    print(f"  Avg time per request: {total_time/len(results):.2f}s")

    if successful == len(results):
        print(f"\n  ✓ All stress test requests succeeded!")
        return True
    else:
        print(f"\n  ✗ {len(results) - successful} requests failed")
        return False


def main():
    """Run all tests"""
    print("="*70)
    print("LANGGRAPH WORKFLOW SIMULATION TEST")
    print("="*70)
    print("\nThis test verifies that reqHistoricalData works in worker threads")
    print("If this passes, the LangGraph workflow should work too!")
    print()

    try:
        # Run tests
        test1_passed = test_sequential()
        test2_passed = test_parallel()
        test3_passed = test_stress()

        # Summary
        print("\n" + "="*70)
        print("TEST SUMMARY")
        print("="*70)
        print(f"  Sequential Test:  {'✓ PASS' if test1_passed else '✗ FAIL'}")
        print(f"  Parallel Test:    {'✓ PASS' if test2_passed else '✗ FAIL'}")
        print(f"  Stress Test:      {'✓ PASS' if test3_passed else '✗ FAIL'}")
        print("="*70)

        if all([test1_passed, test2_passed, test3_passed]):
            print("\n✓ ALL TESTS PASSED!")
            print("\nThe thread-local connection pool is working correctly.")
            print("Historical data fetching works in worker threads.")
            print("The LangGraph workflow should now work without hanging!")
            return 0
        else:
            print("\n✗ SOME TESTS FAILED")
            print("\nThere are still issues with thread-local connections.")
            print("The LangGraph workflow may still hang.")
            return 1

    finally:
        # Cleanup
        print("\nCleaning up connection pool...")
        pool = get_connection_pool()
        pool.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
