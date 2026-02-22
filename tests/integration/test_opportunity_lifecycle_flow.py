"""Integration tests for opportunity lifecycle flow.

Tests the complete workflow of an opportunity from PENDING state through
to EXECUTED or other terminal states, using a real database session.
"""

import json
import pytest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.data.models import Base, ScanResult, ScanOpportunity
from src.data.opportunity_state import OpportunityState
from src.execution.opportunity_lifecycle import OpportunityLifecycleManager
from src.data.repositories import ScanRepository


@pytest.fixture
def test_db_session():
    """Create a test database session with in-memory SQLite."""
    # Create in-memory database
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)

    # Create session
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session

    session.close()


@pytest.fixture
def scan_result(test_db_session):
    """Create a test scan result."""
    scan = ScanResult(
        scan_timestamp=datetime.now(),
        source="manual",
        config_used={"strategy": "naked_put"},
        total_candidates=1,
        validated_count=0,
    )
    test_db_session.add(scan)
    test_db_session.commit()
    return scan


@pytest.fixture
def test_opportunity(test_db_session, scan_result):
    """Create a test opportunity."""
    opportunity = ScanOpportunity(
        scan_id=scan_result.id,
        symbol="AAPL",
        strike=150.0,
        expiration=datetime(2026, 2, 14).date(),
        option_type="PUT",
        premium=0.45,
        bid=0.43,
        ask=0.47,
        otm_pct=0.18,
        dte=14,
        source="manual",
        state="PENDING",
        state_history="[]",
        rejection_reasons="[]",
        execution_attempts=0,
    )
    test_db_session.add(opportunity)
    test_db_session.commit()
    return opportunity


class TestCompleteLifecycleFlow:
    """Integration tests for complete lifecycle workflows."""

    def test_happy_path_pending_to_executed(self, test_db_session, test_opportunity):
        """Test complete happy path: PENDING → EXECUTED."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # PENDING → ENRICHED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.ENRICHED,
            reason="Data enriched from IBKR",
            actor="system",
            metadata={"duration_ms": 1500},
        )
        assert success is True

        # Capture enrichment snapshot
        manager.capture_snapshot(
            opportunity_id=opp_id,
            snapshot_type="enrichment",
            data={
                "bid": 0.43,
                "ask": 0.47,
                "delta": -0.15,
                "iv": 0.25,
            },
        )

        # ENRICHED → VALIDATED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.VALIDATED,
            reason="Passed strategy validation",
            actor="strategy",
        )
        assert success is True

        # Capture validation snapshot
        manager.capture_snapshot(
            opportunity_id=opp_id,
            snapshot_type="validation",
            data={
                "otm_pct": 0.18,
                "dte": 14,
                "trend": "uptrend",
                "passed_checks": ["otm_range", "premium", "dte"],
            },
        )

        # VALIDATED → OFFERED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.OFFERED,
            reason="Presented to user",
            actor="system",
        )
        assert success is True

        # OFFERED → APPROVED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.APPROVED,
            reason="User approved",
            actor="user",
        )
        assert success is True

        # APPROVED → EXECUTING
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.EXECUTING,
            reason="Order placed",
            actor="order_executor",
            metadata={"order_id": "12345"},
        )
        assert success is True

        # EXECUTING → EXECUTED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.EXECUTED,
            reason="Order filled",
            actor="ibkr",
            metadata={"fill_price": 0.45, "fill_time": "2026-01-28T10:00:00"},
        )
        assert success is True

        # Capture execution snapshot
        manager.capture_snapshot(
            opportunity_id=opp_id,
            snapshot_type="execution",
            data={
                "order_id": "12345",
                "fill_price": 0.45,
                "slippage": 0.00,
            },
        )

        # Verify final state
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "EXECUTED"

        # Verify state history
        state_history = json.loads(test_opportunity.state_history)
        assert len(state_history) == 6  # 6 transitions

        # Verify all snapshots were captured
        assert test_opportunity.enrichment_snapshot is not None
        assert test_opportunity.validation_snapshot is not None
        assert test_opportunity.execution_snapshot is not None

    def test_rejection_flow_pending_to_risk_blocked(self, test_db_session, test_opportunity):
        """Test rejection flow: PENDING → RISK_BLOCKED."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # PENDING → ENRICHED
        manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.ENRICHED,
            reason="Data enriched",
            actor="system",
        )

        # Record rejection reasons
        manager.record_rejection(
            opportunity_id=opp_id,
            check_name="spread_pct",
            current_value=0.25,
            limit_value=0.20,
            message="Spread too wide: 25% exceeds limit of 20%",
        )

        manager.record_rejection(
            opportunity_id=opp_id,
            check_name="margin_efficiency",
            current_value=0.015,
            limit_value=0.020,
            message="Margin efficiency too low: 1.5% below minimum 2.0%",
        )

        # ENRICHED → RISK_BLOCKED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.RISK_BLOCKED,
            reason="Failed risk checks",
            actor="risk_governor",
        )
        assert success is True

        # Verify final state
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "RISK_BLOCKED"

        # Verify rejection reasons were recorded
        rejection_reasons = json.loads(test_opportunity.rejection_reasons)
        assert len(rejection_reasons) == 2
        assert rejection_reasons[0]["check_name"] == "spread_pct"
        assert rejection_reasons[1]["check_name"] == "margin_efficiency"

    def test_user_rejection_flow(self, test_db_session, test_opportunity):
        """Test user rejection flow: PENDING → REJECTED."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # PENDING → ENRICHED → VALIDATED → OFFERED
        manager.transition(opp_id, OpportunityState.ENRICHED, "Enriched", "system")
        manager.transition(opp_id, OpportunityState.VALIDATED, "Validated", "strategy")
        manager.transition(opp_id, OpportunityState.OFFERED, "Offered", "system")

        # OFFERED → REJECTED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.REJECTED,
            reason="User rejected",
            actor="user",
            metadata={"user_note": "Don't like the spread"},
        )
        assert success is True

        # Verify final state
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "REJECTED"

        # Verify state history
        state_history = json.loads(test_opportunity.state_history)
        last_transition = state_history[-1]
        assert last_transition["to_state"] == "REJECTED"
        assert last_transition["metadata"]["user_note"] == "Don't like the spread"

    def test_execution_failure_flow(self, test_db_session, test_opportunity):
        """Test execution failure flow: EXECUTING → FAILED."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # Get to EXECUTING state
        manager.transition(opp_id, OpportunityState.ENRICHED, "Enriched", "system")
        manager.transition(opp_id, OpportunityState.VALIDATED, "Validated", "strategy")
        manager.transition(opp_id, OpportunityState.OFFERED, "Offered", "system")
        manager.transition(opp_id, OpportunityState.APPROVED, "Approved", "user")
        manager.transition(opp_id, OpportunityState.EXECUTING, "Executing", "order_executor")

        # EXECUTING → FAILED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.FAILED,
            reason="Order rejected by broker",
            actor="ibkr",
            metadata={"error": "Insufficient margin"},
        )
        assert success is True

        # Verify final state
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "FAILED"

    def test_expiration_flow(self, test_db_session, test_opportunity):
        """Test expiration flow: opportunity expires during workflow."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # Set expiration
        manager.set_expiration(opportunity_id=opp_id, ttl_hours=48)

        # Transition to ENRICHED
        manager.transition(opp_id, OpportunityState.ENRICHED, "Enriched", "system")

        # Can transition to EXPIRED from ENRICHED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.EXPIRED,
            reason="TTL exceeded",
            actor="system",
        )
        assert success is True

        # Verify final state
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "EXPIRED"

    def test_invalid_transition_rejected(self, test_db_session, test_opportunity):
        """Test that invalid transitions are rejected."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # Try to skip directly from PENDING to EXECUTED
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.EXECUTED,
            reason="Invalid skip",
            actor="system",
        )

        # Should fail
        assert success is False

        # State should remain PENDING
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "PENDING"

    def test_cannot_transition_from_terminal_state(self, test_db_session, test_opportunity):
        """Test that terminal states cannot transition."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # Get to EXECUTED state
        manager.transition(opp_id, OpportunityState.ENRICHED, "Enriched", "system")
        manager.transition(opp_id, OpportunityState.VALIDATED, "Validated", "strategy")
        manager.transition(opp_id, OpportunityState.OFFERED, "Offered", "system")
        manager.transition(opp_id, OpportunityState.APPROVED, "Approved", "user")
        manager.transition(opp_id, OpportunityState.EXECUTING, "Executing", "order_executor")
        manager.transition(opp_id, OpportunityState.EXECUTED, "Executed", "ibkr")

        # Try to transition from EXECUTED (terminal state)
        success = manager.transition(
            opportunity_id=opp_id,
            new_state=OpportunityState.PENDING,
            reason="Try to restart",
            actor="system",
        )

        # Should fail
        assert success is False

        # State should remain EXECUTED
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "EXECUTED"

    def test_lifecycle_report_completeness(self, test_db_session, test_opportunity):
        """Test that lifecycle report captures all information."""
        manager = OpportunityLifecycleManager(test_db_session)
        opp_id = test_opportunity.id

        # Execute a few transitions
        manager.transition(opp_id, OpportunityState.ENRICHED, "Enriched", "system")
        manager.capture_snapshot(opp_id, "enrichment", {"bid": 0.43})
        manager.transition(opp_id, OpportunityState.VALIDATED, "Validated", "strategy")
        manager.record_rejection(opp_id, "test_check", 0.1, 0.2, "Test rejection")

        # Get lifecycle report
        report = manager.get_lifecycle_report(opportunity_id=opp_id)

        # Verify report completeness
        assert report["opportunity_id"] == opp_id
        assert report["symbol"] == "AAPL"
        assert report["current_state"] == "VALIDATED"
        assert len(report["state_history"]) == 2
        assert report["snapshots"]["enrichment"] is not None
        assert len(report["rejection_reasons"]) == 1

    def test_check_expired_opportunities(self, test_db_session, test_opportunity):
        """Test finding and expiring old opportunities."""
        manager = OpportunityLifecycleManager(test_db_session)
        repo = ScanRepository(test_db_session)

        # Set expiration in the past
        test_opportunity.expires_at = datetime.now() - timedelta(hours=1)
        test_db_session.commit()

        # Check for expired opportunities
        expired_ids = manager.check_expired_opportunities()

        # Should find our expired opportunity
        assert test_opportunity.id in expired_ids

        # Expire it
        success = manager.expire_opportunity(test_opportunity.id)
        assert success is True

        # Verify state changed
        test_db_session.refresh(test_opportunity)
        assert test_opportunity.state == "EXPIRED"
