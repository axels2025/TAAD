"""Opportunity lifecycle management with full audit trail.

This module manages state transitions for trade opportunities throughout their
complete lifecycle from discovery to execution or rejection.
"""

import json
from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity
from src.data.opportunity_state import (
    OpportunityState,
    StateTransition,
    get_valid_next_states,
    is_terminal_state,
    is_valid_transition,
)


class OpportunityLifecycleManager:
    """Manages opportunity state transitions with full audit trail.

    This class enforces the state machine rules, records all transitions,
    captures snapshots for later analysis, and tracks rejection reasons
    for the learning engine.

    Attributes:
        session: Database session for persistence
    """

    def __init__(self, session: Session):
        """Initialize lifecycle manager.

        Args:
            session: SQLAlchemy database session
        """
        self.session = session

    def transition(
        self,
        opportunity_id: int,
        new_state: OpportunityState,
        reason: str,
        actor: str = "system",
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """Transition opportunity to new state with validation.

        Args:
            opportunity_id: Database ID of opportunity
            new_state: Desired new state
            reason: Human-readable reason for transition
            actor: Who/what caused the transition (system, user, risk_governor, ibkr)
            metadata: Additional context (error messages, user input, etc.)

        Returns:
            True if transition succeeded, False otherwise

        Raises:
            ValueError: If opportunity not found or transition is invalid
        """
        # Load opportunity
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )

        if not opportunity:
            raise ValueError(f"Opportunity {opportunity_id} not found")

        # Parse current state
        current_state = (
            OpportunityState[opportunity.state]
            if opportunity.state
            else OpportunityState.PENDING
        )

        # Check if transition is valid
        if not is_valid_transition(current_state, new_state):
            valid_next = get_valid_next_states(current_state)
            valid_names = [s.name for s in valid_next]
            logger.warning(
                f"Invalid state transition for opportunity {opportunity_id}: "
                f"{current_state.name} -> {new_state.name}. "
                f"Valid transitions: {valid_names}"
            )
            return False

        # Check if current state is terminal
        if is_terminal_state(current_state):
            logger.warning(
                f"Cannot transition opportunity {opportunity_id} from terminal state {current_state.name}"
            )
            return False

        # Create transition record
        transition = StateTransition(
            from_state=current_state,
            to_state=new_state,
            timestamp=datetime.now(),
            reason=reason,
            actor=actor,
            metadata=metadata or {},
        )

        # Load existing state history
        state_history = (
            json.loads(opportunity.state_history) if opportunity.state_history else []
        )

        # Append new transition
        state_history.append(transition.to_dict())

        # Update opportunity
        opportunity.state = new_state.name
        opportunity.state_history = json.dumps(state_history)
        opportunity.updated_at = datetime.now()

        # Commit changes
        try:
            self.session.commit()
            logger.info(
                f"Opportunity {opportunity_id} transitioned: {current_state.name} -> {new_state.name}",
                extra={
                    "opportunity_id": opportunity_id,
                    "from_state": current_state.name,
                    "to_state": new_state.name,
                    "reason": reason,
                    "actor": actor,
                },
            )
            return True
        except Exception as e:
            self.session.rollback()
            logger.error(
                f"Failed to transition opportunity {opportunity_id}: {e}",
                exc_info=True,
            )
            return False

    def capture_snapshot(
        self,
        opportunity_id: int,
        snapshot_type: str,
        data: dict[str, Any],
    ) -> None:
        """Capture point-in-time data for later analysis.

        Args:
            opportunity_id: Database ID of opportunity
            snapshot_type: Type of snapshot (enrichment, validation, execution)
            data: Data to capture

        Raises:
            ValueError: If opportunity not found or invalid snapshot type
        """
        valid_types = ["enrichment", "validation", "execution"]
        if snapshot_type not in valid_types:
            raise ValueError(
                f"Invalid snapshot type: {snapshot_type}. Must be one of {valid_types}"
            )

        # Load opportunity
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )

        if not opportunity:
            raise ValueError(f"Opportunity {opportunity_id} not found")

        # Add timestamp to snapshot
        snapshot_data = {
            "timestamp": datetime.now().isoformat(),
            "data": data,
        }

        # Store snapshot
        if snapshot_type == "enrichment":
            opportunity.enrichment_snapshot = json.dumps(snapshot_data)
        elif snapshot_type == "validation":
            opportunity.validation_snapshot = json.dumps(snapshot_data)
        elif snapshot_type == "execution":
            opportunity.execution_snapshot = json.dumps(snapshot_data)

        opportunity.updated_at = datetime.now()

        try:
            self.session.commit()
            logger.debug(
                f"Captured {snapshot_type} snapshot for opportunity {opportunity_id}",
                extra={
                    "opportunity_id": opportunity_id,
                    "snapshot_type": snapshot_type,
                },
            )
        except Exception as e:
            self.session.rollback()
            logger.error(
                f"Failed to capture snapshot for opportunity {opportunity_id}: {e}",
                exc_info=True,
            )
            raise

    def record_rejection(
        self,
        opportunity_id: int,
        check_name: str,
        current_value: float,
        limit_value: float,
        message: str,
    ) -> None:
        """Record why an opportunity was rejected (for learning).

        Args:
            opportunity_id: Database ID of opportunity
            check_name: Name of the check that failed (e.g., "spread_pct", "margin_efficiency")
            current_value: Actual value that failed
            limit_value: Threshold/limit value
            message: Human-readable rejection message

        Raises:
            ValueError: If opportunity not found
        """
        # Load opportunity
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )

        if not opportunity:
            raise ValueError(f"Opportunity {opportunity_id} not found")

        # Load existing rejections
        rejection_reasons = (
            json.loads(opportunity.rejection_reasons)
            if opportunity.rejection_reasons
            else []
        )

        # Create rejection record
        rejection = {
            "timestamp": datetime.now().isoformat(),
            "check_name": check_name,
            "current_value": current_value,
            "limit_value": limit_value,
            "message": message,
        }

        # Append rejection
        rejection_reasons.append(rejection)

        # Update opportunity
        opportunity.rejection_reasons = json.dumps(rejection_reasons)
        opportunity.updated_at = datetime.now()

        try:
            self.session.commit()
            logger.info(
                f"Recorded rejection for opportunity {opportunity_id}: {message}",
                extra={
                    "opportunity_id": opportunity_id,
                    "check_name": check_name,
                    "current_value": current_value,
                    "limit_value": limit_value,
                },
            )
        except Exception as e:
            self.session.rollback()
            logger.error(
                f"Failed to record rejection for opportunity {opportunity_id}: {e}",
                exc_info=True,
            )
            raise

    def get_lifecycle_report(
        self,
        opportunity_id: int,
    ) -> dict[str, Any]:
        """Get complete lifecycle history for an opportunity.

        Args:
            opportunity_id: Database ID of opportunity

        Returns:
            Dictionary with complete lifecycle information

        Raises:
            ValueError: If opportunity not found
        """
        # Load opportunity
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )

        if not opportunity:
            raise ValueError(f"Opportunity {opportunity_id} not found")

        # Parse JSON fields
        state_history = (
            json.loads(opportunity.state_history) if opportunity.state_history else []
        )
        rejection_reasons = (
            json.loads(opportunity.rejection_reasons)
            if opportunity.rejection_reasons
            else []
        )
        enrichment_snapshot = (
            json.loads(opportunity.enrichment_snapshot)
            if opportunity.enrichment_snapshot
            else None
        )
        validation_snapshot = (
            json.loads(opportunity.validation_snapshot)
            if opportunity.validation_snapshot
            else None
        )
        execution_snapshot = (
            json.loads(opportunity.execution_snapshot)
            if opportunity.execution_snapshot
            else None
        )
        risk_check_results = (
            json.loads(opportunity.risk_check_results)
            if opportunity.risk_check_results
            else None
        )

        # Build report
        report = {
            "opportunity_id": opportunity_id,
            "symbol": opportunity.symbol,
            "strike": opportunity.strike,
            "expiration": opportunity.expiration.isoformat()
            if opportunity.expiration
            else None,
            "current_state": opportunity.state,
            "state_history": state_history,
            "rejection_reasons": rejection_reasons,
            "user_decision": opportunity.user_decision,
            "user_decision_at": opportunity.user_decision_at.isoformat()
            if opportunity.user_decision_at
            else None,
            "user_notes": opportunity.user_notes,
            "execution_attempts": opportunity.execution_attempts,
            "last_error": opportunity.last_error,
            "created_at": opportunity.created_at.isoformat()
            if opportunity.created_at
            else None,
            "updated_at": opportunity.updated_at.isoformat()
            if opportunity.updated_at
            else None,
            "expires_at": opportunity.expires_at.isoformat()
            if opportunity.expires_at
            else None,
            "snapshots": {
                "enrichment": enrichment_snapshot,
                "validation": validation_snapshot,
                "execution": execution_snapshot,
            },
            "risk_check_results": risk_check_results,
            "executed": opportunity.executed,
            "trade_id": opportunity.trade_id,
        }

        return report

    def set_expiration(
        self,
        opportunity_id: int,
        ttl_hours: int = 48,
    ) -> None:
        """Set expiration time for an opportunity.

        Args:
            opportunity_id: Database ID of opportunity
            ttl_hours: Time-to-live in hours (default 48)

        Raises:
            ValueError: If opportunity not found
        """
        opportunity = (
            self.session.query(ScanOpportunity)
            .filter(ScanOpportunity.id == opportunity_id)
            .first()
        )

        if not opportunity:
            raise ValueError(f"Opportunity {opportunity_id} not found")

        opportunity.expires_at = datetime.now() + timedelta(hours=ttl_hours)
        opportunity.updated_at = datetime.now()

        try:
            self.session.commit()
            logger.debug(
                f"Set expiration for opportunity {opportunity_id} to {opportunity.expires_at}",
                extra={
                    "opportunity_id": opportunity_id,
                    "expires_at": opportunity.expires_at,
                },
            )
        except Exception as e:
            self.session.rollback()
            logger.error(
                f"Failed to set expiration for opportunity {opportunity_id}: {e}",
                exc_info=True,
            )
            raise

    def check_expired_opportunities(self) -> list[int]:
        """Find opportunities that have exceeded their TTL.

        Returns:
            List of opportunity IDs that have expired
        """
        now = datetime.now()
        expired_opportunities = (
            self.session.query(ScanOpportunity.id)
            .filter(
                ScanOpportunity.expires_at.isnot(None),
                ScanOpportunity.expires_at < now,
                ScanOpportunity.state.notin_(
                    ["EXPIRED", "EXECUTED", "FAILED", "REJECTED", "SKIPPED"]
                ),
            )
            .all()
        )

        expired_ids = [opp.id for opp in expired_opportunities]

        if expired_ids:
            logger.info(
                f"Found {len(expired_ids)} expired opportunities",
                extra={"expired_count": len(expired_ids), "expired_ids": expired_ids},
            )

        return expired_ids

    def expire_opportunity(self, opportunity_id: int) -> bool:
        """Mark an opportunity as expired.

        Args:
            opportunity_id: Database ID of opportunity

        Returns:
            True if successfully expired, False otherwise
        """
        return self.transition(
            opportunity_id=opportunity_id,
            new_state=OpportunityState.EXPIRED,
            reason="TTL exceeded without action",
            actor="system",
        )
