"""Unit tests for IBKRClient margin calculation methods.

Tests the get_actual_margin() method including retry logic for bug #380.
"""

from unittest.mock import Mock

import pytest

from src.tools.ibkr_client import IBKRClient


@pytest.fixture
def mock_contract():
    """Create mock option contract."""
    contract = Mock()
    contract.symbol = "AAPL"
    contract.strike = 150.0
    contract.right = "P"
    contract.lastTradeDateOrContractMonth = "20260228"
    contract.conId = 12345
    return contract


@pytest.fixture
def client():
    """Create IBKRClient with mocked ib connection."""
    # Create instance without calling __init__
    client = object.__new__(IBKRClient)
    client._is_connected = True
    client.ib = Mock()
    return client


def test_get_actual_margin_success(client, mock_contract):
    """Test whatIfOrder returns valid margin on first attempt."""
    # Mock successful whatIfOrder response
    mock_result = Mock()
    mock_result.initMarginChange = "3500.00"
    client.ib.whatIfOrder.return_value = mock_result
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract)

    assert result == 3500.00
    assert client.ib.whatIfOrder.call_count == 1


def test_get_actual_margin_infinity_retry(client, mock_contract):
    """Test retry when whatIfOrder returns infinity (bug #380)."""
    # Mock infinity on first call, valid on second
    mock_infinity = Mock()
    mock_infinity.initMarginChange = "1.7976931348623157e+308"  # Infinity

    mock_valid = Mock()
    mock_valid.initMarginChange = "3500.00"

    client.ib.whatIfOrder.side_effect = [mock_infinity, mock_valid]
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract)

    assert result == 3500.00
    assert client.ib.whatIfOrder.call_count == 2
    assert client.ib.sleep.call_count >= 1  # Called between retries


def test_get_actual_margin_failure_returns_none(client, mock_contract):
    """Test fallback to None when whatIfOrder fails all retries."""
    # Mock all attempts returning None
    client.ib.whatIfOrder.return_value = None
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract, max_retries=3)

    assert result is None
    assert client.ib.whatIfOrder.call_count == 3


def test_get_actual_margin_exception_retry(client, mock_contract):
    """Test retry when whatIfOrder raises exception."""
    # Mock exception on first call, success on second
    mock_valid = Mock()
    mock_valid.initMarginChange = "3500.00"

    client.ib.whatIfOrder.side_effect = [
        Exception("Connection error"),
        mock_valid,
    ]
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract, max_retries=3)

    assert result == 3500.00
    assert client.ib.whatIfOrder.call_count == 2


def test_get_actual_margin_not_connected(mock_contract):
    """Test returns None when not connected to IBKR."""
    # Create disconnected client
    client = object.__new__(IBKRClient)
    client._is_connected = False
    client.ib = Mock()

    result = client.get_actual_margin(mock_contract)

    assert result is None
    assert client.ib.whatIfOrder.call_count == 0  # Should not attempt call


def test_get_actual_margin_invalid_value(client, mock_contract):
    """Test retry when initMarginChange is invalid string."""
    # Mock invalid string on first call, valid on second
    mock_invalid = Mock()
    mock_invalid.initMarginChange = "INVALID"

    mock_valid = Mock()
    mock_valid.initMarginChange = "3500.00"

    client.ib.whatIfOrder.side_effect = [mock_invalid, mock_valid]
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract, max_retries=3)

    assert result == 3500.00
    assert client.ib.whatIfOrder.call_count == 2


def test_get_actual_margin_empty_string(client, mock_contract):
    """Test retry when initMarginChange is empty string."""
    # Mock empty string on first call, valid on second
    mock_empty = Mock()
    mock_empty.initMarginChange = ""

    mock_valid = Mock()
    mock_valid.initMarginChange = "3500.00"

    client.ib.whatIfOrder.side_effect = [mock_empty, mock_valid]
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract, max_retries=3)

    assert result == 3500.00
    assert client.ib.whatIfOrder.call_count == 2


def test_get_actual_margin_negative_value(client, mock_contract):
    """Test absolute value is returned for negative margin."""
    # Mock negative margin (IBKR sometimes returns negative)
    mock_result = Mock()
    mock_result.initMarginChange = "-3500.00"
    client.ib.whatIfOrder.return_value = mock_result
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract)

    assert result == 3500.00  # Should be absolute value


def test_get_actual_margin_zero_rejected(client, mock_contract):
    """Test that whatIfOrder returning 0 margin is treated as invalid and retried."""
    # Mock zero on first call, valid on second
    mock_zero = Mock()
    mock_zero.initMarginChange = "0.0"

    mock_valid = Mock()
    mock_valid.initMarginChange = "3500.00"

    client.ib.whatIfOrder.side_effect = [mock_zero, mock_valid]
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract, max_retries=3)

    assert result == 3500.00
    assert client.ib.whatIfOrder.call_count == 2


def test_get_actual_margin_zero_all_attempts_returns_none(client, mock_contract):
    """Test that persistent 0 margins exhaust retries and return None."""
    mock_zero = Mock()
    mock_zero.initMarginChange = "0.0"

    client.ib.whatIfOrder.return_value = mock_zero
    client.ib.sleep = Mock()

    result = client.get_actual_margin(mock_contract, max_retries=3)

    assert result is None
    assert client.ib.whatIfOrder.call_count == 3


def test_get_actual_margin_progressive_backoff(client, mock_contract):
    """Test that retry sleep uses progressive backoff."""
    # All attempts return None to force all retries
    client.ib.whatIfOrder.return_value = None
    client.ib.sleep = Mock()

    client.get_actual_margin(mock_contract, max_retries=3)

    # Sleep calls: 0.1*(0+1)=0.1, 0.1*(1+1)=0.2, 0.1*(2+1)=0.3
    sleep_calls = [call.args[0] for call in client.ib.sleep.call_args_list]
    assert sleep_calls == pytest.approx([0.1, 0.2, 0.3])


def test_get_margin_requirement_uses_get_actual_margin(mock_contract):
    """Test get_margin_requirement calls get_actual_margin internally."""
    # Create client
    client = object.__new__(IBKRClient)
    client._is_connected = True
    client.ib = Mock()

    # Mock contract creation and qualification
    client.get_option_contract = Mock(return_value=mock_contract)
    client.qualify_contract = Mock(return_value=mock_contract)

    # Mock get_actual_margin
    client.get_actual_margin = Mock(return_value=3500.00)

    result = client.get_margin_requirement(
        symbol="AAPL",
        strike=150.0,
        expiration="20260228",
        option_type="PUT",
        contracts=5,
    )

    assert result == 3500.00
    client.get_actual_margin.assert_called_once_with(mock_contract, quantity=5)
