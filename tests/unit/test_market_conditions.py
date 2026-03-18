"""Unit tests for MarketConditionMonitor.

Tests market condition monitoring for Tier 2 execution timing:
- Volatility complex fetching (VIX, VVIX, VIX3M)
- SPY price fetching
- Spread calculation across contracts
- Condition evaluation logic with VVIX and term structure
- Favorable/unfavorable assessment
"""

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest
from ib_async import Contract

from src.services.market_conditions import MarketConditionMonitor, MarketConditions
from src.tools.ibkr_client import Quote


@pytest.fixture
def mock_ibkr_client():
    """Fixture for mocked IBKRClient."""
    client = Mock()
    client.get_quote = AsyncMock()
    client.qualify_contracts_async = AsyncMock()
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


def _make_quote(last: float, valid: bool = True) -> Quote:
    """Helper to create a Quote for testing."""
    return Quote(
        bid=last - 0.1 if last > 0 else 0.0,
        ask=last + 0.1 if last > 0 else 0.0,
        last=last,
        volume=10000 if valid else 0,
        is_valid=valid,
        reason="" if valid else "No data",
    )


def _setup_vol_complex(mock_client, vix=18.5, vvix=90.0, vix3m=20.0):
    """Helper to set up volatility complex mocks (VIX + VVIX + VIX3M)."""
    vix_contract = MagicMock(conId=1, symbol="VIX")
    vvix_contract = MagicMock(conId=2, symbol="VVIX")
    vix3m_contract = MagicMock(conId=3, symbol="VIX3M")
    mock_client.qualify_contracts_async.return_value = [
        vix_contract, vvix_contract, vix3m_contract,
    ]

    async def mock_get_quote(contract, timeout=None):
        sym = getattr(contract, "symbol", "")
        if sym == "VIX":
            return _make_quote(vix)
        elif sym == "VVIX":
            return _make_quote(vvix)
        elif sym == "VIX3M":
            return _make_quote(vix3m)
        elif sym == "SPY":
            return _make_quote(450.0)
        return _make_quote(0.0, valid=False)

    mock_client.get_quote = AsyncMock(side_effect=mock_get_quote)


class TestVolatilityComplex:
    """Tests for volatility complex fetching (VIX + VVIX + VIX3M)."""

    @pytest.mark.asyncio
    async def test_get_volatility_complex_success(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test successful fetch of all three volatility indices."""
        _setup_vol_complex(mock_ibkr_client, vix=18.5, vvix=95.0, vix3m=20.0)

        vix, vvix, vix3m = await condition_monitor._get_volatility_complex()

        assert vix == 18.5
        assert vvix == 95.0
        assert vix3m == 20.0

    @pytest.mark.asyncio
    async def test_vvix_fallback_on_failure(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that VVIX unavailability uses conservative default."""
        vix_contract = MagicMock(conId=1, symbol="VIX")
        vvix_contract = MagicMock(conId=2, symbol="VVIX")
        vix3m_contract = MagicMock(conId=3, symbol="VIX3M")
        mock_ibkr_client.qualify_contracts_async.return_value = [
            vix_contract, vvix_contract, vix3m_contract,
        ]

        async def mock_get_quote(contract, timeout=None):
            sym = getattr(contract, "symbol", "")
            if sym == "VIX":
                return _make_quote(22.0)
            elif sym == "VVIX":
                return _make_quote(0.0, valid=False)  # VVIX unavailable
            elif sym == "VIX3M":
                return _make_quote(21.0)
            return _make_quote(0.0, valid=False)

        mock_ibkr_client.get_quote = AsyncMock(side_effect=mock_get_quote)

        vix, vvix, vix3m = await condition_monitor._get_volatility_complex()

        assert vix == 22.0
        assert vvix == 90.0  # Default
        assert vix3m == 21.0

    @pytest.mark.asyncio
    async def test_all_defaults_on_qualify_failure(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that qualify failure returns all defaults."""
        mock_ibkr_client.qualify_contracts_async.return_value = []

        vix, vvix, vix3m = await condition_monitor._get_volatility_complex()

        assert vix == 20.0
        assert vvix == 90.0
        assert vix3m == 22.0

    @pytest.mark.asyncio
    async def test_exception_returns_defaults(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that exception in volatility fetch returns defaults."""
        mock_ibkr_client.qualify_contracts_async.side_effect = Exception("Connection error")

        vix, vvix, vix3m = await condition_monitor._get_volatility_complex()

        assert vix == 20.0
        assert vvix == 90.0
        assert vix3m == 22.0


class TestSPYPriceFetching:
    """Tests for SPY price fetching."""

    @pytest.mark.asyncio
    async def test_get_spy_price_success(self, condition_monitor, mock_ibkr_client):
        """Test successful SPY price fetching."""
        qualified_contract = MagicMock()
        qualified_contract.conId = 756733
        mock_ibkr_client.qualify_contracts_async.return_value = [qualified_contract]
        mock_ibkr_client.get_quote.return_value = _make_quote(450.11)

        spy_price = await condition_monitor._get_spy_price()

        assert spy_price == 450.11

    @pytest.mark.asyncio
    async def test_get_spy_price_invalid_returns_zero(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that invalid SPY quote returns 0.0."""
        mock_ibkr_client.get_quote.return_value = _make_quote(0.0, valid=False)

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
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(5)]

        quotes = [
            Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""),  # 0.04
            Quote(bid=0.50, ask=0.56, last=0.53, volume=1500, is_valid=True, reason=""),  # 0.06
            Quote(bid=0.30, ask=0.32, last=0.31, volume=800, is_valid=True, reason=""),   # 0.02
            Quote(bid=0.60, ask=0.66, last=0.63, volume=2000, is_valid=True, reason=""),  # 0.06
            Quote(bid=0.40, ask=0.42, last=0.41, volume=1200, is_valid=True, reason=""),  # 0.02
        ]
        mock_ibkr_client.get_quote.side_effect = quotes

        avg_spread = await condition_monitor._calculate_average_spread(contracts)

        assert avg_spread == pytest.approx(0.04, abs=1e-9)

    @pytest.mark.asyncio
    async def test_calculate_average_spread_samples_first_five(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that only first 5 contracts are sampled."""
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(10)]

        mock_quote = Quote(
            bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""
        )
        mock_ibkr_client.get_quote.return_value = mock_quote

        await condition_monitor._calculate_average_spread(contracts)

        assert mock_ibkr_client.get_quote.call_count == 5

    @pytest.mark.asyncio
    async def test_calculate_average_spread_no_valid_quotes_returns_zero(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that no valid quotes returns 0.0."""
        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(3)]
        mock_ibkr_client.get_quote.return_value = _make_quote(0.0, valid=False)

        avg_spread = await condition_monitor._calculate_average_spread(contracts)

        assert avg_spread == 0.0


class TestConditionEvaluation:
    """Tests for condition evaluation logic including VVIX and term structure."""

    def test_evaluate_favorable_low_vix(self, condition_monitor):
        """Test favorable conditions with low VIX, normal VVIX, contango."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=15.0, vvix=88.0, vix3m=17.0, term_structure_ratio=0.88, avg_spread=0.03
        )

        assert favorable is True
        assert "VIX low" in reason
        assert len(warnings) == 0

    def test_evaluate_favorable_moderate_vix(self, condition_monitor):
        """Test favorable conditions with moderate VIX."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=20.0, vvix=92.0, vix3m=21.0, term_structure_ratio=0.95, avg_spread=0.04
        )

        assert favorable is True
        assert "VIX moderate" in reason

    def test_evaluate_unfavorable_high_vix(self, condition_monitor):
        """Test unfavorable conditions when VIX too high."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=30.0, vvix=95.0, vix3m=28.0, term_structure_ratio=1.07, avg_spread=0.04
        )

        # Backwardation > 1.05 triggers before VIX check
        assert favorable is False

    def test_evaluate_unfavorable_wide_spreads(self, condition_monitor):
        """Test unfavorable conditions when spreads too wide."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=20.0, vvix=88.0, vix3m=22.0, term_structure_ratio=0.91, avg_spread=0.12
        )

        assert favorable is False
        assert "Spreads too wide" in reason

    def test_evaluate_vvix_extreme_unfavorable(self, condition_monitor):
        """Test VVIX extreme blocks entries even with normal VIX."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=18.0, vvix=135.0, vix3m=20.0, term_structure_ratio=0.90, avg_spread=0.03
        )

        assert favorable is False
        assert "VVIX extreme" in reason
        assert "135" in reason

    def test_evaluate_backwardation_unfavorable(self, condition_monitor):
        """Test term structure backwardation blocks entries."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=22.0, vvix=95.0, vix3m=20.0, term_structure_ratio=1.10, avg_spread=0.04
        )

        assert favorable is False
        assert "backwardation" in reason.lower()
        assert "1.10" in reason

    def test_evaluate_contango_favorable(self, condition_monitor):
        """Test contango (normal) conditions are favorable."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=19.0, vvix=88.0, vix3m=21.0, term_structure_ratio=0.90, avg_spread=0.04
        )

        assert favorable is True
        assert len(warnings) == 0

    def test_evaluate_vvix_elevated_warning(self, condition_monitor):
        """Test VVIX 100-130 produces warning but stays favorable."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=19.0, vvix=115.0, vix3m=21.0, term_structure_ratio=0.90, avg_spread=0.04
        )

        assert favorable is True
        assert len(warnings) == 1
        assert "VVIX elevated" in warnings[0]

    def test_evaluate_mild_backwardation_warning(self, condition_monitor):
        """Test mild backwardation (1.0-1.05) produces warning."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=20.0, vvix=88.0, vix3m=19.5, term_structure_ratio=1.03, avg_spread=0.04
        )

        assert favorable is True
        assert any("backwardation" in w.lower() for w in warnings)

    def test_evaluate_vvix_at_exact_extreme_threshold(self, condition_monitor):
        """Test VVIX at exactly 130 is still favorable (> 130 triggers)."""
        favorable, reason, warnings = condition_monitor._evaluate_conditions(
            vix=19.0, vvix=130.0, vix3m=21.0, term_structure_ratio=0.90, avg_spread=0.04
        )

        assert favorable is True  # Not > 130


class TestCheckConditions:
    """Tests for complete check_conditions workflow."""

    @pytest.mark.asyncio
    async def test_check_conditions_with_sample_contracts(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test complete condition check with sample contracts."""
        # Set up vol complex
        _setup_vol_complex(mock_ibkr_client, vix=14.6, vvix=88.0, vix3m=16.0)

        # Override get_quote to also handle SPY and spread contracts
        original_side_effect = mock_ibkr_client.get_quote.side_effect

        call_count = {"n": 0}

        async def mock_get_quote(contract, timeout=None):
            sym = getattr(contract, "symbol", "")
            if sym in ("VIX", "VVIX", "VIX3M"):
                return await original_side_effect(contract, timeout)
            elif sym == "SPY":
                return _make_quote(450.11)
            else:
                # Spread contracts
                return Quote(
                    bid=0.44, ask=0.48, last=0.46,
                    volume=1000, is_valid=True, reason=""
                )

        mock_ibkr_client.get_quote = AsyncMock(side_effect=mock_get_quote)

        contracts = [Mock(spec=Contract, symbol=f"SYM{i}") for i in range(3)]
        conditions = await condition_monitor.check_conditions(contracts)

        assert isinstance(conditions, MarketConditions)
        assert conditions.vix == 14.6
        assert conditions.vvix == 88.0
        assert conditions.vix3m == 16.0
        assert conditions.term_structure == "contango"
        assert conditions.term_structure_ratio == pytest.approx(14.6 / 16.0, abs=0.01)
        assert conditions.conditions_favorable is True

    @pytest.mark.asyncio
    async def test_check_conditions_without_sample_contracts(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test condition check without sample contracts (spread=0)."""
        _setup_vol_complex(mock_ibkr_client, vix=20.1, vvix=92.0, vix3m=21.5)

        conditions = await condition_monitor.check_conditions(sample_contracts=None)

        assert conditions.vix == 20.1
        assert conditions.vvix == 92.0
        assert conditions.avg_spread == 0.0
        assert conditions.conditions_favorable is True

    @pytest.mark.asyncio
    async def test_check_conditions_term_structure_calculation(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test term structure ratio calculation."""
        _setup_vol_complex(mock_ibkr_client, vix=25.0, vvix=110.0, vix3m=22.0)

        conditions = await condition_monitor.check_conditions()

        assert conditions.term_structure == "backwardation"  # 25/22 > 1.0
        assert conditions.term_structure_ratio == pytest.approx(25.0 / 22.0, abs=0.01)

    @pytest.mark.asyncio
    async def test_check_conditions_timestamp_is_eastern(
        self, condition_monitor, mock_ibkr_client
    ):
        """Test that condition timestamp is in Eastern timezone."""
        _setup_vol_complex(mock_ibkr_client)

        conditions = await condition_monitor.check_conditions()

        assert conditions.timestamp.tzinfo == ZoneInfo("America/New_York")


class TestConfigurableThresholds:
    """Tests for configurable threshold loading."""

    def test_custom_thresholds_from_env(self, mock_ibkr_client):
        """Test that custom thresholds are loaded from environment."""
        with patch.dict(
            "os.environ",
            {
                "TIER2_VIX_LOW": "20",
                "TIER2_VIX_HIGH": "30",
                "TIER2_MAX_SPREAD": "0.10",
                "VVIX_WARN": "110",
                "VVIX_EXTREME": "140",
                "TERM_STRUCTURE_BLOCK": "1.10",
            },
        ):
            monitor = MarketConditionMonitor(mock_ibkr_client)

            assert monitor.vix_low_threshold == 20.0
            assert monitor.vix_high_threshold == 30.0
            assert monitor.max_spread == 0.10
            assert monitor.vvix_warn_threshold == 110.0
            assert monitor.vvix_extreme_threshold == 140.0
            assert monitor.term_structure_block_threshold == 1.10

    def test_default_thresholds_used_when_not_set(self, mock_ibkr_client):
        """Test default thresholds when environment not set."""
        with patch.dict("os.environ", {}, clear=True):
            monitor = MarketConditionMonitor(mock_ibkr_client)

            assert monitor.vix_low_threshold == 18.0
            assert monitor.vix_high_threshold == 25.0
            assert monitor.max_spread == 0.08
            assert monitor.vvix_warn_threshold == 100.0
            assert monitor.vvix_extreme_threshold == 130.0
            assert monitor.term_structure_block_threshold == 1.05
