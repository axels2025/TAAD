"""
IBKR Connection Manager
Thread-safe connection pool for Interactive Brokers TWS/Gateway
Uses a single shared connection across all threads
"""
import logging
import os
import threading
import asyncio
import time
from typing import Optional
from contextlib import contextmanager
from ib_insync import IB, util
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)


class IBKRConnectionPool:
    """
    Thread-safe connection pool for IBKR
    Maintains one connection per thread to avoid event loop conflicts
    Each thread gets its own connection with a unique client ID
    """

    def __init__(self):
        self._connections: dict = {}  # Dict[thread_id -> IB connection]
        self._lock = threading.RLock()  # Reentrant lock for nested locking
        self._in_use_count = 0
        self._host = os.getenv("IBKR_HOST", "127.0.0.1")
        self._port = int(os.getenv("IBKR_PORT", "7497"))
        self._base_client_id = int(os.getenv("IBKR_CLIENT_ID", "1"))

    def _ensure_event_loop(self):
        """Ensure there's an event loop for async operations"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_closed():
                raise RuntimeError("Event loop is closed")
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            logger.debug(f"Created new event loop for thread {threading.current_thread().name}")
        return loop

    def _create_connection(self, client_id: int) -> IB:
        """Create a new IBKR connection for a thread (must hold lock)"""
        # Ensure event loop exists in this thread
        self._ensure_event_loop()

        ib = IB()

        logger.info(
            f"Creating IBKR connection for thread {threading.current_thread().name} "
            f"(Client ID: {client_id})"
        )

        # Use util.run to properly handle async operations
        util.run(
            ib.connectAsync(
                host=self._host,
                port=self._port,
                clientId=client_id,
                timeout=20
            )
        )

        logger.info(f"Connected to IBKR at {self._host}:{self._port} (Client ID: {client_id})")
        return ib

    def _is_connected(self, thread_id: int) -> bool:
        """Check if connection is active for a thread (must hold lock)"""
        if thread_id not in self._connections:
            return False
        return self._connections[thread_id].isConnected()

    @contextmanager
    def get_connection(self):
        """
        Get a connection from the pool (thread-safe)
        Each thread gets its own connection to avoid event loop conflicts

        Usage:
            with pool.get_connection() as ib:
                # Use ib connection
                stocks = ib.reqContractDetails(...)

        Yields:
            IB: Thread-local IB connection instance
        """
        # Get current thread ID
        thread_id = threading.get_ident()
        thread_name = threading.current_thread().name

        # IMPORTANT: Ensure this thread has an event loop
        # ib_insync requires an event loop in the calling thread
        self._ensure_event_loop()

        with self._lock:
            # Create connection if needed or reconnect if disconnected
            if not self._is_connected(thread_id):
                if thread_id in self._connections:
                    logger.warning(f"Thread {thread_name}: Connection lost, reconnecting...")
                    try:
                        self._connections[thread_id].disconnect()
                    except:
                        pass
                    del self._connections[thread_id]

                # Assign unique client ID for this thread
                # Use base_client_id + number of existing connections
                client_id = self._base_client_id + len(self._connections)

                self._connections[thread_id] = self._create_connection(client_id)

            # Increment usage counter
            self._in_use_count += 1
            logger.debug(
                f"Thread {thread_name} acquired connection "
                f"(active threads: {len(self._connections)}, total users: {self._in_use_count})"
            )

        try:
            # Yield connection for this thread
            yield self._connections[thread_id]

        finally:
            # Decrement usage counter
            with self._lock:
                self._in_use_count -= 1
                logger.debug(
                    f"Thread {thread_name} released connection "
                    f"(active threads: {len(self._connections)}, total users: {self._in_use_count})"
                )

                # Don't disconnect - keep connection alive for thread reuse
                # Only disconnect when explicitly closed

    def close(self):
        """Close the connection pool and disconnect all threads"""
        with self._lock:
            logger.info(f"Closing connection pool ({len(self._connections)} connections)")

            for thread_id, connection in list(self._connections.items()):
                if connection and connection.isConnected():
                    try:
                        logger.debug(f"Disconnecting connection for thread {thread_id}")
                        connection.disconnect()
                    except Exception as e:
                        logger.warning(f"Error disconnecting thread {thread_id}: {e}")

            self._connections.clear()
            self._in_use_count = 0
            logger.info("All connections closed")


# Global connection pool singleton
_connection_pool: Optional[IBKRConnectionPool] = None
_pool_lock = threading.Lock()


def get_connection_pool() -> IBKRConnectionPool:
    """Get or create the global connection pool singleton"""
    global _connection_pool

    if _connection_pool is None:
        with _pool_lock:
            if _connection_pool is None:
                _connection_pool = IBKRConnectionPool()
                logger.info("Initialized IBKR connection pool")

    return _connection_pool


@contextmanager
def create_ibkr_connection(
    host: str = None,
    port: int = None,
    client_id: int = None
):
    """
    Get a connection from the shared pool.
    Thread-safe - all threads share the same connection.

    Usage:
        with create_ibkr_connection() as ib:
            # Use ib connection
            stocks = ib.reqContractDetails(...)

    Args:
        host: IBKR host (ignored, uses pool config)
        port: IBKR port (ignored, uses pool config)
        client_id: Client ID (ignored, uses pool config)

    Yields:
        IB: Shared IB connection instance
    """
    pool = get_connection_pool()

    with pool.get_connection() as ib:
        yield ib


def get_account_summary() -> dict:
    """
    Get account summary for margin calculations.
    Uses the connection pool.

    Returns:
        Dict with account values
    """
    with create_ibkr_connection() as ib:
        account_values = ib.accountSummary()

        summary = {}
        for item in account_values:
            summary[item.tag] = float(item.value) if item.value else 0.0

        return summary
