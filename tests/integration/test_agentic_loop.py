"""Integration test for the full event cycle.

Covers:
- Emit event -> process -> reason -> execute -> update memory
- Uses mocked Claude and IBKR
- Verifies audit trail in DB
"""

import asyncio
from datetime import datetime, date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agentic.action_executor import ActionExecutor
from src.agentic.autonomy_governor import AutonomyGovernor
from src.agentic.config import AutonomyConfig, ClaudeConfig, Phase5Config
from src.agentic.event_bus import EventBus, EventType
from src.agentic.health_monitor import HealthMonitor
from src.agentic.reasoning_engine import ClaudeReasoningEngine, DecisionOutput
from src.agentic.working_memory import WorkingMemory
from src.data.database import close_database, init_database
from src.data.models import DaemonEvent, DecisionAudit


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
def event_bus(db_session):
    return EventBus(db_session)


@pytest.fixture
def working_memory(db_session):
    return WorkingMemory(db_session)


@pytest.fixture
def governor(db_session):
    config = AutonomyConfig(initial_level=4, max_level=4)  # L4 for test simplicity
    return AutonomyGovernor(db_session, config)


@pytest.fixture
def mock_reasoning_engine(db_session):
    """Create a mocked reasoning engine that returns a configurable decision."""
    engine = MagicMock(spec=ClaudeReasoningEngine)

    # Default: return MONITOR_ONLY with high confidence
    engine.reason.return_value = DecisionOutput(
        action="MONITOR_ONLY",
        confidence=0.95,
        reasoning="Markets are calm, monitoring only",
        key_factors=["low_vix", "no_positions"],
        risks_considered=["market_gap"],
        metadata={},
    )

    # Mock the internal agent for token tracking
    mock_agent = MagicMock()
    mock_agent.total_input_tokens = 100
    mock_agent.total_output_tokens = 50
    mock_agent.session_cost = 0.01
    engine._reasoning_agent = mock_agent

    return engine


@pytest.fixture
def executor(db_session, governor):
    return ActionExecutor(db_session=db_session, governor=governor)


@pytest.fixture
def health_monitor(db_session, tmp_path):
    pid_file = str(tmp_path / "test_taad.pid")
    return HealthMonitor(db_session=db_session, pid_file=pid_file)


class TestFullEventCycle:
    """Integration tests for the complete event processing pipeline."""

    def test_emit_process_reason_execute_audit(
        self,
        db_session,
        event_bus,
        working_memory,
        governor,
        mock_reasoning_engine,
        executor,
        health_monitor,
    ):
        """Full cycle: emit event -> process -> reason -> execute -> audit trail.

        Simulates the daemon._process_event() pipeline manually with mocked
        Claude reasoning.
        """
        # Step 0: Start health monitor
        health_monitor.start()

        # Step 1: Emit an event
        event = event_bus.emit(EventType.SCHEDULED_CHECK)
        assert event.event_type == "SCHEDULED_CHECK"
        assert event.status == "pending"

        # Step 2: Get pending events
        pending = event_bus.get_pending_events()
        assert len(pending) == 1

        # Step 3: Process event (simulating daemon._process_event)
        event = pending[0]
        event_bus.mark_processing(event)
        health_monitor.record_event(event.event_type)

        # Step 4: Assemble context
        context = working_memory.assemble_context(event.event_type)
        assert context.autonomy_level == 1  # Default level

        # Step 5: Reason with Claude (mocked)
        decision = mock_reasoning_engine.reason(
            context=context,
            event_type=event.event_type,
            event_payload=event.payload,
        )
        assert decision.action == "MONITOR_ONLY"
        assert decision.confidence == 0.95

        # Step 6: Log decision to audit
        audit = DecisionAudit(
            event_id=event.id,
            timestamp=datetime.utcnow(),
            autonomy_level=governor.level,
            event_type=event.event_type,
            action=decision.action,
            confidence=decision.confidence,
            reasoning=decision.reasoning,
            key_factors=decision.key_factors,
            risks_considered=decision.risks_considered,
            autonomy_approved=False,
            input_tokens=100,
            output_tokens=50,
            model_used="claude-opus-4-6",
            cost_usd=0.01,
        )

        # Step 7: Execute action
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision, context={})
        )

        # Update audit with result
        audit.autonomy_approved = result.success
        audit.executed = result.success and result.action not in (
            "MONITOR_ONLY",
            "REQUEST_HUMAN_REVIEW",
        )
        audit.execution_result = {"message": result.message}
        db_session.add(audit)
        db_session.commit()

        health_monitor.record_decision()

        # Step 8: Update working memory
        working_memory.add_decision(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "event_type": event.event_type,
                "action": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning[:200],
                "executed": audit.executed,
                "result": result.message[:200],
            }
        )

        # Step 9: Mark event complete
        event_bus.mark_completed(event)

        # === Verify Audit Trail ===

        # Verify DaemonEvent status
        db_session.refresh(event)
        assert event.status == "completed"
        assert event.completed_at is not None

        # Verify DecisionAudit in DB
        audits = db_session.query(DecisionAudit).all()
        assert len(audits) == 1
        audit_row = audits[0]
        assert audit_row.event_type == "SCHEDULED_CHECK"
        assert audit_row.action == "MONITOR_ONLY"
        assert audit_row.confidence == 0.95
        assert audit_row.autonomy_approved is True
        assert audit_row.executed is False  # MONITOR_ONLY is not "executed"
        assert audit_row.input_tokens == 100
        assert audit_row.output_tokens == 50

        # Verify working memory was updated
        assert len(working_memory.recent_decisions) >= 1
        last = list(working_memory.recent_decisions)[-1]
        assert last["action"] == "MONITOR_ONLY"
        assert last["event_type"] == "SCHEDULED_CHECK"

        # Verify health monitor counters
        assert health_monitor._events_processed == 1
        assert health_monitor._decisions_made == 1

    def test_multiple_events_sequential_processing(
        self,
        db_session,
        event_bus,
        working_memory,
        governor,
        mock_reasoning_engine,
        executor,
        health_monitor,
    ):
        """Multiple events should be processed sequentially with audit for each."""
        health_monitor.start()

        # Emit 3 events
        events = [
            event_bus.emit(EventType.SCHEDULED_CHECK),
            event_bus.emit(EventType.HEARTBEAT),
            event_bus.emit(EventType.SCHEDULED_CHECK),
        ]

        # Process each event
        for event in events:
            event_bus.mark_processing(event)
            health_monitor.record_event(event.event_type)

            context = working_memory.assemble_context(event.event_type)
            decision = mock_reasoning_engine.reason(
                context=context,
                event_type=event.event_type,
                event_payload=event.payload,
            )

            result = asyncio.get_event_loop().run_until_complete(
                executor.execute(decision, context={})
            )

            audit = DecisionAudit(
                event_id=event.id,
                timestamp=datetime.utcnow(),
                autonomy_level=governor.level,
                event_type=event.event_type,
                action=decision.action,
                confidence=decision.confidence,
                reasoning=decision.reasoning,
                autonomy_approved=result.success,
                executed=False,
            )
            db_session.add(audit)
            db_session.commit()

            health_monitor.record_decision()
            event_bus.mark_completed(event)

        # Verify all audits were created
        audits = db_session.query(DecisionAudit).all()
        assert len(audits) == 3

        # Verify all events were completed
        for event in events:
            db_session.refresh(event)
            assert event.status == "completed"

        # Verify health counters
        assert health_monitor._events_processed == 3
        assert health_monitor._decisions_made == 3

    def test_event_failure_recorded(
        self,
        db_session,
        event_bus,
        health_monitor,
    ):
        """When event processing fails, the event should be marked as failed."""
        health_monitor.start()

        event = event_bus.emit(EventType.SCHEDULED_CHECK)
        event_bus.mark_processing(event)

        # Simulate failure
        event_bus.mark_failed(event, "Simulated failure: Claude API timeout")
        health_monitor.record_error()

        # Verify event is marked failed
        db_session.refresh(event)
        assert event.status == "failed"
        assert event.error_message == "Simulated failure: Claude API timeout"

        # Verify error counter
        assert health_monitor._errors == 1

    def test_priority_ordering(self, db_session, event_bus):
        """Events should be retrievable in priority order."""
        # Emit events in reverse priority order
        low_event = event_bus.emit(EventType.HEARTBEAT)  # priority 5
        high_event = event_bus.emit(EventType.ORDER_FILLED)  # priority 2
        critical_event = event_bus.emit(EventType.EMERGENCY_STOP)  # priority 1

        # Get pending events - should be in priority order
        pending = event_bus.get_pending_events()
        assert len(pending) == 3
        assert pending[0].event_type == "EMERGENCY_STOP"
        assert pending[1].event_type == "ORDER_FILLED"
        assert pending[2].event_type == "HEARTBEAT"

    def test_working_memory_persists_across_events(
        self,
        db_session,
        event_bus,
        working_memory,
    ):
        """Working memory decisions should accumulate across events."""
        for i in range(5):
            working_memory.add_decision(
                {
                    "timestamp": datetime.utcnow().isoformat(),
                    "event_type": "SCHEDULED_CHECK",
                    "action": "MONITOR_ONLY",
                    "confidence": 0.9 + i * 0.01,
                    "reasoning": f"Decision #{i}",
                    "executed": False,
                    "result": "OK",
                }
            )

        assert len(working_memory.recent_decisions) == 5

        # Reload working memory from DB (simulating restart)
        reloaded = WorkingMemory(db_session)
        assert len(reloaded.recent_decisions) == 5
        assert list(reloaded.recent_decisions)[-1]["reasoning"] == "Decision #4"

    def test_escalation_creates_audit_trail(
        self,
        db_session,
        event_bus,
        working_memory,
        executor,
    ):
        """When action is escalated, audit trail should show escalation."""
        # Create a governor at L1 (everything escalates)
        l1_config = AutonomyConfig(initial_level=1)
        l1_governor = AutonomyGovernor(db_session, l1_config)
        l1_executor = ActionExecutor(db_session=db_session, governor=l1_governor)

        decision = DecisionOutput(
            action="EXECUTE_TRADES",
            confidence=0.85,
            reasoning="Ready to execute",
            key_factors=["green_light"],
            risks_considered=["market_gap"],
            metadata={},
        )

        result = asyncio.get_event_loop().run_until_complete(
            l1_executor.execute(decision, context={})
        )

        # Escalation is a "success" in that the escalation itself worked
        assert result.success is True
        assert result.action == "REQUEST_HUMAN_REVIEW"

        # Verify pending approvals list
        pending = l1_executor.get_pending_approvals()
        assert len(pending) == 1
        assert pending[0]["decision"]["action"] == "EXECUTE_TRADES"
