"""Opportunity lifecycle state management.

This module defines the state machine for tracking trade opportunities
through their complete lifecycle from discovery to execution or rejection.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any


class OpportunityState(Enum):
    """Explicit states for opportunity lifecycle.

    State Flow (Original):
        PENDING → ENRICHED → VALIDATED → OFFERED → APPROVED → EXECUTING → EXECUTED
                                     ↓
                               RISK_BLOCKED

        From OFFERED can also go to: REJECTED, SKIPPED, EXPIRED
        From EXECUTING can also go to: FAILED

    State Flow (Phase 4 - Sunday-to-Monday Workflow):
        PENDING → ENRICHED → VALIDATED → OFFERED → APPROVED → STAGED
                                                                 ↓
                                              (Monday 9:15 AM) VALIDATING
                                                                 ↓    ↓
                                                              STALE  READY
                                                               (skip)  ↓
                                                        (Monday 9:30 AM)
                                                                    ↓    ↓
                                                                 STALE  CONFIRMED
                                                                 (skip)    ↓
                                                              ADJUSTING → EXECUTING → EXECUTED

        STALE opportunities are skipped (too much price movement)
        ADJUSTING is used when strike/premium needs adjustment during validation
    """

    PENDING = auto()  # Created, awaiting enrichment
    ENRICHED = auto()  # Live data fetched from IBKR
    VALIDATED = auto()  # Passed strategy criteria
    RISK_BLOCKED = auto()  # Failed risk checks (with reasons)
    OFFERED = auto()  # Presented to user
    APPROVED = auto()  # User said yes
    REJECTED = auto()  # User said no
    SKIPPED = auto()  # User skipped (no decision)
    EXECUTING = auto()  # Order in progress
    EXECUTED = auto()  # Order filled
    FAILED = auto()  # Order rejected/error
    EXPIRED = auto()  # TTL exceeded without action

    # Phase 4: Sunday-to-Monday Workflow States
    STAGED = auto()  # Approved by user, waiting for market open
    VALIDATING = auto()  # Pre-market or market-open validation in progress
    READY = auto()  # Passed pre-market check (Stage 1), waiting for open
    STALE = auto()  # Failed pre-market or market-open check, skip this trade
    ADJUSTING = auto()  # Being adjusted (strike/premium change)
    CONFIRMED = auto()  # Passed market-open check (Stage 2), ready for execution


# Valid state transitions - used for validation
VALID_TRANSITIONS = {
    OpportunityState.PENDING: {
        OpportunityState.ENRICHED,
        OpportunityState.EXPIRED,
    },
    OpportunityState.ENRICHED: {
        OpportunityState.VALIDATED,
        OpportunityState.RISK_BLOCKED,
        OpportunityState.EXPIRED,
    },
    OpportunityState.VALIDATED: {
        OpportunityState.OFFERED,
        OpportunityState.RISK_BLOCKED,
        OpportunityState.EXPIRED,
    },
    OpportunityState.RISK_BLOCKED: set(),  # Terminal state
    OpportunityState.OFFERED: {
        OpportunityState.APPROVED,
        OpportunityState.REJECTED,
        OpportunityState.SKIPPED,
        OpportunityState.EXPIRED,
    },
    OpportunityState.APPROVED: {
        OpportunityState.EXECUTING,
        OpportunityState.STAGED,  # Phase 4: Can stage for Monday execution
        OpportunityState.EXPIRED,
    },
    OpportunityState.REJECTED: set(),  # Terminal state
    OpportunityState.SKIPPED: set(),  # Terminal state
    OpportunityState.EXECUTING: {
        OpportunityState.EXECUTED,
        OpportunityState.FAILED,
        OpportunityState.EXPIRED,  # TTL cleanup for unfilled orders
        OpportunityState.STAGED,  # Rollback on daemon restart (no order placed)
    },
    OpportunityState.EXECUTED: set(),  # Terminal state
    OpportunityState.FAILED: set(),  # Terminal state
    OpportunityState.EXPIRED: set(),  # Terminal state
    # Phase 4: Sunday-to-Monday Workflow Transitions
    OpportunityState.STAGED: {
        OpportunityState.VALIDATING,  # Start pre-market validation
        OpportunityState.EXECUTING,  # Autonomous daemon direct execution
        OpportunityState.EXPIRED,  # If not executed before expiration
    },
    OpportunityState.VALIDATING: {
        OpportunityState.READY,  # Passed Stage 1 pre-market check
        OpportunityState.STALE,  # Failed validation (too much price movement)
        OpportunityState.ADJUSTING,  # Needs strike/premium adjustment
        OpportunityState.CONFIRMED,  # Passed Stage 2 market-open check (direct if combined)
        OpportunityState.EXPIRED,  # Unstaged by user or EOD auto-unstage
    },
    OpportunityState.READY: {
        OpportunityState.VALIDATING,  # Re-validate at market open (Stage 2)
        OpportunityState.CONFIRMED,  # Passed market-open check
        OpportunityState.STALE,  # Failed market-open check
        OpportunityState.ADJUSTING,  # Needs adjustment at open
        OpportunityState.EXPIRED,  # Unstaged by user or EOD auto-unstage
    },
    OpportunityState.ADJUSTING: {
        OpportunityState.READY,  # Adjustment successful, proceed
        OpportunityState.CONFIRMED,  # Adjustment successful at open
        OpportunityState.STALE,  # Can't find viable adjustment
        OpportunityState.EXPIRED,  # Unstaged by user or EOD auto-unstage
    },
    OpportunityState.STALE: set(),  # Terminal state - opportunity skipped
    OpportunityState.CONFIRMED: {
        OpportunityState.EXECUTING,  # Proceed to order placement
        OpportunityState.EXPIRED,  # Unstaged by user or EOD auto-unstage
    },
}


# Terminal states that cannot transition further
TERMINAL_STATES = {
    OpportunityState.RISK_BLOCKED,
    OpportunityState.REJECTED,
    OpportunityState.SKIPPED,
    OpportunityState.EXECUTED,
    OpportunityState.FAILED,
    OpportunityState.EXPIRED,
    OpportunityState.STALE,  # Phase 4: Skipped due to price movement
}


@dataclass
class StateTransition:
    """Record of a state change.

    Captures complete context of every state transition for audit trail
    and learning engine analysis.

    Attributes:
        from_state: Previous state
        to_state: New state
        timestamp: When transition occurred
        reason: Human-readable reason for transition
        actor: Who/what caused the transition (system, user, risk_governor, ibkr)
        metadata: Additional context (error messages, user input, etc.)
    """

    from_state: OpportunityState
    to_state: OpportunityState
    timestamp: datetime
    reason: str
    actor: str  # "system", "user", "risk_governor", "ibkr", "strategy"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "from_state": self.from_state.name,
            "to_state": self.to_state.name,
            "timestamp": self.timestamp.isoformat(),
            "reason": self.reason,
            "actor": self.actor,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateTransition":
        """Create StateTransition from dictionary."""
        return cls(
            from_state=OpportunityState[data["from_state"]],
            to_state=OpportunityState[data["to_state"]],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            reason=data["reason"],
            actor=data["actor"],
            metadata=data.get("metadata", {}),
        )


def is_valid_transition(
    from_state: OpportunityState, to_state: OpportunityState
) -> bool:
    """Check if a state transition is valid.

    Args:
        from_state: Current state
        to_state: Desired new state

    Returns:
        True if transition is allowed, False otherwise
    """
    return to_state in VALID_TRANSITIONS.get(from_state, set())


def is_terminal_state(state: OpportunityState) -> bool:
    """Check if a state is terminal (no further transitions allowed).

    Args:
        state: State to check

    Returns:
        True if state is terminal, False otherwise
    """
    return state in TERMINAL_STATES


def get_valid_next_states(state: OpportunityState) -> set[OpportunityState]:
    """Get all valid next states from current state.

    Args:
        state: Current state

    Returns:
        Set of valid next states
    """
    return VALID_TRANSITIONS.get(state, set())
