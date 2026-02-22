"""Tests for LearningLoop EOD reflection and outcome feedback.

Covers:
- run_weekly_learning() delegates to LearningOrchestrator
- record_trade_outcome() links trade to decision
- run_eod_reflection() calls Claude and stores result (mock Claude)
"""

import asyncio
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.agentic.learning_loop import LearningLoop
from src.agentic.reasoning_engine import ClaudeReasoningEngine
from src.agentic.working_memory import WorkingMemory
from src.data.database import close_database, init_database
from src.data.models import DecisionAudit, Trade


@pytest.fixture
def temp_database():
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database):
    from src.data.database import get_session

    session = get_session()
    yield session
    session.close()


@pytest.fixture
def mock_reasoning_engine(db_session):
    """Create a mocked ClaudeReasoningEngine."""
    engine = MagicMock(spec=ClaudeReasoningEngine)
    return engine


@pytest.fixture
def working_memory(db_session):
    """Create a real WorkingMemory backed by in-memory DB."""
    return WorkingMemory(db_session)


@pytest.fixture
def learning_loop(db_session, mock_reasoning_engine, working_memory):
    """Create a LearningLoop with mocked reasoning engine."""
    loop = LearningLoop(
        db_session=db_session,
        reasoning_engine=mock_reasoning_engine,
        working_memory=working_memory,
    )
    return loop


def _create_trade(
    db_session,
    trade_id: str = "TEST-001",
    symbol: str = "AAPL",
    strike: float = 200.0,
    entry_premium: float = 0.50,
    entry_date: datetime | None = None,
    exit_date: datetime | None = None,
    exit_premium: float | None = None,
    profit_loss: float | None = None,
    roi: float | None = None,
    exit_reason: str | None = None,
    days_held: int | None = None,
) -> Trade:
    """Helper to create a Trade in the database."""
    trade = Trade(
        trade_id=trade_id,
        symbol=symbol,
        strike=strike,
        expiration=date.today() + timedelta(days=30),
        option_type="PUT",
        entry_date=entry_date or datetime.utcnow(),
        entry_premium=entry_premium,
        contracts=1,
        dte=30,
        exit_date=exit_date,
        exit_premium=exit_premium,
        profit_loss=profit_loss,
        roi=roi,
        exit_reason=exit_reason,
        days_held=days_held,
    )
    db_session.add(trade)
    db_session.commit()
    return trade


def _create_decision_audit(
    db_session,
    action: str = "EXECUTE_TRADES",
    confidence: float = 0.85,
    reasoning: str = "Test decision",
    executed: bool = True,
    timestamp: datetime | None = None,
) -> DecisionAudit:
    """Helper to create a DecisionAudit in the database."""
    audit = DecisionAudit(
        timestamp=timestamp or datetime.utcnow(),
        autonomy_level=1,
        event_type="SCHEDULED_CHECK",
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        autonomy_approved=True,
        executed=executed,
    )
    db_session.add(audit)
    db_session.commit()
    return audit


class TestRunWeeklyLearning:
    """Tests for run_weekly_learning()."""

    def test_delegates_to_orchestrator(self, learning_loop):
        """run_weekly_learning() should call orchestrator.run_weekly_analysis()."""
        mock_report = MagicMock()
        mock_report.timestamp = datetime.now()
        mock_report.total_trades_analyzed = 50
        mock_report.patterns_detected = 3
        mock_report.patterns_validated = 2
        mock_report.experiments_adopted = ["exp1"]
        mock_report.experiments_rejected = []
        mock_report.changes_applied = ["change1"]
        mock_report.baseline_win_rate = 0.75
        mock_report.baseline_avg_roi = 0.05

        learning_loop.orchestrator.run_weekly_analysis = MagicMock(
            return_value=mock_report
        )

        result = learning_loop.run_weekly_learning()

        learning_loop.orchestrator.run_weekly_analysis.assert_called_once()
        assert result["total_trades"] == 50
        assert result["patterns_detected"] == 3
        assert result["patterns_validated"] == 2
        assert result["experiments_adopted"] == 1
        assert result["experiments_rejected"] == 0
        assert result["changes_applied"] == 1
        assert result["baseline_win_rate"] == 0.75
        assert result["baseline_avg_roi"] == 0.05

    def test_returns_error_on_failure(self, learning_loop):
        """run_weekly_learning() should return error dict on exception."""
        learning_loop.orchestrator.run_weekly_analysis = MagicMock(
            side_effect=RuntimeError("DB connection lost")
        )

        result = learning_loop.run_weekly_learning()

        assert "error" in result
        assert "DB connection lost" in result["error"]

    def test_returns_timestamp(self, learning_loop):
        """run_weekly_learning() should include a timestamp in the result."""
        mock_report = MagicMock()
        mock_report.timestamp = datetime(2026, 2, 19, 16, 0, 0)
        mock_report.total_trades_analyzed = 10
        mock_report.patterns_detected = 1
        mock_report.patterns_validated = 1
        mock_report.experiments_adopted = []
        mock_report.experiments_rejected = []
        mock_report.changes_applied = []
        mock_report.baseline_win_rate = 0.60
        mock_report.baseline_avg_roi = 0.03

        learning_loop.orchestrator.run_weekly_analysis = MagicMock(
            return_value=mock_report
        )

        result = learning_loop.run_weekly_learning()

        assert "timestamp" in result


class TestRecordTradeOutcome:
    """Tests for record_trade_outcome()."""

    def test_links_trade_to_decision(self, learning_loop, db_session):
        """record_trade_outcome() should link the closed trade to its originating decision."""
        # Create a decision audit that preceded the trade
        decision = _create_decision_audit(
            db_session,
            action="EXECUTE_TRADES",
            executed=True,
            timestamp=datetime(2026, 2, 15, 10, 0, 0),
        )

        # Create a trade that was opened after the decision
        trade = _create_trade(
            db_session,
            trade_id="OUTCOME-001",
            entry_date=datetime(2026, 2, 15, 10, 5, 0),
            exit_date=datetime(2026, 2, 18, 14, 0, 0),
            exit_premium=0.10,
            profit_loss=40.0,
            roi=0.80,
            exit_reason="profit_target",
            days_held=3,
        )

        # Mock orchestrator.on_trade_closed to avoid real learning operations
        learning_loop.orchestrator.on_trade_closed = MagicMock()

        learning_loop.record_trade_outcome("OUTCOME-001")

        # Should have called on_trade_closed
        learning_loop.orchestrator.on_trade_closed.assert_called_once_with("OUTCOME-001")

        # Should have added to working memory
        assert len(learning_loop.memory.recent_decisions) >= 1
        last_decision = learning_loop.memory.recent_decisions[-1]
        assert last_decision["type"] == "outcome_feedback"
        assert last_decision["trade_id"] == "OUTCOME-001"
        assert last_decision["profit_loss"] == 40.0

    def test_skips_if_trade_not_found(self, learning_loop, db_session):
        """record_trade_outcome() should silently return if trade doesn't exist."""
        learning_loop.orchestrator.on_trade_closed = MagicMock()

        # Should not raise
        learning_loop.record_trade_outcome("NONEXISTENT-001")

        learning_loop.orchestrator.on_trade_closed.assert_not_called()

    def test_skips_if_trade_not_closed(self, learning_loop, db_session):
        """record_trade_outcome() should skip if trade has no exit_date."""
        _create_trade(
            db_session,
            trade_id="OPEN-001",
            exit_date=None,
        )

        learning_loop.orchestrator.on_trade_closed = MagicMock()

        learning_loop.record_trade_outcome("OPEN-001")

        learning_loop.orchestrator.on_trade_closed.assert_not_called()

    def test_handles_no_linked_decision(self, learning_loop, db_session):
        """record_trade_outcome() should work even if no decision is found."""
        trade = _create_trade(
            db_session,
            trade_id="ORPHAN-001",
            entry_date=datetime(2026, 2, 15, 10, 0, 0),
            exit_date=datetime(2026, 2, 18, 14, 0, 0),
            exit_premium=0.10,
            profit_loss=40.0,
            roi=0.80,
            exit_reason="profit_target",
            days_held=3,
        )

        learning_loop.orchestrator.on_trade_closed = MagicMock()

        # Should not raise even without a linked decision
        learning_loop.record_trade_outcome("ORPHAN-001")

        # Should still add to working memory with linked_decision_id=None
        last_decision = learning_loop.memory.recent_decisions[-1]
        assert last_decision["linked_decision_id"] is None


class TestRunEodReflection:
    """Tests for run_eod_reflection()."""

    def test_calls_claude_and_stores_result(self, learning_loop, db_session, mock_reasoning_engine):
        """run_eod_reflection() should call Claude's reflect() and store result in memory."""
        # Create a decision audit for today
        _create_decision_audit(
            db_session,
            action="EXECUTE_TRADES",
            confidence=0.85,
            reasoning="Executed morning trades",
            timestamp=datetime.now(),
        )

        # Create a trade for today
        _create_trade(
            db_session,
            trade_id="TODAY-001",
            entry_date=datetime.now(),
            entry_premium=0.50,
        )

        # Mock the reflection response from Claude
        mock_reflection = {
            "correct_decisions": ["Morning trade execution was well-timed"],
            "lucky_decisions": [],
            "wrong_decisions": [],
            "patterns_to_investigate": ["VIX below 15 performance"],
            "prior_updates": [],
            "summary": "Good trading day with one well-timed execution",
        }
        mock_reasoning_engine.reflect.return_value = mock_reflection

        result = asyncio.get_event_loop().run_until_complete(
            learning_loop.run_eod_reflection()
        )

        # Verify Claude was called
        mock_reasoning_engine.reflect.assert_called_once()

        # Verify the call args contain decisions and trades data
        call_args = mock_reasoning_engine.reflect.call_args
        decisions_data = call_args[0][0]
        trades_data = call_args[0][1]
        assert len(decisions_data) >= 1
        assert len(trades_data) >= 1

        # Verify result
        assert result["summary"] == "Good trading day with one well-timed execution"
        assert result["decisions_count"] >= 1
        assert result["trades_count"] >= 1
        assert "date" in result

        # Verify stored in working memory
        assert len(learning_loop.memory.reflection_reports) >= 1

    def test_no_activity_skips_claude(self, learning_loop, mock_reasoning_engine):
        """run_eod_reflection() should skip Claude when no decisions or trades today."""
        result = asyncio.get_event_loop().run_until_complete(
            learning_loop.run_eod_reflection()
        )

        # Should NOT have called Claude
        mock_reasoning_engine.reflect.assert_not_called()

        assert result["summary"] == "No decisions or trades today"
        assert "date" in result

    def test_reflection_includes_date(self, learning_loop, db_session, mock_reasoning_engine):
        """run_eod_reflection() result should include today's date."""
        _create_decision_audit(db_session, timestamp=datetime.now())

        mock_reasoning_engine.reflect.return_value = {
            "summary": "Test reflection",
        }

        result = asyncio.get_event_loop().run_until_complete(
            learning_loop.run_eod_reflection()
        )

        assert result["date"] == str(date.today())

    def test_reflection_adds_to_memory(self, learning_loop, db_session, mock_reasoning_engine):
        """run_eod_reflection() should add reflection to working memory."""
        _create_decision_audit(db_session, timestamp=datetime.now())

        mock_reasoning_engine.reflect.return_value = {
            "summary": "End of day analysis",
            "patterns_to_investigate": ["delta_range"],
        }

        initial_count = len(learning_loop.memory.reflection_reports)

        asyncio.get_event_loop().run_until_complete(
            learning_loop.run_eod_reflection()
        )

        assert len(learning_loop.memory.reflection_reports) == initial_count + 1
        latest = learning_loop.memory.reflection_reports[-1]
        assert latest["summary"] == "End of day analysis"

    def test_reflection_serializes_decisions_correctly(
        self, learning_loop, db_session, mock_reasoning_engine
    ):
        """run_eod_reflection() should serialize decision audit data for Claude."""
        _create_decision_audit(
            db_session,
            action="MONITOR_ONLY",
            confidence=0.92,
            reasoning="Markets calm, monitoring",
            executed=False,
            timestamp=datetime.now(),
        )

        mock_reasoning_engine.reflect.return_value = {"summary": "All clear"}

        asyncio.get_event_loop().run_until_complete(
            learning_loop.run_eod_reflection()
        )

        call_args = mock_reasoning_engine.reflect.call_args
        decisions_data = call_args[0][0]

        assert len(decisions_data) == 1
        assert decisions_data[0]["action"] == "MONITOR_ONLY"
        assert decisions_data[0]["confidence"] == 0.92
        assert decisions_data[0]["reasoning"] == "Markets calm, monitoring"
        assert decisions_data[0]["executed"] is False
