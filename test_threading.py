"""
Test script to verify threading fixes work
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from config.ibkr_connection import create_ibkr_connection

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def test_connection_in_thread(thread_id):
    """Test IBKR connection in a thread"""
    logger.info(f"Thread {thread_id} starting...")
    try:
        with create_ibkr_connection() as ib:
            logger.info(f"Thread {thread_id}: Connected successfully!")
            # Test a simple operation
            account = ib.managedAccounts()[0] if ib.managedAccounts() else "Unknown"
            logger.info(f"Thread {thread_id}: Account = {account}")
            return f"Thread {thread_id} success"
    except Exception as e:
        logger.error(f"Thread {thread_id}: Error - {e}")
        return f"Thread {thread_id} failed: {e}"


def main():
    """Test connections from multiple threads"""
    print("Testing IBKR connections in thread pool...")
    print("This simulates how LangGraph runs tools")
    print("-" * 60)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(test_connection_in_thread, i) for i in range(3)]
        results = [f.result() for f in futures]

    print("-" * 60)
    print("Results:")
    for result in results:
        print(f"  {result}")

    if all("success" in r for r in results):
        print("\n✓ All threads connected successfully!")
        return 0
    else:
        print("\n✗ Some threads failed")
        return 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
