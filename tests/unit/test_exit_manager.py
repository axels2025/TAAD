"""Unit tests for ExitManager.

Tests automated exit decision-making and execution.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.config.baseline_strategy import BaselineStrategy, ExitRules
from src.execution.exit_manager import ExitDecision, ExitManager, ExitResult
from src.execution.position_monitor import PositionMonitor, PositionStatus
from src.tools.ibkr_client import IBKRClient


@pytest.fixture
def config():
    """Create test configuration."""
    return BaselineStrategy(
        exit_rules=ExitRules(
            profit_target=0.50,  # 50%
            stop_loss=-2.00,  # -200%
            time_exit_dte=3,
        )
    )


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    client = MagicMock(spec=IBKRClient)
    client.ib = MagicMock()

    # Mock get_option_contract
    client.get_option_contract.return_value = Mock()

    # Mock qualify_contract
    client.qualify_contract.return_value = Mock()

    return client


@pytest.fixture
def mock_position_monitor():
    """Create mock PositionMonitor."""
    monitor = MagicMock(spec=PositionMonitor)
    return monitor


@pytest.fixture
def exit_manager(mock_ibkr_client, mock_position_monitor, config):
    """Create ExitManager instance."""
    return ExitManager(
        ibkr_client=mock_ibkr_client,
        position_monitor=mock_position_monitor,
        config=config,
    )


@pytest.fixture
def profitable_position():
    """Create position with profit."""
    return PositionStatus(
        position_id="POS1",
        symbol="AAPL",
        strike=200.0,
        option_type="P",
        expiration_date="20260215",
        contracts=5,
        entry_premium=0.50,
        current_premium=0.25,  # 50% profit
        current_pnl=125.0,
        current_pnl_pct=0.50,
        days_held=5,
        dte=10,
    )


@pytest.fixture
def losing_position():
    """Create position with loss."""
    return PositionStatus(
        position_id="POS2",
        symbol="MSFT",
        strike=350.0,
        option_type="P",
        expiration_date="20260215",
        contracts=3,
        entry_premium=1.00,
        current_premium=3.00,  # 200% loss
        current_pnl=-600.0,
        current_pnl_pct=-2.00,
        days_held=3,
        dte=7,
    )


@pytest.fixture
def expiring_position():
    """Create position near expiration."""
    return PositionStatus(
        position_id="POS3",
        symbol="GOOGL",
        strike=150.0,
        option_type="P",
        expiration_date="20260215",
        contracts=2,
        entry_premium=0.75,
        current_premium=0.60,
        current_pnl=30.0,
        current_pnl_pct=0.20,
        days_held=7,
        dte=2,  # 2 DTE (below 3 DTE threshold)
    )


class TestFindTradeByPositionId:
    """Test _find_trade_by_position_id composite key lookup."""

    def test_finds_trade_by_composite_key(self, exit_manager):
        """Test finding trade via symbol/strike/expiration match."""
        from datetime import date
        mock_session = MagicMock()
        mock_trade = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_trade

        result = exit_manager._find_trade_by_position_id(
            mock_session, "AAPL_200.0_20260215_P"
        )

        assert result == mock_trade
        # Verify the query used composite filters, not trade_id
        call_args = mock_session.query.return_value.filter.call_args
        assert call_args is not None

    def test_falls_back_to_exact_trade_id(self, exit_manager):
        """Test fallback to exact trade_id for non-standard formats."""
        mock_session = MagicMock()
        mock_trade = Mock()
        # Composite query returns None, exact match returns trade
        mock_session.query.return_value.filter.return_value.first.side_effect = [
            None, mock_trade
        ]

        result = exit_manager._find_trade_by_position_id(mock_session, "AAPL_200.0_20260215_P")

        # Should have called filter twice (composite then fallback)
        assert mock_session.query.return_value.filter.call_count == 2

    def test_handles_malformed_position_id(self, exit_manager):
        """Test graceful handling of non-parseable position IDs."""
        mock_session = MagicMock()
        mock_trade = Mock()
        mock_session.query.return_value.filter.return_value.first.return_value = mock_trade

        # Only 2 parts — can't parse as composite key, falls back to exact match
        result = exit_manager._find_trade_by_position_id(mock_session, "T12345")

        assert result == mock_trade

    def test_returns_none_when_not_found(self, exit_manager):
        """Test returns None when trade doesn't exist."""
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None

        result = exit_manager._find_trade_by_position_id(mock_session, "FAKE_100.0_20260101_P")

        assert result is None


class TestExitManagerInitialization:
    """Test ExitManager initialization."""

    def test_initialization(
        self, exit_manager, mock_ibkr_client, mock_position_monitor, config
    ):
        """Test ExitManager initializes correctly."""
        assert exit_manager.ibkr_client == mock_ibkr_client
        assert exit_manager.position_monitor == mock_position_monitor
        assert exit_manager.config == config


class TestEvaluateExits:
    """Test exit evaluation logic."""

    def test_evaluate_exits_no_positions(
        self, exit_manager, mock_position_monitor
    ):
        """Test evaluating exits with no positions."""
        mock_position_monitor.get_all_positions.return_value = []

        decisions = exit_manager.evaluate_exits()

        assert decisions == {}

    def test_evaluate_exits_profit_target(
        self, exit_manager, mock_position_monitor, profitable_position
    ):
        """Test exit decision for profit target."""
        mock_position_monitor.get_all_positions.return_value = [profitable_position]

        decisions = exit_manager.evaluate_exits()

        assert "POS1" in decisions
        decision = decisions["POS1"]
        assert decision.should_exit
        assert decision.reason == "profit_target"
        assert decision.exit_type == "limit"
        assert decision.urgency == "medium"

    def test_evaluate_exits_stop_loss(
        self, exit_manager, mock_position_monitor, losing_position
    ):
        """Test exit decision for stop loss."""
        mock_position_monitor.get_all_positions.return_value = [losing_position]

        decisions = exit_manager.evaluate_exits()

        assert "POS2" in decisions
        decision = decisions["POS2"]
        assert decision.should_exit
        assert decision.reason == "stop_loss"
        assert decision.exit_type == "market"  # Market order for stop loss
        assert decision.urgency == "high"

    def test_evaluate_exits_time_exit(
        self, exit_manager, mock_position_monitor, expiring_position
    ):
        """Test exit decision for time exit."""
        mock_position_monitor.get_all_positions.return_value = [expiring_position]

        decisions = exit_manager.evaluate_exits()

        assert "POS3" in decisions
        decision = decisions["POS3"]
        assert decision.should_exit
        assert decision.reason == "time_exit"
        assert decision.exit_type == "limit"
        assert decision.urgency == "medium"

    def test_evaluate_exits_no_exit_needed(
        self, exit_manager, mock_position_monitor
    ):
        """Test when no exit is needed."""
        # Position with moderate profit, not at targets
        position = PositionStatus(
            position_id="POS4",
            symbol="AMZN",
            strike=180.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=0.80,
            current_pnl=60.0,
            current_pnl_pct=0.20,  # 20% profit (below 50% target)
            days_held=2,
            dte=10,  # Above 3 DTE
        )

        mock_position_monitor.get_all_positions.return_value = [position]

        decisions = exit_manager.evaluate_exits()

        assert "POS4" in decisions
        decision = decisions["POS4"]
        assert not decision.should_exit
        assert decision.reason == "holding"

    def test_evaluate_exits_multiple_positions(
        self, exit_manager, mock_position_monitor, profitable_position, losing_position
    ):
        """Test evaluating multiple positions."""
        mock_position_monitor.get_all_positions.return_value = [
            profitable_position,
            losing_position,
        ]

        decisions = exit_manager.evaluate_exits()

        assert len(decisions) == 2
        assert "POS1" in decisions
        assert "POS2" in decisions
        assert decisions["POS1"].reason == "profit_target"
        assert decisions["POS2"].reason == "stop_loss"


class TestShouldExitProfitTarget:
    """Test profit target exit logic."""

    def test_should_exit_at_target(self, exit_manager):
        """Test exit at exact profit target."""
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,  # Exactly 50%
            days_held=5,
            dte=10,
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "profit_target"

    def test_should_exit_above_target(self, exit_manager):
        """Test exit above profit target."""
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.20,
            current_pnl=150.0,
            current_pnl_pct=0.60,  # 60% (above 50% target)
            days_held=5,
            dte=10,
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "profit_target"

    def test_should_not_exit_below_target(self, exit_manager):
        """Test no exit below profit target."""
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.30,
            current_pnl=100.0,
            current_pnl_pct=0.40,  # 40% (below 50% target)
            days_held=5,
            dte=10,
        )

        decision = exit_manager._evaluate_position(position)

        assert not decision.should_exit


class TestShouldExitStopLoss:
    """Test stop loss exit logic."""

    def test_should_exit_at_stop_loss(self, exit_manager):
        """Test exit at exact stop loss."""
        position = PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=3.00,
            current_pnl=-600.0,
            current_pnl_pct=-2.00,  # Exactly -200%
            days_held=3,
            dte=7,
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "stop_loss"
        assert decision.exit_type == "market"

    def test_should_exit_below_stop_loss(self, exit_manager):
        """Test exit below stop loss (worse loss)."""
        position = PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=3.50,
            current_pnl=-750.0,
            current_pnl_pct=-2.50,  # -250% (worse than -200%)
            days_held=3,
            dte=7,
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "stop_loss"

    def test_should_not_exit_above_stop_loss(self, exit_manager):
        """Test no exit above stop loss."""
        position = PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=2.50,
            current_pnl=-450.0,
            current_pnl_pct=-1.50,  # -150% (above -200% stop)
            days_held=3,
            dte=7,
        )

        decision = exit_manager._evaluate_position(position)

        assert not decision.should_exit


class TestShouldExitTime:
    """Test time-based exit logic."""

    def test_should_exit_at_time_threshold(self, exit_manager):
        """Test exit at time threshold."""
        position = PositionStatus(
            position_id="POS3",
            symbol="GOOGL",
            strike=150.0,
            option_type="P",
            expiration_date="20260215",
            contracts=2,
            entry_premium=0.75,
            current_premium=0.60,
            current_pnl=30.0,
            current_pnl_pct=0.20,
            days_held=7,
            dte=3,  # Exactly 3 DTE
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "time_exit"

    def test_should_exit_below_time_threshold(self, exit_manager):
        """Test exit below time threshold."""
        position = PositionStatus(
            position_id="POS3",
            symbol="GOOGL",
            strike=150.0,
            option_type="P",
            expiration_date="20260215",
            contracts=2,
            entry_premium=0.75,
            current_premium=0.60,
            current_pnl=30.0,
            current_pnl_pct=0.20,
            days_held=7,
            dte=1,  # 1 DTE (below 3 DTE threshold)
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "time_exit"

    def test_should_not_exit_above_time_threshold(self, exit_manager):
        """Test no exit above time threshold."""
        position = PositionStatus(
            position_id="POS3",
            symbol="GOOGL",
            strike=150.0,
            option_type="P",
            expiration_date="20260215",
            contracts=2,
            entry_premium=0.75,
            current_premium=0.60,
            current_pnl=30.0,
            current_pnl_pct=0.20,
            days_held=3,
            dte=5,  # 5 DTE (above 3 DTE threshold)
        )

        decision = exit_manager._evaluate_position(position)

        assert not decision.should_exit


class TestExitPriority:
    """Test exit priority (profit > stop loss > time)."""

    def test_profit_target_takes_priority(self, exit_manager):
        """Test profit target takes priority over time exit."""
        # Position at profit target AND near expiration
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,  # At profit target
            days_held=5,
            dte=2,  # Below time threshold
        )

        decision = exit_manager._evaluate_position(position)

        # Should exit for profit, not time
        assert decision.should_exit
        assert decision.reason == "profit_target"

    def test_stop_loss_takes_priority_over_time(self, exit_manager):
        """Test stop loss takes priority over time exit."""
        # Position at stop loss AND near expiration
        position = PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=3.00,
            current_pnl=-600.0,
            current_pnl_pct=-2.00,  # At stop loss
            days_held=3,
            dte=2,  # Below time threshold
        )

        decision = exit_manager._evaluate_position(position)

        # Should exit for stop loss, not time
        assert decision.should_exit
        assert decision.reason == "stop_loss"


class TestExecuteExit:
    """Test exit execution."""

    @patch("src.data.database.get_db_session")
    def test_execute_exit_success(
        self, mock_get_db_session, exit_manager, mock_position_monitor, mock_ibkr_client
    ):
        """Test successful exit execution."""
        # Mock database session to avoid real DB queries
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_get_db_session.return_value.__enter__ = Mock(return_value=mock_session)
        mock_get_db_session.return_value.__exit__ = Mock(return_value=False)

        # Setup position
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
        )

        mock_position_monitor.update_position.return_value = position

        # Mock successful order placement via ibkr_client.place_order (async)
        mock_trade = Mock()
        mock_trade.order.orderId = 123
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.avgFillPrice = 0.26

        mock_ibkr_client.place_order = AsyncMock(return_value=mock_trade)
        mock_ibkr_client.sleep = AsyncMock(return_value=None)

        # Create exit decision
        decision = ExitDecision(
            should_exit=True,
            reason="profit_target",
            exit_type="limit",
            limit_price=0.26,
            urgency="medium",
        )

        result = exit_manager.execute_exit("POS1", decision)

        assert result.success
        assert result.position_id == "POS1"
        assert result.order_id == 123
        assert result.exit_price == 0.26
        assert result.exit_reason == "profit_target"

    def test_execute_exit_position_not_found(
        self, exit_manager, mock_position_monitor
    ):
        """Test exit when position not found."""
        mock_position_monitor.update_position.return_value = None

        decision = ExitDecision(
            should_exit=True,
            reason="profit_target",
            exit_type="limit",
        )

        result = exit_manager.execute_exit("INVALID_POS", decision)

        assert not result.success
        assert result.error_message == "Position not found"

    @patch("src.data.database.get_db_session")
    def test_execute_exit_order_rejected(
        self, mock_get_db_session, exit_manager, mock_position_monitor, mock_ibkr_client
    ):
        """Test exit when order is rejected."""
        # Mock database session to avoid real DB queries
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_get_db_session.return_value.__enter__ = Mock(return_value=mock_session)
        mock_get_db_session.return_value.__exit__ = Mock(return_value=False)

        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
        )

        mock_position_monitor.update_position.return_value = position

        # Mock rejected order via ibkr_client.place_order (async)
        mock_trade = Mock()
        mock_trade.orderStatus.status = "Cancelled"
        mock_trade.orderStatus.whyHeld = "Rejected"

        mock_ibkr_client.place_order = AsyncMock(return_value=mock_trade)
        mock_ibkr_client.sleep = AsyncMock(return_value=None)

        decision = ExitDecision(
            should_exit=True,
            reason="profit_target",
            exit_type="limit",
            limit_price=0.26,
        )

        result = exit_manager.execute_exit("POS1", decision)

        assert not result.success
        assert "Rejected" in result.error_message

    @patch("src.data.database.get_db_session")
    def test_execute_exit_handles_exception(
        self, mock_get_db_session, exit_manager, mock_position_monitor, mock_ibkr_client
    ):
        """Test exit handles exceptions."""
        # Mock database session to avoid real DB queries
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.first.return_value = None
        mock_get_db_session.return_value.__enter__ = Mock(return_value=mock_session)
        mock_get_db_session.return_value.__exit__ = Mock(return_value=False)

        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
        )

        mock_position_monitor.update_position.return_value = position
        mock_ibkr_client.place_order = AsyncMock(side_effect=Exception("Connection error"))

        decision = ExitDecision(
            should_exit=True,
            reason="profit_target",
            exit_type="limit",
        )

        result = exit_manager.execute_exit("POS1", decision)

        assert not result.success
        assert "Connection error" in result.error_message


class TestCreateExitOrder:
    """Test exit order creation."""

    def test_create_limit_order(self, exit_manager):
        """Test creating limit exit order."""
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
        )

        order = exit_manager._create_exit_order(position, "limit", 0.26)

        assert order.action == "BUY"
        assert order.totalQuantity == 5
        assert order.lmtPrice == 0.26

    def test_create_market_order(self, exit_manager):
        """Test creating market exit order."""
        position = PositionStatus(
            position_id="POS2",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=3.00,
            current_pnl=-600.0,
            current_pnl_pct=-2.00,
            days_held=3,
            dte=7,
        )

        order = exit_manager._create_exit_order(position, "market", None)

        assert order.action == "BUY"
        assert order.totalQuantity == 3

    def test_create_limit_order_default_price(self, exit_manager):
        """Test creating limit order with default price."""
        position = PositionStatus(
            position_id="POS1",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
        )

        order = exit_manager._create_exit_order(position, "limit", None)

        # Should use current premium * 1.01 as default
        assert order.action == "BUY"
        assert order.totalQuantity == 5
        assert abs(order.lmtPrice - 0.2525) < 0.01  # 0.25 * 1.01


class TestEmergencyExitAll:
    """Test emergency exit all positions."""

    def test_emergency_exit_all_positions(
        self, exit_manager, mock_position_monitor, mock_ibkr_client
    ):
        """Test emergency exit for all positions."""
        # Setup multiple positions
        positions = [
            PositionStatus(
                position_id=f"POS{i}",
                symbol="AAPL",
                strike=200.0,
                option_type="P",
                expiration_date="20260215",
                contracts=5,
                entry_premium=0.50,
                current_premium=0.40,
                current_pnl=50.0,
                current_pnl_pct=0.20,
                days_held=2,
                dte=10,
            )
            for i in range(3)
        ]

        mock_position_monitor.get_all_positions.return_value = positions
        mock_position_monitor.update_position.side_effect = positions

        # Mock successful orders
        mock_trade = Mock()
        mock_trade.order.orderId = 100
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.avgFillPrice = 0.41

        mock_ibkr_client.ib.placeOrder.return_value = mock_trade
        mock_ibkr_client.ib.sleep = Mock()

        results = exit_manager.emergency_exit_all()

        assert len(results) == 3
        for result in results:
            assert result.exit_reason == "emergency_exit"

    def test_emergency_exit_all_empty(
        self, exit_manager, mock_position_monitor
    ):
        """Test emergency exit with no positions."""
        mock_position_monitor.get_all_positions.return_value = []

        results = exit_manager.emergency_exit_all()

        assert len(results) == 0


class TestStaleDataGuard:
    """Test stale market data guard in exit evaluation."""

    def test_stale_data_skips_exit_evaluation(self, exit_manager):
        """Test that stale data returns reason='stale_data' and should_exit=False."""
        position = PositionStatus(
            position_id="POS_STALE",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.50,  # Fallback: same as entry
            current_pnl=0.0,
            current_pnl_pct=0.0,
            days_held=5,
            dte=10,
            market_data_stale=True,
        )

        decision = exit_manager._evaluate_position(position)

        assert not decision.should_exit
        assert decision.reason == "stale_data"
        assert "NO LIVE DATA" in decision.message
        assert "stop loss inactive" in decision.message.lower()

    def test_stale_data_blocks_stop_loss(self, exit_manager):
        """Test that stop loss does NOT trigger when data is stale, even with loss P&L."""
        # Simulate a scenario where entry premium was used as fallback
        # but somehow P&L was negative (shouldn't happen, but defense in depth)
        position = PositionStatus(
            position_id="POS_STALE_LOSS",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=3.00,
            current_pnl=-600.0,
            current_pnl_pct=-2.00,  # At stop loss threshold
            days_held=3,
            dte=7,
            market_data_stale=True,
        )

        decision = exit_manager._evaluate_position(position)

        # Stale guard should catch it BEFORE stop loss check
        assert not decision.should_exit
        assert decision.reason == "stale_data"

    def test_stale_data_blocks_profit_target(self, exit_manager):
        """Test that profit target does NOT trigger when data is stale."""
        position = PositionStatus(
            position_id="POS_STALE_PROFIT",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
            market_data_stale=True,
        )

        decision = exit_manager._evaluate_position(position)

        assert not decision.should_exit
        assert decision.reason == "stale_data"

    def test_live_data_stop_loss_still_triggers(self, exit_manager):
        """Test that stop loss triggers normally when data is NOT stale."""
        position = PositionStatus(
            position_id="POS_LIVE",
            symbol="MSFT",
            strike=350.0,
            option_type="P",
            expiration_date="20260215",
            contracts=3,
            entry_premium=1.00,
            current_premium=3.00,
            current_pnl=-600.0,
            current_pnl_pct=-2.00,
            days_held=3,
            dte=7,
            market_data_stale=False,
        )

        decision = exit_manager._evaluate_position(position)

        assert decision.should_exit
        assert decision.reason == "stop_loss"

    def test_default_market_data_stale_is_false(self):
        """Test that PositionStatus defaults to market_data_stale=False."""
        position = PositionStatus(
            position_id="POS_DEFAULT",
            symbol="AAPL",
            strike=200.0,
            option_type="P",
            expiration_date="20260215",
            contracts=5,
            entry_premium=0.50,
            current_premium=0.25,
            current_pnl=125.0,
            current_pnl_pct=0.50,
            days_held=5,
            dte=10,
        )

        assert position.market_data_stale is False


class TestExitDecisionDataclass:
    """Test ExitDecision dataclass."""

    def test_exit_decision_creation(self):
        """Test creating exit decision."""
        decision = ExitDecision(
            should_exit=True,
            reason="profit_target",
            exit_type="limit",
            limit_price=0.26,
            urgency="medium",
            message="Profit target reached",
        )

        assert decision.should_exit
        assert decision.reason == "profit_target"
        assert decision.exit_type == "limit"
        assert decision.limit_price == 0.26
        assert decision.urgency == "medium"

    def test_exit_decision_defaults(self):
        """Test exit decision default values."""
        decision = ExitDecision(
            should_exit=False,
            reason="holding",
        )

        assert decision.exit_type == "limit"
        assert decision.limit_price is None
        assert decision.urgency == "low"


class TestExitResultDataclass:
    """Test ExitResult dataclass."""

    def test_exit_result_success(self):
        """Test creating successful exit result."""
        result = ExitResult(
            success=True,
            position_id="POS1",
            order_id=123,
            exit_price=0.26,
            exit_reason="profit_target",
        )

        assert result.success
        assert result.position_id == "POS1"
        assert result.order_id == 123
        assert result.exit_price == 0.26
        assert result.error_message is None

    def test_exit_result_failure(self):
        """Test creating failed exit result."""
        result = ExitResult(
            success=False,
            position_id="POS1",
            exit_reason="profit_target",
            error_message="Order rejected",
        )

        assert not result.success
        assert result.order_id is None
        assert result.exit_price is None
        assert result.error_message == "Order rejected"
