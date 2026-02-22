"""Unit tests for OptionsFinder."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

from src.tools.options_finder import OptionsFinder


@pytest.fixture
def mock_ibkr_client():
    """Create a mock IBKR client."""
    client = MagicMock()
    client.is_connected.return_value = True
    return client


@pytest.fixture
def options_finder(mock_ibkr_client):
    """Create OptionsFinder instance."""
    return OptionsFinder(mock_ibkr_client)


class TestOptionsFinderInitialization:
    """Test options finder initialization."""

    def test_initialization(self, mock_ibkr_client):
        """Test options finder initializes correctly."""
        finder = OptionsFinder(mock_ibkr_client)

        assert finder.ibkr_client == mock_ibkr_client


class TestFilterExpirationsByDte:
    """Test DTE filtering."""

    def test_filters_expirations_correctly(self, options_finder):
        """Test expirations are filtered by DTE range."""
        # Create mock chain with various expirations
        today = datetime.now().date()
        exp_5_days = (today + timedelta(days=5)).strftime("%Y%m%d")
        exp_10_days = (today + timedelta(days=10)).strftime("%Y%m%d")
        exp_15_days = (today + timedelta(days=15)).strftime("%Y%m%d")
        exp_30_days = (today + timedelta(days=30)).strftime("%Y%m%d")

        # New API uses a dict with "expirations" key
        chain = {
            "exchange": "SMART",
            "trading_class": "TEST",
            "multiplier": "100",
            "expirations": {exp_5_days, exp_10_days, exp_15_days, exp_30_days},
            "strikes": {100.0, 110.0, 120.0},
        }

        # Filter for 7-14 DTE using new method name
        filtered = options_finder._filter_expirations_by_dte_from_chain(
            chain, dte_range=(7, 14)
        )

        # Should include 10-day but not 5-day, 15-day, or 30-day
        assert exp_10_days in filtered
        assert exp_5_days not in filtered
        assert exp_15_days not in filtered
        assert exp_30_days not in filtered


class TestEstimateMargin:
    """Test margin estimation."""

    def test_calculates_margin_for_put(self, options_finder):
        """Test margin calculation for naked put."""
        stock_price = 180.0
        strike = 150.0
        premium = 0.40

        margin = options_finder._estimate_margin(stock_price, strike, premium)

        # Margin should be positive
        assert margin > 0

        # Should be reasonable (not too low, not too high)
        assert margin > 1000  # More than $10/share
        assert margin < 5000  # Less than $50/share

    def test_minimum_margin_enforced(self, options_finder):
        """Test minimum margin is enforced."""
        stock_price = 50.0
        strike = 45.0  # Deep OTM
        premium = 0.10  # Low premium

        margin = options_finder._estimate_margin(stock_price, strike, premium)

        # Should enforce minimum of 10% of stock value
        min_margin = 0.10 * stock_price * 100
        assert margin >= min_margin


class TestRankOptions:
    """Test option ranking."""

    def test_ranks_by_quality(self, options_finder):
        """Test options are ranked by quality metrics."""
        options = [
            {
                "premium": 0.30,
                "margin_required": 1500,
                "dte": 10,
            },
            {
                "premium": 0.45,  # Higher premium
                "margin_required": 1500,
                "dte": 10,
            },
            {
                "premium": 0.40,
                "margin_required": 1000,  # Better margin efficiency
                "dte": 10,
            },
        ]

        ranked = options_finder._rank_options(options)

        # Best option should be first
        assert (
            ranked[0]["premium"] >= ranked[1]["premium"]
            or ranked[0]["margin_required"] <= ranked[1]["margin_required"]
        )
