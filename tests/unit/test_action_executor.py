"""Tests for ActionExecutor which routes decisions to existing trading functions.

Covers:
- execute() with MONITOR_ONLY returns success
- execute() checks autonomy gate before executing
- execute() escalates when gate denies
- _escalate() adds to pending approvals
- get_pending_approvals() returns pending items
- approve() marks approval
- reject() marks rejection and records override
"""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agentic.action_executor import ActionExecutor, ExecutionResult
from src.agentic.autonomy_governor import AutonomyDecision, AutonomyGovernor
from src.agentic.reasoning_engine import DecisionOutput
from src.data.database import close_database, init_database


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
def governor(db_session):
    return AutonomyGovernor(db_session)


@pytest.fixture
def executor(db_session, governor):
    return ActionExecutor(db_session=db_session, governor=governor)


def _make_decision(
    action: str = "MONITOR_ONLY",
    confidence: float = 0.9,
    reasoning: str = "Test reasoning",
    key_factors: list | None = None,
    risks_considered: list | None = None,
    metadata: dict | None = None,
) -> DecisionOutput:
    """Helper to create a DecisionOutput."""
    return DecisionOutput(
        action=action,
        confidence=confidence,
        reasoning=reasoning,
        key_factors=key_factors or ["factor1"],
        risks_considered=risks_considered or ["risk1"],
        metadata=metadata or {},
    )


class TestExecuteMonitorOnly:
    """Tests for execute() with MONITOR_ONLY action."""

    def test_monitor_only_returns_success(self, executor):
        """MONITOR_ONLY always succeeds and requires no gate check."""
        decision = _make_decision(action="MONITOR_ONLY", confidence=0.95)

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        assert result.success is True
        assert result.action == "MONITOR_ONLY"
        assert "Test reasoning" in result.message

    def test_monitor_only_does_not_escalate(self, executor):
        """MONITOR_ONLY should not add to pending approvals."""
        decision = _make_decision(action="MONITOR_ONLY", confidence=0.95)

        asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        assert len(executor.get_pending_approvals()) == 0


class TestExecuteAutonomyGate:
    """Tests for autonomy gate checks in execute()."""

    def test_gate_approved_executes_action(self, executor, governor):
        """When gate approves, the action handler is called."""
        # Set governor to L4 (full autonomy) so gate always approves
        governor.level = 4

        decision = _make_decision(action="MONITOR_ONLY", confidence=0.9)

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        assert result.success is True

    def test_gate_denied_without_escalation(self, executor, governor):
        """When gate denies without escalation, returns failure."""
        # Mock the governor to deny without escalation
        governor.can_execute = MagicMock(
            return_value=AutonomyDecision(
                approved=False,
                level=1,
                reason="Denied for testing",
                escalation_required=False,
            )
        )

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.9)

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        assert result.success is False
        assert "Autonomy gate denied" in result.message

    def test_gate_denied_with_escalation_triggers_escalate(self, executor, governor):
        """When gate denies with escalation_required=True, decision is escalated."""
        governor.can_execute = MagicMock(
            return_value=AutonomyDecision(
                approved=False,
                level=1,
                reason="Needs human review",
                escalation_required=True,
                escalation_trigger="l1_approval_required",
            )
        )

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.85)

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        # Escalation itself is a success
        assert result.success is True
        assert result.action == "REQUEST_HUMAN_REVIEW"
        assert len(executor.get_pending_approvals()) == 1

    def test_gate_checks_confidence(self, executor, governor):
        """Low confidence triggers mandatory escalation."""
        # L1 governor with default config: confidence < 0.6 triggers escalation
        governor.level = 2

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.3)

        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        # Should escalate due to low confidence mandatory trigger
        assert result.action == "REQUEST_HUMAN_REVIEW"
        assert len(executor.get_pending_approvals()) == 1


class TestEscalate:
    """Tests for the _escalate() method."""

    def test_escalate_adds_to_pending_approvals(self, executor, governor):
        """_escalate() should add a new entry to pending approvals list."""
        decision = _make_decision(
            action="EXECUTE_TRADES",
            confidence=0.8,
            reasoning="Want to execute a trade",
        )
        gate = AutonomyDecision(
            approved=False,
            level=1,
            reason="L1: needs approval",
            escalation_required=True,
            escalation_trigger="l1_approval_required",
        )

        result = asyncio.get_event_loop().run_until_complete(
            executor._escalate(decision, gate)
        )

        assert result.success is True
        assert result.action == "REQUEST_HUMAN_REVIEW"

        pending = executor.get_pending_approvals()
        assert len(pending) == 1
        assert pending[0]["status"] == "pending"
        assert pending[0]["decision"]["action"] == "EXECUTE_TRADES"
        assert pending[0]["escalation_reason"] == "L1: needs approval"
        assert pending[0]["escalation_trigger"] == "l1_approval_required"

    def test_escalate_stores_decision_details(self, executor, governor):
        """Escalated items should contain full decision information."""
        decision = _make_decision(
            action="CLOSE_POSITION",
            confidence=0.75,
            reasoning="Position approaching stop loss",
            key_factors=["delta_high", "vix_elevated"],
            risks_considered=["early_exit", "whipsaw"],
            metadata={"position_id": "POS-123"},
        )
        gate = AutonomyDecision(
            approved=False,
            level=2,
            reason="Close requires review",
            escalation_required=True,
            escalation_trigger="test_trigger",
        )

        asyncio.get_event_loop().run_until_complete(
            executor._escalate(decision, gate)
        )

        pending = executor.get_pending_approvals()
        assert len(pending) == 1
        item = pending[0]
        assert item["decision"]["confidence"] == 0.75
        assert item["decision"]["key_factors"] == ["delta_high", "vix_elevated"]
        assert item["decision"]["risks_considered"] == ["early_exit", "whipsaw"]
        assert item["decision"]["metadata"] == {"position_id": "POS-123"}
        assert "queued_at" in item

    def test_multiple_escalations_accumulate(self, executor, governor):
        """Multiple escalations should accumulate in pending list."""
        for i in range(3):
            decision = _make_decision(
                action="EXECUTE_TRADES",
                confidence=0.8,
                reasoning=f"Trade attempt {i}",
            )
            gate = AutonomyDecision(
                approved=False,
                level=1,
                reason=f"Needs approval #{i}",
                escalation_required=True,
                escalation_trigger="l1_approval_required",
            )
            asyncio.get_event_loop().run_until_complete(
                executor._escalate(decision, gate)
            )

        assert len(executor.get_pending_approvals()) == 3


class TestGetPendingApprovals:
    """Tests for get_pending_approvals()."""

    def test_returns_empty_initially(self, executor):
        """No pending approvals on a fresh executor."""
        assert executor.get_pending_approvals() == []

    def test_returns_only_pending_items(self, executor, governor):
        """After approving one item, it should no longer appear in pending."""
        # Add two escalations
        for i in range(2):
            decision = _make_decision(action="EXECUTE_TRADES", reasoning=f"Trade {i}")
            gate = AutonomyDecision(
                approved=False, level=1, reason=f"Reason {i}",
                escalation_required=True, escalation_trigger="test",
            )
            asyncio.get_event_loop().run_until_complete(
                executor._escalate(decision, gate)
            )

        assert len(executor.get_pending_approvals()) == 2

        # Approve the first one
        executor.approve(0)

        assert len(executor.get_pending_approvals()) == 1


class TestApprove:
    """Tests for approve()."""

    def test_approve_marks_status(self, executor, governor):
        """approve() should change status to 'approved' and set decided_at."""
        decision = _make_decision(action="EXECUTE_TRADES")
        gate = AutonomyDecision(
            approved=False, level=1, reason="test",
            escalation_required=True, escalation_trigger="test",
        )
        asyncio.get_event_loop().run_until_complete(
            executor._escalate(decision, gate)
        )

        result = executor.approve(0)

        assert result is not None
        assert result["status"] == "approved"
        assert "decided_at" in result

    def test_approve_invalid_index_returns_none(self, executor):
        """approve() with out-of-range index returns None."""
        result = executor.approve(0)
        assert result is None

        result = executor.approve(99)
        assert result is None

    def test_approve_negative_index_returns_none(self, executor):
        """approve() with negative index returns None."""
        result = executor.approve(-1)
        assert result is None


class TestReject:
    """Tests for reject()."""

    def test_reject_marks_status_and_reason(self, executor, governor):
        """reject() should change status to 'rejected' and store reason."""
        decision = _make_decision(action="EXECUTE_TRADES")
        gate = AutonomyDecision(
            approved=False, level=1, reason="test",
            escalation_required=True, escalation_trigger="test",
        )
        asyncio.get_event_loop().run_until_complete(
            executor._escalate(decision, gate)
        )

        result = executor.reject(0, reason="Risk too high")

        assert result is not None
        assert result["status"] == "rejected"
        assert result["rejection_reason"] == "Risk too high"
        assert "decided_at" in result

    def test_reject_records_override_in_governor(self, executor, governor):
        """reject() should call governor.record_override() for demotion tracking."""
        decision = _make_decision(action="EXECUTE_TRADES")
        gate = AutonomyDecision(
            approved=False, level=1, reason="test",
            escalation_required=True, escalation_trigger="test",
        )
        asyncio.get_event_loop().run_until_complete(
            executor._escalate(decision, gate)
        )

        # Spy on record_override
        governor.record_override = MagicMock()

        executor.reject(0, reason="Not appropriate")

        governor.record_override.assert_called_once()

    def test_reject_invalid_index_returns_none(self, executor):
        """reject() with out-of-range index returns None."""
        result = executor.reject(0, reason="nope")
        assert result is None


class TestExecutionErrorHandling:
    """Tests for error handling during execution."""

    def test_handler_exception_returns_failure(self, executor, governor):
        """If action handler raises, execute() returns failure with error."""
        # Set L4 so gate passes, but handler will fail
        governor.level = 4

        decision = _make_decision(action="EXECUTE_TRADES", confidence=0.9)

        # The actual handler will fail because IBKR client and scheduler are not set up
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        assert result.success is False
        assert result.error is not None

    def test_adjust_parameters_always_escalates(self, executor, governor):
        """ADJUST_PARAMETERS action always escalates regardless of autonomy level."""
        governor.level = 4  # Even at L4

        decision = _make_decision(action="ADJUST_PARAMETERS", confidence=0.95)

        # At L4, gate passes for ADJUST_PARAMETERS, but the handler itself escalates
        # because _check_mandatory_triggers fires for "parameter_change"
        result = asyncio.get_event_loop().run_until_complete(
            executor.execute(decision)
        )

        assert result.action == "REQUEST_HUMAN_REVIEW"
