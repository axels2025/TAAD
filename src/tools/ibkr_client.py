"""Interactive Brokers API client wrapper with retry logic.

This module provides a robust wrapper around ib_insync with automatic
reconnection, retry logic, and comprehensive error handling.
"""

import asyncio
import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ib_insync import IB, Contract, Index, LimitOrder, Option, Order, Stock, Trade, util
from loguru import logger

from src.config.base import IBKRConfig


@dataclass
class Quote:
    """Live market quote for a contract.

    Attributes:
        bid: Current bid price
        ask: Current ask price
        last: Last traded price (optional)
        volume: Trading volume (optional)
        timestamp: When quote was fetched
        is_valid: Whether the quote has valid bid/ask
        reason: Reason if quote is invalid
    """
    bid: float
    ask: float
    last: float | None = None
    volume: float | None = None
    timestamp: datetime | None = None
    is_valid: bool = True
    reason: str = ""


@dataclass
class OrderAuditEntry:
    """Audit log entry for order operations.

    Tracks all order placement attempts for debugging and reconciliation.

    Attributes:
        timestamp: When the action occurred
        action: Action type (PLACE, CANCEL, MODIFY)
        symbol: Contract symbol
        order_type: Order type (LMT, MKT, Adaptive)
        quantity: Number of contracts
        limit_price: Limit price if applicable
        order_id: IBKR order ID (filled after placement)
        status: Result status (SUBMITTED, FAILED, CANCELLED)
        error: Error message if failed
        reason: Human-readable reason for action
    """
    timestamp: datetime
    action: str
    symbol: str
    order_type: str
    quantity: int
    limit_price: float | None = None
    order_id: int | None = None
    status: str = "PENDING"
    error: str | None = None
    reason: str = ""


class IBKRConnectionError(Exception):
    """Raised when IBKR connection fails."""

    pass


class Error200Filter(logging.Filter):
    """Filter to suppress common harmless IBKR error messages.

    Suppresses:
    - Error 200: No security definition found (expected during contract search)
    - Error 300: Ticker ID not found (harmless timing issue)
    - Error 321: Contract validation (exchange not specified - handled elsewhere)
    - Error 10090/354: Market data subscription (snapshot mode used instead)
    - Error 10197: Competing live session (snapshot mode used instead)
    - Error 2104/2106/2158: Connection status messages (informational only)
    """

    SUPPRESSED_ERRORS = [
        "Error 200",   # No security definition
        "Error 300",   # Ticker ID not found
        "Error 321",   # Contract validation
        "Error 354",   # Market data not subscribed
        "Error 2104",  # Market data farm connection
        "Error 2106",  # HMDS data farm connection
        "Error 2158",  # Sec-def data farm connection
        "Error 10090", # Market data not subscribed
        "Error 10197", # Competing live session
    ]

    def filter(self, record):
        """Return False to suppress harmless error messages."""
        message = record.getMessage()

        # Suppress any of the known harmless errors
        for error_code in self.SUPPRESSED_ERRORS:
            if error_code in message:
                return False

        return True


class IBKRWarningFilter(logging.Filter):
    """Filter to suppress WARNING level messages from ib_insync.

    This filter suppresses non-critical warnings that clutter the output.
    Only allows ERROR and CRITICAL messages through.
    """

    def filter(self, record):
        """Return False for WARNING level messages to suppress them."""
        # Allow ERROR and CRITICAL through, suppress WARNING and below
        return record.levelno >= logging.ERROR


class IBKRErrorConsolidator(logging.Filter):
    """Consolidate common IBKR errors into clean, single-line messages.

    This filter catches verbose IBKR errors and either suppresses them completely
    or replaces them with concise, actionable messages.

    Errors handled:
    - 321: Contract validation (exchange not specified) - SUPPRESSED
    - 10090/354: Market data subscription errors - Consolidated to "No data for SYMBOL"
    - 10197: Competing live session - Consolidated message
    - 300: Ticker ID errors - SUPPRESSED
    - 2104/2106/2158: Connection status messages - SUPPRESSED
    """

    # Track consolidated errors to avoid spam
    _error_counts = {}
    _last_logged = {}
    _consolidation_interval = 60  # seconds

    # Map of error codes to consolidation behavior
    ERROR_PATTERNS = {
        # Completely suppress these (too noisy, not actionable)
        321: "suppress",   # Contract validation
        300: "suppress",   # Ticker ID not found
        2104: "suppress",  # Market data farm connection OK
        2106: "suppress",  # HMDS data farm connection OK
        2158: "suppress",  # Sec-def data farm connection OK

        # Consolidate these into clean messages
        10090: "no_market_data",    # Part of market data not subscribed
        354: "no_market_data",      # Market data not subscribed
        10197: "competing_session", # Competing live session
    }

    def filter(self, record):
        """Filter and consolidate IBKR error messages."""
        import re
        import time
        from loguru import logger as loguru_logger

        message = record.getMessage()

        # Extract error code from message
        error_match = re.search(r'Error (\d+),', message)
        if not error_match:
            return True  # Not an error message, let it through

        error_code = int(error_match.group(1))

        # Check if this error should be handled
        if error_code not in self.ERROR_PATTERNS:
            return True  # Unknown error, let it through

        action = self.ERROR_PATTERNS[error_code]

        # Suppress completely
        if action == "suppress":
            self._error_counts[error_code] = self._error_counts.get(error_code, 0) + 1
            return False

        # Consolidate market data errors
        if action == "no_market_data":
            # Extract symbol from message
            symbol_match = re.search(r'contract: Option\([^,]*symbol=\'([^\']+)\'', message)
            if symbol_match:
                symbol = symbol_match.group(1)
                key = f"no_data_{symbol}"
                current_time = time.time()

                # Log first occurrence at WARNING level
                if key not in self._last_logged:
                    self._last_logged[key] = current_time
                    loguru_logger.warning(
                        f"⚠ IBKR Error 354/10090: Market data not available for {symbol} options. "
                        f"This usually means: (1) No market data subscription for this symbol, "
                        f"(2) Options market closed, or (3) Invalid contract. "
                        f"Check IBKR market data subscriptions."
                    )
                # Subsequent occurrences within interval - just count them
                elif (current_time - self._last_logged[key]) > self._consolidation_interval:
                    self._last_logged[key] = current_time
                    loguru_logger.debug(f"No market data subscription for {symbol} (repeated)")

                return False  # Suppress original verbose message

        # Consolidate competing session errors
        if action == "competing_session":
            current_time = time.time()
            key = "competing_session"

            if key not in self._last_logged or (current_time - self._last_logged[key]) > self._consolidation_interval:
                self._last_logged[key] = current_time
                loguru_logger.warning("⚠ Competing IBKR session detected - using snapshot data instead of live stream")

            return False

        return True

    @classmethod
    def get_suppressed_counts(cls):
        """Get counts of suppressed error messages."""
        return cls._error_counts.copy()

    @classmethod
    def reset_counts(cls):
        """Reset error counters."""
        cls._error_counts.clear()
        cls._last_logged.clear()


class IBKRClient:
    """Wrapper around ib_insync with retry logic and error handling.

    This client provides automatic reconnection and retry logic for
    robust interaction with Interactive Brokers API.

    Example:
        >>> config = IBKRConfig(host="127.0.0.1", port=7497)
        >>> client = IBKRClient(config)
        >>> client.connect()
        >>> contracts = client.get_stock_contract("AAPL")
        >>> client.disconnect()
    """

    def __init__(self, config: IBKRConfig, max_retries: int = 3, suppress_errors: bool = True):
        """Initialize IBKR client.

        Args:
            config: IBKR configuration
            max_retries: Maximum number of connection retry attempts
            suppress_errors: Suppress expected IBKR errors (Error 200) from console output
        """
        self.config = config
        self.max_retries = max_retries
        self.ib = IB()
        self._is_connected = False
        self._suppress_errors = suppress_errors
        self._order_audit_log: list[OrderAuditEntry] = []

        # Disable ib_insync console logging to prevent error spam
        if suppress_errors:
            # Set ib_insync logging to ERROR level (suppress WARNING/INFO/DEBUG)
            logging.getLogger('ib_insync.client').setLevel(logging.ERROR)
            logging.getLogger('ib_insync.wrapper').setLevel(logging.ERROR)
            logging.getLogger('ib_insync.ib').setLevel(logging.ERROR)

            # Add filters to consolidate and suppress common errors
            wrapper_logger = logging.getLogger('ib_insync.wrapper')
            wrapper_logger.addFilter(Error200Filter())
            wrapper_logger.addFilter(IBKRWarningFilter())
            wrapper_logger.addFilter(IBKRErrorConsolidator())  # New consolidated error handler

            client_logger = logging.getLogger('ib_insync.client')
            client_logger.addFilter(IBKRWarningFilter())
            client_logger.addFilter(IBKRErrorConsolidator())

            util.logToConsole(False)

        # Track suppressed errors for debugging
        self._suppressed_error_count = 0

    def _error_filter(self, reqId, errorCode, errorString, contract):
        """Filter out expected errors during contract qualification.

        Error 200 = "No security definition has been found"
        This is expected when qualifying contracts - not all combinations exist.

        Errors 2104, 2106, 2107, 2119, 2158 = Market data connection status
        These are informational messages, not actual errors.
        """
        # Informational error codes that should be suppressed
        informational_codes = {
            200,   # No security definition (expected during qualification)
            300,   # Can't find EId with tickerId (timing/async issue, harmless)
            2104,  # Market data farm connection is OK
            2106,  # HMDS data farm connection is OK
            2107,  # HMDS data farm connection is inactive but should be available upon demand
            2119,  # Market data farm is connecting
            2158,  # Sec-def data farm connection is OK
            10349,  # Order TIF was set to DAY (informational, IBKR auto-corrects)
        }

        if errorCode in informational_codes:
            # Silently count but don't log informational messages
            self._suppressed_error_count += 1
        else:
            # Log other errors normally
            logger.warning(
                f"IBKR Error {errorCode}, reqId {reqId}: {errorString}"
            )

    def get_suppressed_error_count(self) -> int:
        """Get count of suppressed Error 200 messages.

        Returns:
            int: Number of Error 200 messages suppressed
        """
        return self._suppressed_error_count

    def reset_suppressed_error_count(self):
        """Reset the suppressed error counter."""
        self._suppressed_error_count = 0

    def get_error_summary(self) -> dict:
        """Get summary of all suppressed errors.

        Returns:
            dict: Error code to count mapping

        Example:
            >>> client.get_error_summary()
            {'200': 45, '321': 12, '10090': 3}
        """
        summary = {'200': self._suppressed_error_count}
        summary.update(IBKRErrorConsolidator.get_suppressed_counts())
        return summary

    def log_error_summary(self):
        """Log a summary of suppressed errors for debugging."""
        summary = self.get_error_summary()
        if any(count > 0 for count in summary.values()):
            logger.debug(f"Suppressed IBKR errors: {summary}")

    def connect(self, retry: bool = True) -> bool:
        """Connect to Interactive Brokers.

        Args:
            retry: Whether to retry on connection failure

        Returns:
            bool: True if connection successful

        Raises:
            IBKRConnectionError: If connection fails after retries

        Example:
            >>> client = IBKRClient(config)
            >>> client.connect()
            True
        """
        # Patch asyncio to work with ib_insync's event loop
        util.patchAsyncio()

        attempts = 0
        max_attempts = self.max_retries if retry else 1

        # Temporarily suppress all ib_insync console output during connection
        # Save original log levels
        client_logger = logging.getLogger('ib_insync.client')
        wrapper_logger = logging.getLogger('ib_insync.wrapper')
        ib_logger = logging.getLogger('ib_insync.ib')

        original_client_level = client_logger.level
        original_wrapper_level = wrapper_logger.level
        original_ib_level = ib_logger.level

        # Set to CRITICAL to suppress ERROR messages during connection
        if self._suppress_errors:
            client_logger.setLevel(logging.CRITICAL)
            wrapper_logger.setLevel(logging.CRITICAL)
            ib_logger.setLevel(logging.CRITICAL)

        try:
            while attempts < max_attempts:
                try:
                    logger.info(
                        f"Connecting to IBKR at {self.config.host}:{self.config.port}..."
                    )

                    self.ib.connect(
                        host=self.config.host,
                        port=self.config.port,
                        clientId=self.config.client_id,
                        timeout=self.config.timeout,
                    )

                    # Configure market data type based on subscription
                    # Type 1 = Live data (real-time, requires paid subscription)
                    # Type 2 = Frozen data (last available)
                    # Type 3 = Delayed data (15-20 min delay, free)
                    # Type 4 = Delayed-frozen data
                    market_data_type = int(os.getenv("IBKR_MARKET_DATA_TYPE", "1"))
                    self.ib.reqMarketDataType(market_data_type)

                    data_type_names = {1: "live", 2: "frozen", 3: "delayed", 4: "delayed-frozen"}
                    logger.info(f"Configured IBKR to use {data_type_names.get(market_data_type, 'unknown')} market data (type {market_data_type})")

                    # Set up error filtering if enabled
                    if self._suppress_errors:
                        self.ib.errorEvent += self._error_filter

                    self._is_connected = True
                    logger.info("Successfully connected to IBKR")
                    return True

                except Exception as e:
                    attempts += 1
                    logger.warning(
                        f"Connection attempt {attempts}/{max_attempts} failed: {e}"
                    )

                    if attempts < max_attempts:
                        wait_time = 2**attempts  # Exponential backoff
                        logger.info(f"Retrying in {wait_time} seconds...")
                        time.sleep(wait_time)
                    else:
                        error_msg = (
                            f"Failed to connect to IBKR after {max_attempts} attempts"
                        )
                        logger.error(error_msg)
                        raise IBKRConnectionError(error_msg) from e

            return False

        finally:
            # Restore original log levels after connection attempt
            if self._suppress_errors:
                client_logger.setLevel(original_client_level)
                wrapper_logger.setLevel(original_wrapper_level)
                ib_logger.setLevel(original_ib_level)

    def disconnect(self) -> None:
        """Disconnect from Interactive Brokers.

        Example:
            >>> client.disconnect()
        """
        if self._is_connected:
            self.ib.disconnect()
            self._is_connected = False
            logger.info("Disconnected from IBKR")

    def check_market_data_health(self) -> tuple[bool, str]:
        """Check if market data connection is healthy.

        Tests market data by requesting a quote for SPY and checking for:
        - Valid data (not NaN)
        - No TWS conflicts (Error 10197)
        - Data arrives within reasonable time

        Returns:
            Tuple of (is_healthy, error_message)
                is_healthy: True if market data is flowing properly
                error_message: Empty if healthy, otherwise description of problem

        Example:
            >>> is_healthy, error = client.check_market_data_health()
            >>> if not is_healthy:
            ...     print(f"Market data issue: {error}")
        """
        self.ensure_connected()

        try:
            # Test with SPY - most liquid symbol
            spy_contract = self.get_stock_contract("SPY")
            if not spy_contract:
                return False, "Could not create test contract"

            # Try to get market data
            data = self.get_market_data(spy_contract, snapshot=True)

            if data is None:
                # Check if we got Error 10197 (competing live session)
                # This error is logged but we need to detect it
                return (
                    False,
                    "No market data available - possible TWS conflict (Error 10197). "
                    "Close Trader Workstation and try again."
                )

            # Verify data has valid values
            if data.get("bid") and data.get("ask") and data.get("last"):
                logger.info("✓ Market data health check passed")
                return True, ""
            else:
                return (
                    False,
                    "Market data incomplete - missing bid/ask/last prices"
                )

        except Exception as e:
            return False, f"Market data health check failed: {e}"

    def is_connected(self) -> bool:
        """Check if client is connected.

        Returns:
            bool: True if connected

        Example:
            >>> client.is_connected()
            True
        """
        return self._is_connected and self.ib.isConnected()

    def ensure_connected(self) -> None:
        """Ensure connection is active, reconnect if necessary.

        Raises:
            IBKRConnectionError: If reconnection fails
        """
        if not self.is_connected():
            logger.warning("Connection lost, attempting to reconnect...")
            self.connect()

    def get_index_contract(self, symbol: str, exchange: str = "CBOE") -> Index:
        """Get an index contract (SPX, XSP, VIX, etc.).

        Args:
            symbol: Index symbol (e.g. SPX, XSP, VIX)
            exchange: Exchange name (default: CBOE)

        Returns:
            Index contract

        Example:
            >>> contract = client.get_index_contract("SPX")
            >>> print(contract.symbol)
            'SPX'
        """
        self.ensure_connected()
        return Index(symbol, exchange, "USD")

    def get_stock_contract(self, symbol: str, exchange: str = "SMART") -> Stock:
        """Get a stock contract.

        Args:
            symbol: Stock ticker symbol
            exchange: Exchange name (default: SMART)

        Returns:
            Stock contract

        Example:
            >>> contract = client.get_stock_contract("AAPL")
            >>> print(contract.symbol)
            'AAPL'
        """
        self.ensure_connected()
        return Stock(symbol, exchange, "USD")

    def get_option_contract(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str = "P",
        exchange: str = "SMART",
        trading_class: str = "",
    ) -> Option:
        """Get an option contract.

        Args:
            symbol: Underlying stock symbol
            expiration: Expiration date (YYYYMMDD format)
            strike: Strike price
            right: Option right ('P' for put, 'C' for call)
            exchange: Exchange name (default: SMART)
            trading_class: Trading class (required for proper contract qualification)

        Returns:
            Option contract

        Example:
            >>> contract = client.get_option_contract(
            ...     "AAPL", "20240119", 150.0, "P", trading_class="AAPL"
            ... )
        """
        self.ensure_connected()
        return Option(
            symbol, expiration, strike, right, exchange, tradingClass=trading_class
        )

    def qualify_contract(self, contract: Contract) -> Contract | None:
        """Qualify a contract with IBKR to get full details.

        Args:
            contract: Contract to qualify

        Returns:
            Qualified contract or None if qualification fails

        Example:
            >>> stock = Stock("AAPL", "SMART", "USD")
            >>> qualified = client.qualify_contract(stock)
        """
        self.ensure_connected()

        try:
            qualified_contracts = self.ib.qualifyContracts(contract)
            if qualified_contracts:
                return qualified_contracts[0]
            else:
                logger.warning(f"Could not qualify contract: {contract}")
                return None
        except Exception as e:
            logger.error(f"Error qualifying contract: {e}")
            return None

    def get_market_data(self, contract: Contract, snapshot: bool = True) -> dict | None:
        """Get market data for a contract using streaming mode with event-driven wait.

        Args:
            contract: Contract to get data for
            snapshot: Ignored (kept for API compat). Always uses streaming mode
                      for reliability with indices and low-liquidity symbols.

        Returns:
            Dictionary with market data or None on error

        Example:
            >>> stock = client.get_stock_contract("AAPL")
            >>> data = client.get_market_data(stock)
            >>> print(data["last"])
            150.25
        """
        self.ensure_connected()

        try:
            import math
            import time

            # Use streaming mode (not snapshot) — snapshot returns NaN for
            # indices (VIX) and low-liquidity symbols during pre-market
            ticker = self.ib.reqMktData(contract, '', False, False)

            # Event-driven wait: poll every 100ms for up to 3 seconds
            timeout = 3.0
            start = time.time()
            while (time.time() - start) < timeout:
                has_last = (
                    ticker.last is not None
                    and not (isinstance(ticker.last, float) and math.isnan(ticker.last))
                    and ticker.last > 0
                )
                has_bid_ask = (
                    ticker.bid is not None
                    and ticker.ask is not None
                    and not (isinstance(ticker.bid, float) and math.isnan(ticker.bid))
                    and not (isinstance(ticker.ask, float) and math.isnan(ticker.ask))
                    and ticker.bid > 0
                    and ticker.ask > 0
                )
                if has_last or has_bid_ask:
                    break
                self.ib.sleep(0.1)

            # Build data dict from whatever we got
            if ticker:
                last_val = ticker.last if (
                    ticker.last is not None
                    and not (isinstance(ticker.last, float) and math.isnan(ticker.last))
                ) else None
                bid_val = ticker.bid if (
                    ticker.bid is not None
                    and not (isinstance(ticker.bid, float) and math.isnan(ticker.bid))
                ) else None
                ask_val = ticker.ask if (
                    ticker.ask is not None
                    and not (isinstance(ticker.ask, float) and math.isnan(ticker.ask))
                ) else None

                if last_val or (bid_val and ask_val):
                    return {
                        "symbol": contract.symbol,
                        "last": last_val or ((bid_val + ask_val) / 2 if bid_val and ask_val else None),
                        "bid": bid_val,
                        "ask": ask_val,
                        "volume": ticker.volume if not (isinstance(ticker.volume, float) and math.isnan(ticker.volume)) else None,
                        "open": ticker.open if not (isinstance(ticker.open, float) and math.isnan(ticker.open)) else None,
                        "high": ticker.high if not (isinstance(ticker.high, float) and math.isnan(ticker.high)) else None,
                        "low": ticker.low if not (isinstance(ticker.low, float) and math.isnan(ticker.low)) else None,
                        "close": ticker.close if not (isinstance(ticker.close, float) and math.isnan(ticker.close)) else None,
                    }

            logger.warning(
                f"No valid market data for {contract.symbol} after {timeout}s"
            )
            return None

        except Exception as e:
            logger.error(f"Error getting market data: {e}")
            return None

    def get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price (supports pre-market data).

        Args:
            symbol: Stock ticker symbol

        Returns:
            Current stock price or None if unavailable

        Example:
            >>> price = client.get_stock_price("AAPL")
            >>> print(f"${price:.2f}")
            150.25
        """
        contract = self.get_stock_contract(symbol)
        data = self.get_market_data(contract, snapshot=True)

        if data and data.get("last"):
            return data["last"]

        logger.debug(f"No price data available for {symbol}")
        return None

    def get_option_quote(
        self, symbol: str, strike: float, expiration: str, right: str
    ) -> dict | None:
        """Get option quote with bid/ask (supports pre-market data).

        Args:
            symbol: Underlying stock symbol
            strike: Strike price
            expiration: Expiration date (YYYYMMDD format)
            right: Option right ('P' or 'C')

        Returns:
            Dictionary with bid/ask or None if unavailable

        Example:
            >>> quote = client.get_option_quote("AAPL", 150.0, "20240119", "P")
            >>> print(f"Bid: ${quote['bid']:.2f}, Ask: ${quote['ask']:.2f}")
            Bid: $1.50, Ask: $1.55
        """
        contract = self.get_option_contract(symbol, expiration, strike, right)
        qualified = self.qualify_contract(contract)

        if not qualified:
            logger.debug(f"Could not qualify option contract for {symbol}")
            return None

        data = self.get_market_data(qualified, snapshot=True)

        if data and data.get("bid") and data.get("ask"):
            return {
                "symbol": symbol,
                "strike": strike,
                "expiration": expiration,
                "right": right,
                "bid": data["bid"],
                "ask": data["ask"],
                "last": data.get("last"),
            }

        logger.debug(f"No option quote available for {symbol} {strike}{right}")
        return None

    def get_account_summary(self) -> dict:
        """Get account summary information.

        Returns:
            Dictionary with account information

        Example:
            >>> summary = client.get_account_summary()
            >>> print(summary["NetLiquidation"])
            100000.0
        """
        self.ensure_connected()

        try:
            account_values = self.ib.accountSummary()
            summary = {}

            for item in account_values:
                try:
                    # Try to convert to float
                    summary[item.tag] = float(item.value) if item.value else 0.0
                except (ValueError, TypeError):
                    # Keep as string if not numeric
                    summary[item.tag] = item.value

            return summary

        except Exception as e:
            logger.error(f"Error getting account summary: {e}")
            return {}

    def get_contract_details(self, symbol: str) -> dict | None:
        """Get contract details including sector/industry information.

        Args:
            symbol: Stock symbol

        Returns:
            Dictionary with contract details or None if not found

        Example:
            >>> details = client.get_contract_details("AAPL")
            >>> print(details["industry"])
            "Technology"
            >>> print(details["category"])
            "Computers"
        """
        self.ensure_connected()

        try:
            contract = self.get_stock_contract(symbol)
            details_list = self.ib.reqContractDetails(contract)

            if not details_list:
                logger.warning(f"No contract details found for {symbol}")
                return None

            # Get first result (should only be one for stocks)
            details = details_list[0]

            return {
                "symbol": symbol,
                "industry": details.industry or "Unknown",
                "category": details.category or "Unknown",
                "subcategory": details.subcategory or "Unknown",
                "long_name": details.longName or symbol,
                "contract_id": details.contract.conId,
            }

        except Exception as e:
            logger.warning(f"Error getting contract details for {symbol}: {e}")
            return None

    def is_market_open(self, exchange: str = "NYSE") -> dict:
        """Check if market is currently open for trading.

        Uses IBKR's contract details to get actual trading hours and
        compares against current time.

        Args:
            exchange: Exchange to check (NYSE, NASDAQ, CBOE, etc.)

        Returns:
            dict with keys:
                - is_open: bool - True if market is currently open
                - status: str - "open", "closed", "pre_market", "after_hours"
                - next_open: str - Next market open time (ISO format)
                - next_close: str - Next market close time (ISO format)

        Example:
            >>> status = client.is_market_open()
            >>> if status["is_open"]:
            ...     print("Market is open!")
            >>> else:
            ...     print(f"Market closed. Opens at {status['next_open']}")
        """
        self.ensure_connected()

        try:
            from datetime import datetime, time
            import pytz

            # Create a simple stock contract for the exchange
            from ib_insync import Stock
            contract = Stock("SPY", exchange, "USD")

            # Get contract details which include trading hours
            details = self.ib.reqContractDetails(contract)

            if not details:
                logger.warning(f"Could not get market hours for {exchange}")
                return {
                    "is_open": None,
                    "status": "unknown",
                    "next_open": None,
                    "next_close": None,
                }

            # Extract trading hours from first result
            detail = details[0]
            trading_hours = detail.tradingHours
            liquid_hours = detail.liquidHours

            # Get current time in ET (market timezone)
            et_tz = pytz.timezone("America/New_York")
            now_et = datetime.now(et_tz)
            current_time = now_et.time()
            current_weekday = now_et.weekday()  # 0=Monday, 6=Sunday

            # Regular trading hours (approximate - actual varies by exchange)
            market_open_time = time(9, 30)
            market_close_time = time(16, 0)
            pre_market_start = time(4, 0)
            after_hours_end = time(20, 0)

            # Check if weekend
            if current_weekday >= 5:  # Saturday or Sunday
                return {
                    "is_open": False,
                    "status": "closed_weekend",
                    "next_open": "Monday 09:30 ET",
                    "next_close": "Monday 16:00 ET",
                }

            # Check if during regular hours
            if market_open_time <= current_time <= market_close_time:
                return {
                    "is_open": True,
                    "status": "open",
                    "next_open": now_et.strftime("%Y-%m-%d 09:30 ET"),
                    "next_close": now_et.strftime("%Y-%m-%d 16:00 ET"),
                }

            # Check if pre-market
            elif pre_market_start <= current_time < market_open_time:
                return {
                    "is_open": False,
                    "status": "pre_market",
                    "next_open": now_et.strftime("%Y-%m-%d 09:30 ET"),
                    "next_close": now_et.strftime("%Y-%m-%d 16:00 ET"),
                }

            # Check if after-hours
            elif market_close_time < current_time <= after_hours_end:
                return {
                    "is_open": False,
                    "status": "after_hours",
                    "next_open": (now_et.replace(hour=9, minute=30) + pytz.timezone("America/New_York").localize(datetime.now()).utcoffset()).strftime("%Y-%m-%d 09:30 ET"),
                    "next_close": now_et.strftime("%Y-%m-%d 16:00 ET"),
                }

            # Market closed overnight
            else:
                return {
                    "is_open": False,
                    "status": "closed",
                    "next_open": now_et.strftime("%Y-%m-%d 09:30 ET"),
                    "next_close": now_et.strftime("%Y-%m-%d 16:00 ET"),
                }

        except Exception as e:
            logger.error(f"Error checking market hours: {e}")
            return {
                "is_open": None,
                "status": "error",
                "next_open": None,
                "next_close": None,
            }

    def wait_for_market_open(self, check_interval: int = 300):
        """Wait until market opens, checking periodically.

        Args:
            check_interval: Seconds between checks (default: 300 = 5 minutes)

        Example:
            >>> client.wait_for_market_open()
            Market closed. Waiting for open...
            Market opens at 2026-01-27 09:30 ET
            [5 minutes later...]
            Market is now open!
        """
        import time

        while True:
            status = self.is_market_open()

            if status["is_open"]:
                logger.info("✓ Market is open")
                return

            logger.info(
                f"Market {status['status']}. "
                f"Opens at {status['next_open']}. "
                f"Checking again in {check_interval // 60} minutes..."
            )
            time.sleep(check_interval)

    def get_actual_margin(
        self,
        contract,
        quantity: int = 1,
        max_retries: int = 3,
    ) -> Optional[float]:
        """Get actual margin requirement from IBKR using whatIfOrder.

        Works during market hours AND after hours (uses closing price).
        Includes retry logic to handle known bug #380 where whatIfOrder
        occasionally returns infinity.

        Args:
            contract: Qualified option contract
            quantity: Number of contracts (default 1)
            max_retries: Retry attempts for edge cases (default 3)

        Returns:
            Initial margin requirement in dollars, or None if failed

        Example:
            >>> contract = client.get_option_contract("AAPL", "20260228", 150.0, "P")
            >>> qualified = client.qualify_contract(contract)
            >>> margin = client.get_actual_margin(qualified)
            >>> print(f"Margin: ${margin:.2f}")
            Margin: $3750.50
        """
        if not self._is_connected:
            logger.warning("Cannot get margin: not connected to IBKR")
            return None

        from ib_insync import MarketOrder

        order = MarketOrder("SELL", quantity)
        order.tif = "DAY"  # Explicitly set Time-In-Force to avoid IBKR warning

        for attempt in range(max_retries):
            try:
                result = self.ib.whatIfOrder(contract, order)

                if not result:
                    logger.warning(
                        f"whatIfOrder attempt {attempt + 1}/{max_retries} returned None"
                    )
                    self.ib.sleep(0.1 * (attempt + 1))
                    continue

                # Check for valid result (not infinity - known bug #380)
                init_margin = result.initMarginChange
                if init_margin and init_margin != "":
                    try:
                        margin_value = float(init_margin)

                        # Check for infinity bug (#380) and zero-margin bug
                        if abs(margin_value) > 0 and abs(margin_value) < 1e308:
                            logger.debug(
                                f"Got actual margin for {contract.symbol} ${contract.strike}: "
                                f"${abs(margin_value):.2f} (attempt {attempt + 1})"
                            )
                            return abs(margin_value)
                        else:
                            logger.debug(
                                f"whatIfOrder returned invalid margin {margin_value} "
                                f"(attempt {attempt + 1}), retrying..."
                            )
                    except (ValueError, TypeError) as e:
                        logger.warning(
                            f"Failed to parse margin '{init_margin}' (attempt {attempt + 1}): {e}"
                        )

                # Wait and retry with progressive backoff
                self.ib.sleep(0.1 * (attempt + 1))

            except Exception as e:
                logger.warning(f"whatIfOrder attempt {attempt + 1}/{max_retries} failed: {e}")
                self.ib.sleep(0.1 * (attempt + 1))

        logger.warning(
            f"Failed to get actual margin for {contract.symbol} ${contract.strike} "
            f"after {max_retries} attempts"
        )
        return None

    def get_margin_requirement(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        option_type: str,
        contracts: int,
        action: str = "SELL",
    ) -> Optional[float]:
        """Get actual margin requirement from IBKR using whatIfOrder API.

        This is a convenience wrapper around get_actual_margin() that accepts
        symbol/strike/expiration parameters and handles contract creation.

        Works during market hours AND after hours (uses closing price).

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            strike: Strike price
            expiration: Expiration date in YYYYMMDD format (e.g., "20260228")
            option_type: "PUT" or "CALL"
            contracts: Number of contracts
            action: "SELL" for opening naked positions (default: "SELL")

        Returns:
            float: Margin requirement in dollars, or None if error

        Example:
            >>> margin = client.get_margin_requirement("AAPL", 150.0, "20260228", "PUT", 5)
            >>> print(f"Margin required: ${margin:.2f}")
            Margin required: $3750.50
        """
        if not self._is_connected:
            logger.warning("Cannot get margin requirement: not connected to IBKR")
            return None

        try:
            # Create option contract
            # Convert option_type (PUT/CALL) to right (P/C)
            right = "P" if option_type == "PUT" else "C"
            contract = self.get_option_contract(
                symbol=symbol,
                expiration=expiration,
                strike=strike,
                right=right,
            )

            if not contract:
                logger.warning(
                    f"Failed to create contract for {symbol} ${strike} {option_type} {expiration}"
                )
                return None

            # Qualify contract
            qualified = self.qualify_contract(contract)
            if not qualified:
                logger.warning(f"Could not qualify contract for margin check: {contract}")
                return None

            # Use the robust get_actual_margin method with retry logic
            return self.get_actual_margin(qualified, quantity=contracts)

        except Exception as e:
            logger.error(f"Error getting margin requirement: {e}", exc_info=True)
            return None

    # ═════════════════════════════════════════════════════════════════════════
    # ORDER OPERATIONS (Institutional-Grade Wrapper Methods)
    # ═════════════════════════════════════════════════════════════════════════

    async def place_order(
        self,
        contract: Contract,
        order: Order,
        reason: str = "",
    ) -> Trade:
        """Place order with audit logging and error handling.

        All order placement MUST go through this method for:
        - Consistent error handling
        - Audit trail
        - Centralized logging

        Args:
            contract: Qualified contract
            order: Order object (LimitOrder, MarketOrder, etc.)
            reason: Human-readable reason for placement

        Returns:
            Trade object from ib_insync

        Raises:
            Exception: If order placement fails

        Example:
            >>> order = LimitOrder(action='SELL', totalQuantity=5, lmtPrice=0.45)
            >>> trade = await client.place_order(contract, order, "Staged trade AAPL")
        """
        self.ensure_connected()

        # Pre-flight validation
        self._validate_order(order)

        # Audit log entry
        audit = OrderAuditEntry(
            timestamp=datetime.now(),
            action="PLACE",
            symbol=contract.symbol,
            order_type=order.orderType if hasattr(order, 'orderType') else type(order).__name__,
            quantity=order.totalQuantity,
            limit_price=getattr(order, 'lmtPrice', None),
            reason=reason,
        )

        try:
            trade = self.ib.placeOrder(contract, order)
            audit.order_id = trade.order.orderId
            audit.status = "SUBMITTED"
            logger.info(
                f"Order placed: {contract.symbol} {order.action} x{order.totalQuantity} "
                f"@ ${getattr(order, 'lmtPrice', 'MKT')}"
                + (f" ({reason})" if reason else "")
            )
            return trade

        except Exception as e:
            audit.status = "FAILED"
            audit.error = str(e)
            logger.error(f"Order failed: {contract.symbol} - {e}")
            raise

        finally:
            self._order_audit_log.append(audit)

    async def cancel_order(self, order_id: int, reason: str = "") -> bool:
        """Cancel order with retry logic.

        Args:
            order_id: IBKR order ID to cancel
            reason: Human-readable reason for cancellation

        Returns:
            True if cancellation successful, False otherwise

        Example:
            >>> success = await client.cancel_order(123, "Price moved too far")
        """
        self.ensure_connected()

        max_retries = 3

        for attempt in range(max_retries):
            try:
                # Create order with ID for cancellation
                order = Order()
                order.orderId = order_id
                self.ib.cancelOrder(order)

                logger.info(f"Order {order_id} cancelled: {reason}")
                return True

            except Exception as e:
                if attempt < max_retries - 1:
                    await asyncio.sleep(0.2)
                else:
                    logger.error(
                        f"Failed to cancel order {order_id} after {max_retries} attempts: {e}"
                    )
                    return False

        return False

    async def modify_order(
        self,
        trade: Trade,
        new_limit: float,
        reason: str = "",
    ) -> Trade:
        """Modify order limit price.

        Args:
            trade: Existing trade object
            new_limit: New limit price
            reason: Human-readable reason for modification

        Returns:
            Updated Trade object

        Example:
            >>> trade = await client.modify_order(trade, 0.44, "Adjust for fill")
        """
        self.ensure_connected()

        old_limit = trade.order.lmtPrice
        trade.order.lmtPrice = new_limit

        # Audit log
        audit = OrderAuditEntry(
            timestamp=datetime.now(),
            action="MODIFY",
            symbol=trade.contract.symbol,
            order_type=trade.order.orderType if hasattr(trade.order, 'orderType') else type(trade.order).__name__,
            quantity=trade.order.totalQuantity,
            limit_price=new_limit,
            order_id=trade.order.orderId,
            status="MODIFIED",
            reason=reason,
        )
        self._order_audit_log.append(audit)

        result = self.ib.placeOrder(trade.contract, trade.order)
        logger.info(
            f"Order {trade.order.orderId} modified: ${old_limit:.2f} → ${new_limit:.2f}"
            + (f" ({reason})" if reason else "")
        )

        return result

    async def get_quote(
        self,
        contract: Contract,
        timeout: float | None = None,
    ) -> Quote:
        """Get live quote with event-driven timeout.

        Uses event-driven waiting - returns immediately when valid quote
        arrives instead of blindly waiting for fixed timeout.

        Cancels the market data subscription after each call to ensure
        fresh data on subsequent requests (prevents stale ticker reuse).

        Args:
            contract: Contract to get quote for
            timeout: Maximum wait time in seconds (default from env QUOTE_FETCH_TIMEOUT_SECONDS)

        Returns:
            Quote object with bid/ask or invalid quote if timeout

        Example:
            >>> quote = await client.get_quote(contract)
            >>> if quote.is_valid:
            ...     print(f"Bid: ${quote.bid:.2f}, Ask: ${quote.ask:.2f}")
        """
        self.ensure_connected()

        timeout = timeout or float(os.getenv("QUOTE_FETCH_TIMEOUT_SECONDS", "0.5"))

        ticker = self.ib.reqMktData(contract, '', False, False)

        try:
            # Event-driven wait - check every 50ms until valid quote or timeout
            start = time.time()
            while (time.time() - start) < timeout:
                if self._is_valid_quote(ticker):
                    # Safely extract values (indices may have last but no bid/ask)
                    bid = ticker.bid if (ticker.bid is not None and not math.isnan(ticker.bid) and ticker.bid > 0) else 0
                    ask = ticker.ask if (ticker.ask is not None and not math.isnan(ticker.ask) and ticker.ask > 0) else 0
                    last = ticker.last if (ticker.last is not None and not math.isnan(ticker.last)) else 0
                    return Quote(
                        bid=bid,
                        ask=ask,
                        last=last,
                        volume=ticker.volume,
                        timestamp=datetime.now(),
                        is_valid=True,
                        reason="",
                    )
                await asyncio.sleep(0.05)  # Check every 50ms

            # Timeout - return invalid quote
            return Quote(
                bid=0,
                ask=0,
                is_valid=False,
                reason=f"Timeout after {timeout}s",
            )
        finally:
            # Always cancel market data subscription to free the data line
            # and ensure fresh subscriptions on subsequent calls. Without this,
            # ib_insync reuses stale tickers that never received data.
            try:
                self.ib.cancelMktData(contract)
            except Exception:
                pass

    async def get_quotes_batch(
        self,
        contracts: list[Contract],
        timeout: float | None = None,
    ) -> list[Quote]:
        """Get quotes for multiple contracts in parallel.

        Each quote is fetched independently with its own timeout - fast quotes
        don't wait for slow ones. This is more efficient than all-or-nothing
        timeout and provides better fault tolerance.

        Args:
            contracts: List of contracts to get quotes for
            timeout: Maximum wait time per quote in seconds (default from env)

        Returns:
            List of Quote objects in same order as contracts.
            Invalid quotes are returned for contracts that timeout.

        Example:
            >>> quotes = await client.get_quotes_batch([contract1, contract2, contract3])
            >>> valid_quotes = [q for q in quotes if q.is_valid]
            >>> print(f"{len(valid_quotes)}/{len(quotes)} quotes valid")
        """
        self.ensure_connected()

        # Delegate to get_quote() for each contract (parallel execution)
        # This reuses all the event-driven logic, validation, error handling
        timeout = timeout or float(os.getenv("QUOTE_FETCH_TIMEOUT_SECONDS", "0.5"))
        quote_tasks = [self.get_quote(contract, timeout) for contract in contracts]
        quotes = await asyncio.gather(*quote_tasks)

        # Log summary
        valid_count = sum(1 for q in quotes if q.is_valid)
        logger.debug(
            f"Batch quotes: {valid_count}/{len(contracts)} valid "
            f"(timeout={timeout}s per quote)"
        )

        return quotes

    def _is_valid_quote(self, ticker) -> bool:
        """Check if ticker has valid market data.

        Valid if we have bid/ask pair OR a valid last price (for indices like VIX
        that don't have bid/ask).

        Args:
            ticker: Ticker object from ib_insync

        Returns:
            True if valid market data is available
        """
        has_bid_ask = (
            ticker.bid is not None
            and ticker.bid > 0
            and not math.isnan(ticker.bid)
            and ticker.ask is not None
            and ticker.ask > 0
            and not math.isnan(ticker.ask)
        )
        has_last = (
            ticker.last is not None
            and ticker.last > 0
            and not math.isnan(ticker.last)
        )
        return has_bid_ask or has_last

    async def qualify_contracts_async(
        self,
        *contracts: Contract,
    ) -> list[Contract]:
        """Batch qualify contracts asynchronously.

        Args:
            contracts: Variable number of Contract objects

        Returns:
            List of qualified contracts

        Example:
            >>> contracts = [contract1, contract2, contract3]
            >>> qualified = await client.qualify_contracts_async(*contracts)
        """
        self.ensure_connected()

        return await self.ib.qualifyContractsAsync(*contracts)

    def _validate_order(self, order: Order) -> None:
        """Pre-flight order validation.

        Args:
            order: Order to validate

        Raises:
            ValueError: If order is invalid
        """
        if order.totalQuantity <= 0:
            raise ValueError(f"Invalid quantity: {order.totalQuantity}")

        if hasattr(order, 'lmtPrice') and order.lmtPrice is not None:
            if order.lmtPrice <= 0:
                raise ValueError(f"Invalid limit price: {order.lmtPrice}")

    # ─────────────────────────────────────────────────────────────────────────
    # TRADE & POSITION QUERIES (For monitoring and reconciliation)
    # ─────────────────────────────────────────────────────────────────────────

    def get_trades(self) -> list:
        """Get all trades for this session.

        Returns:
            List of Trade objects from ib_insync

        Example:
            >>> trades = client.get_trades()
            >>> for trade in trades:
            ...     print(f"Order {trade.order.orderId}: {trade.orderStatus.status}")
        """
        self.ensure_connected()
        return self.ib.trades()

    def get_orders(self) -> list:
        """Get all open orders.

        Returns:
            List of Order objects

        Example:
            >>> orders = client.get_orders()
            >>> print(f"Found {len(orders)} open orders")
        """
        self.ensure_connected()
        return self.ib.orders()

    def get_positions(self) -> list:
        """Get all current positions.

        Returns:
            List of Position objects

        Example:
            >>> positions = client.get_positions()
            >>> for pos in positions:
            ...     print(f"{pos.contract.symbol}: {pos.position} contracts")
        """
        self.ensure_connected()
        return self.ib.positions()

    def get_executions(self) -> list:
        """Get all executions (fills) for this session.

        Returns:
            List of Execution objects

        Example:
            >>> executions = client.get_executions()
            >>> for exec in executions:
            ...     print(f"Filled {exec.shares} @ ${exec.price}")
        """
        self.ensure_connected()
        return self.ib.executions()

    def get_fills(self) -> list:
        """Get all fills with commission details.

        Returns:
            List of Fill objects with commission reports

        Example:
            >>> fills = client.get_fills()
            >>> for fill in fills:
            ...     if fill.commissionReport:
            ...         print(f"Commission: ${fill.commissionReport.commission}")
        """
        self.ensure_connected()
        return self.ib.fills()

    def get_req_executions(self) -> list:
        """Request executions from IBKR server via reqExecutions().

        Unlike get_executions() (which returns cached ib.executions()) and
        get_fills() (which returns cached ib.fills()), this method makes an
        active API request to the IBKR server. It returns Fill objects that
        include real orderIds, permIds, and actual fill prices — even for
        orders placed in prior API sessions.

        Returns:
            List of Fill objects with .execution.orderId, .execution.permId,
            .execution.avgPrice, and .commissionReport

        Example:
            >>> fills = client.get_req_executions()
            >>> for fill in fills:
            ...     print(f"Order {fill.execution.orderId}: "
            ...           f"{fill.execution.side} @ ${fill.execution.avgPrice}")
        """
        from ib_insync import ExecutionFilter

        self.ensure_connected()
        fills = self.ib.reqExecutions(ExecutionFilter())
        logger.info(f"reqExecutions returned {len(fills)} fills")
        return fills

    def get_historical_executions(self, days_back: int = 7) -> list:
        """Get fills from the current API session, optionally filtered by date.

        NOTE: This only returns fills from the current API session (ib.fills()).
        It does NOT fetch historical executions from the server. For cross-session
        executions, use get_req_executions() which calls ib.reqExecutions().

        Args:
            days_back: Number of days to look back (default: 7)

        Returns:
            List of Fill objects from the current session only

        Example:
            >>> fills = client.get_historical_executions(days_back=7)
            >>> for fill in fills:
            ...     print(f"{fill.time}: {fill.execution.side} @ ${fill.execution.avgPrice}")
        """
        from datetime import datetime, timedelta

        self.ensure_connected()

        # Use ib.fills() which gives all fills from current session
        # NOTE: ib.fills() only returns fills from the current API session.
        all_fills = self.ib.fills()

        logger.info(f"Retrieved {len(all_fills)} fills from IBKR")

        # Filter by date if needed
        if days_back and days_back > 0:
            cutoff_date = datetime.now() - timedelta(days=days_back)
            filtered_fills = []

            for fill in all_fills:
                if hasattr(fill, 'time') and fill.time:
                    fill_time = fill.time
                    # Handle timezone-aware datetime
                    if hasattr(fill_time, 'replace'):
                        fill_time_naive = fill_time.replace(tzinfo=None)
                        if fill_time_naive >= cutoff_date:
                            filtered_fills.append(fill)
                else:
                    # Include fills without timestamps
                    filtered_fills.append(fill)

            logger.info(f"Filtered to {len(filtered_fills)} fills within last {days_back} days")
            return filtered_fills

        return all_fills

    async def sleep(self, seconds: float) -> None:
        """Async sleep wrapper.

        Uses asyncio.sleep for better async behavior.

        Args:
            seconds: Number of seconds to sleep

        Example:
            >>> await client.sleep(1.0)
        """
        await asyncio.sleep(seconds)

    # ─────────────────────────────────────────────────────────────────────────
    # EVENT ACCESS (Direct access for callbacks)
    # ─────────────────────────────────────────────────────────────────────────

    @property
    def order_status_event(self):
        """Direct access to order status events for callbacks.

        Example:
            >>> client.order_status_event += my_order_callback
        """
        return self.ib.orderStatusEvent

    @property
    def exec_details_event(self):
        """Direct access to execution events for callbacks.

        Example:
            >>> client.exec_details_event += my_execution_callback
        """
        return self.ib.execDetailsEvent

    # ─────────────────────────────────────────────────────────────────────────
    # AUDIT & DIAGNOSTICS
    # ─────────────────────────────────────────────────────────────────────────

    def get_order_audit_log(self) -> list[OrderAuditEntry]:
        """Return all orders placed this session.

        Returns:
            List of OrderAuditEntry objects

        Example:
            >>> log = client.get_order_audit_log()
            >>> for entry in log:
            ...     print(f"{entry.timestamp}: {entry.action} {entry.symbol}")
        """
        return self._order_audit_log.copy()

    def clear_order_audit_log(self) -> None:
        """Clear the order audit log.

        Useful when starting a new trading session.
        """
        self._order_audit_log.clear()
        logger.debug("Order audit log cleared")

    def __enter__(self) -> "IBKRClient":
        """Context manager entry."""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:  # type: ignore
        """Context manager exit."""
        self.disconnect()
