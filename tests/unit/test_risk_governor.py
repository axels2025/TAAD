"""Unit tests for RiskGovernor.

Tests risk limit enforcement and circuit breakers.
"""

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.config.base import Config
from src.execution.position_monitor import PositionMonitor, PositionStatus
from src.execution.risk_governor import PostTradeMarginResult, RiskGovernor, RiskLimitCheck
from src.services.kill_switch import KillSwitch
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient
from src.utils.timezone import us_trading_date


@pytest.fixture(autouse=True)
def _clear_earnings_cache():
    """Clear the module-level earnings cache before each test.

    The earnings_service module uses a global _earnings_cache dict.
    If other test files (e.g. test_market_context.py) populate this cache
    with real or mocked earnings data, it leaks into risk_governor tests
    and causes the earnings check to unexpectedly reject trades.
    """
    from src.services import earnings_service
    earnings_service._earnings_cache.clear()
    yield
    earnings_service._earnings_cache.clear()


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    client = MagicMock(spec=IBKRClient)

    # Mock account summary
    client.get_account_summary.return_value = {
        "NetLiquidation": 100000.0,
        "AvailableFunds": 80000.0,
        "BuyingPower": 200000.0,
        "ExcessLiquidity": 50000.0,
    }

    # Default: WhatIf returns None (unavailable) so existing tests use estimate
    client.get_margin_requirement.return_value = None

    return client


@pytest.fixture
def mock_position_monitor():
    """Create mock PositionMonitor."""
    monitor = MagicMock(spec=PositionMonitor)

    # Default: no positions
    monitor.get_all_positions.return_value = []

    return monitor


@pytest.fixture
def config(monkeypatch):
    """Create test configuration using code defaults (skip .env)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test123456789")
    return Config(_env_file=None)


@pytest.fixture
def test_kill_switch(tmp_path):
    """Create a test-isolated KillSwitch using a temp file."""
    halt_file = tmp_path / "test_kill_switch.json"
    return KillSwitch(halt_file=halt_file, register_signals=False)


@pytest.fixture
def risk_governor(mock_ibkr_client, mock_position_monitor, config, test_kill_switch):
    """Create RiskGovernor instance."""
    return RiskGovernor(
        ibkr_client=mock_ibkr_client,
        position_monitor=mock_position_monitor,
        config=config,
        kill_switch=test_kill_switch,
    )


@pytest.fixture
def sample_opportunity():
    """Create sample trade opportunity."""
    return TradeOpportunity(
        symbol="AAPL",
        strike=200.0,
        expiration=datetime.now() + timedelta(days=10),
        option_type="PUT",
        premium=0.50,
        contracts=5,
        otm_pct=0.15,
        dte=10,
        stock_price=235.0,
        trend="uptrend",
        confidence=0.85,
        reasoning="Test trade opportunity",
        margin_required=1000.0,
    )


class TestRiskGovernorInitialization:
    """Test RiskGovernor initialization."""

    def test_initialization(self, risk_governor, config):
        """Test RiskGovernor initializes correctly from Config values."""
        assert risk_governor.MAX_DAILY_LOSS_PCT == config.max_daily_loss
        assert risk_governor.MAX_POSITION_LOSS == config.max_position_loss
        assert risk_governor.MAX_POSITIONS == config.max_positions
        assert risk_governor.MAX_POSITIONS_PER_DAY == config.max_positions_per_day
        assert risk_governor.MAX_SECTOR_CONCENTRATION == config.risk_limits.max_sector_concentration
        assert risk_governor.MAX_MARGIN_UTILIZATION == config.max_margin_utilization
        assert not risk_governor._trading_halted
        assert risk_governor._trades_today == 0

    def test_initialization_sets_last_reset_date(self, risk_governor):
        """Test last reset date is set to today (US Eastern)."""
        assert risk_governor._last_reset_date == us_trading_date()


class TestTradingHalt:
    """Test trading halt functionality."""

    def test_emergency_halt(self, risk_governor):
        """Test emergency halt stops trading."""
        assert not risk_governor.is_halted()

        risk_governor.emergency_halt("Test halt")

        assert risk_governor.is_halted()
        assert risk_governor._halt_reason == "Test halt"

    def test_resume_trading(self, risk_governor):
        """Test resume trading clears halt."""
        risk_governor.emergency_halt("Test halt")
        assert risk_governor.is_halted()

        risk_governor.resume_trading()

        assert not risk_governor.is_halted()
        assert risk_governor._halt_reason == ""

    def test_pre_trade_check_rejects_when_halted(
        self, risk_governor, sample_opportunity
    ):
        """Test pre-trade check rejects trades when halted."""
        risk_governor.emergency_halt("Manual override")

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Trading halted" in result.reason
        assert result.limit_name == "trading_halt"


class TestDailyLossLimit:
    """Test daily loss limit enforcement."""

    def test_daily_loss_within_limit(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test trade approved when daily loss within limit."""
        # Mock positions with small loss (-1%)
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id="POS1",
                symbol="TSLA",
                strike=300.0,
                option_type="P",
                expiration_date="20260219",
                contracts=5,
                entry_premium=0.50,
                current_premium=0.55,
                current_pnl=-250.0,  # -$250
                current_pnl_pct=-0.10,
                days_held=2,
                dte=8,
            )
        ]

        result = risk_governor.pre_trade_check(sample_opportunity)

        # Should pass daily loss check
        assert result.approved

    def test_daily_loss_exceeds_limit(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test trade rejected when daily loss exceeds -2%."""
        # Mock positions with large loss (-3% of account)
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id="POS1",
                symbol="TSLA",
                strike=300.0,
                option_type="P",
                expiration_date="20260219",
                contracts=5,
                entry_premium=0.50,
                current_premium=1.50,
                current_pnl=-2500.0,  # -$2,500
                current_pnl_pct=-2.0,
                days_held=2,
                dte=8,
            ),
            PositionStatus(
                position_id="POS2",
                symbol="MSFT",
                strike=350.0,
                option_type="P",
                expiration_date="20260219",
                contracts=3,
                entry_premium=1.00,
                current_premium=2.00,
                current_pnl=-300.0,  # -$300
                current_pnl_pct=-1.0,
                days_held=1,
                dte=9,
            ),
        ]
        # Total loss: -$2,800 = -2.8% of $100k account

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Daily loss limit exceeded" in result.reason
        assert result.limit_name == "daily_loss"
        # Should trigger emergency halt
        assert risk_governor.is_halted()

    def test_daily_loss_circuit_breaker_triggers(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test circuit breaker halts trading on daily loss."""
        # Mock large loss
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id="POS1",
                symbol="TSLA",
                strike=300.0,
                option_type="P",
                expiration_date="20260219",
                contracts=10,
                entry_premium=1.00,
                current_premium=3.00,
                current_pnl=-2000.0,
                current_pnl_pct=-2.0,
                days_held=1,
                dte=5,
            )
        ]

        risk_governor.pre_trade_check(sample_opportunity)

        # Circuit breaker should have triggered
        assert risk_governor.is_halted()
        assert "Daily loss limit exceeded" in risk_governor._halt_reason


class TestMaxPositions:
    """Test maximum positions limit."""

    def test_max_positions_not_reached(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test trade approved when positions below limit."""
        # Mock 5 positions (limit is 10)
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id=f"POS{i}",
                symbol=f"SYM{i}",
                strike=100.0 + i,
                option_type="P",
                expiration_date="20260219",
                contracts=1,
                entry_premium=0.50,
                current_premium=0.45,
                current_pnl=5.0,
                current_pnl_pct=0.10,
                days_held=1,
                dte=9,
            )
            for i in range(5)
        ]

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_max_positions_reached(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test trade rejected when at max positions."""
        # Mock positions at limit (uses MAX_POSITIONS from config)
        max_pos = risk_governor.MAX_POSITIONS
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id=f"POS{i}",
                symbol=f"SYM{i}",
                strike=100.0 + i,
                option_type="P",
                expiration_date="20260219",
                contracts=1,
                entry_premium=0.50,
                current_premium=0.45,
                current_pnl=5.0,
                current_pnl_pct=0.10,
                days_held=1,
                dte=9,
            )
            for i in range(max_pos)
        ]

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Max positions reached" in result.reason
        assert result.limit_name == "max_positions"


class TestMaxPositionsPerDay:
    """Test maximum positions per day limit."""

    def test_max_trades_per_day_not_reached(self, risk_governor, sample_opportunity):
        """Test trade approved when daily trades below limit."""
        risk_governor._trades_today = 5  # 5 trades today (limit is 10)

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_max_trades_per_day_reached(self, risk_governor, sample_opportunity):
        """Test trade rejected when at daily trade limit."""
        risk_governor._trades_today = 10  # At limit

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Max trades per day reached" in result.reason
        assert result.limit_name == "max_trades_per_day"

    def test_record_trade_increments_counter(self, risk_governor, sample_opportunity):
        """Test recording trade increments daily counter."""
        assert risk_governor._trades_today == 0

        risk_governor.record_trade(sample_opportunity)

        assert risk_governor._trades_today == 1

        risk_governor.record_trade(sample_opportunity)

        assert risk_governor._trades_today == 2

    def test_daily_counter_resets_on_new_day(self, risk_governor, sample_opportunity):
        """Test daily counter resets when day changes."""
        risk_governor._trades_today = 5
        risk_governor._last_reset_date = us_trading_date() - timedelta(days=1)

        # Trigger reset by calling pre_trade_check
        risk_governor.pre_trade_check(sample_opportunity)

        assert risk_governor._trades_today == 0
        assert risk_governor._last_reset_date == us_trading_date()


class TestMarginUtilization:
    """Test margin utilization limit."""

    def test_margin_sufficient(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade approved when margin is sufficient."""
        # Available funds: $80,000, required: $1,000
        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_margin_insufficient(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade rejected when insufficient margin."""
        # Set available funds lower than requirement
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 500.0,  # Less than required $1,000
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Insufficient margin" in result.reason
        assert result.limit_name == "margin_utilization"

    def test_margin_utilization_too_high(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade rejected when margin utilization would exceed 80%."""
        # Set buying power and available funds to trigger high utilization
        # Available funds is sufficient but utilization would be too high
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 500000.0,
            "AvailableFunds": 60000.0,  # Enough for trade
            "BuyingPower": 100000.0,
            "ExcessLiquidity": 50000.0,
        }

        # Trade requires $1,000
        # Current used: 40000 (100000 - 60000)
        # After trade: 41000 / 100000 = 41% (should pass)
        # Per-trade cap: 10% of 500k = $50k (not triggered)

        result = risk_governor.pre_trade_check(sample_opportunity)

        # Should pass with current settings
        assert result.approved

        # Now test with trade requiring more margin that would push utilization over 80%
        sample_opportunity.margin_required = 45000.0
        # After trade: (40000 + 45000) / 100000 = 85% > 80%
        # Per-trade cap: $45k < $50k (not triggered)

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        # Should reject due to utilization (not insufficient funds)
        assert "Margin utilization too high" in result.reason


class TestWhatIfMarginVerification:
    """Test Layer 2 WhatIf margin verification."""

    def test_whatif_confirms_estimate(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade approved when WhatIf confirms the estimate."""
        mock_ibkr_client.get_margin_requirement.return_value = 1000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved
        mock_ibkr_client.get_margin_requirement.assert_called_once_with(
            symbol="AAPL",
            strike=200.0,
            expiration=sample_opportunity.expiration.strftime("%Y%m%d"),
            option_type="PUT",
            contracts=5,
        )

    def test_whatif_higher_than_estimate_still_passes(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade approved when WhatIf is higher but still within limits."""
        # Estimate is $1,000, WhatIf returns $2,000 — still fine for $80k available
        mock_ibkr_client.get_margin_requirement.return_value = 2000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_whatif_higher_than_estimate_causes_rejection(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade rejected when WhatIf reveals margin exceeds available funds."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 5000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        # Estimate is $1,000 (passes Layer 1), but WhatIf is $6,000 (exceeds $5,000)
        sample_opportunity.margin_required = 1000.0
        mock_ibkr_client.get_margin_requirement.return_value = 6000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "WhatIf" in result.reason
        assert "6,000" in result.reason

    def test_whatif_unavailable_falls_back_to_estimate(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade uses estimate when WhatIf returns None."""
        mock_ibkr_client.get_margin_requirement.return_value = None

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_whatif_exception_falls_back_to_estimate(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade uses estimate when WhatIf raises an exception."""
        mock_ibkr_client.get_margin_requirement.side_effect = Exception("API timeout")

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_whatif_triggers_utilization_rejection(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test WhatIf margin can trigger utilization percentage rejection."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 500000.0,
            "AvailableFunds": 60000.0,
            "BuyingPower": 100000.0,
            "ExcessLiquidity": 50000.0,
        }
        # Estimate $1,000 (utilization 41% — passes)
        # WhatIf $25,000 (utilization 65,000/100,000 = 65% — passes)
        # Per-trade cap: 10% of 500k = $50k (not triggered)
        sample_opportunity.margin_required = 1000.0
        mock_ibkr_client.get_margin_requirement.return_value = 25000.0

        result = risk_governor.pre_trade_check(sample_opportunity)
        assert result.approved

        # WhatIf $45,000 (utilization 85,000/100,000 = 85% > 80% — rejected)
        # Per-trade cap: $45k < $50k (not triggered)
        mock_ibkr_client.get_margin_requirement.return_value = 45000.0

        result = risk_governor.pre_trade_check(sample_opportunity)
        assert not result.approved
        assert "Margin utilization too high" in result.reason

    def test_layer1_fast_reject_skips_whatif(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test Layer 1 rejection skips the WhatIf API call entirely."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 500.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        sample_opportunity.margin_required = 1000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Insufficient margin" in result.reason
        mock_ibkr_client.get_margin_requirement.assert_not_called()


class TestPerTradeMarginCap:
    """Test per-trade margin cap enforcement."""

    def test_trade_within_cap(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade approved when margin is within per-trade cap."""
        # NetLiq=100k, cap=10%, margin=$1k — well within cap
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        sample_opportunity.margin_required = 1000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_trade_exceeds_cap(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade rejected when margin exceeds per-trade cap."""
        # NetLiq=100k, cap=10%=$10k, margin=$15k — exceeds cap
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        sample_opportunity.margin_required = 15000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert result.limit_name == "per_trade_margin_cap"
        assert "15,000" in result.reason
        assert "10%" in result.reason

    def test_trade_exactly_at_cap(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test trade approved when margin exactly equals per-trade cap."""
        # NetLiq=100k, cap=10%=$10k, margin=$10k — exactly at cap
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        sample_opportunity.margin_required = 10000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_whatif_margin_triggers_cap_rejection(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """Test WhatIf margin can trigger per-trade cap rejection."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        # Estimate $5k (within cap), WhatIf $12k (exceeds 10% cap)
        sample_opportunity.margin_required = 5000.0
        mock_ibkr_client.get_margin_requirement.return_value = 12000.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert result.limit_name == "per_trade_margin_cap"

    def test_custom_cap_from_env(
        self, mock_ibkr_client, mock_position_monitor, sample_opportunity,
        monkeypatch, test_kill_switch,
    ):
        """Test per-trade cap is configurable via environment variable."""
        monkeypatch.setenv("MAX_MARGIN_PER_TRADE_PCT", "0.05")

        from src.config.base import Config, reset_config
        reset_config()
        custom_config = Config()
        governor = RiskGovernor(
            mock_ibkr_client, mock_position_monitor, custom_config,
            kill_switch=test_kill_switch,
        )

        assert governor.MAX_MARGIN_PER_TRADE_PCT == 0.05

        # NetLiq=100k, cap=5%=$5k, margin=$6k — exceeds 5% cap
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }
        sample_opportunity.margin_required = 6000.0

        result = governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert result.limit_name == "per_trade_margin_cap"
        assert "5%" in result.reason


class TestSectorConcentration:
    """Test sector concentration limit."""

    def test_no_positions_passes(
        self, risk_governor, sample_opportunity
    ):
        """Test sector check passes when no existing positions."""
        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_different_sector_passes(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test approved when new trade is in a different sector than existing."""
        # 3 existing Technology positions, new trade is Healthcare (UNH)
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id=f"POS{i}",
                symbol=sym,
                strike=200.0,
                option_type="P",
                expiration_date="20260219",
                contracts=1,
                entry_premium=0.50,
                current_premium=0.45,
                current_pnl=5.0,
                current_pnl_pct=0.10,
                days_held=1,
                dte=9,
            )
            for i, sym in enumerate(["MSFT", "GOOGL", "NVDA"])
        ]
        sample_opportunity.symbol = "UNH"  # Healthcare

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_same_sector_exceeds_limit(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test rejected when adding same sector would exceed 30% limit.

        3 of 9 existing positions are Technology. Adding another Technology
        trade makes 4/10 = 40% > 30% limit.
        """
        # 3 Tech + 6 non-Tech = 9 existing positions
        positions = []
        tech_symbols = ["MSFT", "GOOGL", "NVDA"]
        other_symbols = ["JPM", "UNH", "XOM", "HON", "WMT", "PFE"]
        for i, sym in enumerate(tech_symbols + other_symbols):
            positions.append(
                PositionStatus(
                    position_id=f"POS{i}",
                    symbol=sym,
                    strike=200.0,
                    option_type="P",
                    expiration_date="20260219",
                    contracts=1,
                    entry_premium=0.50,
                    current_premium=0.45,
                    current_pnl=5.0,
                    current_pnl_pct=0.10,
                    days_held=1,
                    dte=9,
                )
            )
        mock_position_monitor.get_all_positions.return_value = positions

        # New trade is also Technology (AAPL) → 4/10 = 40% > 30%
        sample_opportunity.symbol = "INTC"
        sample_opportunity.strike = 30.0  # Different from any position

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert result.limit_name == "sector_concentration"
        assert "Technology" in result.reason
        assert "40%" in result.reason

    def test_at_limit_passes(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test approved when concentration is exactly at 30% limit."""
        # 2 Tech + 7 non-Tech = 9 existing. Adding Tech = 3/10 = 30%
        positions = []
        tech_symbols = ["MSFT", "GOOGL"]
        other_symbols = ["JPM", "UNH", "XOM", "HON", "WMT", "PFE", "VZ"]
        for i, sym in enumerate(tech_symbols + other_symbols):
            positions.append(
                PositionStatus(
                    position_id=f"POS{i}",
                    symbol=sym,
                    strike=200.0,
                    option_type="P",
                    expiration_date="20260219",
                    contracts=1,
                    entry_premium=0.50,
                    current_premium=0.45,
                    current_pnl=5.0,
                    current_pnl_pct=0.10,
                    days_held=1,
                    dte=9,
                )
            )
        mock_position_monitor.get_all_positions.return_value = positions

        # New trade is Tech → 3/10 = 30% = limit (not exceeding)
        sample_opportunity.symbol = "INTC"
        sample_opportunity.strike = 30.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved

    def test_unknown_sector_concentrated(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test Unknown sector is counted for concentration."""
        # 3 Unknown sector positions out of 9
        positions = []
        unknown_symbols = ["ZZZZ", "YYYY", "XXXX"]  # Not in sector map
        known_symbols = ["JPM", "UNH", "XOM", "HON", "WMT", "PFE"]
        for i, sym in enumerate(unknown_symbols + known_symbols):
            positions.append(
                PositionStatus(
                    position_id=f"POS{i}",
                    symbol=sym,
                    strike=200.0,
                    option_type="P",
                    expiration_date="20260219",
                    contracts=1,
                    entry_premium=0.50,
                    current_premium=0.45,
                    current_pnl=5.0,
                    current_pnl_pct=0.10,
                    days_held=1,
                    dte=9,
                )
            )
        mock_position_monitor.get_all_positions.return_value = positions

        # New trade is also Unknown → 4/10 = 40% > 30%
        sample_opportunity.symbol = "WWWW"
        sample_opportunity.strike = 50.0

        result = risk_governor.pre_trade_check(sample_opportunity)

        assert not result.approved
        assert "Unknown" in result.reason


class TestRiskStatus:
    """Test risk status reporting."""

    def test_get_risk_status(
        self, risk_governor, mock_position_monitor, mock_ibkr_client
    ):
        """Test risk status returns current metrics."""
        # Setup positions
        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id="POS1",
                symbol="AAPL",
                strike=200.0,
                option_type="P",
                expiration_date="20260219",
                contracts=5,
                entry_premium=0.50,
                current_premium=0.45,
                current_pnl=25.0,
                current_pnl_pct=0.10,
                days_held=2,
                dte=8,
            ),
            PositionStatus(
                position_id="POS2",
                symbol="MSFT",
                strike=350.0,
                option_type="P",
                expiration_date="20260219",
                contracts=3,
                entry_premium=1.00,
                current_premium=0.90,
                current_pnl=30.0,
                current_pnl_pct=0.10,
                days_held=1,
                dte=9,
            ),
        ]

        risk_governor._trades_today = 3

        status = risk_governor.get_risk_status()

        assert status["trading_halted"] is False
        assert status["current_positions"] == 2
        assert status["max_positions"] == risk_governor.MAX_POSITIONS
        assert status["trades_today"] == 3
        assert status["max_trades_today"] == risk_governor.MAX_POSITIONS_PER_DAY
        assert status["daily_pnl"] == 55.0  # $25 + $30
        assert status["daily_pnl_pct"] == 0.00055  # $55 / $100,000
        assert status["daily_loss_limit"] == -0.02
        assert status["account_value"] == 100000.0

    def test_get_risk_status_when_halted(self, risk_governor):
        """Test risk status shows halt information."""
        risk_governor.emergency_halt("Test emergency")

        status = risk_governor.get_risk_status()

        assert status["trading_halted"] is True
        assert status["halt_reason"] == "Test emergency"


class TestPreTradeCheckIntegration:
    """Test complete pre-trade check workflow."""

    def test_all_checks_pass(self, risk_governor, sample_opportunity):
        """Test trade approved when all checks pass."""
        result = risk_governor.pre_trade_check(sample_opportunity)

        assert result.approved
        assert result.reason == "All risk checks passed"
        assert result.limit_name == "all_checks"

    def test_checks_execute_in_order(
        self, risk_governor, mock_position_monitor, sample_opportunity
    ):
        """Test checks execute in correct priority order."""
        # Set up to fail on second check (daily loss)
        risk_governor._trading_halted = False  # First check passes

        mock_position_monitor.get_all_positions.return_value = [
            PositionStatus(
                position_id="POS1",
                symbol="TSLA",
                strike=300.0,
                option_type="P",
                expiration_date="20260219",
                contracts=50,
                entry_premium=1.00,
                current_premium=3.00,
                current_pnl=-10000.0,  # Huge loss
                current_pnl_pct=-2.0,
                days_held=1,
                dte=5,
            )
        ]

        result = risk_governor.pre_trade_check(sample_opportunity)

        # Should fail on daily loss check (second check)
        assert not result.approved
        assert result.limit_name == "daily_loss"


class TestRiskLimitCheck:
    """Test RiskLimitCheck dataclass."""

    def test_risk_limit_check_approved(self):
        """Test creating approved risk limit check."""
        check = RiskLimitCheck(
            approved=True,
            reason="Within limits",
            limit_name="test_limit",
            current_value=50.0,
            limit_value=100.0,
            utilization_pct=50.0,
        )

        assert check.approved
        assert check.reason == "Within limits"
        assert check.utilization_pct == 50.0

    def test_risk_limit_check_rejected(self):
        """Test creating rejected risk limit check."""
        check = RiskLimitCheck(
            approved=False,
            reason="Limit exceeded",
            limit_name="test_limit",
            current_value=120.0,
            limit_value=100.0,
            utilization_pct=120.0,
        )

        assert not check.approved
        assert check.reason == "Limit exceeded"
        assert check.utilization_pct == 120.0


class TestPostTradeMarginVerification:
    """Test post-trade margin verification."""

    def test_healthy_margin_after_trade(
        self, risk_governor, mock_ibkr_client
    ):
        """Test healthy margin state returns is_healthy=True."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor.verify_post_trade_margin(symbol="AAPL")

        assert result.is_healthy
        assert result.available_funds == 80000.0
        assert result.excess_liquidity == 50000.0
        assert result.net_liquidation == 100000.0
        assert result.warning == ""
        assert not risk_governor.is_halted()

    def test_low_excess_liquidity_triggers_halt(
        self, risk_governor, mock_ibkr_client
    ):
        """Test trading halts when ExcessLiquidity < 10% of NetLiquidation."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 5000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 8000.0,  # 8% < 10% threshold
        }

        result = risk_governor.verify_post_trade_margin(symbol="TSLA")

        assert not result.is_healthy
        assert "DANGER" in result.warning
        assert "ExcessLiquidity" in result.warning
        assert risk_governor.is_halted()

    def test_exact_threshold_is_not_healthy(
        self, risk_governor, mock_ibkr_client
    ):
        """Test ExcessLiquidity exactly at 10% is not healthy (must be >10%)."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 10000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 10000.0,  # Exactly 10%
        }

        result = risk_governor.verify_post_trade_margin()

        assert not result.is_healthy
        assert risk_governor.is_halted()

    def test_above_threshold_is_healthy(
        self, risk_governor, mock_ibkr_client
    ):
        """Test ExcessLiquidity above 10% is healthy."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 15000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 10001.0,  # Just above 10%
        }

        result = risk_governor.verify_post_trade_margin()

        assert result.is_healthy
        assert not risk_governor.is_halted()

    def test_margin_utilization_calculation(
        self, risk_governor, mock_ibkr_client
    ):
        """Test margin utilization percentage is correctly calculated."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 60000.0,  # 40k used
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor.verify_post_trade_margin()

        # (200000 - 60000) / 200000 * 100 = 70%
        assert abs(result.margin_utilization_pct - 70.0) < 0.01

    def test_verification_failure_does_not_halt(
        self, risk_governor, mock_ibkr_client
    ):
        """Test that API failure during verification does not halt trading."""
        mock_ibkr_client.get_account_summary.side_effect = Exception("Connection lost")

        result = risk_governor.verify_post_trade_margin(symbol="AAPL")

        assert result.is_healthy  # Don't halt on verification failure
        assert "Verification failed" in result.warning
        assert not risk_governor.is_halted()

    def test_empty_symbol_context(
        self, risk_governor, mock_ibkr_client
    ):
        """Test verification works without symbol context."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor.verify_post_trade_margin()

        assert result.is_healthy


class TestAccountHealthCache:
    """Test account health caching (4.4 — faster health monitoring)."""

    def test_cache_populated_on_first_call(self, risk_governor, mock_ibkr_client):
        """First call fetches from IBKR and populates cache."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "ExcessLiquidity": 50000.0,
            "MaintMarginReq": 20000.0,
        }

        result = risk_governor.check_account_health()

        assert result["NetLiquidation"] == 100000.0
        assert result["AvailableFunds"] == 80000.0
        assert result["ExcessLiquidity"] == 50000.0
        assert result["healthy"] is True
        mock_ibkr_client.get_account_summary.assert_called_once()

    def test_cache_reused_within_interval(self, risk_governor, mock_ibkr_client):
        """Second call within 5 minutes uses cache, no IBKR call."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "ExcessLiquidity": 50000.0,
            "MaintMarginReq": 20000.0,
        }

        risk_governor.check_account_health()
        mock_ibkr_client.get_account_summary.reset_mock()

        risk_governor.check_account_health()
        mock_ibkr_client.get_account_summary.assert_not_called()

    def test_cache_refreshes_after_interval(self, risk_governor, mock_ibkr_client):
        """Call after 5+ minutes refreshes from IBKR."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "ExcessLiquidity": 50000.0,
            "MaintMarginReq": 20000.0,
        }

        risk_governor.check_account_health()
        mock_ibkr_client.get_account_summary.reset_mock()

        # Simulate stale cache
        risk_governor._last_health_check = datetime.now() - timedelta(minutes=6)

        risk_governor.check_account_health()
        mock_ibkr_client.get_account_summary.assert_called_once()

    def test_unhealthy_when_no_excess_liquidity(self, risk_governor, mock_ibkr_client):
        """Account with zero excess liquidity is unhealthy."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 0,
            "ExcessLiquidity": 0,
            "MaintMarginReq": 100000.0,
        }

        result = risk_governor.check_account_health()

        assert result["healthy"] is False

    def test_margin_check_uses_cached_summary(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """_check_margin_utilization uses cached account summary."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        # First call populates cache
        risk_governor._check_margin_utilization(sample_opportunity)
        mock_ibkr_client.get_account_summary.reset_mock()

        # Second call should use cache
        risk_governor._check_margin_utilization(sample_opportunity)
        mock_ibkr_client.get_account_summary.assert_not_called()


class TestWeeklyLossLimit:
    """Test weekly loss circuit breaker (6.1A)."""

    def test_weekly_loss_exceeds_limit_halts_trading(
        self, risk_governor, mock_ibkr_client
    ):
        """Weekly loss > 5% triggers emergency halt."""
        # Set week-start equity to 100k
        risk_governor._week_start_equity = 100000.0
        risk_governor._week_start_date = datetime.now()

        # Current equity dropped to 94k (-6%)
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 94000.0,
            "AvailableFunds": 70000.0,
            "BuyingPower": 180000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor._check_weekly_loss_limit()

        assert not result.approved
        assert "weekly" in result.reason.lower()
        assert risk_governor.is_halted()

    def test_weekly_loss_within_limit_passes(
        self, risk_governor, mock_ibkr_client
    ):
        """Weekly loss < 5% passes."""
        risk_governor._week_start_equity = 100000.0
        risk_governor._week_start_date = datetime.now()

        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 97000.0,
            "AvailableFunds": 75000.0,
            "BuyingPower": 190000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor._check_weekly_loss_limit()

        assert result.approved
        assert not risk_governor.is_halted()

    def test_weekly_resets_on_monday(self, risk_governor, mock_ibkr_client):
        """Week-start equity resets on Monday."""
        # Set stale week start from last week
        from datetime import timedelta
        risk_governor._week_start_equity = 90000.0
        risk_governor._week_start_date = datetime.now() - timedelta(days=7)

        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        # Force Monday check by patching datetime
        with patch("src.execution.risk_governor.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 9, 10, 0)  # Monday
            mock_dt.side_effect = lambda *args, **kw: datetime(*args, **kw)
            result = risk_governor._check_weekly_loss_limit()

        assert result.approved
        assert risk_governor._week_start_equity == 100000.0


class TestMaxDrawdown:
    """Test max drawdown circuit breaker (6.1A)."""

    def test_drawdown_exceeds_limit_halts_trading(
        self, risk_governor, mock_ibkr_client
    ):
        """Drawdown > 10% triggers emergency halt."""
        risk_governor._peak_equity = 100000.0

        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 88000.0,
            "AvailableFunds": 60000.0,
            "BuyingPower": 170000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor._check_max_drawdown()

        assert not result.approved
        assert "drawdown" in result.reason.lower()
        assert risk_governor.is_halted()

    def test_drawdown_within_limit_passes(self, risk_governor, mock_ibkr_client):
        """Drawdown < 10% passes."""
        risk_governor._peak_equity = 100000.0

        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 95000.0,
            "AvailableFunds": 75000.0,
            "BuyingPower": 190000.0,
            "ExcessLiquidity": 50000.0,
        }

        result = risk_governor._check_max_drawdown()

        assert result.approved
        assert not risk_governor.is_halted()

    def test_peak_equity_tracks_upward(self, risk_governor, mock_ibkr_client):
        """Peak equity updates when equity increases."""
        risk_governor._peak_equity = 90000.0

        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 50000.0,
        }

        risk_governor._check_max_drawdown()

        assert risk_governor._peak_equity == 100000.0


class TestExcessLiquidity:
    """Test ExcessLiquidity monitoring (6.2A)."""

    def test_low_excess_liquidity_rejects_trade(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """ExcessLiquidity < 10% of NLV rejects trade."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 8000.0,  # 8% < 10%
        }

        result = risk_governor._check_margin_utilization(sample_opportunity)

        assert not result.approved
        assert "excessliquidity" in result.reason.lower()

    def test_adequate_excess_liquidity_passes(
        self, risk_governor, mock_ibkr_client, sample_opportunity
    ):
        """ExcessLiquidity > 20% of NLV passes without warning."""
        mock_ibkr_client.get_account_summary.return_value = {
            "NetLiquidation": 100000.0,
            "AvailableFunds": 80000.0,
            "BuyingPower": 200000.0,
            "ExcessLiquidity": 25000.0,  # 25% > 20%
        }

        result = risk_governor._check_margin_utilization(sample_opportunity)

        assert result.approved
