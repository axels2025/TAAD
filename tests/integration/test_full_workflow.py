"""Integration test for full autonomous trading workflow.

Tests all 4 components working together:
- OrderExecutor: Places trades
- PositionMonitor: Tracks positions
- ExitManager: Manages exits
- RiskGovernor: Enforces risk limits

Workflow:
1. RiskGovernor checks trade opportunity
2. OrderExecutor places order (if approved)
3. PositionMonitor tracks open position
4. ExitManager evaluates exit signals
5. OrderExecutor closes position (exit)
"""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

import pytest

from src.config.base import Config
from src.config.baseline_strategy import BaselineStrategy, ExitRules
from src.execution.exit_manager import ExitManager
from src.execution.order_executor import OrderExecutor
from src.execution.position_monitor import PositionMonitor
from src.execution.risk_governor import RiskGovernor
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient


@pytest.fixture
def config():
    """Create test configuration."""
    return Config()


@pytest.fixture
def strategy_config():
    """Create strategy configuration."""
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

    # Mock account summary
    client.get_account_summary.return_value = {
        "NetLiquidation": 100000.0,
        "AvailableFunds": 80000.0,
        "BuyingPower": 200000.0,
    }

    # Mock get_option_contract
    mock_contract = Mock()
    mock_contract.symbol = "AAPL"
    mock_contract.strike = 200.0
    mock_contract.lastTradeDateOrContractMonth = "20260130"
    mock_contract.right = "P"
    client.get_option_contract.return_value = mock_contract

    # Mock qualify_contract
    client.qualify_contract.return_value = mock_contract

    # Mock market data
    mock_ticker = Mock()
    mock_ticker.bid = 0.49
    mock_ticker.ask = 0.51
    mock_ticker.last = 0.50
    mock_ticker.modelGreeks = Mock()
    mock_ticker.modelGreeks.delta = -0.25
    mock_ticker.modelGreeks.theta = 0.05
    mock_ticker.modelGreeks.gamma = 0.02
    mock_ticker.modelGreeks.vega = 0.10
    client.get_market_data.return_value = {
        "bid": 0.49,
        "ask": 0.51,
        "last": 0.50,
        "close": 0.48,
    }

    client.ib.reqMktData.return_value = mock_ticker
    client.ib.sleep = Mock()

    return client


@pytest.fixture
def order_executor(mock_ibkr_client, config):
    """Create OrderExecutor instance."""
    return OrderExecutor(
        ibkr_client=mock_ibkr_client,
        config=config,
        dry_run=False,  # Real mode for integration test
    )


@pytest.fixture
def position_monitor(mock_ibkr_client, strategy_config):
    """Create PositionMonitor instance."""
    return PositionMonitor(
        ibkr_client=mock_ibkr_client,
        config=strategy_config,
        update_interval_minutes=15,
    )


@pytest.fixture
def exit_manager(mock_ibkr_client, position_monitor, strategy_config):
    """Create ExitManager instance."""
    return ExitManager(
        ibkr_client=mock_ibkr_client,
        position_monitor=position_monitor,
        config=strategy_config,
    )


@pytest.fixture
def risk_governor(mock_ibkr_client, position_monitor, config):
    """Create RiskGovernor instance."""
    return RiskGovernor(
        ibkr_client=mock_ibkr_client,
        position_monitor=position_monitor,
        config=config,
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
        reasoning="Test trade opportunity for integration testing",
        margin_required=1000.0,
    )


class TestFullWorkflow:
    """Test complete trading workflow."""

    def test_successful_entry_workflow(
        self,
        order_executor,
        risk_governor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test successful trade entry workflow.

        Workflow:
        1. RiskGovernor validates trade
        2. OrderExecutor places order
        3. Order is filled
        """
        # Step 1: Risk check
        risk_check = risk_governor.pre_trade_check(sample_opportunity)

        assert risk_check.approved, f"Risk check failed: {risk_check.reason}"

        # Step 2: Place order
        mock_trade = Mock()
        mock_trade.order.orderId = 100
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.avgFillPrice = 0.50
        mock_trade.orderStatus.filled = 5

        mock_ibkr_client.ib.placeOrder.return_value = mock_trade

        result = order_executor.execute_trade(
            opportunity=sample_opportunity,
            order_type="LIMIT",
            limit_price=0.50,
        )

        # Step 3: Verify execution
        assert result.success, f"Order execution failed: {result.error_message}"
        assert result.order_id == 100
        assert result.filled_quantity == 5

        # Step 4: Record trade in risk governor
        risk_governor.record_trade(sample_opportunity)

        assert risk_governor._trades_today == 1

    def test_risk_rejection_workflow(
        self,
        risk_governor,
        position_monitor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test trade rejection by risk governor.

        Workflow:
        1. Set up positions exceeding daily loss limit
        2. RiskGovernor rejects new trade
        3. No order is placed
        """
        # Step 1: Setup positions with large loss
        mock_ib_position = Mock()
        mock_ib_position.contract = Mock()
        mock_ib_position.contract.symbol = "MSFT"
        mock_ib_position.contract.strike = 350.0
        mock_ib_position.contract.lastTradeDateOrContractMonth = "20260130"
        mock_ib_position.contract.right = "P"
        mock_ib_position.position = -10
        mock_ib_position.avgCost = -1.00

        # Mock ticker with large loss
        mock_ticker = Mock()
        mock_ticker.bid = 3.00
        mock_ticker.ask = 3.02
        mock_ticker.modelGreeks = None

        mock_ibkr_client.ib.positions.return_value = [mock_ib_position]
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker

        # Step 2: Risk check should fail
        risk_check = risk_governor.pre_trade_check(sample_opportunity)

        assert not risk_check.approved
        assert risk_check.limit_name == "daily_loss"

        # Step 3: Verify trading halted
        assert risk_governor.is_halted()

    def test_position_monitoring_workflow(
        self,
        order_executor,
        position_monitor,
        risk_governor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test position monitoring after entry.

        Workflow:
        1. Place trade
        2. PositionMonitor finds open position
        3. Calculate P&L
        4. Check for alerts
        """
        # Step 1: Place trade
        risk_check = risk_governor.pre_trade_check(sample_opportunity)
        assert risk_check.approved

        mock_trade = Mock()
        mock_trade.order.orderId = 101
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.avgFillPrice = 0.50
        mock_trade.orderStatus.filled = 5

        mock_ibkr_client.ib.placeOrder.return_value = mock_trade

        result = order_executor.execute_trade(
            opportunity=sample_opportunity,
            order_type="LIMIT",
            limit_price=0.50,
        )

        assert result.success

        # Step 2: Setup IBKR to return position
        mock_ib_position = Mock()
        mock_ib_position.contract = Mock()
        mock_ib_position.contract.symbol = "AAPL"
        mock_ib_position.contract.strike = 200.0
        mock_ib_position.contract.lastTradeDateOrContractMonth = (
            datetime.now() + timedelta(days=10)
        ).strftime("%Y%m%d")
        mock_ib_position.contract.right = "P"
        mock_ib_position.position = -5
        mock_ib_position.avgCost = -0.50

        # Mock current price showing profit
        mock_ticker = Mock()
        mock_ticker.bid = 0.24
        mock_ticker.ask = 0.26  # Mid = 0.25 (50% profit!)
        mock_ticker.modelGreeks = Mock()
        mock_ticker.modelGreeks.delta = -0.20
        mock_ticker.modelGreeks.theta = 0.06
        mock_ticker.modelGreeks.gamma = 0.01
        mock_ticker.modelGreeks.vega = 0.08

        mock_ibkr_client.ib.positions.return_value = [mock_ib_position]
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker

        # Step 3: Monitor positions
        positions = position_monitor.get_all_positions()

        assert len(positions) == 1
        position = positions[0]

        assert position.symbol == "AAPL"
        assert position.contracts == 5
        assert position.entry_premium == 0.50
        assert abs(position.current_premium - 0.25) < 0.01
        assert position.current_pnl > 0  # Profitable

        # Step 4: Check alerts
        alerts = position_monitor.check_alerts()

        # Should have profit target alert (at 50%)
        profit_alerts = [a for a in alerts if a.alert_type == "profit_target"]
        assert len(profit_alerts) > 0

    def test_exit_decision_workflow(
        self,
        exit_manager,
        position_monitor,
        mock_ibkr_client,
    ):
        """Test exit decision making.

        Workflow:
        1. Setup position at profit target
        2. ExitManager evaluates exits
        3. Exit decision generated
        """
        # Step 1: Setup position at profit target
        mock_ib_position = Mock()
        mock_ib_position.contract = Mock()
        mock_ib_position.contract.symbol = "AAPL"
        mock_ib_position.contract.strike = 200.0
        mock_ib_position.contract.lastTradeDateOrContractMonth = (
            datetime.now() + timedelta(days=10)
        ).strftime("%Y%m%d")
        mock_ib_position.contract.right = "P"
        mock_ib_position.position = -5
        mock_ib_position.avgCost = -0.50

        # At profit target
        mock_ticker = Mock()
        mock_ticker.bid = 0.24
        mock_ticker.ask = 0.26
        mock_ticker.modelGreeks = None

        mock_ibkr_client.ib.positions.return_value = [mock_ib_position]
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker

        # Step 2: Evaluate exits
        decisions = exit_manager.evaluate_exits()

        assert len(decisions) > 0

        # Step 3: Verify exit decision
        position_id = list(decisions.keys())[0]
        decision = decisions[position_id]

        assert decision.should_exit
        assert decision.reason == "profit_target"
        assert decision.exit_type == "limit"

    def test_complete_trade_lifecycle(
        self,
        order_executor,
        position_monitor,
        exit_manager,
        risk_governor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test complete trade lifecycle from entry to exit.

        Complete Workflow:
        1. Risk check → Approve
        2. Place entry order → Filled
        3. Monitor position → Profitable
        4. Exit decision → Profit target
        5. Place exit order → Filled
        """
        # ============================================================
        # PHASE 1: ENTRY
        # ============================================================

        # Step 1: Risk check
        risk_check = risk_governor.pre_trade_check(sample_opportunity)
        assert risk_check.approved

        # Step 2: Place entry order
        mock_entry_trade = Mock()
        mock_entry_trade.order.orderId = 200
        mock_entry_trade.orderStatus.status = "Filled"
        mock_entry_trade.orderStatus.avgFillPrice = 0.50
        mock_entry_trade.orderStatus.filled = 5

        mock_ibkr_client.ib.placeOrder.return_value = mock_entry_trade

        entry_result = order_executor.execute_trade(
            opportunity=sample_opportunity,
            order_type="LIMIT",
            limit_price=0.50,
        )

        assert entry_result.success
        assert entry_result.order_id == 200

        # Record trade
        risk_governor.record_trade(sample_opportunity)

        # ============================================================
        # PHASE 2: MONITORING
        # ============================================================

        # Step 3: Setup open position
        mock_ib_position = Mock()
        mock_ib_position.contract = Mock()
        mock_ib_position.contract.symbol = "AAPL"
        mock_ib_position.contract.strike = 200.0
        mock_ib_position.contract.lastTradeDateOrContractMonth = (
            datetime.now() + timedelta(days=10)
        ).strftime("%Y%m%d")
        mock_ib_position.contract.right = "P"
        mock_ib_position.position = -5
        mock_ib_position.avgCost = -0.50

        # Position has reached profit target
        mock_ticker_monitor = Mock()
        mock_ticker_monitor.bid = 0.24
        mock_ticker_monitor.ask = 0.26  # 50% profit
        mock_ticker_monitor.modelGreeks = None

        mock_ibkr_client.ib.positions.return_value = [mock_ib_position]
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker_monitor

        positions = position_monitor.get_all_positions()
        assert len(positions) == 1

        position = positions[0]
        assert position.current_pnl > 0

        # ============================================================
        # PHASE 3: EXIT DECISION
        # ============================================================

        # Step 4: Evaluate exits
        decisions = exit_manager.evaluate_exits()
        assert len(decisions) > 0

        position_id = position.position_id
        decision = decisions[position_id]

        assert decision.should_exit
        assert decision.reason == "profit_target"

        # ============================================================
        # PHASE 4: EXIT EXECUTION
        # ============================================================

        # Step 5: Execute exit
        mock_exit_trade = Mock()
        mock_exit_trade.order.orderId = 201
        mock_exit_trade.orderStatus.status = "Filled"
        mock_exit_trade.orderStatus.avgFillPrice = 0.25

        mock_ibkr_client.ib.placeOrder.return_value = mock_exit_trade

        # Mock position update
        position_monitor.update_position = Mock(return_value=position)

        exit_result = exit_manager.execute_exit(position_id, decision)

        assert exit_result.success
        assert exit_result.order_id == 201
        assert exit_result.exit_reason == "profit_target"

        # ============================================================
        # VERIFICATION
        # ============================================================

        # Verify complete lifecycle
        assert entry_result.success  # Entry successful
        assert len(positions) == 1  # Position tracked
        assert decision.should_exit  # Exit signal generated
        assert exit_result.success  # Exit successful

        # Verify risk tracking
        assert risk_governor._trades_today == 1


class TestErrorHandling:
    """Test error handling across components."""

    def test_order_failure_doesnt_break_workflow(
        self,
        order_executor,
        risk_governor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test workflow continues after order failure."""
        # Risk check passes
        risk_check = risk_governor.pre_trade_check(sample_opportunity)
        assert risk_check.approved

        # Order placement fails
        mock_ibkr_client.ib.placeOrder.side_effect = Exception("Connection error")

        result = order_executor.execute_trade(
            opportunity=sample_opportunity,
            order_type="LIMIT",
            limit_price=0.50,
        )

        # Order fails gracefully
        assert not result.success
        assert result.error_message is not None

        # Risk governor state unchanged (trade not recorded)
        assert risk_governor._trades_today == 0

    def test_position_monitor_handles_no_positions(
        self, position_monitor, mock_ibkr_client
    ):
        """Test position monitor with no positions."""
        mock_ibkr_client.ib.positions.return_value = []

        positions = position_monitor.get_all_positions()

        assert positions == []

        alerts = position_monitor.check_alerts()

        assert alerts == []

    def test_exit_manager_handles_missing_position(
        self, exit_manager, position_monitor
    ):
        """Test exit manager when position not found."""
        position_monitor.update_position = Mock(return_value=None)

        from src.execution.exit_manager import ExitDecision

        decision = ExitDecision(
            should_exit=True,
            reason="profit_target",
            exit_type="limit",
        )

        result = exit_manager.execute_exit("INVALID_POS", decision)

        assert not result.success
        assert "not found" in result.error_message.lower()


class TestRiskEnforcement:
    """Test risk enforcement throughout workflow."""

    def test_daily_loss_halts_new_trades(
        self,
        order_executor,
        risk_governor,
        position_monitor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test daily loss limit halts new trades."""
        # Setup large loss
        mock_ib_position = Mock()
        mock_ib_position.contract = Mock()
        mock_ib_position.contract.symbol = "MSFT"
        mock_ib_position.contract.strike = 350.0
        mock_ib_position.contract.lastTradeDateOrContractMonth = "20260130"
        mock_ib_position.contract.right = "P"
        mock_ib_position.position = -20
        mock_ib_position.avgCost = -1.00

        mock_ticker = Mock()
        mock_ticker.bid = 2.50
        mock_ticker.ask = 2.52
        mock_ticker.modelGreeks = None

        mock_ibkr_client.ib.positions.return_value = [mock_ib_position]
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker

        # Risk check fails
        risk_check = risk_governor.pre_trade_check(sample_opportunity)

        assert not risk_check.approved
        assert risk_governor.is_halted()

        # Verify no order placed
        # (in real workflow, order_executor wouldn't be called)

    def test_max_positions_enforced(
        self,
        risk_governor,
        position_monitor,
        sample_opportunity,
        mock_ibkr_client,
    ):
        """Test max positions limit enforced."""
        # Setup 10 positions (at limit)
        positions = []
        for i in range(10):
            pos = Mock()
            pos.contract = Mock()
            pos.contract.symbol = f"STOCK{i}"
            pos.contract.strike = 100.0
            pos.contract.lastTradeDateOrContractMonth = "20260130"
            pos.contract.right = "P"
            pos.position = -1
            pos.avgCost = -0.50
            positions.append(pos)

        mock_ibkr_client.ib.positions.return_value = positions

        # Mock ticker
        mock_ticker = Mock()
        mock_ticker.bid = 0.40
        mock_ticker.ask = 0.42
        mock_ticker.modelGreeks = None
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker

        # Risk check fails
        risk_check = risk_governor.pre_trade_check(sample_opportunity)

        assert not risk_check.approved
        assert risk_check.limit_name == "max_positions"


class TestDataFlow:
    """Test data flows correctly between components."""

    def test_position_data_flows_to_exit_manager(
        self,
        position_monitor,
        exit_manager,
        mock_ibkr_client,
    ):
        """Test position data flows from monitor to exit manager."""
        # Setup position
        mock_ib_position = Mock()
        mock_ib_position.contract = Mock()
        mock_ib_position.contract.symbol = "AAPL"
        mock_ib_position.contract.strike = 200.0
        mock_ib_position.contract.lastTradeDateOrContractMonth = (
            datetime.now() + timedelta(days=10)
        ).strftime("%Y%m%d")
        mock_ib_position.contract.right = "P"
        mock_ib_position.position = -5
        mock_ib_position.avgCost = -0.50

        mock_ticker = Mock()
        mock_ticker.bid = 0.24
        mock_ticker.ask = 0.26
        mock_ticker.modelGreeks = None

        mock_ibkr_client.ib.positions.return_value = [mock_ib_position]
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker

        # Get positions from monitor
        positions = position_monitor.get_all_positions()
        assert len(positions) == 1

        # Exit manager uses same position data
        decisions = exit_manager.evaluate_exits()
        assert len(decisions) == 1

        # Data matches
        position = positions[0]
        decision_key = list(decisions.keys())[0]

        assert position.position_id == decision_key

    def test_risk_state_persists_across_trades(
        self, risk_governor, sample_opportunity
    ):
        """Test risk governor maintains state across trades."""
        assert risk_governor._trades_today == 0

        # Record multiple trades
        for i in range(3):
            risk_governor.record_trade(sample_opportunity)

        assert risk_governor._trades_today == 3

        # Check still enforces limit
        for i in range(7):
            risk_governor.record_trade(sample_opportunity)

        assert risk_governor._trades_today == 10

        # Next trade should be rejected
        risk_check = risk_governor.pre_trade_check(sample_opportunity)

        assert not risk_check.approved
        assert risk_check.limit_name == "max_trades_per_day"
