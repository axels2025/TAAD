"""Unit tests for MarketConditionMonitor.

Tests market condition monitoring for Tier 2 execution timing:
- VIX fetching and default handling
- SPY price fetching
- Spread calculation across contracts
- Condition evaluation logic
- Favorable/unfavorable assessment
"""

import os
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from ib_insync import Contract

from src.services.market_conditions import MarketConditionMonitor, MarketConditions
from src.tools.ibkr_client import Quote


@pytest.fixture
def mock_ibkr_client():
    """Fixture for mocked IBKRClient."""
    client = Mock()
    client.get_quote = AsyncMock()
    return client


@pytest.fixture
def condition_monitor(mock_ibkr_client):
    """Fixture for MarketConditionMonitor with default thresholds."""
    with patch.dict(
        "os.environ",
        {
            "TIER2_VIX_LOW": "18",
            "TIER2_VIX_HIGH": "25",
            "TIER2_MAX_SPREAD": "0.08",
        },
    ):
        return MarketConditionMonitor(mock_ibkr_client)


class TestVIXFetching:
    """Tests for VIX level fetching."""

    @pytest.mark.asyncio
    async def test_get_vix_success(self, condition_monitor, mock_ibkr_client):
        """Test successful VIX fetching."""
        # Mock VIX quote
        vix_quote = Quote(
            bid=14.5,
            ask=14.7,
            last=14.6,
            volume=10000,
            is_valid=True,
            reason=""
        )
        mock_ibkr_client.get_quote.return_value = vix_quote

        vix = await condition_monitor._get_vix()

        assert vix == 14.6
        # Verify VIX contract was requested
        call_args = mock_ibkr_client.get_quote.call_args
        contract = call_args[0][0]
        assert contract.symbol == "VIX"
        assert contract.secType == "IND"
        assert contract.exchange == "CBOE"

    @pytest.mark.asyncio
    async def test_get_vix_invalid_quote_uses_default(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that invalid VIX quote uses default value."""
        # Mock invalid VIX quote
        invalid_quote = Quote(
            bid=0.0,
            ask=0.0,
            last=0.0,
            volume=0,
            is_valid=False,
            reason="No market data"
        )
        mock_ibkr_client.get_quote.return_value = invalid_quote

        vix = await condition_monitor._get_vix()

        assert vix == 20.0  # Default conservative value

    @pytest.mark.asyncio
    async def test_get_vix_exception_uses_default(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that VIX fetch exception uses default value."""
        mock_ibkr_client.get_quote.side_effect = Exception("Connection error")

        vix = await condition_monitor._get_vix()

        assert vix == 20.0  # Default conservative value


class TestSPYPriceFetching:
    """Tests for SPY price fetching."""

    @pytest.mark.asyncio
    async def test_get_spy_price_success(self, condition_monitor, mock_ibkr_client):
        """Test successful SPY price fetching."""
        # Mock SPY quote
        spy_quote = Quote(
            bid=450.10,
            ask=450.12,
            last=450.11,
            volume=50000,
            is_valid=True,
            reason=""
        )
        mock_ibkr_client.get_quote.return_value = spy_quote

        spy_price = await condition_monitor._get_spy_price()

        assert spy_price == 450.11

    @pytest.mark.asyncio
    async def test_get_spy_price_invalid_returns_zero(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that invalid SPY quote returns 0.0."""
        invalid_quote = Quote(
            bid=0.0,
            ask=0.0,
            last=0.0,
            volume=0,
            is_valid=False,
            reason="No data"
        )
        mock_ibkr_client.get_quote.return_value = invalid_quote

        spy_price = await condition_monitor._get_spy_price()

        assert spy_price == 0.0

    @pytest.mark.asyncio
    async def test_get_spy_price_exception_returns_zero(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that SPY fetch exception returns 0.0."""
        mock_ibkr_client.get_quote.side_effect = Exception("Network error")

        spy_price = await condition_monitor._get_spy_price()

        assert spy_price == 0.0


class TestSpreadCalculation:
    """Tests for average spread calculation."""

    @pytest.mark.asyncio
    async def test_calculate_average_spread_success(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test average spread calculation across contracts."""
        # Create mock contracts
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(5)]

        # Mock quotes with different spreads
        quotes = [
            Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""),  # spread: 0.04
            Quote(bid=0.50, ask=0.56, last=0.53, volume=1500, is_valid=True, reason=""),  # spread: 0.06
            Quote(bid=0.30, ask=0.32, last=0.31, volume=800, is_valid=True, reason=""),   # spread: 0.02
            Quote(bid=0.60, ask=0.66, last=0.63, volume=2000, is_valid=True, reason=""),  # spread: 0.06
            Quote(bid=0.40, ask=0.42, last=0.41, volume=1200, is_valid=True, reason=""),  # spread: 0.02
        ]
        mock_ibkr_client.get_quote.side_effect = quotes

        avg_spread = await condition_monitor._calculate_average_spread(contracts)

        # Average: (0.04 + 0.06 + 0.02 + 0.06 + 0.02) / 5 = 0.04
        assert avg_spread == pytest.approx(0.04, abs=1e-9)

    @pytest.mark.asyncio
    async def test_calculate_average_spread_samples_first_five(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that only first 5 contracts are sampled."""
        # Create 10 contracts
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(10)]

        mock_quote = Quote(
            bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""
        )
        mock_ibkr_client.get_quote.return_value = mock_quote

        await condition_monitor._calculate_average_spread(contracts)

        # Should only call get_quote 5 times (not 10)
        assert mock_ibkr_client.get_quote.call_count == 5

    @pytest.mark.asyncio
    async def test_calculate_average_spread_skips_invalid(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that invalid quotes are skipped in average."""
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(3)]

        # Mix of valid and invalid quotes
        quotes = [
            Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""),   # valid
            Quote(bid=0.0, ask=0.0, last=0.0, volume=0, is_valid=False, reason="No data"),  # invalid
            Quote(bid=0.30, ask=0.34, last=0.32, volume=800, is_valid=True, reason=""),    # valid
        ]
        mock_ibkr_client.get_quote.side_effect = quotes

        avg_spread = await condition_monitor._calculate_average_spread(contracts)

        # Average of valid only: (0.04 + 0.04) / 2 = 0.04
        assert avg_spread == pytest.approx(0.04, abs=1e-9)

    @pytest.mark.asyncio
    async def test_calculate_average_spread_no_valid_quotes_returns_zero(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that no valid quotes returns 0.0."""
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(3)]

        # All invalid
        invalid_quote = Quote(
            bid=0.0, ask=0.0, last=0.0, volume=0, is_valid=False, reason="No data"
        )
        mock_ibkr_client.get_quote.return_value = invalid_quote

        avg_spread = await condition_monitor._calculate_average_spread(contracts)

        assert avg_spread == 0.0


class TestConditionEvaluation:
    """Tests for condition evaluation logic."""

    def test_evaluate_conditions_favorable_low_vix(self, condition_monitor):
        """Test favorable conditions with low VIX."""
        # VIX=15 (< 18), spread=0.03 (< 0.08)
        favorable, reason = condition_monitor._evaluate_conditions(vix=15.0, avg_spread=0.03)

        assert favorable is True
        assert "VIX low" in reason
        assert "spreads tight" in reason

    def test_evaluate_conditions_favorable_moderate_vix(self, condition_monitor):
        """Test favorable conditions with moderate VIX."""
        # VIX=20 (18-25), spread=0.04 (< 0.08)
        favorable, reason = condition_monitor._evaluate_conditions(vix=20.0, avg_spread=0.04)

        assert favorable is True
        assert "VIX moderate" in reason
        assert "spreads acceptable" in reason

    def test_evaluate_conditions_unfavorable_high_vix(self, condition_monitor):
        """Test unfavorable conditions when VIX too high."""
        # VIX=30 (> 25), spread=0.04
        favorable, reason = condition_monitor._evaluate_conditions(vix=30.0, avg_spread=0.04)

        assert favorable is False
        assert "VIX too high" in reason
        assert "30.0" in reason

    def test_evaluate_conditions_unfavorable_wide_spreads(self, condition_monitor):
        """Test unfavorable conditions when spreads too wide."""
        # VIX=20, spread=0.12 (> 0.08)
        favorable, reason = condition_monitor._evaluate_conditions(vix=20.0, avg_spread=0.12)

        assert favorable is False
        assert "Spreads too wide" in reason
        assert "0.12" in reason

    def test_evaluate_conditions_vix_exactly_at_low_threshold(self, condition_monitor):
        """Test VIX exactly at low threshold."""
        # VIX=18 (exactly at threshold)
        favorable, reason = condition_monitor._evaluate_conditions(vix=18.0, avg_spread=0.03)

        assert favorable is True
        assert "VIX moderate" in reason  # Not "low" since >= 18

    def test_evaluate_conditions_vix_exactly_at_high_threshold(self, condition_monitor):
        """Test VIX exactly at high threshold."""
        # VIX=25 (exactly at threshold)
        favorable, reason = condition_monitor._evaluate_conditions(vix=25.0, avg_spread=0.03)

        assert favorable is True  # Not unfavorable until > 25


class TestCheckConditions:
    """Tests for complete check_conditions workflow."""

    @pytest.mark.asyncio
    async def test_check_conditions_with_sample_contracts(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test complete condition check with sample contracts."""
        # Mock VIX
        vix_quote = Quote(bid=14.5, ask=14.7, last=14.6, volume=10000, is_valid=True, reason="")

        # Mock SPY
        spy_quote = Quote(bid=450.10, ask=450.12, last=450.11, volume=50000, is_valid=True, reason="")

        # Mock contract quotes (for spread)
        contract_quote = Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason="")

        # Set up side effects in order: VIX, SPY, then 3 contracts
        mock_ibkr_client.get_quote.side_effect = [
            vix_quote,
            spy_quote,
            contract_quote,
            contract_quote,
            contract_quote,
        ]

        contracts = [Mock(spec=Contract) for _ in range(3)]
        conditions = await condition_monitor.check_conditions(contracts)

        assert isinstance(conditions, MarketConditions)
        assert conditions.vix == 14.6
        assert conditions.spy_price == 450.11
        assert conditions.avg_spread == pytest.approx(0.04, abs=1e-9)
        assert conditions.conditions_favorable is True
        assert "VIX low" in conditions.reason

    @pytest.mark.asyncio
    async def test_check_conditions_without_sample_contracts(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test condition check without sample contracts (spread=0)."""
        # Mock VIX
        vix_quote = Quote(bid=20.0, ask=20.2, last=20.1, volume=10000, is_valid=True, reason="")

        # Mock SPY
        spy_quote = Quote(bid=450.10, ask=450.12, last=450.11, volume=50000, is_valid=True, reason="")

        mock_ibkr_client.get_quote.side_effect = [vix_quote, spy_quote]

        conditions = await condition_monitor.check_conditions(sample_contracts=None)

        assert conditions.vix == 20.1
        assert conditions.spy_price == 450.11
        assert conditions.avg_spread == 0.0  # No contracts provided
        assert conditions.conditions_favorable is True  # VIX moderate, spread 0

    @pytest.mark.asyncio
    async def test_check_conditions_timestamp_is_eastern(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that condition timestamp is in Eastern timezone."""
        vix_quote = Quote(bid=15.0, ask=15.2, last=15.1, volume=10000, is_valid=True, reason="")
        spy_quote = Quote(bid=450.0, ask=450.2, last=450.1, volume=50000, is_valid=True, reason="")

        mock_ibkr_client.get_quote.side_effect = [vix_quote, spy_quote]

        conditions = await condition_monitor.check_conditions()

        assert conditions.timestamp.tzinfo == ZoneInfo("America/New_York")


class TestConfigurableThresholds:
    """Tests for configurable threshold loading."""

    @pytest.mark.asyncio
    async def test_custom_thresholds_from_env(self, mock_ibkr_client):
        """Test that custom thresholds are loaded from environment."""
        with patch.dict(
            "os.environ",
            {
                "TIER2_VIX_LOW": "20",
                "TIER2_VIX_HIGH": "30",
                "TIER2_MAX_SPREAD": "0.10",
            },
        ):
            monitor = MarketConditionMonitor(mock_ibkr_client)

            assert monitor.vix_low_threshold == 20.0
            assert monitor.vix_high_threshold == 30.0
            assert monitor.max_spread == 0.10

    @pytest.mark.asyncio
    async def test_default_thresholds_used_when_not_set(self, mock_ibkr_client):
        """Test default thresholds when environment not set."""
        with patch.dict("os.environ", {}, clear=True):
            monitor = MarketConditionMonitor(mock_ibkr_client)

            assert monitor.vix_low_threshold == 18.0  # Default
            assert monitor.vix_high_threshold == 25.0  # Default
            assert monitor.max_spread == 0.08  # Default
