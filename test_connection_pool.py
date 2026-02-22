"""
Test script to verify IBKR connection pool works correctly
Tests thread-safety and connection reuse
"""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from config.ibkr_connection import create_ibkr_connection, get_connection_pool

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_connection_in_thread(thread_id: int, delay: float = 0):
    """Test IBKR connection in a thread"""
    if delay > 0:
        time.sleep(delay)

    logger.info(f"Thread {thread_id} starting...")

    try:
        with create_ibkr_connection() as ib:
            logger.info(f"Thread {thread_id}: Got connection, checking...")

            # Test connection
            if ib.isConnected():
                account = ib.managedAccounts()[0] if ib.managedAccounts() else "Unknown"
                logger.info(f"Thread {thread_id}: ✓ Connected to account {account}")

                # Simulate some work
                time.sleep(0.5)

                logger.info(f"Thread {thread_id}: Work completed")
                return {
                    'thread_id': thread_id,
                    'success': True,
                    'account': account,
                    'connection_id': id(ib)  # Memory address to verify same instance
                }
            else:
                return {
                    'thread_id': thread_id,
                    'success': False,
                    'error': 'Not connected'
                }

    except Exception as e:
        logger.error(f"Thread {thread_id}: Error - {e}")
        return {
            'thread_id': thread_id,
            'success': False,
            'error': str(e)
        }


def test_sequential():
    """Test sequential connection usage"""
    print("\n" + "="*60)
    print("TEST 1: Sequential Access")
    print("="*60)

    results = []
    for i in range(3):
        result = test_connection_in_thread(i)
        results.append(result)
        time.sleep(0.2)

    # Check results
    connection_ids = [r['connection_id'] for r in results if r['success']]
    unique_connections = len(set(connection_ids))

    print("\nResults:")
    print(f"  Total calls: {len(results)}")
    print(f"  Successful: {sum(1 for r in results if r['success'])}")
    print(f"  Unique connections: {unique_connections}")

    if unique_connections == 1:
        print("  ✓ All calls used the SAME connection (good!)")
    else:
        print(f"  ✗ Calls used {unique_connections} different connections (bad!)")

    return unique_connections == 1


def test_parallel():
    """Test parallel connection usage (simulates LangGraph)"""
    print("\n" + "="*60)
    print("TEST 2: Parallel Access (LangGraph Simulation)")
    print("="*60)

    results = []

    # Launch 5 threads simultaneously (like LangGraph does)
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [
            executor.submit(test_connection_in_thread, i, delay=0.1)
            for i in range(5)
        ]

        for future in as_completed(futures):
            results.append(future.result())

    # Check results
    connection_ids = [r['connection_id'] for r in results if r['success']]
    unique_connections = len(set(connection_ids))

    print("\nResults:")
    print(f"  Total calls: {len(results)}")
    print(f"  Successful: {sum(1 for r in results if r['success'])}")
    print(f"  Unique connections: {unique_connections}")

    if unique_connections == 1:
        print("  ✓ All threads used the SAME connection (good!)")
    else:
        print(f"  ✗ Threads used {unique_connections} different connections (bad!)")

    return unique_connections == 1


def test_stress():
    """Stress test with many rapid parallel accesses"""
    print("\n" + "="*60)
    print("TEST 3: Stress Test (10 Parallel Threads)")
    print("="*60)

    results = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(test_connection_in_thread, i, delay=0)
            for i in range(10)
        ]

        for future in as_completed(futures):
            results.append(future.result())

    # Check results
    successful = sum(1 for r in results if r['success'])
    connection_ids = [r['connection_id'] for r in results if r['success']]
    unique_connections = len(set(connection_ids))

    print("\nResults:")
    print(f"  Total calls: {len(results)}")
    print(f"  Successful: {successful}")
    print(f"  Failed: {len(results) - successful}")
    print(f"  Unique connections: {unique_connections}")

    if unique_connections == 1 and successful == len(results):
        print("  ✓ All threads used the SAME connection successfully!")
    else:
        print(f"  ✗ Issue detected - {unique_connections} connections, {successful}/{len(results)} succeeded")

    return unique_connections == 1 and successful == len(results)


def main():
    """Run all tests"""
    print("="*60)
    print("IBKR CONNECTION POOL TEST SUITE")
    print("="*60)
    print("\nThis tests that:")
    print("  1. All threads share the SAME connection")
    print("  2. Connection pool is thread-safe")
    print("  3. No race conditions occur")
    print()

    # Run tests
    test1_passed = test_sequential()
    test2_passed = test_parallel()
    test3_passed = test_stress()

    # Summary
    print("\n" + "="*60)
    print("TEST SUMMARY")
    print("="*60)
    print(f"  Sequential Test:  {'✓ PASS' if test1_passed else '✗ FAIL'}")
    print(f"  Parallel Test:    {'✓ PASS' if test2_passed else '✗ FAIL'}")
    print(f"  Stress Test:      {'✓ PASS' if test3_passed else '✗ FAIL'}")
    print("="*60)

    if all([test1_passed, test2_passed, test3_passed]):
        print("\n✓ ALL TESTS PASSED!")
        print("Connection pool is working correctly.")
        print("The system will use a SINGLE shared connection.")
        return 0
    else:
        print("\n✗ SOME TESTS FAILED")
        print("Connection pool needs debugging.")
        return 1

    # Cleanup
    pool = get_connection_pool()
    pool.close()


if __name__ == "__main__":
    import sys
    sys.exit(main())
