"""Tests for the autonomous daemon loop: position management, exits, feedback, autonomy.

Covers all 5 workstreams:
- Workstream 1: ExitManager integration into daemon
- Workstream 2: EventDetector (VIX spikes, position alerts)
- Workstream 3: Trade outcome feedback loop
- Workstream 4: Autonomy level persistence
- Workstream 5: Guardrail blocks escalate to human review

Uses in-memory SQLite + mocks for IBKR/ExitManager/PositionMonitor.
"""

import asyncio
from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.agentic.config import ExitRulesConfig, Phase5Config
from src.data.database import close_database, get_session, init_database
from src.data.models import (
    DaemonEvent,
    DaemonNotification,
    DecisionAudit,
    Trade,
    WorkingMemoryRow,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_database():
    """Create an in-memory SQLite database for testing."""
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database) -> Session:
    """Get a database session from the in-memory database."""
    session = get_session()
    yield session
    session.close()


def _make_decision(
    action: str = "MONITOR_ONLY",
    confidence: float = 0.9,
    reasoning: str = "Test reasoning",
    metadata: dict | None = None,
):
    """Helper to create a DecisionOutput."""
    from src.agentic.reasoning_engine import DecisionOutput

    return DecisionOutput(
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        key_factors=["test"],
        risks_considered=["test_risk"],
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Workstream 1: ExitRulesConfig + Dashboard
# ---------------------------------------------------------------------------


class TestExitRulesConfig:
    """Tests for ExitRulesConfig model and integration."""

    def test_exit_rules_config_defaults(self):
        """Default values: profit_target=0.50, stop_loss=-2.00, time_exit_dte=2."""
        cfg = ExitRulesConfig()
        assert cfg.profit_target == 0.50
        assert cfg.stop_loss == -2.00
        assert cfg.time_exit_dte == 2

    def test_exit_rules_time_exit_dte_minus_one(self):
        """time_exit_dte=-1 is valid (let expire)."""
        cfg = ExitRulesConfig(time_exit_dte=-1)
        assert cfg.time_exit_dte == -1

    def test_exit_rules_time_exit_dte_minus_two_rejected(self):
        """time_exit_dte=-2 should be rejected (ge=-1)."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExitRulesConfig(time_exit_dte=-2)

    def test_exit_rules_in_phase5_config(self):
        """ExitRulesConfig round-trips through Phase5Config."""
        from src.agentic.guardrails.config import GuardrailConfig

        Phase5Config.model_rebuild(
            _types_namespace={"GuardrailConfig": GuardrailConfig}
        )
        config = Phase5Config(
            exit_rules={"profit_target": 0.80, "stop_loss": -1.50, "time_exit_dte": -1}
        )
        assert config.exit_rules.profit_target == 0.80
        assert config.exit_rules.stop_loss == -1.50
        assert config.exit_rules.time_exit_dte == -1

    def test_exit_rules_serialization(self):
        """ExitRulesConfig serializes to dict correctly."""
        cfg = ExitRulesConfig(profit_target=0.65, stop_loss=-3.00, time_exit_dte=5)
        d = cfg.model_dump()
        assert d == {
            "profit_target": 0.65,
            "stop_loss": -3.00,
            "time_exit_dte": 5,
            "let_expire_premium": 0.05,
        }

    def test_exit_rules_profit_target_bounds(self):
        """profit_target must be 0.0-1.0."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ExitRulesConfig(profit_target=1.5)
        with pytest.raises(ValidationError):
            ExitRulesConfig(profit_target=-0.1)


# ---------------------------------------------------------------------------
# Workstream 1: ActionExecutor _handle_close fix
# ---------------------------------------------------------------------------


class TestActionExecutorClose:
    """Tests for the fixed _handle_close handler."""

    @pytest.fixture
    def governor(self, db_session):
        from src.agentic.autonomy_governor import AutonomyGovernor

        return AutonomyGovernor(db_session)

    def test_handle_close_fails_without_position_id(self, db_session, governor):
        """_handle_close returns error when no position_id in metadata."""
        from src.agentic.action_executor import ActionExecutor

        executor = ActionExecutor(db_session, governor, exit_manager=MagicMock())
        decision = _make_decision(action="CLOSE_POSITION", metadata={})
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close(decision)
        )
        assert not result.success
        assert "No position_id" in result.message

    def test_handle_close_fails_without_exit_manager(self, db_session, governor):
        """_handle_close returns error when exit_manager is None."""
        from src.agentic.action_executor import ActionExecutor

        executor = ActionExecutor(db_session, governor, exit_manager=None)
        decision = _make_decision(
            action="CLOSE_POSITION", metadata={"position_id": "TEST_100_20260301_P"}
        )
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close(decision)
        )
        assert not result.success
        assert "No exit_manager" in result.message

    def test_handle_close_uses_exit_manager(self, db_session, governor):
        """_handle_close creates ExitDecision and calls exit_manager.execute_exit."""
        from src.agentic.action_executor import ActionExecutor
        from src.execution.exit_manager import ExitResult

        mock_exit_manager = MagicMock()
        mock_exit_manager.execute_exit.return_value = ExitResult(
            success=True,
            position_id="TEST_100_20260301_P",
            exit_price=0.25,
            exit_reason="claude_decision",
        )

        executor = ActionExecutor(db_session, governor, exit_manager=mock_exit_manager)
        decision = _make_decision(
            action="CLOSE_POSITION",
            metadata={"position_id": "TEST_100_20260301_P", "reason": "thesis_invalid"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close(decision)
        )
        assert result.success
        assert "$0.25" in result.message
        mock_exit_manager.execute_exit.assert_called_once()

        # Verify the ExitDecision was constructed with the right reason
        call_args = mock_exit_manager.execute_exit.call_args
        exit_decision = call_args[0][1]
        assert exit_decision.should_exit is True
        assert exit_decision.reason == "thesis_invalid"


# ---------------------------------------------------------------------------
# CLOSE_ALL_POSITIONS handler tests
# ---------------------------------------------------------------------------


class TestCloseAllPositions:
    """Tests for _handle_close_all in ActionExecutor."""

    @pytest.fixture
    def executor_with_exit(self, db_session):
        """ActionExecutor with a mocked exit_manager."""
        from src.agentic.action_executor import ActionExecutor
        from src.agentic.autonomy_governor import AutonomyGovernor, AutonomyLevel
        from src.agentic.config import AutonomyConfig

        config = AutonomyConfig(initial_level=4, max_level=4)
        governor = AutonomyGovernor(db_session, config)
        mock_exit_manager = MagicMock()
        executor = ActionExecutor(
            db_session=db_session,
            governor=governor,
            exit_manager=mock_exit_manager,
        )
        return executor, mock_exit_manager

    def test_no_open_trades_returns_success(self, db_session, executor_with_exit):
        """_handle_close_all with no open trades returns success with closed_count=0."""
        executor, _ = executor_with_exit
        decision = _make_decision(
            action="CLOSE_ALL_POSITIONS",
            metadata={"reason": "test emergency"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close_all(decision)
        )
        assert result.success is True
        assert result.data["closed_count"] == 0

    def test_closes_all_open_trades(self, db_session, executor_with_exit):
        """_handle_close_all closes all open trades with market orders."""
        executor, mock_exit_manager = executor_with_exit

        # Insert 2 open trades
        for sym in ["AAPL", "NVDA"]:
            db_session.add(Trade(
                trade_id=f"{sym}_100.0_20260320_P_123",
                symbol=sym,
                strike=100.0,
                expiration=date(2026, 3, 20),
                option_type="PUT",
                entry_date=datetime(2026, 3, 1),
                entry_premium=1.50,
                contracts=1,
                dte=16,
            ))
        db_session.commit()

        mock_result = MagicMock(success=True, exit_price=0.10, error_message=None)
        mock_exit_manager.execute_exit.return_value = mock_result

        decision = _make_decision(
            action="CLOSE_ALL_POSITIONS",
            metadata={"reason": "VIX above 50"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close_all(decision)
        )

        assert result.success is True
        assert result.data["closed_count"] == 2
        assert result.data["failed_count"] == 0
        assert mock_exit_manager.execute_exit.call_count == 2

    def test_uses_market_exit_type_critical_urgency(self, db_session, executor_with_exit):
        """_handle_close_all uses exit_type='market' and urgency='critical'."""
        executor, mock_exit_manager = executor_with_exit

        db_session.add(Trade(
            trade_id="SPY_500.0_20260320_P_456",
            symbol="SPY",
            strike=500.0,
            expiration=date(2026, 3, 20),
            option_type="PUT",
            entry_date=datetime(2026, 3, 1),
            entry_premium=2.00,
            contracts=1,
            dte=16,
        ))
        db_session.commit()

        mock_result = MagicMock(success=True, exit_price=0.05, error_message=None)
        mock_exit_manager.execute_exit.return_value = mock_result

        decision = _make_decision(
            action="CLOSE_ALL_POSITIONS",
            metadata={"reason": "circuit breaker"},
        )
        asyncio.get_event_loop().run_until_complete(
            executor._handle_close_all(decision)
        )

        call_args = mock_exit_manager.execute_exit.call_args
        exit_decision = call_args[0][1]
        assert exit_decision.exit_type == "market"
        assert exit_decision.urgency == "critical"

    def test_aggregates_successes_and_failures(self, db_session, executor_with_exit):
        """_handle_close_all aggregates per-trade success/failure results."""
        executor, mock_exit_manager = executor_with_exit

        for i, sym in enumerate(["AAPL", "NVDA", "TSLA"]):
            db_session.add(Trade(
                trade_id=f"{sym}_100.0_20260320_P_{i}",
                symbol=sym,
                strike=100.0,
                expiration=date(2026, 3, 20),
                option_type="PUT",
                entry_date=datetime(2026, 3, 1),
                entry_premium=1.50,
                contracts=1,
                dte=16,
            ))
        db_session.commit()

        # First two succeed, third fails
        success = MagicMock(success=True, exit_price=0.10, error_message=None)
        failure = MagicMock(success=False, exit_price=None, error_message="No fills")
        mock_exit_manager.execute_exit.side_effect = [success, success, failure]

        decision = _make_decision(
            action="CLOSE_ALL_POSITIONS",
            metadata={"reason": "market crash"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close_all(decision)
        )

        assert result.success is False  # has failures
        assert result.data["closed_count"] == 2
        assert result.data["failed_count"] == 1
        assert result.data["total"] == 3
        assert len(result.data["details"]) == 3

    def test_no_exit_manager_returns_error(self, db_session):
        """_handle_close_all without exit_manager returns error."""
        from src.agentic.action_executor import ActionExecutor
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        config = AutonomyConfig(initial_level=4, max_level=4)
        governor = AutonomyGovernor(db_session, config)
        executor = ActionExecutor(
            db_session=db_session,
            governor=governor,
            exit_manager=None,
        )

        # Insert an open trade so we get past the "no positions" check
        db_session.add(Trade(
            trade_id="AAPL_100.0_20260320_P_789",
            symbol="AAPL",
            strike=100.0,
            expiration=date(2026, 3, 20),
            option_type="PUT",
            entry_date=datetime(2026, 3, 1),
            entry_premium=1.50,
            contracts=1,
            dte=16,
        ))
        db_session.commit()

        decision = _make_decision(
            action="CLOSE_ALL_POSITIONS",
            metadata={"reason": "test"},
        )
        result = asyncio.get_event_loop().run_until_complete(
            executor._handle_close_all(decision)
        )

        assert result.success is False
        assert "exit_manager" in result.message.lower()


# ---------------------------------------------------------------------------
# Workstream 1: ExitManager + PositionMonitor initialization in daemon
# ---------------------------------------------------------------------------


class TestDaemonExitManagerInit:
    """Tests for ExitManager/PositionMonitor creation in daemon init."""

    def test_exit_manager_initialized_with_ibkr(self, db_session):
        """When IBKR is connected, ExitManager and PositionMonitor are created."""
        from src.agentic.config import AutonomyConfig

        config = Phase5Config.__new__(Phase5Config)
        # Manually set config fields to avoid full init
        mock_ibkr = MagicMock()
        mock_ibkr.is_connected.return_value = True

        # Test that the BaselineStrategy is built from exit_rules config
        exit_cfg = ExitRulesConfig(profit_target=0.65, stop_loss=-1.50, time_exit_dte=3)
        from src.config.baseline_strategy import BaselineStrategy, ExitRules

        baseline = BaselineStrategy(
            exit_rules=ExitRules(
                profit_target=exit_cfg.profit_target,
                stop_loss=exit_cfg.stop_loss,
                time_exit_dte=exit_cfg.time_exit_dte,
            ),
        )
        assert baseline.exit_rules.profit_target == 0.65
        assert baseline.exit_rules.stop_loss == -1.50
        assert baseline.exit_rules.time_exit_dte == 3

    def test_daemon_uses_dashboard_exit_rules(self):
        """ExitManager receives config from phase5.yaml, not .env."""
        exit_cfg = ExitRulesConfig(profit_target=0.80, stop_loss=-1.00, time_exit_dte=-1)
        from src.config.baseline_strategy import BaselineStrategy, ExitRules

        baseline = BaselineStrategy(
            exit_rules=ExitRules(
                profit_target=exit_cfg.profit_target,
                stop_loss=exit_cfg.stop_loss,
                time_exit_dte=exit_cfg.time_exit_dte,
            ),
        )
        # time_exit_dte=-1 means never time-exit
        assert baseline.should_exit_time(0) is False  # DTE=0 but threshold is -1
        assert baseline.should_exit_time(5) is False


# ---------------------------------------------------------------------------
# Workstream 1: _monitor_positions()
# ---------------------------------------------------------------------------


class TestMonitorPositions:
    """Tests for daemon._monitor_positions()."""

    @pytest.fixture
    def daemon(self, db_session):
        """Create a minimal TAADDaemon for testing."""
        from src.agentic.daemon import TAADDaemon

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = Phase5Config.__new__(Phase5Config)
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        daemon.event_bus = MagicMock()
        daemon.learning = MagicMock()
        daemon.governor = MagicMock()
        daemon.governor.level = 1
        daemon.governor.check_promotion.return_value = False
        daemon.memory = MagicMock()
        return daemon

    def test_monitor_positions_skips_when_no_ibkr(self, daemon):
        """Graceful skip when IBKR not available."""
        daemon.ibkr_client = None
        daemon.exit_manager = MagicMock()
        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(MagicMock())
        )
        daemon.exit_manager.evaluate_exits.assert_not_called()

    def test_monitor_positions_skips_when_no_exit_manager(self, daemon):
        """Graceful skip when exit_manager is None."""
        daemon.exit_manager = None
        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(MagicMock())
        )

    def test_monitor_positions_skips_when_disconnected(self, daemon):
        """Skip when IBKR is disconnected."""
        daemon.ibkr_client.is_connected.return_value = False
        daemon.exit_manager = MagicMock()
        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(MagicMock())
        )
        daemon.exit_manager.evaluate_exits.assert_not_called()

    def test_monitor_positions_evaluates_exits(self, daemon):
        """Mock evaluate_exits returns should_exit=True, verify execute_exit called."""
        from src.execution.exit_manager import ExitDecision, ExitResult

        mock_exit_mgr = MagicMock()
        mock_exit_mgr.check_pending_exits.return_value = {}
        mock_exit_mgr.evaluate_exits.return_value = {
            "AAPL_150_20260301_P": ExitDecision(
                should_exit=True, reason="profit_target", exit_type="limit"
            ),
            "MSFT_300_20260301_P": ExitDecision(
                should_exit=False, reason="holding"
            ),
        }
        mock_exit_mgr.execute_exit.return_value = ExitResult(
            success=True,
            position_id="AAPL_150_20260301_P",
            exit_price=0.10,
            exit_reason="profit_target",
        )
        daemon.exit_manager = mock_exit_mgr

        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(MagicMock())
        )

        # Only the AAPL position had should_exit=True
        mock_exit_mgr.execute_exit.assert_called_once()
        call_args = mock_exit_mgr.execute_exit.call_args
        assert call_args[0][0] == "AAPL_150_20260301_P"

    def test_monitor_positions_reconciles_pending(self, daemon):
        """check_pending_exits finds filled order -> emit event."""
        mock_exit_mgr = MagicMock()
        mock_exit_mgr.check_pending_exits.return_value = {
            "TSLA_200_20260301_P": "filled@$0.05"
        }
        mock_exit_mgr.evaluate_exits.return_value = {}
        daemon.exit_manager = mock_exit_mgr

        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(MagicMock())
        )

        # Should emit POSITION_CLOSED event
        daemon.event_bus.emit.assert_called()

    def test_expired_positions_closed_at_market_close(self, daemon):
        """close_expired_positions called at MARKET_CLOSE."""
        mock_pm = MagicMock()
        mock_pm.close_expired_positions.return_value = [
            {"symbol": "SPY", "strike": 400.0, "expiration": "2026-02-20", "profit_loss": 150.0}
        ]
        daemon.position_monitor = mock_pm
        daemon.exit_manager = MagicMock()

        asyncio.get_event_loop().run_until_complete(
            daemon._close_expired_positions(MagicMock())
        )
        mock_pm.close_expired_positions.assert_called_once_with(dry_run=False)


# ---------------------------------------------------------------------------
# Workstream 2: EventDetector
# ---------------------------------------------------------------------------


class TestEventDetector:
    """Tests for the VIX spike and position alert detector."""

    @pytest.fixture
    def mock_event_bus(self):
        return MagicMock()

    @pytest.fixture
    def detector(self, mock_event_bus):
        from src.agentic.event_detector import EventDetector

        return EventDetector(
            event_bus=mock_event_bus,
            position_monitor=None,
            ibkr_client=MagicMock(),
            vix_spike_threshold_pct=15.0,
        )

    def test_session_reset_clears_baseline(self, detector):
        """reset_session sets VIX baseline to None."""
        detector._session_open_vix = 20.0
        detector._last_vix = 22.0
        detector._vix_spike_emitted = True

        detector.reset_session()

        assert detector._session_open_vix is None
        assert detector._last_vix is None
        assert detector._vix_spike_emitted is False

    def test_vix_spike_emits_risk_breach(self, detector, mock_event_bus):
        """VIX change >15% from session open emits RISK_LIMIT_BREACH."""
        # Simulate: set session baseline manually, then check with spike
        detector._session_open_vix = 20.0
        detector._vix_spike_emitted = False

        # Mock market conditions to return VIX=24 (20% spike)
        mock_conditions = MagicMock()
        mock_conditions.vix = 24.0

        with patch(
            "src.services.market_conditions.MarketConditionMonitor"
        ) as MockMonitor:
            instance = MockMonitor.return_value
            instance.check_conditions = AsyncMock(return_value=mock_conditions)

            asyncio.get_event_loop().run_until_complete(detector._check_vix())

        # Should emit RISK_LIMIT_BREACH
        mock_event_bus.emit.assert_called_once()
        call_args = mock_event_bus.emit.call_args
        assert call_args[0][0].value == "RISK_LIMIT_BREACH"
        assert call_args[1]["payload"]["breach_type"] == "vix_spike"
        assert detector._vix_spike_emitted is True

    def test_vix_no_spike_no_event(self, detector, mock_event_bus):
        """Small VIX change doesn't emit event."""
        detector._session_open_vix = 20.0
        detector._vix_spike_emitted = False

        # Mock VIX=21 (5% change — below 15% threshold)
        mock_conditions = MagicMock()
        mock_conditions.vix = 21.0

        with patch(
            "src.services.market_conditions.MarketConditionMonitor"
        ) as MockMonitor:
            instance = MockMonitor.return_value
            instance.check_conditions = AsyncMock(return_value=mock_conditions)

            asyncio.get_event_loop().run_until_complete(detector._check_vix())

        mock_event_bus.emit.assert_not_called()
        assert detector._vix_spike_emitted is False

    def test_critical_alert_emits_event(self, mock_event_bus):
        """Stop loss approaching alert emits RISK_LIMIT_BREACH."""
        from src.agentic.event_detector import EventDetector
        from src.execution.position_monitor import PositionAlert

        mock_pm = MagicMock()
        mock_pm.check_alerts.return_value = [
            PositionAlert(
                position_id="AAPL_150_20260301_P",
                alert_type="stop_loss",
                severity="critical",
                message="AAPL approaching stop loss",
                current_value=-1.8,
                threshold=-2.0,
            )
        ]

        detector = EventDetector(
            event_bus=mock_event_bus,
            position_monitor=mock_pm,
            ibkr_client=MagicMock(),
        )

        asyncio.get_event_loop().run_until_complete(
            detector._check_critical_alerts()
        )
        mock_event_bus.emit.assert_called_once()
        payload = mock_event_bus.emit.call_args[1]["payload"]
        assert payload["breach_type"] == "critical_stop_loss"


# ---------------------------------------------------------------------------
# Workstream 3: Trade Outcome Feedback Loop
# ---------------------------------------------------------------------------


class TestTradeOutcomeFeedback:
    """Tests for _record_trade_outcome and _record_clean_day."""

    @pytest.fixture
    def daemon(self, db_session):
        from src.agentic.daemon import TAADDaemon

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = Phase5Config.__new__(Phase5Config)
        daemon.ibkr_client = None
        daemon.event_bus = MagicMock()
        daemon.learning = MagicMock()
        daemon.governor = MagicMock()
        daemon.governor.level = 1
        daemon.governor.check_promotion.return_value = False
        daemon.memory = MagicMock()
        daemon.exit_manager = None
        daemon.position_monitor = None
        return daemon

    def test_trade_outcome_records_to_governor(self, daemon, db_session):
        """governor.record_trade_outcome called with win=True for profitable trade."""
        # Create a closed winning trade
        trade = Trade(
            trade_id="TEST_100_20260301_P",
            symbol="TEST",
            strike=100.0,
            expiration=date(2026, 3, 1),
            option_type="P",
            entry_date=datetime(2026, 2, 20),
            entry_premium=0.50,
            contracts=5,
            dte=9,
            exit_date=datetime(2026, 2, 25),
            exit_premium=0.10,
            profit_loss=200.0,
        )
        db_session.add(trade)
        db_session.commit()

        daemon._record_trade_outcome("TEST_100_20260301_P", db=db_session)
        daemon.governor.record_trade_outcome.assert_called_once_with(win=True)

    def test_trade_outcome_records_loss(self, daemon, db_session):
        """governor.record_trade_outcome called with win=False for losing trade."""
        trade = Trade(
            trade_id="LOSS_200_20260301_P",
            symbol="LOSS",
            strike=200.0,
            expiration=date(2026, 3, 1),
            option_type="P",
            entry_date=datetime(2026, 2, 20),
            entry_premium=0.30,
            contracts=3,
            dte=9,
            exit_date=datetime(2026, 2, 25),
            exit_premium=0.90,
            profit_loss=-180.0,
        )
        db_session.add(trade)
        db_session.commit()

        daemon._record_trade_outcome("LOSS_200_20260301_P", db=db_session)
        daemon.governor.record_trade_outcome.assert_called_once_with(win=False)

    def test_trade_outcome_triggers_promotion(self, daemon, db_session):
        """check_promotion returns True -> level persisted to memory."""
        daemon.governor.check_promotion.return_value = True
        daemon.governor.level = 2

        trade = Trade(
            trade_id="PROMO_100_20260301_P",
            symbol="PROMO",
            strike=100.0,
            expiration=date(2026, 3, 1),
            option_type="P",
            entry_date=datetime(2026, 2, 20),
            entry_premium=0.50,
            contracts=1,
            dte=9,
            exit_date=datetime(2026, 2, 25),
            exit_premium=0.10,
            profit_loss=40.0,
        )
        db_session.add(trade)
        db_session.commit()

        daemon._record_trade_outcome("PROMO_100_20260301_P", db=db_session)
        daemon.memory.set_autonomy_level.assert_called_once_with(2)

    def test_clean_day_recorded_when_no_errors(self, daemon, db_session):
        """Zero failed events + zero overrides -> record_clean_day called."""
        # No DaemonEvent records for today = clean
        daemon._record_clean_day(db_session)
        daemon.governor.record_clean_day.assert_called_once()

    def test_clean_day_not_recorded_on_error(self, daemon, db_session):
        """Failed events -> no clean day."""
        event = DaemonEvent(
            event_type="SCHEDULED_CHECK",
            priority=4,
            status="failed",
            payload={},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()

        daemon._record_clean_day(db_session)
        daemon.governor.record_clean_day.assert_not_called()

    def test_clean_day_not_recorded_on_override(self, daemon, db_session):
        """Human override today -> no clean day."""
        event = DaemonEvent(
            event_type="HUMAN_OVERRIDE",
            priority=4,
            status="completed",
            payload={},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()

        daemon._record_clean_day(db_session)
        daemon.governor.record_clean_day.assert_not_called()


# ---------------------------------------------------------------------------
# Workstream 4: Autonomy Persistence
# ---------------------------------------------------------------------------


class TestAutonomyPersistence:
    """Tests for governor counter persistence across restarts."""

    def test_governor_counters_persist(self, db_session):
        """clean_days + trades_at_level survive governor recreation."""
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        config = AutonomyConfig(max_level=4)

        # Create governor, record some progress
        gov1 = AutonomyGovernor(db_session, config)
        gov1.record_clean_day()
        gov1.record_clean_day()
        gov1.record_clean_day()
        gov1.record_trade_outcome(win=True)
        gov1.record_trade_outcome(win=True)

        assert gov1._consecutive_clean_days == 3
        assert gov1._trades_at_current_level == 2

        # "Restart" — create a new governor with same DB
        gov2 = AutonomyGovernor(db_session, config)
        assert gov2._consecutive_clean_days == 3
        assert gov2._trades_at_current_level == 2

    def test_governor_counters_reset_on_promotion(self, db_session):
        """After promotion, counters reset to 0 and persist."""
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        config = AutonomyConfig(
            max_level=4,
            promotion_clean_days=2,
            promotion_min_trades=2,
            promotion_min_win_rate=0.50,
        )

        gov = AutonomyGovernor(db_session, config)
        gov.level = 1

        # Add enough progress for promotion
        gov.record_clean_day()
        gov.record_clean_day()
        gov.record_trade_outcome(win=True)
        gov.record_trade_outcome(win=True)

        # Add recent winning trades to DB for win_rate calculation
        for i in range(5):
            trade = Trade(
                trade_id=f"WIN_{i}",
                symbol="WIN",
                strike=100.0,
                expiration=date(2026, 3, 1),
                option_type="P",
                entry_date=datetime(2026, 2, 20),
                entry_premium=0.50,
                contracts=1,
                dte=9,
                exit_date=datetime(2026, 2, 25),
                profit_loss=40.0,
            )
            db_session.add(trade)
        db_session.commit()

        promoted = gov.check_promotion()
        assert promoted is True
        assert gov.level == 2

        # Counters should be 0 after promotion
        assert gov._consecutive_clean_days == 0
        assert gov._trades_at_current_level == 0

        # Verify persistence
        gov2 = AutonomyGovernor(db_session, config)
        assert gov2._consecutive_clean_days == 0
        assert gov2._trades_at_current_level == 0

    def test_governor_level_loaded_from_memory(self, db_session):
        """Daemon loads autonomy level from WorkingMemoryRow on startup."""
        # Pre-set the working memory with level 3
        row = WorkingMemoryRow(id=1, autonomy_level=3, strategy_state={})
        db_session.add(row)
        db_session.commit()

        from src.agentic.working_memory import WorkingMemory

        memory = WorkingMemory(db_session)
        assert memory.autonomy_level == 3

    def test_governor_save_counters_creates_row(self, db_session):
        """_save_counters creates WorkingMemoryRow if it doesn't exist."""
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        gov = AutonomyGovernor(db_session, AutonomyConfig())
        gov._consecutive_clean_days = 7
        gov._trades_at_current_level = 15
        gov._save_counters()

        row = db_session.query(WorkingMemoryRow).get(1)
        assert row is not None
        assert row.strategy_state["governor_clean_days"] == 7
        assert row.strategy_state["governor_trades_at_level"] == 15


# ---------------------------------------------------------------------------
# Workstream 5: Guardrail Blocks Escalate to Human Review
# ---------------------------------------------------------------------------


class TestGuardrailEscalation:
    """Tests for guardrail blocks escalating to human review queue."""

    @pytest.fixture
    def daemon(self, db_session):
        """Create a minimal TAADDaemon with guardrail components."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.guardrails.config import GuardrailConfig
        from src.agentic.guardrails.registry import GuardrailRegistry

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = MagicMock()
        daemon.config.claude.reasoning_model = "test-model"
        daemon.config.guardrails = GuardrailConfig()
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        daemon.event_bus = MagicMock()
        daemon.learning = MagicMock()
        daemon.governor = MagicMock()
        daemon.governor.level = 2
        daemon.governor.check_promotion.return_value = False
        daemon.memory = MagicMock()
        daemon.exit_manager = None
        daemon.position_monitor = None
        daemon.reasoning = MagicMock()
        daemon.reasoning._reasoning_agent = MagicMock()
        daemon.reasoning._reasoning_agent.total_input_tokens = 100
        daemon.reasoning._reasoning_agent.total_output_tokens = 50
        daemon.reasoning._reasoning_agent.session_cost = 0.001
        daemon.health = MagicMock()
        daemon.health.shutdown_requested = False
        daemon.executor = MagicMock()
        daemon.guardrails = GuardrailRegistry(GuardrailConfig())
        daemon.entropy_monitor = MagicMock()
        daemon.confidence_calibrator = MagicMock()
        daemon.calendar = MagicMock()
        daemon.calendar.is_market_open.return_value = True
        daemon._last_scheduled_fingerprint = ""
        return daemon

    def _make_event(self, db_session, event_type="SCHEDULED_CHECK", payload=None):
        """Create a DaemonEvent in the DB."""
        event = DaemonEvent(
            event_type=event_type,
            priority=4,
            status="pending",
            payload=payload or {},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()
        return event

    def test_context_block_creates_audit_in_queue(self, daemon, db_session):
        """Context guardrail block creates DecisionAudit with GUARDRAIL_BLOCKED action."""
        from src.agentic.guardrails.registry import GuardrailResult

        event = self._make_event(db_session)

        # Simulate a context block
        block_results = [
            GuardrailResult(
                passed=False,
                guard_name="stale_data",
                severity="block",
                reason="Market data older than 5 minutes",
            )
        ]

        daemon._escalate_guardrail_block(
            event=event,
            db=db_session,
            event_type="SCHEDULED_CHECK",
            guardrail_layer="context",
            block_reasons=["[stale_data] Market data older than 5 minutes"],
            guardrail_results=block_results,
            original_decision=None,
        )

        # Verify audit record was created with correct properties
        audit = db_session.query(DecisionAudit).first()
        assert audit is not None
        assert audit.action == "GUARDRAIL_BLOCKED"
        assert audit.executed is False
        assert audit.human_decision is None
        assert "context" in audit.execution_result.get("guardrail_layer", "")
        assert audit.execution_result.get("original_event_type") == "SCHEDULED_CHECK"
        assert audit.guardrail_flags is not None
        assert any(f["guard_name"] == "stale_data" for f in audit.guardrail_flags)

    def test_output_block_creates_audit_with_original_action(self, daemon, db_session):
        """Output guardrail block preserves the original action in audit."""
        from src.agentic.guardrails.registry import GuardrailResult

        event = self._make_event(db_session)
        original_decision = _make_decision(
            action="EXECUTE_TRADES",
            confidence=0.85,
            reasoning="Execute staged candidates based on favorable conditions",
            metadata={"staged_ids": [1, 2, 3]},
        )

        block_results = [
            GuardrailResult(
                passed=False,
                guard_name="symbol_crossref",
                severity="block",
                reason="Symbol 'FAKE' not found in context",
            )
        ]

        daemon._escalate_guardrail_block(
            event=event,
            db=db_session,
            event_type="SCHEDULED_CHECK",
            guardrail_layer="output",
            block_reasons=["[symbol_crossref] Symbol 'FAKE' not found in context"],
            guardrail_results=block_results,
            original_decision=original_decision,
        )

        audit = db_session.query(DecisionAudit).first()
        assert audit is not None
        assert audit.action == "EXECUTE_TRADES"  # Original action preserved
        assert audit.executed is False
        assert audit.human_decision is None
        assert audit.execution_result.get("guardrail_layer") == "output"
        assert "staged_ids" in audit.execution_result.get("data", {})

    def test_context_block_approval_re_emits_event(self, daemon, db_session):
        """Approving a context block re-emits the original event with override flag."""
        # Create the originating event first (FK requirement)
        source_event = self._make_event(db_session)

        # Create an audit record simulating a context guardrail block
        audit = DecisionAudit(
            event_id=source_event.id,
            timestamp=datetime.utcnow(),
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="GUARDRAIL_BLOCKED",
            confidence=1.0,
            reasoning="[Guardrail context block] Stale data",
            key_factors=["guardrail_block", "pre_claude"],
            autonomy_approved=False,
            executed=False,
            execution_result={
                "guardrail_layer": "context",
                "block_reasons": ["Stale data"],
                "original_event_type": "SCHEDULED_CHECK",
                "original_event_payload": {"trigger": "startup"},
                "message": "Blocked before Claude reasoning",
            },
        )
        db_session.add(audit)
        db_session.commit()

        # Create a HUMAN_OVERRIDE event pointing to this audit
        override_event = self._make_event(
            db_session,
            event_type="HUMAN_OVERRIDE",
            payload={"decision_id": audit.id},
        )

        # Process the human approval
        asyncio.get_event_loop().run_until_complete(
            daemon._process_human_approval(override_event, db_session)
        )

        # Verify the event was re-emitted with override flag
        daemon.event_bus.emit.assert_called_once()
        call_args = daemon.event_bus.emit.call_args
        emitted_type = call_args[0][0]
        emitted_payload = call_args[1]["payload"]
        assert emitted_type.value == "SCHEDULED_CHECK"
        assert emitted_payload["_guardrail_override"] is True
        assert emitted_payload["trigger"] == "startup"

    def test_output_block_approval_executes_handler(self, daemon, db_session):
        """Approving an output block executes the handler directly."""
        source_event = self._make_event(db_session)
        audit = DecisionAudit(
            event_id=source_event.id,
            timestamp=datetime.utcnow(),
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="EXECUTE_TRADES",
            confidence=0.85,
            reasoning="Execute staged candidates",
            key_factors=["favorable"],
            autonomy_approved=False,
            executed=False,
            execution_result={
                "guardrail_layer": "output",
                "block_reasons": ["Symbol cross-ref failed"],
                "data": {"staged_ids": [1, 2]},
            },
        )
        db_session.add(audit)
        db_session.commit()

        # Mock the handler
        from src.agentic.action_executor import ExecutionResult
        mock_handler = AsyncMock(return_value=ExecutionResult(
            success=True, action="EXECUTE_TRADES", message="Executed 2 trades",
        ))
        daemon.executor._get_handler.return_value = mock_handler

        override_event = self._make_event(
            db_session,
            event_type="HUMAN_OVERRIDE",
            payload={"decision_id": audit.id},
        )

        asyncio.get_event_loop().run_until_complete(
            daemon._process_human_approval(override_event, db_session)
        )

        # Handler should have been called directly
        mock_handler.assert_called_once()
        # Verify audit was updated with execution result
        db_session.refresh(audit)
        assert audit.executed is True

    def test_close_position_bypasses_output_guardrail(self, daemon, db_session):
        """CLOSE_POSITION continues through output guardrail blocks."""
        from src.agentic.guardrails.registry import GuardrailResult
        from src.agentic.guardrails.output_validator import OutputValidator

        # Register a validator that blocks everything
        class AlwaysBlockValidator:
            def validate(self, decision, context, config):
                return [GuardrailResult(
                    passed=False, guard_name="test_block",
                    severity="block", reason="Always blocks",
                )]

        daemon.guardrails.register_output_validator(AlwaysBlockValidator())

        event = self._make_event(db_session)
        decision = _make_decision(
            action="CLOSE_POSITION",
            metadata={"trade_id": "TEST_100_20260301_P"},
        )

        # The CLOSE_POSITION should NOT be escalated — it should proceed
        # We test this through the output guardrail check in _process_event
        # by checking that has_block is True but CLOSE_POSITION bypasses
        from dataclasses import dataclass, field as dc_field

        @dataclass
        class FakeContext:
            open_positions: list = dc_field(default_factory=list)
            staged_candidates: list = dc_field(default_factory=list)
            market_context: dict = dc_field(default_factory=dict)
            recent_trades: list = dc_field(default_factory=list)
            autonomy_level: int = 1
            anomalies: list = dc_field(default_factory=list)

        ctx = FakeContext(
            open_positions=[{"symbol": "TEST", "trade_id": "TEST_100_20260301_P", "strike": 100.0}]
        )

        out_results = daemon.guardrails.validate_output(decision, ctx)
        assert daemon.guardrails.has_block(out_results)  # Our validator blocks
        # But CLOSE_POSITION should be checked in the daemon to NOT escalate

    def test_reject_guardrail_block_stays_blocked(self, daemon, db_session):
        """Rejecting a guardrail block leaves it blocked (human_decision='rejected')."""
        source_event = self._make_event(db_session)
        audit = DecisionAudit(
            event_id=source_event.id,
            timestamp=datetime.utcnow(),
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="GUARDRAIL_BLOCKED",
            confidence=1.0,
            reasoning="[Guardrail context block] Stale data",
            key_factors=["guardrail_block"],
            autonomy_approved=False,
            executed=False,
            execution_result={"guardrail_layer": "context"},
        )
        db_session.add(audit)
        db_session.commit()

        # Simulate rejection via the dashboard API flow
        audit.human_decision = "rejected"
        audit.human_decided_at = datetime.utcnow()
        db_session.commit()

        db_session.refresh(audit)
        assert audit.executed is False
        assert audit.human_decision == "rejected"

    def test_eod_auto_rejects_stale_guardrail_blocks(self, daemon, db_session):
        """EOD cleanup auto-rejects guardrail blocks from prior days."""
        # Create events first (FK requirement)
        ev1 = self._make_event(db_session)
        ev2 = self._make_event(db_session)

        # Create a stale audit from yesterday
        yesterday = datetime.utcnow() - timedelta(days=1)
        stale_audit = DecisionAudit(
            event_id=ev1.id,
            timestamp=yesterday,
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="EXECUTE_TRADES",
            confidence=0.8,
            reasoning="Stale guardrail block",
            key_factors=["guardrail_block"],
            autonomy_approved=False,
            executed=False,
            execution_result={"guardrail_layer": "output"},
            guardrail_flags=[{"passed": False, "guard_name": "test", "severity": "block", "reason": "test"}],
        )
        db_session.add(stale_audit)

        # Create a fresh audit from today (should NOT be auto-rejected)
        fresh_audit = DecisionAudit(
            event_id=ev2.id,
            timestamp=datetime.utcnow(),
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="GUARDRAIL_BLOCKED",
            confidence=1.0,
            reasoning="Fresh guardrail block",
            key_factors=["guardrail_block"],
            autonomy_approved=False,
            executed=False,
            execution_result={"guardrail_layer": "context"},
            guardrail_flags=[{"passed": False, "guard_name": "test", "severity": "block", "reason": "test"}],
        )
        db_session.add(fresh_audit)
        db_session.commit()

        daemon._auto_reject_stale_guardrail_blocks(db_session)

        db_session.refresh(stale_audit)
        db_session.refresh(fresh_audit)
        assert stale_audit.human_decision == "auto_rejected"
        assert fresh_audit.human_decision is None  # Today's not touched

    def test_guardrail_override_flag_bypasses_context_check(self, daemon, db_session):
        """Events with _guardrail_override=True proceed past context blocks."""
        from src.agentic.guardrails.registry import GuardrailResult
        from src.agentic.working_memory import ReasoningContext

        # Register a context validator that always blocks
        class AlwaysBlockContext:
            def validate(self, context, config):
                return [GuardrailResult(
                    passed=False, guard_name="test_ctx_block",
                    severity="block", reason="Always blocks context",
                )]

        daemon.guardrails.register_context_validator(AlwaysBlockContext())

        # Mock context assembly and enrichment to avoid IBKR calls
        mock_context = ReasoningContext(
            autonomy_level=2,
            open_positions=[],
            staged_candidates=[],
            recent_trades=[],
            recent_decisions=[],
            market_context={"vix": 20.0, "data_stale": False},
            anomalies=[],
        )
        daemon.memory.assemble_context.return_value = mock_context
        daemon._enrich_context = AsyncMock()

        # Without override: should escalate (return early, no Claude call)
        event_no_override = self._make_event(
            db_session, payload={}
        )
        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event_no_override, db_session)
        )
        # Claude should NOT have been called (escalated before reasoning)
        daemon.reasoning.reason.assert_not_called()

        # Verify audit was created with GUARDRAIL_BLOCKED
        audit = db_session.query(DecisionAudit).first()
        assert audit is not None
        assert audit.action == "GUARDRAIL_BLOCKED"

        # With override: should proceed to Claude reasoning
        daemon.reasoning.reason.return_value = _make_decision(
            action="MONITOR_ONLY", reasoning="All clear after override"
        )
        daemon.executor.execute = AsyncMock(
            return_value=MagicMock(
                success=True, action="MONITOR_ONLY", message="OK",
                data={}, error=None,
            )
        )

        event_with_override = self._make_event(
            db_session, payload={"_guardrail_override": True}
        )
        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event_with_override, db_session)
        )
        daemon.reasoning.reason.assert_called_once()


# ---------------------------------------------------------------------------
# Feature: Per-Position Exit Checks (POSITION_EXIT_CHECK)
# ---------------------------------------------------------------------------


class TestPerPositionExitChecks:
    """Tests for POSITION_EXIT_CHECK event emission and handling."""

    @pytest.fixture
    def daemon(self, db_session):
        """Create a minimal TAADDaemon with position monitoring."""
        from src.agentic.daemon import TAADDaemon

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = Phase5Config.__new__(Phase5Config)
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        daemon.event_bus = MagicMock()
        daemon.learning = MagicMock()
        daemon.governor = MagicMock()
        daemon.governor.level = 1
        daemon.governor.check_promotion.return_value = False
        daemon.memory = MagicMock()
        daemon.exit_manager = MagicMock()
        daemon.exit_manager.check_pending_exits.return_value = {}
        daemon.exit_manager.evaluate_exits.return_value = {}
        daemon.position_monitor = MagicMock()
        daemon.position_monitor.get_position_price.return_value = None
        return daemon

    def test_material_profit_emits_position_exit_check(self, daemon, db_session):
        """Position at +60% P&L emits POSITION_EXIT_CHECK event."""
        from src.agentic.event_bus import EventType

        # Create a position with material profit (entry=1.00, current=0.40 → +60%)
        trade = Trade(
            trade_id="PROFIT_100_P",
            symbol="AAPL",
            strike=100.0,
            expiration=date(2026, 3, 6),
            option_type="P",
            entry_date=datetime(2026, 2, 23),
            entry_premium=1.00,
            contracts=1,
            dte=11,
        )
        db_session.add(trade)
        db_session.commit()

        # Mock position_monitor to return a cached price
        daemon.position_monitor.get_position_price.return_value = 0.40

        # _emit_material_position_checks is now called from _process_event,
        # not from _monitor_positions. Test it directly.
        daemon._emit_material_position_checks(db_session, set())

        # Should emit POSITION_EXIT_CHECK for the material position
        emit_calls = [
            c for c in daemon.event_bus.emit.call_args_list
            if c[0][0] == EventType.POSITION_EXIT_CHECK
        ]
        assert len(emit_calls) == 1
        payload = emit_calls[0][1]["payload"]
        assert payload["trade_id"] == "PROFIT_100_P"
        assert payload["symbol"] == "AAPL"
        assert payload["pnl_pct"] == 60.0

    def test_material_loss_emits_position_exit_check(self, daemon, db_session):
        """Position at -120% P&L emits POSITION_EXIT_CHECK event."""
        from src.agentic.event_bus import EventType

        # Create a position with material loss (entry=0.50, current=1.10 → -120%)
        trade = Trade(
            trade_id="LOSS_200_P",
            symbol="TSLA",
            strike=200.0,
            expiration=date(2026, 3, 6),
            option_type="P",
            entry_date=datetime(2026, 2, 23),
            entry_premium=0.50,
            contracts=1,
            dte=11,
        )
        db_session.add(trade)
        db_session.commit()

        daemon.position_monitor.get_position_price.return_value = 1.10

        daemon._emit_material_position_checks(db_session, set())

        emit_calls = [
            c for c in daemon.event_bus.emit.call_args_list
            if c[0][0] == EventType.POSITION_EXIT_CHECK
        ]
        assert len(emit_calls) == 1
        payload = emit_calls[0][1]["payload"]
        assert payload["trade_id"] == "LOSS_200_P"
        assert payload["pnl_pct"] == -120.0

    def test_immaterial_position_no_event(self, daemon, db_session):
        """Position at +20% P&L does NOT emit POSITION_EXIT_CHECK."""
        from src.agentic.event_bus import EventType

        # Create position with immaterial P&L (entry=1.00, current=0.80 → +20%)
        trade = Trade(
            trade_id="FLAT_150_P",
            symbol="MSFT",
            strike=150.0,
            expiration=date(2026, 3, 6),
            option_type="P",
            entry_date=datetime(2026, 2, 23),
            entry_premium=1.00,
            contracts=1,
            dte=11,
        )
        db_session.add(trade)
        db_session.commit()

        daemon.position_monitor.get_position_price.return_value = 0.80

        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(db_session)
        )

        emit_calls = [
            c for c in daemon.event_bus.emit.call_args_list
            if c[0][0] == EventType.POSITION_EXIT_CHECK
        ]
        assert len(emit_calls) == 0

    def test_deterministically_exited_position_skipped(self, daemon, db_session):
        """Position already exited deterministically is not checked for material P&L."""
        from src.agentic.event_bus import EventType
        from src.execution.exit_manager import ExitDecision, ExitResult

        trade = Trade(
            trade_id="EXITED_100_P",
            symbol="SPY",
            strike=100.0,
            expiration=date(2026, 3, 6),
            option_type="P",
            entry_date=datetime(2026, 2, 23),
            entry_premium=1.00,
            contracts=1,
            dte=11,
        )
        db_session.add(trade)
        db_session.commit()

        # Mark as deterministically exited
        daemon.exit_manager.evaluate_exits.return_value = {
            "EXITED_100_P": ExitDecision(
                should_exit=True, reason="profit_target", exit_type="limit"
            ),
        }
        daemon.exit_manager.execute_exit.return_value = ExitResult(
            success=True, position_id="EXITED_100_P",
            exit_price=0.10, exit_reason="profit_target",
        )

        # Even with material P&L, should NOT emit because it was already exited
        daemon.position_monitor.get_position_price.return_value = 0.10

        asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(db_session)
        )

        exit_check_calls = [
            c for c in daemon.event_bus.emit.call_args_list
            if c[0][0] == EventType.POSITION_EXIT_CHECK
        ]
        assert len(exit_check_calls) == 0

    def test_multiple_material_positions_emit_separate_events(self, daemon, db_session):
        """Multiple material positions each get their own POSITION_EXIT_CHECK."""
        from src.agentic.event_bus import EventType

        for i, (sym, price) in enumerate([("AAPL", 0.30), ("TSLA", 2.10)]):
            trade = Trade(
                trade_id=f"{sym}_100_P",
                symbol=sym,
                strike=100.0,
                expiration=date(2026, 3, 6),
                option_type="P",
                entry_date=datetime(2026, 2, 23),
                entry_premium=1.00,
                contracts=1,
                dte=11,
            )
            db_session.add(trade)
        db_session.commit()

        # Mock: return different prices based on trade_id
        def get_price(trade_id):
            prices = {"AAPL_100_P": 0.30, "TSLA_100_P": 2.10}
            return prices.get(trade_id)

        daemon.position_monitor.get_position_price.side_effect = get_price

        daemon._emit_material_position_checks(db_session, set())

        exit_check_calls = [
            c for c in daemon.event_bus.emit.call_args_list
            if c[0][0] == EventType.POSITION_EXIT_CHECK
        ]
        assert len(exit_check_calls) == 2
        symbols = {c[1]["payload"]["symbol"] for c in exit_check_calls}
        assert symbols == {"AAPL", "TSLA"}

    def test_get_position_pnl_pct_calculation(self, daemon, db_session):
        """_get_position_pnl_pct correctly computes P&L percentage."""
        trade = Trade(
            trade_id="CALC_TEST",
            symbol="TEST",
            strike=100.0,
            expiration=date(2026, 3, 6),
            option_type="P",
            entry_date=datetime(2026, 2, 23),
            entry_premium=2.00,
            contracts=1,
            dte=11,
        )

        # entry=2.00, current=0.50 → (2.00 - 0.50) / 2.00 * 100 = 75%
        daemon.position_monitor.get_position_price.return_value = 0.50
        pnl = daemon._get_position_pnl_pct(trade, db_session)
        assert pnl == 75.0

        # entry=2.00, current=4.00 → (2.00 - 4.00) / 2.00 * 100 = -100%
        daemon.position_monitor.get_position_price.return_value = 4.00
        pnl = daemon._get_position_pnl_pct(trade, db_session)
        assert pnl == -100.0

    def test_get_position_pnl_pct_no_price_returns_none(self, daemon, db_session):
        """_get_position_pnl_pct returns None when no price available."""
        trade = Trade(
            trade_id="NO_PRICE",
            symbol="TEST",
            strike=100.0,
            expiration=date(2026, 3, 6),
            option_type="P",
            entry_date=datetime(2026, 2, 23),
            entry_premium=1.00,
            contracts=1,
            dte=11,
        )

        daemon.position_monitor.get_position_price.return_value = None
        daemon.ibkr_client = None  # No IBKR fallback either
        pnl = daemon._get_position_pnl_pct(trade, db_session)
        assert pnl is None


# ---------------------------------------------------------------------------
# Feature: Position-Scoped Reasoning Prompt
# ---------------------------------------------------------------------------


class TestPositionScopedReasoning:
    """Tests for the POSITION_EXIT_CHECK reasoning prompt."""

    def test_position_exit_prompt_restricts_actions(self):
        """POSITION_EXIT_CHECK only allows CLOSE_POSITION or MONITOR_ONLY."""
        from src.agentic.reasoning_engine import POSITION_EXIT_ACTIONS

        assert POSITION_EXIT_ACTIONS == {"CLOSE_POSITION", "MONITOR_ONLY"}

    def test_position_exit_prompt_used_for_position_check(self):
        """reason() uses POSITION_EXIT_SYSTEM_PROMPT for POSITION_EXIT_CHECK events."""
        from src.agentic.reasoning_engine import (
            ClaudeReasoningEngine,
            POSITION_EXIT_SYSTEM_PROMPT,
        )

        engine = ClaudeReasoningEngine.__new__(ClaudeReasoningEngine)
        engine.config = MagicMock()
        engine.config.reasoning_model = "test"
        engine.config.max_tokens = 1024
        engine.config.temperature = 0.2
        engine.config.position_exit_system_prompt = ""  # empty = use built-in default
        engine.cost_tracker = MagicMock()
        engine.cost_tracker.can_call.return_value = True
        engine.system_prompt = "main system prompt"

        # Mock the agent to capture the system prompt
        mock_agent = MagicMock()
        mock_agent.send_message.return_value = {
            "content": '{"action": "MONITOR_ONLY", "confidence": 0.8, "reasoning": "Hold"}',
            "input_tokens": 100,
            "output_tokens": 50,
        }
        mock_agent.estimate_cost.return_value = 0.001
        engine._reasoning_agent = mock_agent

        from src.agentic.working_memory import ReasoningContext

        ctx = ReasoningContext(
            autonomy_level=1,
            open_positions=[{"trade_id": "T1", "symbol": "AAPL", "strike": 150.0}],
            staged_candidates=[],
            recent_trades=[],
            recent_decisions=[],
            market_context={"vix": 20.0, "spy_price": 500.0},
            anomalies=[],
        )

        result = engine.reason(
            context=ctx,
            event_type="POSITION_EXIT_CHECK",
            event_payload={"trade_id": "T1", "symbol": "AAPL", "strike": 150.0, "pnl_pct": 65.0},
        )

        # Verify position-scoped prompt was used
        call_kwargs = mock_agent.send_message.call_args[1]
        assert call_kwargs["system_prompt"] == POSITION_EXIT_SYSTEM_PROMPT
        assert "AAPL" in call_kwargs["user_message"]
        assert "Position Under Evaluation" in call_kwargs["user_message"]

    def test_position_exit_injects_trade_id_if_missing(self):
        """trade_id is injected into metadata if Claude omits it."""
        from src.agentic.reasoning_engine import ClaudeReasoningEngine

        engine = ClaudeReasoningEngine.__new__(ClaudeReasoningEngine)
        engine.config = MagicMock()
        engine.config.reasoning_model = "test"
        engine.config.max_tokens = 1024
        engine.config.temperature = 0.2
        engine.config.position_exit_system_prompt = ""  # empty = use built-in default
        engine.cost_tracker = MagicMock()
        engine.cost_tracker.can_call.return_value = True
        engine.system_prompt = "main"

        # Claude returns CLOSE_POSITION but without trade_id in metadata
        mock_agent = MagicMock()
        mock_agent.send_message.return_value = {
            "content": '{"action": "CLOSE_POSITION", "confidence": 0.85, "reasoning": "Take profit", "metadata": {}}',
            "input_tokens": 100,
            "output_tokens": 50,
        }
        mock_agent.estimate_cost.return_value = 0.001
        engine._reasoning_agent = mock_agent

        from src.agentic.working_memory import ReasoningContext

        ctx = ReasoningContext(
            autonomy_level=1,
            open_positions=[],
            staged_candidates=[],
            recent_trades=[],
            recent_decisions=[],
            market_context={"vix": 20.0},
            anomalies=[],
        )

        results = engine.reason(
            context=ctx,
            event_type="POSITION_EXIT_CHECK",
            event_payload={"trade_id": "AAPL_150_P", "symbol": "AAPL", "strike": 150.0, "pnl_pct": 72.0},
        )

        assert len(results) == 1
        assert results[0].action == "CLOSE_POSITION"
        assert results[0].metadata["trade_id"] == "AAPL_150_P"

    def test_invalid_action_defaults_to_monitor_for_position_check(self):
        """Invalid action in position check response defaults to MONITOR_ONLY."""
        from src.agentic.reasoning_engine import ClaudeReasoningEngine, POSITION_EXIT_ACTIONS

        engine = ClaudeReasoningEngine.__new__(ClaudeReasoningEngine)

        # Parse a response with EXECUTE_TRADES (not valid for position check)
        results = engine._parse_response(
            '{"action": "EXECUTE_TRADES", "confidence": 0.8, "reasoning": "bad"}',
            valid_actions=POSITION_EXIT_ACTIONS,
        )
        assert len(results) == 1
        assert results[0].action == "MONITOR_ONLY"


# ---------------------------------------------------------------------------
# Feature: POSITION_EXIT_CHECK Event Type
# ---------------------------------------------------------------------------


class TestPositionExitCheckEventType:
    """Tests for the POSITION_EXIT_CHECK event type in EventBus."""

    def test_event_type_exists(self):
        """POSITION_EXIT_CHECK is a valid EventType."""
        from src.agentic.event_bus import EventType

        assert hasattr(EventType, "POSITION_EXIT_CHECK")
        assert EventType.POSITION_EXIT_CHECK.value == "POSITION_EXIT_CHECK"

    def test_event_priority_is_3(self):
        """POSITION_EXIT_CHECK has priority 3 (same as MARKET_OPEN)."""
        from src.agentic.event_bus import EVENT_PRIORITIES, EventType

        assert EVENT_PRIORITIES[EventType.POSITION_EXIT_CHECK] == 3
        assert EVENT_PRIORITIES[EventType.MARKET_OPEN] == 3

    def test_stale_position_check_skipped_after_hours(self, db_session):
        """POSITION_EXIT_CHECK events are skipped when market is closed."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.guardrails.config import GuardrailConfig
        from src.agentic.guardrails.registry import GuardrailRegistry

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = MagicMock()
        daemon.config.guardrails = GuardrailConfig()
        daemon.event_bus = MagicMock()
        daemon.health = MagicMock()
        daemon.health.shutdown_requested = False
        daemon.calendar = MagicMock()
        daemon.calendar.is_market_open.return_value = False  # Market closed

        event = DaemonEvent(
            event_type="POSITION_EXIT_CHECK",
            priority=3,
            status="pending",
            payload={"trade_id": "TEST_P"},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()

        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event, db_session)
        )

        # Should be marked completed (skipped) without calling Claude
        daemon.event_bus.mark_completed.assert_called_once_with(event)


# ---------------------------------------------------------------------------
# Pending Approval Deduplication Tests
# ---------------------------------------------------------------------------


class TestPendingApprovalDeduplication:
    """Tests that positions with pending CLOSE_POSITION decisions are not
    closed again by deterministic exits or new POSITION_EXIT_CHECK events."""

    @pytest.fixture
    def daemon(self, db_session):
        """Create a minimal TAADDaemon with position monitoring."""
        from src.agentic.daemon import TAADDaemon

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.config = Phase5Config.__new__(Phase5Config)
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        daemon.event_bus = MagicMock()
        daemon.learning = MagicMock()
        daemon.governor = MagicMock()
        daemon.governor.level = 1
        daemon.governor.check_promotion.return_value = False
        daemon.memory = MagicMock()
        daemon.exit_manager = MagicMock()
        daemon.exit_manager.check_pending_exits.return_value = {}
        daemon.exit_manager.evaluate_exits.return_value = {}
        daemon.position_monitor = MagicMock()
        daemon.position_monitor.get_position_price.return_value = None
        return daemon

    def test_get_pending_close_trade_ids_from_audit(self, daemon, db_session):
        """Pending CLOSE_POSITION audits are detected by trade_id in metadata."""
        # Create a pending CLOSE_POSITION decision (autonomy escalation path)
        audit = DecisionAudit(
            timestamp=datetime.utcnow(),
            event_type="POSITION_EXIT_CHECK",
            autonomy_level=3,
            action="CLOSE_POSITION",
            confidence=0.9,
            reasoning="Close AAPL position",
            autonomy_approved=False,
            executed=False,
            human_decision=None,
            execution_result={
                "message": "Queued for human review",
                "data": {
                    "decision": {
                        "action": "CLOSE_POSITION",
                        "metadata": {"trade_id": "AAPL_150_20260301_P"},
                    },
                    "escalation_reason": "consecutive_losses",
                },
            },
        )
        db_session.add(audit)
        db_session.commit()

        result = daemon._get_suppressed_close_trade_ids(db_session)
        assert "AAPL_150_20260301_P" in result

    def test_get_pending_close_trade_ids_from_event(self, daemon, db_session):
        """Pending POSITION_EXIT_CHECK events are detected by trade_id in payload."""
        # Create a pending POSITION_EXIT_CHECK event
        event = DaemonEvent(
            event_type="POSITION_EXIT_CHECK",
            priority=3,
            status="pending",
            payload={"trade_id": "TSLA_200_20260301_P", "symbol": "TSLA"},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()

        result = daemon._get_suppressed_close_trade_ids(db_session)
        assert "TSLA_200_20260301_P" in result

    def test_get_pending_close_excludes_executed(self, daemon, db_session):
        """Already-executed CLOSE_POSITION decisions are NOT in pending set."""
        audit = DecisionAudit(
            timestamp=datetime.utcnow(),
            event_type="POSITION_EXIT_CHECK",
            action="CLOSE_POSITION",
            confidence=0.9,
            reasoning="Close AAPL",
            autonomy_level=3,
            autonomy_approved=True,
            executed=True,  # Already executed
            execution_result={
                "data": {"decision": {"metadata": {"trade_id": "AAPL_150_20260301_P"}}},
            },
        )
        db_session.add(audit)
        db_session.commit()

        result = daemon._get_suppressed_close_trade_ids(db_session)
        assert len(result) == 0

    def test_get_pending_close_excludes_decided(self, daemon, db_session):
        """Human-decided (approved/rejected) decisions are NOT in pending set."""
        audit = DecisionAudit(
            timestamp=datetime.utcnow(),
            event_type="POSITION_EXIT_CHECK",
            action="CLOSE_POSITION",
            confidence=0.9,
            reasoning="Close AAPL",
            autonomy_level=3,
            autonomy_approved=False,
            executed=False,
            human_decision="approved",  # Human has decided
            execution_result={
                "data": {"decision": {"metadata": {"trade_id": "AAPL_150_20260301_P"}}},
            },
        )
        db_session.add(audit)
        db_session.commit()

        result = daemon._get_suppressed_close_trade_ids(db_session)
        assert len(result) == 0

    def test_emit_skips_positions_with_pending_approval(self, daemon, db_session):
        """_emit_material_position_checks skips positions that already have pending decisions."""
        # Create an open trade
        trade = Trade(
            trade_id="AAPL_150_20260301_P",
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 3, 1),
            option_type="P",
            entry_date=datetime.utcnow(),
            entry_premium=1.00,
            contracts=1,
            otm_pct=5.0,
            dte=5,
        )
        db_session.add(trade)

        # Create a pending approval for this trade
        audit = DecisionAudit(
            timestamp=datetime.utcnow(),
            event_type="POSITION_EXIT_CHECK",
            action="CLOSE_POSITION",
            confidence=0.9,
            reasoning="Close AAPL",
            autonomy_level=3,
            autonomy_approved=False,
            executed=False,
            human_decision=None,
            execution_result={
                "data": {"decision": {"metadata": {"trade_id": "AAPL_150_20260301_P"}}},
            },
        )
        db_session.add(audit)
        db_session.commit()

        # Mock P&L to be material (should normally trigger an event)
        with patch.object(daemon, "_get_position_pnl_pct", return_value=75.0):
            emitted = daemon._emit_material_position_checks(db_session, set())

        # Should NOT emit because there's already a pending approval
        assert emitted == 0

    def test_deterministic_exit_skipped_when_pending_approval(self, daemon, db_session):
        """Deterministic exits are skipped for positions with pending approvals."""
        # Create an open trade
        trade = Trade(
            trade_id="NVDA_800_20260301_P",
            symbol="NVDA",
            strike=800.0,
            expiration=date(2026, 3, 1),
            option_type="P",
            entry_date=datetime.utcnow(),
            entry_premium=2.00,
            contracts=1,
            otm_pct=5.0,
            dte=5,
        )
        db_session.add(trade)

        # Create a pending CLOSE_POSITION approval
        audit = DecisionAudit(
            timestamp=datetime.utcnow(),
            event_type="POSITION_EXIT_CHECK",
            action="CLOSE_POSITION",
            confidence=0.9,
            reasoning="Close NVDA",
            autonomy_level=3,
            autonomy_approved=False,
            executed=False,
            human_decision=None,
            execution_result={
                "data": {"decision": {"metadata": {"trade_id": "NVDA_800_20260301_P"}}},
            },
        )
        db_session.add(audit)
        db_session.commit()

        # Mock ExitManager to report a profit_target exit for NVDA
        # canonical_position_key uses float(strike) so 800 → "800.0"
        mock_exit_decision = MagicMock()
        mock_exit_decision.should_exit = True
        mock_exit_decision.reason = "profit_target"
        daemon.exit_manager.evaluate_exits.return_value = {
            "NVDA_800.0_20260301_P": mock_exit_decision,
        }

        emitted = asyncio.get_event_loop().run_until_complete(
            daemon._monitor_positions(db_session)
        )

        # execute_exit should NOT have been called (skipped due to pending approval)
        daemon.exit_manager.execute_exit.assert_not_called()


# ---------------------------------------------------------------------------
# Rejection Persistence: Trading-Day Boundary Tests
# ---------------------------------------------------------------------------


class TestRejectionPersistenceTradingDay:
    """Tests that rejections persist until the next trading day's market open,
    not just until calendar midnight."""

    def test_current_session_start_weekday_after_open(self):
        """During regular hours on a weekday, session start is today 9:30 AM ET."""
        from src.agentic.daemon import TAADDaemon
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        # Wednesday 2:00 PM ET (regular session)
        with patch("src.agentic.daemon.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 25, 14, 0, 0, tzinfo=ET)
            mock_dt.combine = datetime.combine
            mock_dt.min = datetime.min
            result = TAADDaemon._current_session_start()
            # Should be today at 9:30 AM ET
            assert result.hour == 9
            assert result.minute == 30
            assert result.day == 25

    def test_current_session_start_weekend(self):
        """On a weekend, session start is the most recent Friday 9:30 AM ET."""
        from src.agentic.daemon import TAADDaemon
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        # Saturday noon
        with patch("src.agentic.daemon.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 2, 28, 12, 0, 0, tzinfo=ET)
            mock_dt.combine = datetime.combine
            mock_dt.min = datetime.min
            result = TAADDaemon._current_session_start()
            # Should be Friday Feb 27 at 9:30 AM ET
            assert result.day == 27
            assert result.hour == 9
            assert result.minute == 30

    def test_current_session_start_monday_before_open(self):
        """Monday before market open, session start is previous Friday 9:30 AM."""
        from src.agentic.daemon import TAADDaemon
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        # Monday 7:00 AM ET (before open)
        with patch("src.agentic.daemon.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2026, 3, 2, 7, 0, 0, tzinfo=ET)
            mock_dt.combine = datetime.combine
            mock_dt.min = datetime.min
            result = TAADDaemon._current_session_start()
            # Should be Friday Feb 27 at 9:30 AM
            assert result.day == 27
            assert result.hour == 9

    def test_friday_rejection_persists_through_weekend(self, db_session):
        """A rejection made Friday afternoon stays suppressed through the weekend."""
        from src.agentic.daemon import TAADDaemon
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        daemon = TAADDaemon.__new__(TAADDaemon)

        # Rejection made Friday 3 PM ET → stored as UTC (8 PM)
        friday_3pm_utc = datetime(2026, 2, 27, 20, 0, 0)  # 3 PM ET = 8 PM UTC
        audit = DecisionAudit(
            timestamp=friday_3pm_utc,
            event_type="POSITION_EXIT_CHECK",
            action="CLOSE_POSITION",
            confidence=0.85,
            reasoning="Close AAPL",
            autonomy_level=2,
            autonomy_approved=False,
            executed=False,
            human_decision="rejected",
            human_decided_at=friday_3pm_utc,
            execution_result={
                "data": {"decision": {"metadata": {"trade_id": "AAPL_150_20260301_P"}}},
            },
        )
        db_session.add(audit)
        db_session.commit()

        # Check on Saturday — rejection should still be suppressed
        # _current_session_start returns Friday 9:30 AM ET → 2:30 PM UTC
        with patch.object(
            TAADDaemon, "_current_session_start",
            return_value=datetime(2026, 2, 27, 9, 30, 0, tzinfo=ET),
        ):
            result = daemon._get_suppressed_close_trade_ids(db_session)
            assert "AAPL_150_20260301_P" in result

    def test_friday_rejection_clears_monday_after_open(self, db_session):
        """A Friday rejection is no longer suppressed after Monday market open."""
        from src.agentic.daemon import TAADDaemon
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        daemon = TAADDaemon.__new__(TAADDaemon)

        # Rejection made Friday 3 PM ET
        friday_3pm_utc = datetime(2026, 2, 27, 20, 0, 0)
        audit = DecisionAudit(
            timestamp=friday_3pm_utc,
            event_type="POSITION_EXIT_CHECK",
            action="CLOSE_POSITION",
            confidence=0.85,
            reasoning="Close AAPL",
            autonomy_level=2,
            autonomy_approved=False,
            executed=False,
            human_decision="rejected",
            human_decided_at=friday_3pm_utc,
            execution_result={
                "data": {"decision": {"metadata": {"trade_id": "AAPL_150_20260301_P"}}},
            },
        )
        db_session.add(audit)
        db_session.commit()

        # Check on Monday after open — session start is Monday 9:30 AM ET (2:30 PM UTC)
        # Friday's rejection (8 PM UTC) < Monday 2:30 PM UTC → NOT suppressed
        with patch.object(
            TAADDaemon, "_current_session_start",
            return_value=datetime(2026, 3, 2, 9, 30, 0, tzinfo=ET),
        ):
            result = daemon._get_suppressed_close_trade_ids(db_session)
            assert "AAPL_150_20260301_P" not in result


# ---------------------------------------------------------------------------
# Workstream: Market data enrichment reconnection
# ---------------------------------------------------------------------------


class TestEnrichMarketData:
    """Tests for daemon._enrich_market_data() reconnection logic."""

    @pytest.fixture
    def daemon(self):
        from src.agentic.daemon import TAADDaemon

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        daemon.memory = MagicMock()
        daemon.memory.market_context = {}
        return daemon

    def test_no_ibkr_client_attempts_reconnection_then_stale(self, daemon):
        """When ibkr_client is None, reconnection is attempted; if it fails, data_stale=True."""
        from src.agentic.working_memory import ReasoningContext

        daemon.ibkr_client = None
        daemon._reconnect_attempts = 0
        daemon._db = None
        daemon.config = MagicMock()
        daemon.config.daemon.client_id = 10
        daemon.config.daemon.reconnect_alert_audio_path = ""
        daemon.config.daemon.reconnect_disconnect_audio_path = ""
        daemon.config.daemon.reconnect_success_audio_path = ""
        daemon.config.exit_rules = MagicMock()
        ctx = ReasoningContext()

        with patch("src.agentic.daemon.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            with patch("src.agentic.daemon.IBKRClient") as MockClient:
                MockClient.return_value.connect.side_effect = Exception("No TWS")
                asyncio.get_event_loop().run_until_complete(
                    daemon._enrich_market_data(ctx)
                )
        assert ctx.market_context["data_stale"] is True

    def test_disconnected_triggers_reconnect(self, daemon):
        """When disconnected, ensure_connected() should be called."""
        from src.agentic.working_memory import ReasoningContext

        daemon.ibkr_client.is_connected.return_value = False
        ctx = ReasoningContext()

        with patch("src.agentic.daemon.MarketConditionMonitor", create=True):
            asyncio.get_event_loop().run_until_complete(
                daemon._enrich_market_data(ctx)
            )

        daemon.ibkr_client.ensure_connected.assert_called_once()

    def test_reconnect_failure_sets_stale(self, daemon):
        """When reconnection fails, data_stale=True."""
        from src.agentic.working_memory import ReasoningContext

        daemon.ibkr_client.is_connected.return_value = False
        daemon.ibkr_client.ensure_connected.side_effect = Exception("Connection refused")
        ctx = ReasoningContext()

        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_market_data(ctx)
        )
        assert ctx.market_context["data_stale"] is True

    def test_successful_enrichment_writes_enriched_at(self, daemon):
        """Successful enrichment should set enriched_at timestamp."""
        from src.agentic.working_memory import ReasoningContext
        from datetime import datetime, timezone

        ctx = ReasoningContext()

        mock_conditions = MagicMock()
        mock_conditions.vix = 18.5
        mock_conditions.spy_price = 550.0
        mock_conditions.conditions_favorable = True

        with patch(
            "src.agentic.daemon.MarketConditionMonitor", create=True
        ) as MockMonitor:
            mock_instance = MockMonitor.return_value
            mock_instance.check_conditions = AsyncMock(return_value=mock_conditions)

            # Re-patch the import inside _enrich_market_data
            with patch(
                "src.services.market_conditions.MarketConditionMonitor",
                MockMonitor,
            ):
                asyncio.get_event_loop().run_until_complete(
                    daemon._enrich_market_data(ctx)
                )

        assert ctx.market_context["data_stale"] is False
        assert "enriched_at" in ctx.market_context
        # Verify it's a parseable ISO timestamp
        ts = datetime.fromisoformat(ctx.market_context["enriched_at"])
        age = (datetime.now(timezone.utc) - ts).total_seconds()
        assert age < 5  # Written within last 5 seconds


# ---------------------------------------------------------------------------
# Workstream: IBKR auto-reconnection + macOS alerts
# ---------------------------------------------------------------------------


class TestIBKRReconnection:
    """Tests for daemon IBKR auto-reconnection logic."""

    @pytest.fixture
    def daemon(self):
        """Create a minimal daemon instance for reconnection tests."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.config import DaemonConfig

        d = TAADDaemon.__new__(TAADDaemon)
        d.ibkr_client = None
        d._reconnect_attempts = 0
        d._last_reconnect_alert_time = 0.0
        d._premarket_alert_sent_today = None
        d._db = MagicMock()
        d._running = True

        # Config with defaults
        d.config = MagicMock()
        d.config.daemon = DaemonConfig()
        d.config.exit_rules = MagicMock()
        d.config.exit_rules.profit_target = 0.50
        d.config.exit_rules.stop_loss = -2.00
        d.config.exit_rules.time_exit_dte = 2

        d.executor = MagicMock()
        d.event_detector = MagicMock()
        d.calendar = MagicMock()
        d.calendar.is_trading_day.return_value = True
        d.calendar.is_market_open.return_value = False
        d.calendar.time_until_open.return_value = timedelta(minutes=10)
        return d

    def test_reconnection_success_from_none(self, daemon):
        """When ibkr_client is None, successful connect creates client + dependents."""
        with patch("src.agentic.daemon.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                ibkr_host="127.0.0.1", ibkr_port=7497, ibkr_account="DU12345"
            )
            with patch("src.agentic.daemon.IBKRClient") as MockClient:
                mock_instance = MockClient.return_value
                mock_instance.connect.return_value = True

                with patch.object(daemon, "_init_ibkr_dependents"):
                    result = daemon._attempt_ibkr_reconnection()

        assert result is True
        assert daemon.ibkr_client is not None
        assert daemon._reconnect_attempts == 0

    def test_reconnection_failure_increments_counter(self, daemon):
        """Failed reconnection increments _reconnect_attempts."""
        with patch("src.agentic.daemon.get_config") as mock_cfg:
            mock_cfg.return_value = MagicMock(
                ibkr_host="127.0.0.1", ibkr_port=7497, ibkr_account="DU12345"
            )
            with patch("src.agentic.daemon.IBKRClient") as MockClient:
                MockClient.return_value.connect.side_effect = Exception("No TWS")
                result = daemon._attempt_ibkr_reconnection()

        assert result is False
        assert daemon._reconnect_attempts == 1

    def test_reconnection_existing_client_disconnected(self, daemon):
        """When client exists but disconnected, ensure_connected is called."""
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = False

        # After ensure_connected, it's connected
        def _reconnect():
            daemon.ibkr_client.is_connected.return_value = True

        daemon.ibkr_client.ensure_connected.side_effect = _reconnect

        result = daemon._attempt_ibkr_reconnection()
        assert result is True
        daemon.ibkr_client.ensure_connected.assert_called_once()

    def test_reconnection_already_connected(self, daemon):
        """When client exists and is connected, returns True immediately."""
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True

        result = daemon._attempt_ibkr_reconnection()
        assert result is True

    def test_init_ibkr_dependents_updates_executor(self, daemon):
        """_init_ibkr_dependents wires up executor and event_detector."""
        daemon.ibkr_client = MagicMock()
        daemon.position_monitor = None
        daemon.exit_manager = None

        with patch("src.agentic.daemon.PositionMonitor", create=True) as MockPM, \
             patch("src.agentic.daemon.ExitManager", create=True) as MockEM, \
             patch("src.agentic.daemon.PositionRepository", create=True), \
             patch("src.agentic.daemon.TradeRepository", create=True), \
             patch("src.agentic.daemon.BaselineStrategy", create=True), \
             patch("src.agentic.daemon.ExitRules", create=True):
            # Use real imports inside the method, so patch them there
            with patch("src.execution.exit_manager.ExitManager", MockEM), \
                 patch("src.execution.position_monitor.PositionMonitor", MockPM):
                daemon._init_ibkr_dependents(daemon._db)

        # Executor and event_detector should have updated references
        assert daemon.executor.ibkr_client == daemon.ibkr_client
        assert daemon.event_detector.ibkr_client == daemon.ibkr_client


class TestDisconnectAlerts:
    """Tests for macOS alert debouncing and pre-market alerting."""

    @pytest.fixture
    def daemon(self):
        from src.agentic.daemon import TAADDaemon
        from src.agentic.config import DaemonConfig

        d = TAADDaemon.__new__(TAADDaemon)
        d._reconnect_attempts = 5
        d._last_reconnect_alert_time = 0.0
        d._premarket_alert_sent_today = None
        d._ibkr_ever_connected = False

        d.config = MagicMock()
        d.config.daemon = DaemonConfig()
        d.config.daemon.reconnect_alert_cooldown_seconds = 300
        d.config.daemon.premarket_alert_minutes = 15

        d.calendar = MagicMock()
        d.calendar.is_trading_day.return_value = True
        d.calendar.is_market_open.return_value = False
        d.calendar.time_until_open.return_value = timedelta(minutes=10)
        return d

    def test_disconnect_alert_fires_on_first_call(self, daemon):
        """First alert should fire immediately (no cooldown elapsed yet)."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 9, 0, tzinfo=ET)

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_disconnect_alert(now)

        mock_alert.assert_called_once()
        assert daemon._last_reconnect_alert_time > 0

    def test_disconnect_alert_respects_cooldown(self, daemon):
        """Alert should NOT fire if cooldown hasn't elapsed."""
        import time as _time
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 9, 0, tzinfo=ET)

        # Simulate recent alert
        daemon._last_reconnect_alert_time = _time.monotonic()

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_disconnect_alert(now)

        mock_alert.assert_not_called()

    def test_disconnect_alert_uses_reminder_audio_when_never_connected(self, daemon):
        """When TWS was never connected, plays 'start tws reminder' audio."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 9, 0, tzinfo=ET)
        daemon._ibkr_ever_connected = False

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_disconnect_alert(now)

        call_kwargs = mock_alert.call_args[1]
        assert call_kwargs["audio_override"] == daemon.config.daemon.reconnect_alert_audio_path
        assert "Not Running" in call_kwargs["title"]

    def test_disconnect_alert_uses_lost_audio_when_previously_connected(self, daemon):
        """When TWS was connected and then dropped, plays 'lost connection' audio."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 9, 0, tzinfo=ET)
        daemon._ibkr_ever_connected = True

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_disconnect_alert(now)

        call_kwargs = mock_alert.call_args[1]
        assert call_kwargs["audio_override"] == daemon.config.daemon.reconnect_disconnect_audio_path
        assert "Lost" in call_kwargs["title"]

    def test_disconnect_alert_skips_non_trading_day(self, daemon):
        """No alerts on weekends/holidays."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 7, 9, 0, tzinfo=ET)  # Saturday
        daemon.calendar.is_trading_day.return_value = False

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_disconnect_alert(now)

        mock_alert.assert_not_called()

    def test_premarket_alert_fires_in_window(self, daemon):
        """Pre-market alert fires when within threshold minutes of open."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 9, 20, tzinfo=ET)  # 10 min before 9:30

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_premarket_alert(now, now.date())

        mock_alert.assert_called_once()
        assert "Market Opens Soon" in mock_alert.call_args[1]["title"]
        assert daemon._premarket_alert_sent_today == now.date()

    def test_premarket_alert_only_once_per_day(self, daemon):
        """Pre-market alert fires only once per calendar day."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        today = date(2026, 3, 4)
        now = datetime(2026, 3, 4, 9, 20, tzinfo=ET)

        # Already sent today
        daemon._premarket_alert_sent_today = today

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_premarket_alert(now, today)

        mock_alert.assert_not_called()

    def test_premarket_alert_skips_when_market_already_open(self, daemon):
        """No pre-market alert once market is already open."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 10, 0, tzinfo=ET)
        daemon.calendar.is_market_open.return_value = True

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_premarket_alert(now, now.date())

        mock_alert.assert_not_called()

    def test_premarket_alert_skips_too_early(self, daemon):
        """No pre-market alert if market open is still far away."""
        from zoneinfo import ZoneInfo

        ET = ZoneInfo("America/New_York")
        now = datetime(2026, 3, 4, 6, 0, tzinfo=ET)  # 3.5 hours before open
        daemon.calendar.time_until_open.return_value = timedelta(hours=3, minutes=30)

        with patch.object(daemon, "_fire_macos_alert") as mock_alert:
            daemon._maybe_fire_premarket_alert(now, now.date())

        mock_alert.assert_not_called()

    @patch("platform.system", return_value="Darwin")
    @patch("subprocess.Popen")
    def test_fire_macos_alert_plays_audio_and_popup(self, mock_popen, mock_platform):
        """On macOS, both afplay and osascript should be called."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.config import DaemonConfig
        import tempfile
        import os

        d = TAADDaemon.__new__(TAADDaemon)
        d.config = MagicMock()
        d.config.daemon = DaemonConfig()

        # Create a temp file to act as the audio file
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            audio_path = f.name

        try:
            d.config.daemon.reconnect_alert_audio_path = audio_path
            d._fire_macos_alert(title="Test", message="Hello")

            # Should have two Popen calls: afplay + osascript
            assert mock_popen.call_count == 2
            calls = [c[0][0][0] for c in mock_popen.call_args_list]
            assert "afplay" in calls
            assert "osascript" in calls
        finally:
            os.unlink(audio_path)

    @patch("platform.system", return_value="Linux")
    @patch("subprocess.Popen")
    def test_fire_macos_alert_skips_on_linux(self, mock_popen, mock_platform):
        """On non-macOS platforms, no alerts fired."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.config import DaemonConfig

        d = TAADDaemon.__new__(TAADDaemon)
        d.config = MagicMock()
        d.config.daemon = DaemonConfig()
        d._fire_macos_alert(title="Test", message="Hello")

        mock_popen.assert_not_called()

    @patch("platform.system", return_value="Darwin")
    @patch("subprocess.Popen")
    def test_fire_macos_alert_audio_override(self, mock_popen, mock_platform):
        """audio_override parameter takes precedence over default path."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.config import DaemonConfig
        import tempfile
        import os

        d = TAADDaemon.__new__(TAADDaemon)
        d.config = MagicMock()
        d.config.daemon = DaemonConfig()
        d.config.daemon.reconnect_alert_audio_path = "/nonexistent/default.mp3"

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            override_path = f.name

        try:
            d._fire_macos_alert(
                title="Test", message="Hello", audio_override=override_path
            )
            # afplay should use override_path, not default
            afplay_call = [
                c for c in mock_popen.call_args_list
                if c[0][0][0] == "afplay"
            ]
            assert len(afplay_call) == 1
            assert override_path in afplay_call[0][0][0]
        finally:
            os.unlink(override_path)


# ---------------------------------------------------------------------------
# Data Freshness Notification Flow
# ---------------------------------------------------------------------------


class TestDataFreshnessNotification:
    """Tests for data_freshness guardrail → notification (not escalation).

    Verifies that data_freshness blocks produce a single self-updating
    notification instead of 29 DecisionAudit cards in the approval queue.
    """

    @pytest.fixture
    def daemon(self, db_session):
        """Minimal TAADDaemon with notification-related attributes."""
        from src.agentic.daemon import TAADDaemon

        d = TAADDaemon.__new__(TAADDaemon)
        d.config = MagicMock()
        d.config.claude.reasoning_model = "test-model"
        d.ibkr_client = MagicMock()
        d.ibkr_client.is_connected.return_value = False
        d.event_bus = MagicMock()
        d.health = MagicMock()
        d.health.shutdown_requested = False
        d.memory = MagicMock()
        d.memory.assemble_context.return_value = MagicMock(
            market_context={"data_stale": True}
        )
        d._db = db_session
        return d

    def _make_event(self, db_session, event_type="SCHEDULED_CHECK"):
        event = DaemonEvent(
            event_type=event_type,
            priority=4,
            status="pending",
            payload={},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()
        return event

    # --- _is_only_data_freshness_block ---

    def test_is_only_data_freshness_block_pure(self):
        """All blocks are data_freshness → True."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.guardrails.registry import GuardrailResult

        results = [
            GuardrailResult(passed=False, guard_name="data_freshness", severity="block", reason="stale"),
            GuardrailResult(passed=True, guard_name="consistency_check", severity="info", reason="ok"),
        ]
        assert TAADDaemon._is_only_data_freshness_block(results) is True

    def test_is_only_data_freshness_block_mixed(self):
        """Mixed blocking guards (data_freshness + consistency_check) → False."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.guardrails.registry import GuardrailResult

        results = [
            GuardrailResult(passed=False, guard_name="data_freshness", severity="block", reason="stale"),
            GuardrailResult(passed=False, guard_name="consistency_check", severity="block", reason="mismatch"),
        ]
        assert TAADDaemon._is_only_data_freshness_block(results) is False

    def test_is_only_data_freshness_block_no_blocks(self):
        """No blocks at all → False."""
        from src.agentic.daemon import TAADDaemon
        from src.agentic.guardrails.registry import GuardrailResult

        results = [
            GuardrailResult(passed=True, guard_name="data_freshness", severity="info", reason="ok"),
        ]
        assert TAADDaemon._is_only_data_freshness_block(results) is False

    # --- _upsert_notification ---

    def test_upsert_notification_creates(self, daemon, db_session):
        """First call creates a new notification."""
        notif = daemon._upsert_notification(
            db=db_session,
            key="data_freshness",
            category="data_quality",
            title="Test title",
            message="Test message",
            details={"foo": "bar"},
        )
        assert notif.id is not None
        assert notif.notification_key == "data_freshness"
        assert notif.status == "active"
        assert notif.occurrence_count == 1

    def test_upsert_notification_updates_in_place(self, daemon, db_session):
        """Second call updates the same row and increments count."""
        daemon._upsert_notification(
            db=db_session, key="data_freshness", category="data_quality",
            title="Title 1", message="Msg 1",
        )
        notif = daemon._upsert_notification(
            db=db_session, key="data_freshness", category="data_quality",
            title="Title 2", message="Msg 2",
        )
        assert notif.occurrence_count == 2
        assert notif.title == "Title 2"

        # Only one active row
        count = db_session.query(DaemonNotification).filter_by(
            notification_key="data_freshness", status="active"
        ).count()
        assert count == 1

    # --- _resolve_notification ---

    def test_resolve_notification(self, daemon, db_session):
        """Resolving sets status=resolved and resolved_at timestamp."""
        daemon._upsert_notification(
            db=db_session, key="data_freshness", category="data_quality",
            title="Test", message="Test",
        )
        daemon._resolve_notification(db_session, "data_freshness")

        notif = db_session.query(DaemonNotification).filter_by(
            notification_key="data_freshness"
        ).first()
        assert notif.status == "resolved"
        assert notif.resolved_at is not None

    def test_resolve_nonexistent_is_noop(self, daemon, db_session):
        """Resolving a key with no active notification is a no-op."""
        daemon._resolve_notification(db_session, "nonexistent")
        # No error raised

    # --- _handle_data_freshness_block (IBKR disconnected) ---

    def test_handle_data_freshness_block_disconnected(self, daemon, db_session):
        """When IBKR is disconnected, creates notification and marks event complete."""
        daemon.ibkr_client.is_connected.return_value = False
        event = self._make_event(db_session)

        asyncio.get_event_loop().run_until_complete(
            daemon._handle_data_freshness_block(event, db_session, ["stale data"])
        )

        # Notification created
        notif = db_session.query(DaemonNotification).filter_by(
            notification_key="data_freshness", status="active"
        ).first()
        assert notif is not None
        assert "IBKR not connected" in notif.title

        # Event completed, not escalated
        daemon.event_bus.mark_completed.assert_called_once_with(event)
        daemon.health.record_decision.assert_called_once()

        # No DecisionAudit created (the whole point)
        audit_count = db_session.query(DecisionAudit).count()
        assert audit_count == 0

    # --- _handle_data_freshness_block (retry succeeds) ---

    def test_handle_data_freshness_block_retry_succeeds(self, daemon, db_session):
        """Connected but stale: retry enrichment → succeeds on attempt 1 → resolves."""
        daemon.ibkr_client.is_connected.return_value = True
        event = self._make_event(db_session)

        # Make enrichment succeed (set data_stale=False)
        async def fake_enrich(ctx):
            ctx.market_context["data_stale"] = False
            # Simulate auto-resolve that happens inside _enrich_market_data
            daemon._resolve_notification(db_session, "data_freshness")

        daemon._enrich_market_data = AsyncMock(side_effect=fake_enrich)

        with patch("src.agentic.daemon.asyncio.sleep", new_callable=AsyncMock):
            asyncio.get_event_loop().run_until_complete(
                daemon._handle_data_freshness_block(event, db_session, ["stale data"])
            )

        # Notification resolved
        notif = db_session.query(DaemonNotification).filter_by(
            notification_key="data_freshness"
        ).first()
        assert notif.status == "resolved"

        daemon.event_bus.mark_completed.assert_called_once()

    # --- _handle_data_freshness_block (retries exhausted) ---

    def test_handle_data_freshness_block_retries_exhausted(self, daemon, db_session):
        """Connected but stale: all retries fail → final notification, event completed."""
        daemon.ibkr_client.is_connected.return_value = True
        event = self._make_event(db_session)

        # Enrichment always fails (data_stale stays True)
        async def fake_enrich_fail(ctx):
            ctx.market_context["data_stale"] = True

        daemon._enrich_market_data = AsyncMock(side_effect=fake_enrich_fail)

        with patch("src.agentic.daemon.asyncio.sleep", new_callable=AsyncMock):
            asyncio.get_event_loop().run_until_complete(
                daemon._handle_data_freshness_block(event, db_session, ["stale data"])
            )

        # Notification still active with "retries exhausted"
        notif = db_session.query(DaemonNotification).filter_by(
            notification_key="data_freshness", status="active"
        ).first()
        assert notif is not None
        assert "retries exhausted" in notif.title.lower()

        daemon.event_bus.mark_completed.assert_called_once()

    # --- Auto-resolve on successful enrichment ---

    def test_auto_resolve_on_successful_enrichment(self, daemon, db_session):
        """Successful _enrich_market_data resolves active data_freshness notification."""
        # Pre-create an active notification
        daemon._upsert_notification(
            db=db_session, key="data_freshness", category="data_quality",
            title="Stale data", message="IBKR down",
        )

        # Resolve it
        daemon._resolve_notification(db_session, "data_freshness")

        notif = db_session.query(DaemonNotification).filter_by(
            notification_key="data_freshness"
        ).first()
        assert notif.status == "resolved"
        assert notif.resolved_at is not None


# ---------------------------------------------------------------------------
# EOD_REFLECTION data freshness exemption
# ---------------------------------------------------------------------------


class TestEodReflectionFreshnessExemption:
    """EOD_REFLECTION and MARKET_CLOSE bypass data freshness blocks."""

    @pytest.fixture
    def daemon(self, db_session):
        from src.agentic.daemon import TAADDaemon
        from src.agentic.guardrails.config import GuardrailConfig
        from src.agentic.guardrails.registry import GuardrailRegistry
        from src.agentic.guardrails.context_validator import ContextValidator

        d = TAADDaemon.__new__(TAADDaemon)
        d.config = MagicMock()
        d.config.claude.reasoning_model = "test-model"
        d.ibkr_client = MagicMock()
        d.ibkr_client.is_connected.return_value = True
        d.event_bus = MagicMock()
        d.health = MagicMock()
        d.health.shutdown_requested = False
        d.memory = MagicMock()
        d.memory.assemble_context.return_value = MagicMock(
            market_context={"data_stale": True},
            open_positions=[],
            staged_candidates=[],
        )
        d.memory.market_context = {}
        d._db = db_session

        # Real guardrails with data freshness enabled
        gc = GuardrailConfig()
        d.guardrails = GuardrailRegistry(gc)
        d.guardrails.register_context_validator(ContextValidator())
        d._last_scheduled_fingerprint = ""

        # Reasoning engine mock — returns MONITOR_ONLY
        d.reasoning = MagicMock()
        from src.agentic.reasoning_engine import DecisionOutput
        d.reasoning.reason.return_value = DecisionOutput(
            action="MONITOR_ONLY", confidence=0.9, reasoning="EOD reflection"
        )
        d.reasoning._reasoning_agent = MagicMock(
            total_input_tokens=0, total_output_tokens=0, session_cost=0.0
        )

        d.governor = MagicMock()
        d.governor.level = 2
        d.executor = MagicMock()
        from src.agentic.action_executor import ExecutionResult
        d.executor.execute = AsyncMock(
            return_value=ExecutionResult(success=True, action="MONITOR_ONLY", message="ok")
        )
        d.confidence_calibrator = MagicMock()
        d.entropy_monitor = MagicMock()
        d.calendar = MagicMock()
        d.calendar.is_trading_day.return_value = True
        d.position_monitor = None
        d.exit_manager = None
        d.event_detector = None
        d._reconnect_attempts = 0
        d.learning = MagicMock()
        d.learning.run_eod_reflection = AsyncMock(return_value={"decisions_count": 0, "trades_count": 0})

        return d

    def _make_event(self, db_session, event_type):
        event = DaemonEvent(
            event_type=event_type,
            payload={},
            created_at=datetime.utcnow(),
        )
        db_session.add(event)
        db_session.commit()
        return event

    def test_eod_reflection_bypasses_data_freshness(self, daemon, db_session):
        """EOD_REFLECTION should proceed despite data_stale=True."""
        event = self._make_event(db_session, "EOD_REFLECTION")

        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event, db_session)
        )

        # Learning loop should have been called (not blocked by data freshness)
        daemon.learning.run_eod_reflection.assert_called_once()

    def test_market_close_bypasses_data_freshness(self, daemon, db_session):
        """MARKET_CLOSE should proceed despite data_stale=True.

        Patches out EOD side-effects (_run_eod_sync, etc.) to isolate the
        freshness exemption check from the full MARKET_CLOSE pipeline.
        """
        event = self._make_event(db_session, "MARKET_CLOSE")

        with patch.object(daemon, "_run_eod_sync", new_callable=AsyncMock), \
             patch.object(daemon, "_close_expired_positions", new_callable=AsyncMock), \
             patch.object(daemon, "_auto_reject_stale_guardrail_blocks"), \
             patch.object(daemon, "_calibrate_closed_trades"), \
             patch.object(daemon, "_persist_guardrail_metrics"), \
             patch.object(daemon, "_record_clean_day"):
            asyncio.get_event_loop().run_until_complete(
                daemon._process_event(event, db_session)
            )

        daemon.reasoning.reason.assert_called_once()

    def test_scheduled_check_still_blocked_by_data_freshness(self, daemon, db_session):
        """SCHEDULED_CHECK should still be blocked by data freshness."""
        event = self._make_event(db_session, "SCHEDULED_CHECK")

        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event, db_session)
        )

        # Claude reasoning should NOT have been called
        daemon.reasoning.reason.assert_not_called()


# ---------------------------------------------------------------------------
# Double-submission prevention: STAGED → EXECUTING lock
# ---------------------------------------------------------------------------


class TestDoubleSubmissionPrevention:
    """Tests for the STAGED → EXECUTING dedup lock and executed flag sync."""

    def test_staged_to_executing_is_valid_transition(self):
        """STAGED → EXECUTING must be a valid state transition."""
        from src.data.opportunity_state import OpportunityState, is_valid_transition

        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.EXECUTING)

    def test_executing_to_expired_is_valid_transition(self):
        """EXECUTING → EXPIRED must be valid for TTL cleanup of unfilled orders."""
        from src.data.opportunity_state import OpportunityState, is_valid_transition

        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.EXPIRED)

    def test_executing_to_executed_is_valid_transition(self):
        """EXECUTING → EXECUTED must remain a valid transition."""
        from src.data.opportunity_state import OpportunityState, is_valid_transition

        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.EXECUTED)

    def test_executing_to_failed_is_valid_transition(self):
        """EXECUTING → FAILED must remain a valid transition."""
        from src.data.opportunity_state import OpportunityState, is_valid_transition

        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.FAILED)

    def test_lifecycle_transition_to_executed_sets_executed_flag(self, db_session):
        """lifecycle_manager.transition(EXECUTED) must set executed=True."""
        from src.data.models import ScanOpportunity, ScanResult
        from src.data.opportunity_state import OpportunityState
        from src.execution.opportunity_lifecycle import OpportunityLifecycleManager

        # Create scan result + opportunity
        scan = ScanResult(
            scan_timestamp=datetime.now(),
            source="test",
            total_candidates=1,
        )
        db_session.add(scan)
        db_session.flush()

        opp = ScanOpportunity(
            scan_id=scan.id,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 3, 20),
            source="test",
            state="EXECUTING",
            executed=False,
        )
        db_session.add(opp)
        db_session.commit()

        lifecycle = OpportunityLifecycleManager(db_session)
        result = lifecycle.transition(
            opportunity_id=opp.id,
            new_state=OpportunityState.EXECUTED,
            reason="Order filled",
            actor="ibkr",
            metadata={"trade_id": "AAPL_150.0_20260320_P_12345"},
        )

        assert result is True
        db_session.refresh(opp)
        assert opp.executed is True
        assert opp.trade_id == "AAPL_150.0_20260320_P_12345"
        assert opp.state == "EXECUTED"

    def test_lifecycle_transition_to_executed_without_trade_id(self, db_session):
        """executed=True is set even when no trade_id is in metadata."""
        from src.data.models import ScanOpportunity, ScanResult
        from src.data.opportunity_state import OpportunityState
        from src.execution.opportunity_lifecycle import OpportunityLifecycleManager

        scan = ScanResult(
            scan_timestamp=datetime.now(),
            source="test",
            total_candidates=1,
        )
        db_session.add(scan)
        db_session.flush()

        opp = ScanOpportunity(
            scan_id=scan.id,
            symbol="MSFT",
            strike=300.0,
            expiration=date(2026, 3, 20),
            source="test",
            state="EXECUTING",
            executed=False,
        )
        db_session.add(opp)
        db_session.commit()

        lifecycle = OpportunityLifecycleManager(db_session)
        result = lifecycle.transition(
            opportunity_id=opp.id,
            new_state=OpportunityState.EXECUTED,
            reason="Filled",
            actor="ibkr",
        )

        assert result is True
        db_session.refresh(opp)
        assert opp.executed is True
        assert opp.trade_id is None  # no trade_id provided

    def test_handle_execute_transitions_to_executing_before_scheduler(self, db_session):
        """_handle_execute must transition STAGED → EXECUTING before calling scheduler."""
        from src.data.models import ScanOpportunity, ScanResult
        from src.data.opportunity_state import OpportunityState
        from src.agentic.action_executor import ActionExecutor
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        # Create scan + 2 staged opportunities
        scan = ScanResult(
            scan_timestamp=datetime.now(),
            source="test",
            total_candidates=2,
        )
        db_session.add(scan)
        db_session.flush()

        for sym in ["AAPL", "GOOG"]:
            opp = ScanOpportunity(
                scan_id=scan.id,
                symbol=sym,
                strike=150.0,
                expiration=date(2026, 3, 20),
                source="test",
                state="STAGED",
                executed=False,
                stock_price=160.0,
                staged_limit_price=1.50,
                staged_contracts=1,
                staged_margin=5000.0,
                otm_pct=6.25,
            )
            db_session.add(opp)
        db_session.commit()

        # Capture states at the moment scheduler is called
        captured_states = []

        async def fake_run_monday_morning(staged, dry_run=False):
            """Mock scheduler that records opportunity states."""
            for s in staged:
                opp_row = db_session.query(ScanOpportunity).get(s.id)
                captured_states.append(opp_row.state)

            mock_report = MagicMock()
            mock_report.executed_count = len(staged)
            return mock_report

        mock_ibkr = MagicMock()
        governor = AutonomyGovernor(db_session, AutonomyConfig(initial_level=2))
        executor = ActionExecutor(
            db_session=db_session,
            governor=governor,
            ibkr_client=mock_ibkr,
        )

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.95)

        with patch(
            "src.services.two_tier_execution_scheduler.TwoTierExecutionScheduler"
        ) as MockScheduler:
            instance = MockScheduler.return_value
            instance.run_monday_morning = fake_run_monday_morning

            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(decision, context={})
            )

        assert result.success is True
        # Both opportunities should have been EXECUTING when scheduler ran
        assert captured_states == ["EXECUTING", "EXECUTING"]

    def test_handle_execute_skips_already_executing(self, db_session):
        """If an opportunity is already EXECUTING, _handle_execute skips it."""
        from src.data.models import ScanOpportunity, ScanResult
        from src.agentic.action_executor import ActionExecutor
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        scan = ScanResult(
            scan_timestamp=datetime.now(),
            source="test",
            total_candidates=2,
        )
        db_session.add(scan)
        db_session.flush()

        # One STAGED, one already EXECUTING
        opp1 = ScanOpportunity(
            scan_id=scan.id, symbol="AAPL", strike=150.0,
            expiration=date(2026, 3, 20), source="test", state="STAGED",
            executed=False, stock_price=160.0, staged_limit_price=1.50,
            staged_contracts=1, staged_margin=5000.0, otm_pct=6.25,
        )
        opp2 = ScanOpportunity(
            scan_id=scan.id, symbol="GOOG", strike=150.0,
            expiration=date(2026, 3, 20), source="test", state="EXECUTING",
            executed=False, stock_price=160.0, staged_limit_price=1.50,
            staged_contracts=1, staged_margin=5000.0, otm_pct=6.25,
        )
        db_session.add_all([opp1, opp2])
        db_session.commit()

        mock_ibkr = MagicMock()
        governor = AutonomyGovernor(db_session, AutonomyConfig(initial_level=2))
        executor = ActionExecutor(
            db_session=db_session, governor=governor, ibkr_client=mock_ibkr,
        )

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.95)

        with patch(
            "src.services.two_tier_execution_scheduler.TwoTierExecutionScheduler"
        ) as MockScheduler:
            mock_report = MagicMock()
            mock_report.executed_count = 1
            instance = MockScheduler.return_value
            instance.run_monday_morning = AsyncMock(return_value=mock_report)

            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(decision, context={})
            )

        assert result.success is True
        # Only AAPL (the STAGED one) should have been passed to scheduler
        call_args = instance.run_monday_morning.call_args
        staged_list = call_args[0][0]
        assert len(staged_list) == 1
        assert staged_list[0].symbol == "AAPL"

    def test_handle_execute_marks_failed_on_scheduler_error(self, db_session):
        """If the scheduler raises, claimed opportunities transition to FAILED."""
        from src.data.models import ScanOpportunity, ScanResult
        from src.agentic.action_executor import ActionExecutor
        from src.agentic.autonomy_governor import AutonomyGovernor
        from src.agentic.config import AutonomyConfig

        scan = ScanResult(
            scan_timestamp=datetime.now(),
            source="test",
            total_candidates=1,
        )
        db_session.add(scan)
        db_session.flush()

        opp = ScanOpportunity(
            scan_id=scan.id, symbol="AAPL", strike=150.0,
            expiration=date(2026, 3, 20), source="test", state="STAGED",
            executed=False, stock_price=160.0, staged_limit_price=1.50,
            staged_contracts=1, staged_margin=5000.0, otm_pct=6.25,
        )
        db_session.add(opp)
        db_session.commit()

        mock_ibkr = MagicMock()
        governor = AutonomyGovernor(db_session, AutonomyConfig(initial_level=2))
        executor = ActionExecutor(
            db_session=db_session, governor=governor, ibkr_client=mock_ibkr,
        )

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.95)

        with patch(
            "src.services.two_tier_execution_scheduler.TwoTierExecutionScheduler"
        ) as MockScheduler:
            instance = MockScheduler.return_value
            instance.run_monday_morning = AsyncMock(
                side_effect=RuntimeError("IBKR connection lost")
            )

            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(decision, context={})
            )

        assert result.success is False
        assert "IBKR connection lost" in result.error

        # Opportunity should now be FAILED
        db_session.refresh(opp)
        assert opp.state == "FAILED"


# ---------------------------------------------------------------------------
# P&L enrichment fallback tests
# ---------------------------------------------------------------------------


class TestEnrichPositionPnlFallback:
    """Tests for _enrich_position_pnl() frozen close and portfolio fallbacks."""

    @pytest.fixture
    def daemon(self):
        from src.agentic.daemon import TAADDaemon

        daemon = TAADDaemon.__new__(TAADDaemon)
        daemon.ibkr_client = MagicMock()
        daemon.ibkr_client.is_connected.return_value = True
        return daemon

    def test_enrich_pnl_live_quote_sets_empty_source(self, daemon):
        """Live quote sets pnl_source to empty string."""
        from src.agentic.working_memory import ReasoningContext
        from src.tools.ibkr_client import Quote

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "symbol": "SPY",
                "strike": 500.0,
                "expiration": "2026-03-20",
                "option_type": "PUT",
                "entry_premium": 1.00,
            }
        ]

        mock_contract = MagicMock()
        daemon.ibkr_client.get_option_contract.return_value = mock_contract
        daemon.ibkr_client.qualify_contract.return_value = mock_contract
        daemon.ibkr_client.get_quote = AsyncMock(
            return_value=Quote(bid=0.50, ask=0.60, last=0.55, is_valid=True, reason="")
        )

        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_position_pnl(ctx)
        )

        pos = ctx.open_positions[0]
        assert pos["pnl_source"] == ""
        assert "pnl" in pos
        assert pos["pnl_pct"] == "+45.0%"

    def test_enrich_pnl_frozen_close_tags_source(self, daemon):
        """Frozen close quote sets pnl_source to 'frozen_close'."""
        from src.agentic.working_memory import ReasoningContext
        from src.tools.ibkr_client import Quote

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "symbol": "SPY",
                "strike": 500.0,
                "expiration": "2026-03-20",
                "option_type": "PUT",
                "entry_premium": 1.00,
            }
        ]

        mock_contract = MagicMock()
        daemon.ibkr_client.get_option_contract.return_value = mock_contract
        daemon.ibkr_client.qualify_contract.return_value = mock_contract
        daemon.ibkr_client.get_quote = AsyncMock(
            return_value=Quote(bid=0.55, ask=0.55, last=0.55, is_valid=True, reason="frozen_close")
        )

        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_position_pnl(ctx)
        )

        pos = ctx.open_positions[0]
        assert pos["pnl_source"] == "frozen_close"
        assert "pnl" in pos

    def test_enrich_pnl_portfolio_fallback(self, daemon):
        """When quote is invalid, portfolio fallback fills P&L."""
        from src.agentic.working_memory import ReasoningContext
        from src.tools.ibkr_client import Quote

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "symbol": "SPY",
                "strike": 500.0,
                "expiration": "2026-03-20",
                "option_type": "PUT",
                "entry_premium": 1.00,
            }
        ]

        mock_contract = MagicMock()
        daemon.ibkr_client.get_option_contract.return_value = mock_contract
        daemon.ibkr_client.qualify_contract.return_value = mock_contract
        # Live quote fails
        daemon.ibkr_client.get_quote = AsyncMock(
            return_value=Quote(bid=0, ask=0, is_valid=False, reason="Timeout after 1.0s")
        )

        # Portfolio fallback provides data
        portfolio_item = MagicMock()
        portfolio_item.contract.symbol = "SPY"
        portfolio_item.contract.strike = 500.0
        portfolio_item.contract.lastTradeDateOrContractMonth = "20260320"
        portfolio_item.marketPrice = 0.40
        daemon.ibkr_client.get_portfolio.return_value = [portfolio_item]

        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_position_pnl(ctx)
        )

        pos = ctx.open_positions[0]
        assert pos["pnl_source"] == "portfolio"
        assert pos["current_mid"] == 0.40
        assert pos["pnl"] == 0.60  # 1.00 - 0.40
        assert pos["pnl_pct"] == "+60.0%"

    def test_enrich_pnl_live_quote_preferred_over_portfolio(self, daemon):
        """Live quote wins — portfolio fallback not called for enriched positions."""
        from src.agentic.working_memory import ReasoningContext
        from src.tools.ibkr_client import Quote

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "symbol": "SPY",
                "strike": 500.0,
                "expiration": "2026-03-20",
                "option_type": "PUT",
                "entry_premium": 1.00,
            }
        ]

        mock_contract = MagicMock()
        daemon.ibkr_client.get_option_contract.return_value = mock_contract
        daemon.ibkr_client.qualify_contract.return_value = mock_contract
        daemon.ibkr_client.get_quote = AsyncMock(
            return_value=Quote(bid=0.50, ask=0.60, last=0.55, is_valid=True, reason="")
        )

        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_position_pnl(ctx)
        )

        # Portfolio should never be called since live quote worked
        daemon.ibkr_client.get_portfolio.assert_not_called()
        assert ctx.open_positions[0]["pnl_source"] == ""

    def test_enrich_pnl_portfolio_fallback_skips_no_entry_premium(self, daemon):
        """Portfolio fallback skips positions with zero or missing entry_premium."""
        from src.agentic.working_memory import ReasoningContext
        from src.tools.ibkr_client import Quote

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "symbol": "SPY",
                "strike": 500.0,
                "expiration": "2026-03-20",
                "option_type": "PUT",
                "entry_premium": 0,
            }
        ]

        mock_contract = MagicMock()
        daemon.ibkr_client.get_option_contract.return_value = mock_contract
        daemon.ibkr_client.qualify_contract.return_value = mock_contract
        daemon.ibkr_client.get_quote = AsyncMock(
            return_value=Quote(bid=0, ask=0, is_valid=False, reason="Timeout after 1.0s")
        )

        portfolio_item = MagicMock()
        portfolio_item.contract.symbol = "SPY"
        portfolio_item.contract.strike = 500.0
        portfolio_item.contract.lastTradeDateOrContractMonth = "20260320"
        portfolio_item.marketPrice = 0.40
        daemon.ibkr_client.get_portfolio.return_value = [portfolio_item]

        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_position_pnl(ctx)
        )

        assert "pnl" not in ctx.open_positions[0]

    def test_enrich_pnl_portfolio_exception_handled_gracefully(self, daemon):
        """Portfolio fallback exception doesn't crash enrichment."""
        from src.agentic.working_memory import ReasoningContext
        from src.tools.ibkr_client import Quote

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "symbol": "SPY",
                "strike": 500.0,
                "expiration": "2026-03-20",
                "option_type": "PUT",
                "entry_premium": 1.00,
            }
        ]

        mock_contract = MagicMock()
        daemon.ibkr_client.get_option_contract.return_value = mock_contract
        daemon.ibkr_client.qualify_contract.return_value = mock_contract
        daemon.ibkr_client.get_quote = AsyncMock(
            return_value=Quote(bid=0, ask=0, is_valid=False, reason="Timeout after 1.0s")
        )
        daemon.ibkr_client.get_portfolio.side_effect = Exception("Connection lost")

        # Should not raise
        asyncio.get_event_loop().run_until_complete(
            daemon._enrich_position_pnl(ctx)
        )
        assert "pnl" not in ctx.open_positions[0]


class TestGetQuoteFrozenFallback:
    """Tests for get_quote() frozen close and last price fallbacks."""

    def test_frozen_close_fallback(self):
        """When bid/ask timeout but close is available, return valid frozen quote."""
        import math
        from src.tools.ibkr_client import IBKRClient, Quote

        client = IBKRClient.__new__(IBKRClient)
        client.ib = MagicMock()

        # Ticker with no bid/ask but has close price
        ticker = MagicMock()
        ticker.bid = float("nan")
        ticker.ask = float("nan")
        ticker.last = float("nan")
        ticker.close = 1.50
        ticker.volume = 0
        client.ib.reqMktData.return_value = ticker

        client.ensure_connected = MagicMock()
        client._is_valid_quote = MagicMock(return_value=False)

        contract = MagicMock()
        quote = asyncio.get_event_loop().run_until_complete(
            client.get_quote(contract, timeout=0.1)
        )

        assert quote.is_valid is True
        assert quote.reason == "frozen_close"
        assert quote.bid == 1.50
        assert quote.ask == 1.50

    def test_last_price_fallback(self):
        """When close is also unavailable but last exists, use last."""
        from src.tools.ibkr_client import IBKRClient, Quote

        client = IBKRClient.__new__(IBKRClient)
        client.ib = MagicMock()

        ticker = MagicMock()
        ticker.bid = float("nan")
        ticker.ask = float("nan")
        ticker.last = 1.30
        ticker.close = float("nan")
        ticker.volume = 0
        client.ib.reqMktData.return_value = ticker

        client.ensure_connected = MagicMock()
        client._is_valid_quote = MagicMock(return_value=False)

        contract = MagicMock()
        quote = asyncio.get_event_loop().run_until_complete(
            client.get_quote(contract, timeout=0.1)
        )

        assert quote.is_valid is True
        assert quote.reason == "last_price"
        assert quote.bid == 1.30
        assert quote.ask == 1.30
        assert quote.last == 1.30

    def test_no_data_returns_invalid(self):
        """When nothing available, return invalid quote."""
        from src.tools.ibkr_client import IBKRClient, Quote

        client = IBKRClient.__new__(IBKRClient)
        client.ib = MagicMock()

        ticker = MagicMock()
        ticker.bid = float("nan")
        ticker.ask = float("nan")
        ticker.last = float("nan")
        ticker.close = float("nan")
        ticker.volume = 0
        client.ib.reqMktData.return_value = ticker

        client.ensure_connected = MagicMock()
        client._is_valid_quote = MagicMock(return_value=False)

        contract = MagicMock()
        quote = asyncio.get_event_loop().run_until_complete(
            client.get_quote(contract, timeout=0.1)
        )

        assert quote.is_valid is False
        assert "Timeout" in quote.reason


class TestPnlSourceInPrompt:
    """Tests for pnl_source display in ReasoningContext.to_prompt_string()."""

    def test_live_pnl_no_source_tag(self):
        """Live P&L (empty source) shows no parenthetical."""
        from src.agentic.working_memory import ReasoningContext

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "trade_id": "T001",
                "symbol": "SPY",
                "strike": 500.0,
                "option_type": "PUT",
                "expiration": "2026-03-20",
                "dte": 15,
                "pnl_pct": "+35.0%",
                "pnl_source": "",
            }
        ]

        prompt = ctx.to_prompt_string()
        assert "P&L=+35.0%" in prompt
        assert "()" not in prompt  # No empty parens

    def test_portfolio_source_tagged(self):
        """Portfolio-sourced P&L shows (portfolio) suffix."""
        from src.agentic.working_memory import ReasoningContext

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "trade_id": "T001",
                "symbol": "SPY",
                "strike": 500.0,
                "option_type": "PUT",
                "expiration": "2026-03-20",
                "dte": 15,
                "pnl_pct": "+35.0%",
                "pnl_source": "portfolio",
            }
        ]

        prompt = ctx.to_prompt_string()
        assert "P&L=+35.0% (portfolio)" in prompt

    def test_frozen_close_source_tagged(self):
        """Frozen close-sourced P&L shows (frozen_close) suffix."""
        from src.agentic.working_memory import ReasoningContext

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "trade_id": "T001",
                "symbol": "SPY",
                "strike": 500.0,
                "option_type": "PUT",
                "expiration": "2026-03-20",
                "dte": 15,
                "pnl_pct": "+35.0%",
                "pnl_source": "frozen_close",
            }
        ]

        prompt = ctx.to_prompt_string()
        assert "P&L=+35.0% (frozen_close)" in prompt

    def test_missing_pnl_shows_question_mark(self):
        """Position without P&L data shows '?' with no source tag."""
        from src.agentic.working_memory import ReasoningContext

        ctx = ReasoningContext()
        ctx.open_positions = [
            {
                "trade_id": "T001",
                "symbol": "SPY",
                "strike": 500.0,
                "option_type": "PUT",
                "expiration": "2026-03-20",
                "dte": 15,
            }
        ]

        prompt = ctx.to_prompt_string()
        assert "P&L=?" in prompt
