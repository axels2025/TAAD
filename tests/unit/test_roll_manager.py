"""Tests for RollManager — defensive position rolling."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.execution.position_monitor import PositionStatus
from src.services.roll_manager import RollDecision, RollManager, RollTarget


def make_position(
    symbol="AAPL",
    strike=170.0,
    dte=5,
    current_pnl_pct=0.50,
    delta=-0.20,
    current_premium=0.15,
    contracts=5,
) -> PositionStatus:
    """Helper to create a test PositionStatus."""
    exp_date = (datetime.now() + timedelta(days=dte)).strftime("%Y%m%d")
    return PositionStatus(
        position_id=f"{symbol}_{strike}_{exp_date}_P",
        symbol=symbol,
        strike=strike,
        option_type="P",
        expiration_date=exp_date,
        contracts=contracts,
        entry_premium=0.50,
        current_premium=current_premium,
        current_pnl=175.0,
        current_pnl_pct=current_pnl_pct,
        days_held=10,
        dte=dte,
        delta=delta,
    )


@pytest.fixture
def ibkr_client():
    """Create mock IBKR client."""
    return MagicMock()


@pytest.fixture
def roll_manager(ibkr_client):
    """Create RollManager with mock client."""
    return RollManager(ibkr_client)


class TestRollEvaluation:
    """Test RollManager.evaluate_roll() decision logic."""

    def test_max_rolls_exceeded(self, roll_manager):
        """Position already rolled max times → no roll."""
        position = make_position()
        decision = roll_manager.evaluate_roll(position, roll_count=2)
        assert decision.should_roll is False
        assert "Max rolls" in decision.reason

    def test_insufficient_profit(self, roll_manager):
        """Position not profitable enough → no roll."""
        position = make_position(current_pnl_pct=0.10)
        decision = roll_manager.evaluate_roll(position, roll_count=0)
        assert decision.should_roll is False
        assert "Insufficient profit" in decision.reason

    def test_dte_too_high(self, roll_manager):
        """Position has too many DTE → no roll."""
        position = make_position(dte=15)
        decision = roll_manager.evaluate_roll(position, roll_count=0)
        assert decision.should_roll is False
        assert "DTE too high" in decision.reason

    def test_delta_too_deep(self, roll_manager):
        """Delta too deep ITM → no roll (too risky)."""
        position = make_position(delta=-0.55)
        decision = roll_manager.evaluate_roll(position, roll_count=0)
        assert decision.should_roll is False
        assert "Delta too deep" in decision.reason

    def test_delta_none_passes(self, roll_manager):
        """Delta None (unavailable) does not block roll."""
        position = make_position(delta=None)
        # Will still fail at find_roll_target since IBKR is mocked
        roll_manager.ibkr_client.get_option_contract.return_value = MagicMock()
        roll_manager.ibkr_client.qualify_contract.return_value = None
        decision = roll_manager.evaluate_roll(position, roll_count=0)
        # Should get past delta check — fails at target finding
        assert decision.should_roll is False
        assert "No viable roll target" in decision.reason

    @patch("src.services.roll_manager.RollManager._find_roll_target")
    @patch("src.services.roll_manager.RollManager._check_earnings_safe")
    def test_viable_roll_recommended(self, mock_earnings, mock_target, roll_manager):
        """All conditions met → roll recommended."""
        position = make_position(dte=5, current_pnl_pct=0.50, delta=-0.20)

        mock_target.return_value = RollTarget(
            symbol="AAPL",
            new_strike=170.0,
            new_expiration="20260220",
            new_premium_estimate=0.40,
            close_cost_estimate=0.15,
            net_credit_estimate=125.0,
            contracts=5,
        )
        mock_earnings.return_value = True

        decision = roll_manager.evaluate_roll(position, roll_count=0)
        assert decision.should_roll is True
        assert decision.target is not None
        assert decision.target.net_credit_estimate == 125.0

    @patch("src.services.roll_manager.RollManager._find_roll_target")
    @patch("src.services.roll_manager.RollManager._check_earnings_safe")
    def test_earnings_blocks_roll(self, mock_earnings, mock_target, roll_manager):
        """Earnings within new DTE → no roll."""
        position = make_position(dte=5, current_pnl_pct=0.50)

        mock_target.return_value = RollTarget(
            symbol="AAPL",
            new_strike=170.0,
            new_expiration="20260220",
            new_premium_estimate=0.40,
            close_cost_estimate=0.15,
            net_credit_estimate=125.0,
            contracts=5,
        )
        mock_earnings.return_value = False

        decision = roll_manager.evaluate_roll(position, roll_count=0)
        assert decision.should_roll is False
        assert "Earnings" in decision.reason


class TestRollTarget:
    """Test RollTarget dataclass."""

    def test_roll_target_fields(self):
        """Verify RollTarget fields."""
        target = RollTarget(
            symbol="AAPL",
            new_strike=170.0,
            new_expiration="20260220",
            new_premium_estimate=0.40,
            close_cost_estimate=0.15,
            net_credit_estimate=125.0,
            contracts=5,
        )
        assert target.symbol == "AAPL"
        assert target.net_credit_estimate == 125.0


class TestFindRollTarget:
    """Test _find_roll_target() with mocked IBKR."""

    def test_no_qualified_contract_returns_none(self, roll_manager):
        """Cannot qualify new contract → no target."""
        position = make_position()
        roll_manager.ibkr_client.get_option_contract.return_value = MagicMock()
        roll_manager.ibkr_client.qualify_contract.return_value = None

        target = roll_manager._find_roll_target(position)
        assert target is None

    def test_net_debit_returns_none(self, roll_manager):
        """New premium less than close cost → no target (net debit)."""
        position = make_position(current_premium=0.50)

        mock_contract = MagicMock()
        roll_manager.ibkr_client.get_option_contract.return_value = mock_contract
        roll_manager.ibkr_client.qualify_contract.return_value = mock_contract

        # Mock quote with low bid (new premium < close cost)
        mock_quote = MagicMock()
        mock_quote.is_valid = True
        mock_quote.bid = 0.30  # Less than current_premium of 0.50
        mock_quote.last = None

        with patch("asyncio.run", return_value=mock_quote):
            target = roll_manager._find_roll_target(position)

        assert target is None

    def test_net_credit_returns_target(self, roll_manager):
        """New premium more than close cost → viable target."""
        position = make_position(current_premium=0.15, contracts=5)

        mock_contract = MagicMock()
        roll_manager.ibkr_client.get_option_contract.return_value = mock_contract
        roll_manager.ibkr_client.qualify_contract.return_value = mock_contract

        mock_quote = MagicMock()
        mock_quote.is_valid = True
        mock_quote.bid = 0.40
        mock_quote.last = None

        with patch("asyncio.run", return_value=mock_quote):
            target = roll_manager._find_roll_target(position)

        assert target is not None
        assert target.new_premium_estimate == 0.40
        assert target.close_cost_estimate == 0.15
        assert target.net_credit_estimate == (0.40 - 0.15) * 5 * 100  # $125
