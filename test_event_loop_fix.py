"""
Quick test to verify event loop fix works
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from config.ibkr_connection import create_ibkr_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_in_thread(thread_id):
    """Test connection in a thread"""
    logger.info(f"Thread {thread_id} starting...")

    try:
        with create_ibkr_connection() as ib:
            # Try to call an ib_insync method that requires event loop
            if ib.isConnected():
                account = ib.managedAccounts()[0] if ib.managedAccounts() else "Unknown"
                logger.info(f"Thread {thread_id}: ✓ SUCCESS - Account {account}")
                return True
    except RuntimeError as e:
        if "event loop" in str(e).lower():
            logger.error(f"Thread {thread_id}: ✗ FAILED - Event loop error: {e}")
            return False
        raise
    except Exception as e:
        logger.error(f"Thread {thread_id}: ✗ FAILED - {e}")
        return False


def main():
    print("Testing event loop fix in ThreadPoolExecutor...")
    print("-" * 60)

    # Simulate LangGraph's ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(test_in_thread, i) for i in range(3)]
        results = [f.result() for f in futures]

    print("-" * 60)
    if all(results):
        print("✓ ALL THREADS PASSED - Event loop fix works!")
        return 0
    else:
        print("✗ SOME THREADS FAILED - Event loop issue persists")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
