"""Integration tests for IBKR connection.

NOTE: These tests require Interactive Brokers TWS or Gateway to be running
and configured for paper trading. Tests will be skipped if connection fails.
"""

import pytest

from src.config.base import IBKRConfig
from src.tools.ibkr_client import IBKRClient, IBKRConnectionError


@pytest.fixture(scope="module")
def ibkr_config():
    """IBKR configuration for testing."""
    return IBKRConfig(host="127.0.0.1", port=7497, client_id=999)


@pytest.fixture(scope="module")
def ibkr_client(ibkr_config):
    """IBKR client for testing."""
    client = IBKRClient(ibkr_config, max_retries=1)

    # Try to connect, skip tests if connection fails
    try:
        client.connect(retry=False)
        yield client
        client.disconnect()
    except IBKRConnectionError:
        pytest.skip(
            "IBKR connection not available. Ensure TWS/Gateway is running on port 7497."
        )


@pytest.mark.integration
class TestIBKRConnection:
    """Tests for IBKR connection."""

    def test_connect(self, ibkr_config) -> None:
        """Test basic connection to IBKR."""
        client = IBKRClient(ibkr_config, max_retries=1)

        try:
            result = client.connect(retry=False)
            assert result is True
            assert client.is_connected() is True
            client.disconnect()
            assert client.is_connected() is False
        except IBKRConnectionError:
            pytest.skip("IBKR not available")

    def test_connection_retry(self, ibkr_config) -> None:
        """Test connection retry logic with invalid port."""
        # Use invalid port to test retry
        bad_config = IBKRConfig(host="127.0.0.1", port=9999, client_id=999)
        client = IBKRClient(bad_config, max_retries=2)

        with pytest.raises(IBKRConnectionError):
            client.connect()

    def test_context_manager(self, ibkr_config) -> None:
        """Test using client as context manager."""
        try:
            with IBKRClient(ibkr_config, max_retries=1) as client:
                assert client.is_connected() is True
        except IBKRConnectionError:
            pytest.skip("IBKR not available")


@pytest.mark.integration
class TestIBKROperations:
    """Tests for IBKR operations (requires active connection)."""

    def test_get_stock_contract(self, ibkr_client) -> None:
        """Test getting a stock contract."""
        contract = ibkr_client.get_stock_contract("AAPL")
        assert contract.symbol == "AAPL"
        assert contract.secType == "STK"
        assert contract.currency == "USD"

    def test_qualify_contract(self, ibkr_client) -> None:
        """Test qualifying a contract."""
        stock = ibkr_client.get_stock_contract("AAPL")
        qualified = ibkr_client.qualify_contract(stock)

        assert qualified is not None
        assert qualified.symbol == "AAPL"
        assert qualified.conId > 0  # Contract should have valid conId

    def test_get_market_data(self, ibkr_client) -> None:
        """Test getting market data for a stock."""
        stock = ibkr_client.get_stock_contract("AAPL")
        data = ibkr_client.get_market_data(stock, snapshot=True)

        if data:  # Market might be closed
            assert "symbol" in data
            assert data["symbol"] == "AAPL"
            assert "last" in data or "close" in data

    def test_get_account_summary(self, ibkr_client) -> None:
        """Test getting account summary."""
        summary = ibkr_client.get_account_summary()

        assert isinstance(summary, dict)
        # Paper trading accounts should have some standard fields
        # Note: fields may vary, so we just check it's not empty
        assert len(summary) > 0

    def test_ensure_connected(self, ibkr_client) -> None:
        """Test ensure_connected method."""
        # Should work when already connected
        ibkr_client.ensure_connected()
        assert ibkr_client.is_connected() is True


@pytest.mark.integration
class TestIBKRErrorHandling:
    """Tests for IBKR error handling."""

    def test_invalid_symbol(self, ibkr_client) -> None:
        """Test handling of invalid stock symbol."""
        contract = ibkr_client.get_stock_contract("INVALID_SYMBOL_XYZ")
        qualified = ibkr_client.qualify_contract(contract)

        # Should return None for invalid symbol
        assert qualified is None

    def test_market_data_unavailable(self, ibkr_client) -> None:
        """Test handling when market data is unavailable."""
        # Use an obscure/invalid symbol
        stock = ibkr_client.get_stock_contract("ZZZZZ")
        data = ibkr_client.get_market_data(stock, snapshot=True)

        # Should return None or empty dict for unavailable data
        assert data is None or len(data) == 0
