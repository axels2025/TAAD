"""Unit tests for opportunity state machine.

Tests the OpportunityState enum, StateTransition dataclass, and state
transition validation logic.
"""

import pytest
from datetime import datetime

from src.data.opportunity_state import (
    OpportunityState,
    StateTransition,
    is_valid_transition,
    is_terminal_state,
    get_valid_next_states,
    TERMINAL_STATES,
)


class TestOpportunityState:
    """Tests for OpportunityState enum."""

    def test_all_states_defined(self):
        """Test that all expected states are defined."""
        expected_states = {
            # Original states
            "PENDING",
            "ENRICHED",
            "VALIDATED",
            "RISK_BLOCKED",
            "OFFERED",
            "APPROVED",
            "REJECTED",
            "SKIPPED",
            "EXECUTING",
            "EXECUTED",
            "FAILED",
            "EXPIRED",
            # Phase 4: Sunday-to-Monday workflow states
            "STAGED",
            "VALIDATING",
            "READY",
            "STALE",
            "ADJUSTING",
            "CONFIRMED",
        }
        actual_states = {state.name for state in OpportunityState}
        assert actual_states == expected_states

    def test_state_names_uppercase(self):
        """Test that all state names are uppercase."""
        for state in OpportunityState:
            assert state.name == state.name.upper()


class TestStateTransition:
    """Tests for StateTransition dataclass."""

    def test_create_transition(self):
        """Test creating a state transition."""
        transition = StateTransition(
            from_state=OpportunityState.PENDING,
            to_state=OpportunityState.ENRICHED,
            timestamp=datetime(2026, 1, 28, 10, 0, 0),
            reason="Data enrichment completed",
            actor="system",
            metadata={"duration_ms": 1500},
        )

        assert transition.from_state == OpportunityState.PENDING
        assert transition.to_state == OpportunityState.ENRICHED
        assert transition.timestamp == datetime(2026, 1, 28, 10, 0, 0)
        assert transition.reason == "Data enrichment completed"
        assert transition.actor == "system"
        assert transition.metadata == {"duration_ms": 1500}

    def test_transition_to_dict(self):
        """Test converting transition to dictionary."""
        transition = StateTransition(
            from_state=OpportunityState.VALIDATED,
            to_state=OpportunityState.OFFERED,
            timestamp=datetime(2026, 1, 28, 10, 0, 0),
            reason="Presenting to user",
            actor="system",
            metadata={"rank": 1},
        )

        result = transition.to_dict()

        assert result["from_state"] == "VALIDATED"
        assert result["to_state"] == "OFFERED"
        assert result["timestamp"] == "2026-01-28T10:00:00"
        assert result["reason"] == "Presenting to user"
        assert result["actor"] == "system"
        assert result["metadata"] == {"rank": 1}

    def test_transition_from_dict(self):
        """Test creating transition from dictionary."""
        data = {
            "from_state": "APPROVED",
            "to_state": "EXECUTING",
            "timestamp": "2026-01-28T10:00:00",
            "reason": "Placing order",
            "actor": "order_executor",
            "metadata": {"order_id": "12345"},
        }

        transition = StateTransition.from_dict(data)

        assert transition.from_state == OpportunityState.APPROVED
        assert transition.to_state == OpportunityState.EXECUTING
        assert transition.timestamp == datetime(2026, 1, 28, 10, 0, 0)
        assert transition.reason == "Placing order"
        assert transition.actor == "order_executor"
        assert transition.metadata == {"order_id": "12345"}

    def test_transition_default_metadata(self):
        """Test that metadata defaults to empty dict."""
        transition = StateTransition(
            from_state=OpportunityState.PENDING,
            to_state=OpportunityState.ENRICHED,
            timestamp=datetime.now(),
            reason="Test",
            actor="system",
        )

        assert transition.metadata == {}


class TestValidTransitions:
    """Tests for state transition validation."""

    def test_valid_transition_pending_to_enriched(self):
        """Test valid transition from PENDING to ENRICHED."""
        assert is_valid_transition(OpportunityState.PENDING, OpportunityState.ENRICHED)

    def test_valid_transition_enriched_to_validated(self):
        """Test valid transition from ENRICHED to VALIDATED."""
        assert is_valid_transition(OpportunityState.ENRICHED, OpportunityState.VALIDATED)

    def test_valid_transition_validated_to_offered(self):
        """Test valid transition from VALIDATED to OFFERED."""
        assert is_valid_transition(OpportunityState.VALIDATED, OpportunityState.OFFERED)

    def test_valid_transition_offered_to_approved(self):
        """Test valid transition from OFFERED to APPROVED."""
        assert is_valid_transition(OpportunityState.OFFERED, OpportunityState.APPROVED)

    def test_valid_transition_approved_to_executing(self):
        """Test valid transition from APPROVED to EXECUTING."""
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.EXECUTING)

    def test_valid_transition_executing_to_executed(self):
        """Test valid transition from EXECUTING to EXECUTED."""
        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.EXECUTED)

    def test_valid_transition_to_risk_blocked(self):
        """Test valid transition to RISK_BLOCKED from various states."""
        assert is_valid_transition(OpportunityState.ENRICHED, OpportunityState.RISK_BLOCKED)
        assert is_valid_transition(OpportunityState.VALIDATED, OpportunityState.RISK_BLOCKED)

    def test_valid_transition_to_expired(self):
        """Test valid transition to EXPIRED from various states."""
        assert is_valid_transition(OpportunityState.PENDING, OpportunityState.EXPIRED)
        assert is_valid_transition(OpportunityState.ENRICHED, OpportunityState.EXPIRED)
        assert is_valid_transition(OpportunityState.VALIDATED, OpportunityState.EXPIRED)
        assert is_valid_transition(OpportunityState.OFFERED, OpportunityState.EXPIRED)

    def test_invalid_transition_pending_to_executed(self):
        """Test invalid transition skipping states."""
        assert not is_valid_transition(OpportunityState.PENDING, OpportunityState.EXECUTED)

    def test_invalid_transition_from_terminal_state(self):
        """Test that terminal states cannot transition."""
        assert not is_valid_transition(OpportunityState.EXECUTED, OpportunityState.PENDING)
        assert not is_valid_transition(OpportunityState.FAILED, OpportunityState.EXECUTING)
        assert not is_valid_transition(OpportunityState.REJECTED, OpportunityState.APPROVED)
        assert not is_valid_transition(OpportunityState.EXPIRED, OpportunityState.ENRICHED)

    def test_invalid_transition_backwards(self):
        """Test that backwards transitions are not allowed."""
        assert not is_valid_transition(OpportunityState.EXECUTED, OpportunityState.EXECUTING)
        assert not is_valid_transition(OpportunityState.EXECUTING, OpportunityState.APPROVED)
        assert not is_valid_transition(OpportunityState.ENRICHED, OpportunityState.PENDING)


class TestTerminalStates:
    """Tests for terminal state detection."""

    def test_terminal_states_defined(self):
        """Test that all terminal states are defined."""
        expected_terminal = {
            OpportunityState.RISK_BLOCKED,
            OpportunityState.REJECTED,
            OpportunityState.SKIPPED,
            OpportunityState.EXECUTED,
            OpportunityState.FAILED,
            OpportunityState.EXPIRED,
            OpportunityState.STALE,  # Phase 4: Skipped due to price movement
        }
        assert TERMINAL_STATES == expected_terminal

    def test_is_terminal_state_true(self):
        """Test that terminal states are correctly identified."""
        assert is_terminal_state(OpportunityState.EXECUTED)
        assert is_terminal_state(OpportunityState.FAILED)
        assert is_terminal_state(OpportunityState.REJECTED)
        assert is_terminal_state(OpportunityState.SKIPPED)
        assert is_terminal_state(OpportunityState.EXPIRED)
        assert is_terminal_state(OpportunityState.RISK_BLOCKED)

    def test_is_terminal_state_false(self):
        """Test that non-terminal states are not identified as terminal."""
        assert not is_terminal_state(OpportunityState.PENDING)
        assert not is_terminal_state(OpportunityState.ENRICHED)
        assert not is_terminal_state(OpportunityState.VALIDATED)
        assert not is_terminal_state(OpportunityState.OFFERED)
        assert not is_terminal_state(OpportunityState.APPROVED)
        assert not is_terminal_state(OpportunityState.EXECUTING)


class TestGetValidNextStates:
    """Tests for getting valid next states."""

    def test_get_valid_next_states_pending(self):
        """Test valid next states from PENDING."""
        valid_next = get_valid_next_states(OpportunityState.PENDING)
        assert valid_next == {OpportunityState.ENRICHED, OpportunityState.EXPIRED}

    def test_get_valid_next_states_enriched(self):
        """Test valid next states from ENRICHED."""
        valid_next = get_valid_next_states(OpportunityState.ENRICHED)
        assert valid_next == {
            OpportunityState.VALIDATED,
            OpportunityState.RISK_BLOCKED,
            OpportunityState.EXPIRED,
        }

    def test_get_valid_next_states_offered(self):
        """Test valid next states from OFFERED."""
        valid_next = get_valid_next_states(OpportunityState.OFFERED)
        assert valid_next == {
            OpportunityState.APPROVED,
            OpportunityState.REJECTED,
            OpportunityState.SKIPPED,
            OpportunityState.EXPIRED,
        }

    def test_get_valid_next_states_terminal(self):
        """Test that terminal states have no valid next states."""
        assert get_valid_next_states(OpportunityState.EXECUTED) == set()
        assert get_valid_next_states(OpportunityState.FAILED) == set()
        assert get_valid_next_states(OpportunityState.REJECTED) == set()
        assert get_valid_next_states(OpportunityState.EXPIRED) == set()


class TestCompleteLifecycle:
    """Tests for complete lifecycle paths."""

    def test_happy_path_lifecycle(self):
        """Test the complete happy path: PENDING -> EXECUTED."""
        # PENDING -> ENRICHED
        assert is_valid_transition(OpportunityState.PENDING, OpportunityState.ENRICHED)

        # ENRICHED -> VALIDATED
        assert is_valid_transition(OpportunityState.ENRICHED, OpportunityState.VALIDATED)

        # VALIDATED -> OFFERED
        assert is_valid_transition(OpportunityState.VALIDATED, OpportunityState.OFFERED)

        # OFFERED -> APPROVED
        assert is_valid_transition(OpportunityState.OFFERED, OpportunityState.APPROVED)

        # APPROVED -> EXECUTING
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.EXECUTING)

        # EXECUTING -> EXECUTED
        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.EXECUTED)

        # EXECUTED is terminal
        assert is_terminal_state(OpportunityState.EXECUTED)

    def test_rejection_lifecycle(self):
        """Test rejection path: PENDING -> RISK_BLOCKED."""
        # PENDING -> ENRICHED
        assert is_valid_transition(OpportunityState.PENDING, OpportunityState.ENRICHED)

        # ENRICHED -> RISK_BLOCKED
        assert is_valid_transition(OpportunityState.ENRICHED, OpportunityState.RISK_BLOCKED)

        # RISK_BLOCKED is terminal
        assert is_terminal_state(OpportunityState.RISK_BLOCKED)

    def test_user_rejection_lifecycle(self):
        """Test user rejection path: PENDING -> REJECTED."""
        # PENDING -> ENRICHED -> VALIDATED -> OFFERED
        assert is_valid_transition(OpportunityState.PENDING, OpportunityState.ENRICHED)
        assert is_valid_transition(OpportunityState.ENRICHED, OpportunityState.VALIDATED)
        assert is_valid_transition(OpportunityState.VALIDATED, OpportunityState.OFFERED)

        # OFFERED -> REJECTED
        assert is_valid_transition(OpportunityState.OFFERED, OpportunityState.REJECTED)

        # REJECTED is terminal
        assert is_terminal_state(OpportunityState.REJECTED)

    def test_execution_failure_lifecycle(self):
        """Test execution failure path: EXECUTING -> FAILED."""
        # Get to EXECUTING state
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.EXECUTING)

        # EXECUTING -> FAILED
        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.FAILED)

        # FAILED is terminal
        assert is_terminal_state(OpportunityState.FAILED)


class TestPhase4StagingWorkflow:
    """Tests for Phase 4 Sunday-to-Monday workflow states."""

    def test_approved_to_staged(self):
        """Test transition from APPROVED to STAGED."""
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.STAGED)

    def test_staged_to_validating(self):
        """Test transition from STAGED to VALIDATING."""
        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.VALIDATING)

    def test_staged_to_expired(self):
        """Test transition from STAGED to EXPIRED."""
        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.EXPIRED)

    def test_validating_to_ready(self):
        """Test transition from VALIDATING to READY (Stage 1 passed)."""
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.READY)

    def test_validating_to_stale(self):
        """Test transition from VALIDATING to STALE (price moved too much)."""
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.STALE)

    def test_validating_to_adjusting(self):
        """Test transition from VALIDATING to ADJUSTING (needs adjustment)."""
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.ADJUSTING)

    def test_validating_to_confirmed(self):
        """Test transition from VALIDATING to CONFIRMED (direct path)."""
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.CONFIRMED)

    def test_ready_to_validating(self):
        """Test transition from READY to VALIDATING (Stage 2 recheck)."""
        assert is_valid_transition(OpportunityState.READY, OpportunityState.VALIDATING)

    def test_ready_to_confirmed(self):
        """Test transition from READY to CONFIRMED (Stage 2 passed)."""
        assert is_valid_transition(OpportunityState.READY, OpportunityState.CONFIRMED)

    def test_ready_to_stale(self):
        """Test transition from READY to STALE (failed at open)."""
        assert is_valid_transition(OpportunityState.READY, OpportunityState.STALE)

    def test_ready_to_adjusting(self):
        """Test transition from READY to ADJUSTING (needs adjustment at open)."""
        assert is_valid_transition(OpportunityState.READY, OpportunityState.ADJUSTING)

    def test_adjusting_to_ready(self):
        """Test transition from ADJUSTING to READY (adjustment successful)."""
        assert is_valid_transition(OpportunityState.ADJUSTING, OpportunityState.READY)

    def test_adjusting_to_confirmed(self):
        """Test transition from ADJUSTING to CONFIRMED (adjustment at open)."""
        assert is_valid_transition(OpportunityState.ADJUSTING, OpportunityState.CONFIRMED)

    def test_adjusting_to_stale(self):
        """Test transition from ADJUSTING to STALE (no viable adjustment)."""
        assert is_valid_transition(OpportunityState.ADJUSTING, OpportunityState.STALE)

    def test_confirmed_to_executing(self):
        """Test transition from CONFIRMED to EXECUTING."""
        assert is_valid_transition(OpportunityState.CONFIRMED, OpportunityState.EXECUTING)

    def test_stale_is_terminal(self):
        """Test that STALE is a terminal state."""
        assert is_terminal_state(OpportunityState.STALE)
        assert get_valid_next_states(OpportunityState.STALE) == set()

    def test_invalid_staged_transitions(self):
        """Test invalid transitions from STAGED."""
        # Cannot go directly to EXECUTING
        assert not is_valid_transition(OpportunityState.STAGED, OpportunityState.EXECUTING)
        # Cannot go directly to CONFIRMED
        assert not is_valid_transition(OpportunityState.STAGED, OpportunityState.CONFIRMED)
        # Cannot go back to APPROVED
        assert not is_valid_transition(OpportunityState.STAGED, OpportunityState.APPROVED)

    def test_full_staging_lifecycle_happy_path(self):
        """Test complete staging lifecycle: APPROVED -> STAGED -> EXECUTED."""
        # APPROVED -> STAGED (Sunday)
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.STAGED)

        # STAGED -> VALIDATING (Monday 9:15 AM)
        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.VALIDATING)

        # VALIDATING -> READY (Stage 1 passed)
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.READY)

        # READY -> CONFIRMED (Stage 2 passed at 9:30 AM)
        assert is_valid_transition(OpportunityState.READY, OpportunityState.CONFIRMED)

        # CONFIRMED -> EXECUTING
        assert is_valid_transition(OpportunityState.CONFIRMED, OpportunityState.EXECUTING)

        # EXECUTING -> EXECUTED
        assert is_valid_transition(OpportunityState.EXECUTING, OpportunityState.EXECUTED)

    def test_staging_lifecycle_with_adjustment(self):
        """Test staging lifecycle with adjustment needed."""
        # APPROVED -> STAGED -> VALIDATING
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.STAGED)
        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.VALIDATING)

        # VALIDATING -> ADJUSTING (3-5% deviation)
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.ADJUSTING)

        # ADJUSTING -> READY (adjustment successful)
        assert is_valid_transition(OpportunityState.ADJUSTING, OpportunityState.READY)

        # Continue to execution
        assert is_valid_transition(OpportunityState.READY, OpportunityState.CONFIRMED)
        assert is_valid_transition(OpportunityState.CONFIRMED, OpportunityState.EXECUTING)

    def test_staging_lifecycle_stale_at_premarket(self):
        """Test staging lifecycle where trade becomes stale at pre-market."""
        # APPROVED -> STAGED -> VALIDATING
        assert is_valid_transition(OpportunityState.APPROVED, OpportunityState.STAGED)
        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.VALIDATING)

        # VALIDATING -> STALE (>10% deviation)
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.STALE)

        # STALE is terminal
        assert is_terminal_state(OpportunityState.STALE)

    def test_staging_lifecycle_stale_at_open(self):
        """Test staging lifecycle where trade becomes stale at market open."""
        # Get to READY state
        assert is_valid_transition(OpportunityState.STAGED, OpportunityState.VALIDATING)
        assert is_valid_transition(OpportunityState.VALIDATING, OpportunityState.READY)

        # READY -> STALE at open (premium collapsed)
        assert is_valid_transition(OpportunityState.READY, OpportunityState.STALE)

        # STALE is terminal
        assert is_terminal_state(OpportunityState.STALE)

    def test_get_valid_next_states_staged(self):
        """Test valid next states from STAGED."""
        valid_next = get_valid_next_states(OpportunityState.STAGED)
        assert valid_next == {
            OpportunityState.VALIDATING,
            OpportunityState.EXPIRED,
        }

    def test_get_valid_next_states_validating(self):
        """Test valid next states from VALIDATING."""
        valid_next = get_valid_next_states(OpportunityState.VALIDATING)
        assert valid_next == {
            OpportunityState.READY,
            OpportunityState.STALE,
            OpportunityState.ADJUSTING,
            OpportunityState.CONFIRMED,
            OpportunityState.EXPIRED,
        }

    def test_get_valid_next_states_ready(self):
        """Test valid next states from READY."""
        valid_next = get_valid_next_states(OpportunityState.READY)
        assert valid_next == {
            OpportunityState.VALIDATING,
            OpportunityState.CONFIRMED,
            OpportunityState.STALE,
            OpportunityState.ADJUSTING,
            OpportunityState.EXPIRED,
        }

    def test_get_valid_next_states_confirmed(self):
        """Test valid next states from CONFIRMED."""
        valid_next = get_valid_next_states(OpportunityState.CONFIRMED)
        assert valid_next == {OpportunityState.EXECUTING, OpportunityState.EXPIRED}
