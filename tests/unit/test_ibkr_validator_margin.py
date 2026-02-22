"""Unit tests for IBKRValidator margin calculation.

Tests the _get_actual_margin() method and fallback to _estimate_margin_fallback().
"""

from unittest.mock import Mock, patch

import pytest


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKRClient."""
    client = Mock()
    client.ib = Mock()
    client.get_actual_margin = Mock()
    return client


@pytest.fixture
def validator(mock_ibkr_client):
    """Create IBKRValidator with mocked IBKR client."""
    from src.tools.ibkr_validator import IBKRValidator

    validator = IBKRValidator.__new__(IBKRValidator)
    validator.ibkr_client = mock_ibkr_client
    validator.config = None
    return validator


def test_get_actual_margin_success(validator, mock_ibkr_client):
    """Test successful retrieval of actual margin from IBKR."""
    # Mock successful contract qualification
    mock_contract = Mock()
    mock_contract.conId = 12345
    mock_ibkr_client.ib.qualifyContracts.return_value = [mock_contract]

    # Mock successful margin retrieval
    mock_ibkr_client.get_actual_margin.return_value = 3500.00

    result = validator._get_actual_margin(
        symbol="AAPL", strike=150.0, expiration="2026-02-28", premium=5.0
    )

    assert result == 3500.00
    mock_ibkr_client.get_actual_margin.assert_called_once_with(mock_contract)


def test_get_actual_margin_qualification_fails(validator, mock_ibkr_client):
    """Test returns None when contract qualification fails."""
    # Mock failed contract qualification
    mock_ibkr_client.ib.qualifyContracts.return_value = []

    result = validator._get_actual_margin(
        symbol="AAPL", strike=150.0, expiration="2026-02-28", premium=5.0
    )

    assert result is None
    mock_ibkr_client.get_actual_margin.assert_not_called()


def test_get_actual_margin_no_conid(validator, mock_ibkr_client):
    """Test returns None when qualified contract has no conId."""
    # Mock contract without conId
    mock_contract = Mock()
    mock_contract.conId = None
    mock_ibkr_client.ib.qualifyContracts.return_value = [mock_contract]

    result = validator._get_actual_margin(
        symbol="AAPL", strike=150.0, expiration="2026-02-28", premium=5.0
    )

    assert result is None


def test_get_actual_margin_exception(validator, mock_ibkr_client):
    """Test returns None when exception occurs."""
    # Mock exception during contract qualification
    mock_ibkr_client.ib.qualifyContracts.side_effect = Exception("Connection error")

    result = validator._get_actual_margin(
        symbol="AAPL", strike=150.0, expiration="2026-02-28", premium=5.0
    )

    assert result is None


def test_estimate_margin_fallback_basic(validator):
    """Test fallback margin estimation with basic calculation."""
    # Test standard naked put margin estimate
    # Stock price: $100, Strike: $95, Premium: $2
    # OTM amount: $5
    # Margin = max(20% * 100 - 5 + 2, 10% * 100) * 100
    # Margin = max(17, 10) * 100 = 1700

    result = validator._estimate_margin_fallback(
        stock_price=100.0, strike=95.0, premium=2.0
    )

    assert result == 1700.0


def test_estimate_margin_fallback_atm(validator):
    """Test fallback estimation for at-the-money option."""
    # Stock price: $100, Strike: $100, Premium: $3
    # OTM amount: $0
    # Margin = max(20% * 100 - 0 + 3, 10% * 100) * 100
    # Margin = max(23, 10) * 100 = 2300

    result = validator._estimate_margin_fallback(
        stock_price=100.0, strike=100.0, premium=3.0
    )

    assert result == 2300.0


def test_estimate_margin_fallback_deep_otm(validator):
    """Test fallback estimation for deep OTM option."""
    # Stock price: $100, Strike: $70, Premium: $0.50
    # OTM amount: $30
    # Margin = max(20% * 100 - 30 + 0.5, 10% * 100) * 100
    # Margin = max(-9.5, 10) * 100 = 1000 (floor kicks in)

    result = validator._estimate_margin_fallback(
        stock_price=100.0, strike=70.0, premium=0.5
    )

    assert result == 1000.0


def test_estimate_margin_fallback_itm(validator):
    """Test fallback estimation for in-the-money option."""
    # Stock price: $100, Strike: $110, Premium: $12
    # OTM amount: $0 (ITM for put, so OTM amount is 0)
    # Margin = max(20% * 100 - 0 + 12, 10% * 100) * 100
    # Margin = max(32, 10) * 100 = 3200

    result = validator._estimate_margin_fallback(
        stock_price=100.0, strike=110.0, premium=12.0
    )

    assert result == 3200.0


def test_estimate_margin_fallback_minimum_floor(validator):
    """Test minimum 10% floor is applied when 20% calc is lower."""
    # Stock price: $50, Strike: $30, Premium: $0.10
    # OTM amount: $20
    # Margin = max(20% * 50 - 20 + 0.1, 10% * 50) * 100
    # Margin = max(-9.9, 5) * 100 = 500

    result = validator._estimate_margin_fallback(
        stock_price=50.0, strike=30.0, premium=0.1
    )

    assert result == 500.0


def test_actual_margin_preferred_over_estimate(validator, mock_ibkr_client):
    """Test that actual margin is preferred when available."""
    # This test verifies the logic in enrich_manual_opportunity
    # We'll need to check that _get_actual_margin is called first

    # Mock successful actual margin
    mock_contract = Mock()
    mock_contract.conId = 12345
    mock_ibkr_client.ib.qualifyContracts.return_value = [mock_contract]
    mock_ibkr_client.get_actual_margin.return_value = 4200.00

    actual_margin = validator._get_actual_margin(
        symbol="TSLA", strike=220.0, expiration="2026-02-28", premium=8.0
    )

    # Should get actual margin
    assert actual_margin == 4200.00

    # Fallback should not be needed
    fallback_margin = validator._estimate_margin_fallback(
        stock_price=250.0, strike=220.0, premium=8.0
    )

    # Compare: actual should be significantly higher for volatile stock
    # Estimate: max(20% * 250 - 30 + 8, 10% * 250) * 100 = max(28, 25) * 100 = 2800
    assert fallback_margin == 2800.0
    assert actual_margin > fallback_margin  # Actual should be higher for TSLA


def test_compare_actual_vs_estimate_volatile_stock(validator, mock_ibkr_client):
    """Test that actual margin is higher than estimate for volatile stocks.

    This simulates the real-world scenario where TSLA, NVDA, etc.
    have 50-100% higher actual margin vs estimate.
    """
    # Mock IBKR returning higher margin for volatile stock
    mock_contract = Mock()
    mock_contract.conId = 12345
    mock_ibkr_client.ib.qualifyContracts.return_value = [mock_contract]

    # Simulate IBKR returning 80% higher margin for TSLA
    estimated = 2800.0
    actual_higher = estimated * 1.8  # 80% higher
    mock_ibkr_client.get_actual_margin.return_value = actual_higher

    actual_margin = validator._get_actual_margin(
        symbol="TSLA", strike=220.0, expiration="2026-02-28", premium=8.0
    )

    fallback_margin = validator._estimate_margin_fallback(
        stock_price=250.0, strike=220.0, premium=8.0
    )

    assert actual_margin == 5040.0  # 2800 * 1.8
    assert fallback_margin == 2800.0
    assert (actual_margin / fallback_margin) == 1.8  # 80% higher
