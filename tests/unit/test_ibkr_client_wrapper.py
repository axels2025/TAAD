"""Unit tests for IBKRClient wrapper methods.

Tests the institutional-grade wrapper methods including:
- place_order() with audit logging
- cancel_order() with retry logic
- modify_order() for price adjustments
- get_quote() with event-driven timeout
- qualify_contracts_async() for batch operations
- Audit log functionality
"""

import asyncio
import math
import time
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from src.config.base import IBKRConfig
from src.tools.ibkr_client import IBKRClient, OrderAuditEntry, Quote


@pytest.fixture
def ibkr_config():
    """Fixture for IBKR configuration."""
    return IBKRConfig(
        host="127.0.0.1",
        port=7497,
        client_id=1,
        timeout=10,
    )


@pytest.fixture
def mock_ib():
    """Fixture for mocked ib_insync IB object."""
    ib = Mock()
    ib.isConnected.return_value = True
    ib.placeOrder = Mock()
    ib.cancelOrder = Mock()
    ib.reqMktData = Mock()
    ib.qualifyContractsAsync = AsyncMock()
    ib.orderStatusEvent = Mock()
    ib.execDetailsEvent = Mock()
    return ib


@pytest.fixture
def client(ibkr_config, mock_ib):
    """Fixture for IBKRClient with mocked IB."""
    client = IBKRClient(ibkr_config, suppress_errors=True)
    client.ib = mock_ib
    client._is_connected = True
    return client


@pytest.fixture
def mock_contract():
    """Fixture for a mock contract."""
    contract = Mock()
    contract.symbol = "AAPL"
    contract.strike = 150.0
    return contract


@pytest.fixture
def mock_order():
    """Fixture for a mock order."""
    from ib_insync import LimitOrder

    order = LimitOrder(action="SELL", totalQuantity=5, lmtPrice=0.45)
    order.tif = "DAY"
    return order


class TestPlaceOrder:
    """Tests for place_order() wrapper method."""

    @pytest.mark.asyncio
    async def test_place_order_creates_audit_entry(
        self, client, mock_contract, mock_order
    ):
        """Test that place_order creates audit log entry."""
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        client.ib.placeOrder.return_value = mock_trade

        await client.place_order(mock_contract, mock_order, reason="Test order")

        audit_log = client.get_order_audit_log()
        assert len(audit_log) == 1
        assert audit_log[0].action == "PLACE"
        assert audit_log[0].symbol == "AAPL"
        assert audit_log[0].quantity == 5
        assert audit_log[0].limit_price == 0.45
        assert audit_log[0].order_id == 12345
        assert audit_log[0].status == "SUBMITTED"
        assert audit_log[0].reason == "Test order"

    @pytest.mark.asyncio
    async def test_place_order_validates_quantity(
        self, client, mock_contract, mock_order
    ):
        """Test that place_order validates quantity > 0."""
        mock_order.totalQuantity = 0

        with pytest.raises(ValueError, match="Invalid quantity"):
            await client.place_order(mock_contract, mock_order)

    @pytest.mark.asyncio
    async def test_place_order_validates_limit_price(
        self, client, mock_contract, mock_order
    ):
        """Test that place_order validates limit price > 0."""
        mock_order.lmtPrice = -0.10

        with pytest.raises(ValueError, match="Invalid limit price"):
            await client.place_order(mock_contract, mock_order)

    @pytest.mark.asyncio
    async def test_place_order_logs_failure_in_audit(
        self, client, mock_contract, mock_order
    ):
        """Test that failed orders are logged in audit."""
        client.ib.placeOrder.side_effect = Exception("Connection error")

        with pytest.raises(Exception, match="Connection error"):
            await client.place_order(mock_contract, mock_order)

        audit_log = client.get_order_audit_log()
        assert len(audit_log) == 1
        assert audit_log[0].status == "FAILED"
        assert "Connection error" in audit_log[0].error

    @pytest.mark.asyncio
    async def test_place_order_returns_trade_object(
        self, client, mock_contract, mock_order
    ):
        """Test that place_order returns the Trade object."""
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        client.ib.placeOrder.return_value = mock_trade

        result = await client.place_order(mock_contract, mock_order)

        assert result == mock_trade
        assert result.order.orderId == 12345


class TestCancelOrder:
    """Tests for cancel_order() wrapper method."""

    @pytest.mark.asyncio
    async def test_cancel_order_succeeds_on_first_try(self, client):
        """Test that cancel_order succeeds on first attempt."""
        result = await client.cancel_order(12345, reason="Test cancel")

        assert result is True
        client.ib.cancelOrder.assert_called_once()

    @pytest.mark.asyncio
    async def test_cancel_order_retries_on_failure(self, client):
        """Test that cancel_order retries on failure."""
        client.ib.cancelOrder.side_effect = [
            Exception("Temporary error"),
            Exception("Temporary error"),
            None,  # Success on third try
        ]

        result = await client.cancel_order(12345)

        assert result is True
        assert client.ib.cancelOrder.call_count == 3

    @pytest.mark.asyncio
    async def test_cancel_order_fails_after_max_retries(self, client):
        """Test that cancel_order fails after max retries."""
        client.ib.cancelOrder.side_effect = Exception("Persistent error")

        result = await client.cancel_order(12345)

        assert result is False
        assert client.ib.cancelOrder.call_count == 3  # Max retries


class TestModifyOrder:
    """Tests for modify_order() wrapper method."""

    @pytest.mark.asyncio
    async def test_modify_order_updates_limit_price(self, client):
        """Test that modify_order updates the limit price."""
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.order.lmtPrice = 0.45
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.orderType = "LMT"
        mock_trade.order.totalQuantity = 5

        mock_updated_trade = Mock()
        client.ib.placeOrder.return_value = mock_updated_trade

        result = await client.modify_order(
            mock_trade, new_limit=0.44, reason="Price adjustment"
        )

        assert mock_trade.order.lmtPrice == 0.44
        assert result == mock_updated_trade

    @pytest.mark.asyncio
    async def test_modify_order_creates_audit_entry(self, client):
        """Test that modify_order creates audit log entry."""
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.order.lmtPrice = 0.45
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.orderType = "LMT"
        mock_trade.order.totalQuantity = 5

        client.ib.placeOrder.return_value = Mock()

        await client.modify_order(mock_trade, new_limit=0.44)

        audit_log = client.get_order_audit_log()
        assert len(audit_log) == 1
        assert audit_log[0].action == "MODIFY"
        assert audit_log[0].order_id == 12345
        assert audit_log[0].limit_price == 0.44


class TestGetQuote:
    """Tests for get_quote() with event-driven timeout."""

    @pytest.mark.asyncio
    async def test_get_quote_returns_immediately_on_valid_quote(self, client, mock_contract):
        """Test that get_quote returns immediately when valid quote arrives."""
        # Mock ticker with valid bid/ask
        mock_ticker = Mock()
        mock_ticker.bid = 0.44
        mock_ticker.ask = 0.48
        mock_ticker.last = 0.46
        mock_ticker.volume = 1000

        client.ib.reqMktData.return_value = mock_ticker

        start = time.time()
        quote = await client.get_quote(mock_contract, timeout=0.5)
        elapsed = time.time() - start

        assert quote.is_valid
        assert quote.bid == 0.44
        assert quote.ask == 0.48
        # Should return much faster than timeout (< 100ms vs 500ms)
        assert elapsed < 0.2

    @pytest.mark.asyncio
    async def test_get_quote_waits_for_valid_quote(self, client, mock_contract):
        """Test that get_quote waits until valid quote arrives."""
        # Mock ticker that becomes valid after a delay
        mock_ticker = Mock()

        # Initially invalid
        mock_ticker.bid = None
        mock_ticker.ask = None
        mock_ticker.last = None

        async def delayed_valid_quote():
            await asyncio.sleep(0.15)
            mock_ticker.bid = 0.44
            mock_ticker.ask = 0.48
            mock_ticker.last = 0.46

        client.ib.reqMktData.return_value = mock_ticker

        # Start background task to make quote valid
        asyncio.create_task(delayed_valid_quote())

        quote = await client.get_quote(mock_contract, timeout=0.5)

        assert quote.is_valid
        assert quote.bid == 0.44
        assert quote.ask == 0.48

    @pytest.mark.asyncio
    async def test_get_quote_times_out_on_no_valid_quote(self, client, mock_contract):
        """Test that get_quote times out if no valid quote arrives."""
        # Mock ticker that never becomes valid
        mock_ticker = Mock()
        mock_ticker.bid = None
        mock_ticker.ask = None
        mock_ticker.last = None

        client.ib.reqMktData.return_value = mock_ticker

        quote = await client.get_quote(mock_contract, timeout=0.1)

        assert not quote.is_valid
        assert "Timeout" in quote.reason

    @pytest.mark.asyncio
    async def test_get_quote_rejects_nan_values(self, client, mock_contract):
        """Test that get_quote rejects NaN bid/ask values."""
        # Mock ticker with NaN values and no valid last
        mock_ticker = Mock()
        mock_ticker.bid = math.nan
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        client.ib.reqMktData.return_value = mock_ticker

        quote = await client.get_quote(mock_contract, timeout=0.1)

        assert not quote.is_valid

    @pytest.mark.asyncio
    async def test_get_quote_rejects_zero_values(self, client, mock_contract):
        """Test that get_quote rejects zero bid/ask values."""
        # Mock ticker with zero values and no valid last
        mock_ticker = Mock()
        mock_ticker.bid = 0
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        client.ib.reqMktData.return_value = mock_ticker

        quote = await client.get_quote(mock_contract, timeout=0.1)

        assert not quote.is_valid

    @pytest.mark.asyncio
    async def test_get_quote_uses_env_timeout(self, client, mock_contract):
        """Test that get_quote uses QUOTE_FETCH_TIMEOUT_SECONDS from env."""
        mock_ticker = Mock()
        mock_ticker.bid = None
        mock_ticker.ask = None
        mock_ticker.last = None

        client.ib.reqMktData.return_value = mock_ticker

        with patch.dict("os.environ", {"QUOTE_FETCH_TIMEOUT_SECONDS": "0.2"}):
            start = time.time()
            quote = await client.get_quote(mock_contract)  # No timeout specified
            elapsed = time.time() - start

            assert not quote.is_valid
            # Should use env timeout of 0.2s
            assert 0.15 < elapsed < 0.35


class TestQualifyContractsAsync:
    """Tests for qualify_contracts_async() batch method."""

    @pytest.mark.asyncio
    async def test_qualify_contracts_async_returns_qualified_list(self, client):
        """Test that qualify_contracts_async returns qualified contracts."""
        mock_contract1 = Mock()
        mock_contract2 = Mock()

        mock_qualified1 = Mock()
        mock_qualified2 = Mock()

        client.ib.qualifyContractsAsync.return_value = [
            mock_qualified1,
            mock_qualified2,
        ]

        result = await client.qualify_contracts_async(mock_contract1, mock_contract2)

        assert len(result) == 2
        assert result[0] == mock_qualified1
        assert result[1] == mock_qualified2


class TestEventAccess:
    """Tests for event property access."""

    def test_order_status_event_property(self, client):
        """Test that order_status_event property returns IB event."""
        event = client.order_status_event

        assert event == client.ib.orderStatusEvent

    def test_exec_details_event_property(self, client):
        """Test that exec_details_event property returns IB event."""
        event = client.exec_details_event

        assert event == client.ib.execDetailsEvent


class TestAuditLog:
    """Tests for audit log functionality."""

    @pytest.mark.asyncio
    async def test_audit_log_tracks_multiple_orders(
        self, client, mock_contract, mock_order
    ):
        """Test that audit log tracks multiple order operations."""
        mock_trade1 = Mock()
        mock_trade1.order.orderId = 12345

        mock_trade2 = Mock()
        mock_trade2.order.orderId = 12346

        client.ib.placeOrder.side_effect = [mock_trade1, mock_trade2]

        await client.place_order(mock_contract, mock_order, "First order")
        await client.place_order(mock_contract, mock_order, "Second order")

        audit_log = client.get_order_audit_log()
        assert len(audit_log) == 2
        assert audit_log[0].order_id == 12345
        assert audit_log[1].order_id == 12346

    def test_clear_audit_log(self, client):
        """Test that clear_audit_log removes all entries."""
        # Add a manual entry
        client._order_audit_log.append(
            OrderAuditEntry(
                timestamp=datetime.now(),
                action="TEST",
                symbol="TEST",
                order_type="LMT",
                quantity=1,
            )
        )

        assert len(client.get_order_audit_log()) == 1

        client.clear_order_audit_log()

        assert len(client.get_order_audit_log()) == 0

    @pytest.mark.asyncio
    async def test_audit_log_tracks_action_types(self, client):
        """Test that audit log tracks different action types."""
        # Place order
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.order.lmtPrice = 0.45
        mock_trade.contract.symbol = "AAPL"
        mock_trade.order.orderType = "LMT"
        mock_trade.order.totalQuantity = 5

        client.ib.placeOrder.return_value = mock_trade

        mock_contract = Mock()
        mock_contract.symbol = "AAPL"

        from ib_insync import LimitOrder

        order = LimitOrder(action="SELL", totalQuantity=5, lmtPrice=0.45)

        await client.place_order(mock_contract, order)
        await client.modify_order(mock_trade, 0.44)

        audit_log = client.get_order_audit_log()
        assert len(audit_log) == 2
        assert audit_log[0].action == "PLACE"
        assert audit_log[1].action == "MODIFY"


class TestIsValidQuote:
    """Tests for _is_valid_quote() helper method."""

    def test_is_valid_quote_accepts_valid_bid_ask(self, client):
        """Test that _is_valid_quote accepts valid bid/ask."""
        mock_ticker = Mock()
        mock_ticker.bid = 0.44
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is True

    def test_is_valid_quote_accepts_valid_last_only(self, client):
        """Test that _is_valid_quote accepts valid last (e.g. indices like VIX)."""
        mock_ticker = Mock()
        mock_ticker.bid = None
        mock_ticker.ask = None
        mock_ticker.last = 22.5

        assert client._is_valid_quote(mock_ticker) is True

    def test_is_valid_quote_rejects_none_bid_no_last(self, client):
        """Test that _is_valid_quote rejects None bid when no valid last."""
        mock_ticker = Mock()
        mock_ticker.bid = None
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is False

    def test_is_valid_quote_rejects_none_ask_no_last(self, client):
        """Test that _is_valid_quote rejects None ask when no valid last."""
        mock_ticker = Mock()
        mock_ticker.bid = 0.44
        mock_ticker.ask = None
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is False

    def test_is_valid_quote_rejects_zero_bid_no_last(self, client):
        """Test that _is_valid_quote rejects zero bid when no valid last."""
        mock_ticker = Mock()
        mock_ticker.bid = 0
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is False

    def test_is_valid_quote_rejects_nan_bid_no_last(self, client):
        """Test that _is_valid_quote rejects NaN bid when no valid last."""
        mock_ticker = Mock()
        mock_ticker.bid = math.nan
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is False

    def test_is_valid_quote_rejects_negative_bid_no_last(self, client):
        """Test that _is_valid_quote rejects negative bid when no valid last."""
        mock_ticker = Mock()
        mock_ticker.bid = -0.10
        mock_ticker.ask = 0.48
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is False

    def test_is_valid_quote_rejects_all_invalid(self, client):
        """Test that _is_valid_quote rejects when all fields are invalid."""
        mock_ticker = Mock()
        mock_ticker.bid = None
        mock_ticker.ask = None
        mock_ticker.last = None

        assert client._is_valid_quote(mock_ticker) is False


class TestGetQuotesBatch:
    """Tests for get_quotes_batch() batch quote fetching."""

    @pytest.mark.asyncio
    async def test_get_quotes_batch_returns_all_quotes(self, client, mock_ib):
        """Test that get_quotes_batch returns quotes for all contracts."""
        # Mock contracts
        contracts = [Mock(), Mock(), Mock()]
        contracts[0].symbol = "AAPL"
        contracts[1].symbol = "MSFT"
        contracts[2].symbol = "GOOGL"

        # Mock tickers with valid quotes
        mock_tickers = []
        for i in range(3):
            ticker = Mock()
            ticker.bid = 0.44 + i * 0.05
            ticker.ask = 0.48 + i * 0.05
            ticker.last = 0.46 + i * 0.05
            ticker.volume = 1000
            mock_tickers.append(ticker)

        mock_ib.reqMktData.side_effect = mock_tickers

        # Execute
        quotes = await client.get_quotes_batch(contracts)

        # Verify
        assert len(quotes) == 3
        assert all(q.is_valid for q in quotes)
        assert quotes[0].bid == 0.44
        assert quotes[1].bid == 0.49
        assert quotes[2].bid == 0.54

    @pytest.mark.asyncio
    async def test_get_quotes_batch_handles_mixed_validity(self, client, mock_ib):
        """Test get_quotes_batch when some quotes are invalid."""
        contracts = [Mock(), Mock(), Mock()]

        # First ticker: valid quote (returns immediately)
        ticker1 = Mock()
        ticker1.bid = 0.44
        ticker1.ask = 0.48
        ticker1.last = 0.46
        ticker1.volume = 1000

        # Second ticker: invalid quote (None bid)
        ticker2 = Mock()
        ticker2.bid = None
        ticker2.ask = 0.50
        ticker2.last = None
        ticker2.volume = 0

        # Third ticker: valid quote
        ticker3 = Mock()
        ticker3.bid = 0.52
        ticker3.ask = 0.56
        ticker3.last = 0.54
        ticker3.volume = 500

        mock_ib.reqMktData.side_effect = [ticker1, ticker2, ticker3]

        # Execute with short timeout
        quotes = await client.get_quotes_batch(contracts, timeout=0.2)

        # Verify
        assert len(quotes) == 3
        assert quotes[0].is_valid is True
        assert quotes[1].is_valid is False  # Invalid quote
        assert quotes[2].is_valid is True

        # Verify invalid quote has reason
        assert "Timeout" in quotes[1].reason

    @pytest.mark.asyncio
    async def test_get_quotes_batch_independent_timeouts(self, client, mock_ib):
        """Test that quotes have independent timeouts (fast don't wait for slow)."""
        contracts = [Mock(), Mock()]

        # First ticker: valid immediately
        ticker1 = Mock()
        ticker1.bid = 0.44
        ticker1.ask = 0.48
        ticker1.last = 0.46
        ticker1.volume = 1000

        # Second ticker: invalid (will timeout)
        ticker2 = Mock()
        ticker2.bid = None
        ticker2.ask = None
        ticker2.last = None
        ticker2.volume = 0

        mock_ib.reqMktData.side_effect = [ticker1, ticker2]

        # Execute with short timeout
        start = time.time()
        quotes = await client.get_quotes_batch(contracts, timeout=0.2)
        elapsed = time.time() - start

        # Verify timing: should complete around 0.2s (not wait for both to timeout)
        assert elapsed < 0.5  # Should not wait multiple timeouts

        # First quote should be valid, second should timeout
        assert quotes[0].is_valid is True
        assert quotes[1].is_valid is False

    @pytest.mark.asyncio
    async def test_get_quotes_batch_empty_list(self, client):
        """Test get_quotes_batch with empty contract list."""
        quotes = await client.get_quotes_batch([])

        assert quotes == []

    @pytest.mark.asyncio
    async def test_get_quotes_batch_single_contract(self, client, mock_ib):
        """Test get_quotes_batch with single contract."""
        contract = Mock()
        contract.symbol = "AAPL"

        ticker = Mock()
        ticker.bid = 0.44
        ticker.ask = 0.48
        ticker.last = 0.46
        ticker.volume = 1000

        mock_ib.reqMktData.return_value = ticker

        quotes = await client.get_quotes_batch([contract])

        assert len(quotes) == 1
        assert quotes[0].is_valid is True
        assert quotes[0].bid == 0.44

    @pytest.mark.asyncio
    async def test_get_quotes_batch_preserves_order(self, client, mock_ib):
        """Test that quotes are returned in same order as contracts."""
        contracts = [Mock(), Mock(), Mock()]
        contracts[0].symbol = "AAPL"
        contracts[1].symbol = "MSFT"
        contracts[2].symbol = "GOOGL"

        # Mock tickers with different bids
        tickers = []
        for i in range(3):
            ticker = Mock()
            ticker.bid = 0.40 + i * 0.10
            ticker.ask = 0.45 + i * 0.10
            ticker.last = 0.42 + i * 0.10
            ticker.volume = 1000
            tickers.append(ticker)

        mock_ib.reqMktData.side_effect = tickers

        quotes = await client.get_quotes_batch(contracts)

        # Verify order matches
        assert len(quotes) == 3
        assert quotes[0].bid == pytest.approx(0.40, abs=1e-9)  # AAPL
        assert quotes[1].bid == pytest.approx(0.50, abs=1e-9)  # MSFT
        assert quotes[2].bid == pytest.approx(0.60, abs=1e-9)  # GOOGL

    @pytest.mark.asyncio
    async def test_get_quotes_batch_uses_custom_timeout(self, client, mock_ib):
        """Test get_quotes_batch respects custom timeout parameter."""
        contracts = [Mock()]

        # Invalid ticker (will timeout)
        ticker = Mock()
        ticker.bid = None
        ticker.ask = None
        ticker.last = None
        ticker.volume = 0

        mock_ib.reqMktData.return_value = ticker

        # Use custom timeout
        start = time.time()
        quotes = await client.get_quotes_batch(contracts, timeout=0.1)
        elapsed = time.time() - start

        # Verify timeout was respected
        assert elapsed < 0.3  # Should timeout around 0.1s
        assert quotes[0].is_valid is False
        assert "0.1" in quotes[0].reason  # Timeout message includes timeout value
