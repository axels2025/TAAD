"""Unit tests for opportunity lifecycle manager.

Tests the OpportunityLifecycleManager class that manages state transitions,
snapshots, and rejection tracking.
"""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock

from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity
from src.data.opportunity_state import OpportunityState
from src.execution.opportunity_lifecycle import OpportunityLifecycleManager


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    session = Mock(spec=Session)
    session.commit = Mock()
    session.rollback = Mock()
    return session


@pytest.fixture
def sample_opportunity():
    """Create a sample opportunity for testing."""
    opportunity = Mock(spec=ScanOpportunity)
    opportunity.id = 1
    opportunity.symbol = "AAPL"
    opportunity.strike = 150.0
    opportunity.expiration = datetime(2026, 2, 14).date()
    opportunity.state = "PENDING"
    opportunity.state_history = "[]"
    opportunity.updated_at = datetime(2026, 1, 28)
    opportunity.enrichment_snapshot = None
    opportunity.validation_snapshot = None
    opportunity.execution_snapshot = None
    opportunity.rejection_reasons = "[]"
    opportunity.risk_check_results = None
    opportunity.user_decision = None
    opportunity.user_decision_at = None
    opportunity.user_notes = None
    opportunity.execution_attempts = 0
    opportunity.last_error = None
    opportunity.created_at = datetime(2026, 1, 28)
    opportunity.expires_at = None
    opportunity.executed = False
    opportunity.trade_id = None
    return opportunity


class TestOpportunityLifecycleManager:
    """Tests for OpportunityLifecycleManager."""

    def test_init(self, mock_session):
        """Test lifecycle manager initialization."""
        manager = OpportunityLifecycleManager(mock_session)
        assert manager.session == mock_session

    def test_transition_valid(self, mock_session, sample_opportunity):
        """Test valid state transition."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute
        result = manager.transition(
            opportunity_id=1,
            new_state=OpportunityState.ENRICHED,
            reason="Data enriched",
            actor="system",
            metadata={"duration_ms": 1500},
        )

        # Verify
        assert result is True
        assert sample_opportunity.state == "ENRICHED"
        assert sample_opportunity.updated_at is not None

        # Check state history was updated
        state_history = json.loads(sample_opportunity.state_history)
        assert len(state_history) == 1
        assert state_history[0]["from_state"] == "PENDING"
        assert state_history[0]["to_state"] == "ENRICHED"
        assert state_history[0]["reason"] == "Data enriched"
        assert state_history[0]["actor"] == "system"
        assert state_history[0]["metadata"] == {"duration_ms": 1500}

        mock_session.commit.assert_called_once()

    def test_transition_invalid(self, mock_session, sample_opportunity):
        """Test invalid state transition."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        sample_opportunity.state = "PENDING"
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute - try to skip directly to EXECUTED
        result = manager.transition(
            opportunity_id=1,
            new_state=OpportunityState.EXECUTED,
            reason="Invalid transition",
            actor="system",
        )

        # Verify
        assert result is False
        assert sample_opportunity.state == "PENDING"  # State unchanged
        mock_session.commit.assert_not_called()

    def test_transition_from_terminal_state(self, mock_session, sample_opportunity):
        """Test that transitions from terminal states are rejected."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        sample_opportunity.state = "EXECUTED"
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute
        result = manager.transition(
            opportunity_id=1,
            new_state=OpportunityState.PENDING,
            reason="Try to restart",
            actor="system",
        )

        # Verify
        assert result is False
        mock_session.commit.assert_not_called()

    def test_transition_opportunity_not_found(self, mock_session):
        """Test transition when opportunity doesn't exist."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = None

        # Execute & Verify
        with pytest.raises(ValueError, match="Opportunity .* not found"):
            manager.transition(
                opportunity_id=999,
                new_state=OpportunityState.ENRICHED,
                reason="Test",
                actor="system",
            )

    def test_capture_enrichment_snapshot(self, mock_session, sample_opportunity):
        """Test capturing enrichment snapshot."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        snapshot_data = {
            "bid": 0.45,
            "ask": 0.50,
            "delta": -0.15,
            "iv": 0.25,
        }

        # Execute
        manager.capture_snapshot(
            opportunity_id=1,
            snapshot_type="enrichment",
            data=snapshot_data,
        )

        # Verify
        assert sample_opportunity.enrichment_snapshot is not None
        enrichment = json.loads(sample_opportunity.enrichment_snapshot)
        assert "timestamp" in enrichment
        assert enrichment["data"] == snapshot_data
        mock_session.commit.assert_called_once()

    def test_capture_validation_snapshot(self, mock_session, sample_opportunity):
        """Test capturing validation snapshot."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        snapshot_data = {
            "otm_pct": 0.18,
            "dte": 14,
            "trend": "uptrend",
            "passed_checks": ["otm_range", "premium", "dte"],
        }

        # Execute
        manager.capture_snapshot(
            opportunity_id=1,
            snapshot_type="validation",
            data=snapshot_data,
        )

        # Verify
        assert sample_opportunity.validation_snapshot is not None
        validation = json.loads(sample_opportunity.validation_snapshot)
        assert validation["data"] == snapshot_data
        mock_session.commit.assert_called_once()

    def test_capture_execution_snapshot(self, mock_session, sample_opportunity):
        """Test capturing execution snapshot."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        snapshot_data = {
            "order_id": "12345",
            "fill_price": 0.47,
            "slippage": 0.02,
            "timestamp": "2026-01-28T10:00:00",
        }

        # Execute
        manager.capture_snapshot(
            opportunity_id=1,
            snapshot_type="execution",
            data=snapshot_data,
        )

        # Verify
        assert sample_opportunity.execution_snapshot is not None
        execution = json.loads(sample_opportunity.execution_snapshot)
        assert execution["data"] == snapshot_data
        mock_session.commit.assert_called_once()

    def test_capture_snapshot_invalid_type(self, mock_session, sample_opportunity):
        """Test capturing snapshot with invalid type."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute & Verify
        with pytest.raises(ValueError, match="Invalid snapshot type"):
            manager.capture_snapshot(
                opportunity_id=1,
                snapshot_type="invalid",
                data={"foo": "bar"},
            )

    def test_capture_snapshot_opportunity_not_found(self, mock_session):
        """Test capturing snapshot when opportunity doesn't exist."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = None

        # Execute & Verify
        with pytest.raises(ValueError, match="Opportunity .* not found"):
            manager.capture_snapshot(
                opportunity_id=999,
                snapshot_type="enrichment",
                data={},
            )

    def test_record_rejection(self, mock_session, sample_opportunity):
        """Test recording rejection reason."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute
        manager.record_rejection(
            opportunity_id=1,
            check_name="spread_pct",
            current_value=0.25,
            limit_value=0.20,
            message="Spread too wide: 25% exceeds limit of 20%",
        )

        # Verify
        rejection_reasons = json.loads(sample_opportunity.rejection_reasons)
        assert len(rejection_reasons) == 1

        rejection = rejection_reasons[0]
        assert rejection["check_name"] == "spread_pct"
        assert rejection["current_value"] == 0.25
        assert rejection["limit_value"] == 0.20
        assert rejection["message"] == "Spread too wide: 25% exceeds limit of 20%"
        assert "timestamp" in rejection

        mock_session.commit.assert_called_once()

    def test_record_multiple_rejections(self, mock_session, sample_opportunity):
        """Test recording multiple rejection reasons."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute - first rejection
        manager.record_rejection(
            opportunity_id=1,
            check_name="spread_pct",
            current_value=0.25,
            limit_value=0.20,
            message="Spread too wide",
        )

        # Execute - second rejection
        manager.record_rejection(
            opportunity_id=1,
            check_name="margin_efficiency",
            current_value=0.015,
            limit_value=0.020,
            message="Margin efficiency too low",
        )

        # Verify
        rejection_reasons = json.loads(sample_opportunity.rejection_reasons)
        assert len(rejection_reasons) == 2
        assert rejection_reasons[0]["check_name"] == "spread_pct"
        assert rejection_reasons[1]["check_name"] == "margin_efficiency"

    def test_get_lifecycle_report(self, mock_session, sample_opportunity):
        """Test getting complete lifecycle report."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)

        # Add some state history
        sample_opportunity.state = "EXECUTED"
        sample_opportunity.state_history = json.dumps([
            {
                "from_state": "PENDING",
                "to_state": "ENRICHED",
                "timestamp": "2026-01-28T10:00:00",
                "reason": "Data enriched",
                "actor": "system",
                "metadata": {},
            },
            {
                "from_state": "ENRICHED",
                "to_state": "VALIDATED",
                "timestamp": "2026-01-28T10:01:00",
                "reason": "Validation passed",
                "actor": "strategy",
                "metadata": {},
            },
        ])

        sample_opportunity.user_decision = "approved"
        sample_opportunity.user_decision_at = datetime(2026, 1, 28, 10, 5, 0)
        sample_opportunity.executed = True
        sample_opportunity.trade_id = "TRADE_123"

        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute
        report = manager.get_lifecycle_report(opportunity_id=1)

        # Verify
        assert report["opportunity_id"] == 1
        assert report["symbol"] == "AAPL"
        assert report["strike"] == 150.0
        assert report["current_state"] == "EXECUTED"
        assert len(report["state_history"]) == 2
        assert report["user_decision"] == "approved"
        assert report["executed"] is True
        assert report["trade_id"] == "TRADE_123"

    def test_set_expiration(self, mock_session, sample_opportunity):
        """Test setting expiration time."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute
        manager.set_expiration(opportunity_id=1, ttl_hours=48)

        # Verify
        assert sample_opportunity.expires_at is not None
        time_diff = sample_opportunity.expires_at - datetime.now()
        assert 47 < time_diff.total_seconds() / 3600 < 49  # Roughly 48 hours

        mock_session.commit.assert_called_once()

    def test_check_expired_opportunities(self, mock_session):
        """Test finding expired opportunities."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)

        # Mock query result
        expired_opp1 = Mock()
        expired_opp1.id = 1
        expired_opp2 = Mock()
        expired_opp2.id = 2

        mock_session.query().filter().all.return_value = [expired_opp1, expired_opp2]

        # Execute
        expired_ids = manager.check_expired_opportunities()

        # Verify
        assert expired_ids == [1, 2]

    def test_expire_opportunity(self, mock_session, sample_opportunity):
        """Test marking opportunity as expired."""
        # Setup
        manager = OpportunityLifecycleManager(mock_session)
        sample_opportunity.state = "PENDING"
        mock_session.query().filter().first.return_value = sample_opportunity

        # Execute
        result = manager.expire_opportunity(opportunity_id=1)

        # Verify
        assert result is True
        assert sample_opportunity.state == "EXPIRED"
