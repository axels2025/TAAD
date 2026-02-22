"""Unit tests for the unstage feature.

Tests state machine transitions, dashboard API endpoints,
and EOD auto-unstage in the daemon.
"""

import pytest
from datetime import datetime
from unittest.mock import Mock, MagicMock, patch

from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity
from src.data.opportunity_state import OpportunityState, is_valid_transition


# ---------------------------------------------------------------------------
# State machine transition tests
# ---------------------------------------------------------------------------

class TestExpiredTransitionFromPreExecStates:
    """Test that EXPIRED is a valid target from all pre-execution states."""

    @pytest.mark.parametrize(
        "from_state",
        [
            OpportunityState.STAGED,
            OpportunityState.VALIDATING,
            OpportunityState.READY,
            OpportunityState.ADJUSTING,
            OpportunityState.CONFIRMED,
        ],
    )
    def test_expired_transition_valid_from_all_pre_exec_states(self, from_state):
        """STAGED, VALIDATING, READY, ADJUSTING, CONFIRMED -> EXPIRED should all be valid."""
        assert is_valid_transition(from_state, OpportunityState.EXPIRED)

    def test_expired_transition_invalid_from_executed(self):
        """EXECUTED -> EXPIRED should NOT be valid (terminal state)."""
        assert not is_valid_transition(OpportunityState.EXECUTED, OpportunityState.EXPIRED)

    def test_expired_transition_invalid_from_executing(self):
        """EXECUTING -> EXPIRED should NOT be valid (already in-flight)."""
        assert not is_valid_transition(OpportunityState.EXECUTING, OpportunityState.EXPIRED)


# ---------------------------------------------------------------------------
# Dashboard API endpoint tests
# ---------------------------------------------------------------------------

@pytest.fixture
def dashboard_client():
    """Create a FastAPI test client for the dashboard."""
    from src.agentic.dashboard_api import create_dashboard_app

    app = create_dashboard_app(auth_token="")  # No auth for testing
    from fastapi.testclient import TestClient

    return TestClient(app)


class TestUnstageEndpoint:
    """Tests for POST /api/unstage/{id}."""

    def test_unstage_endpoint_expires_opportunity(self, dashboard_client):
        """POST /api/unstage/{id} should transition to EXPIRED and return success."""
        opp = Mock(spec=ScanOpportunity)
        opp.id = 42
        opp.symbol = "AAPL"
        opp.state = "STAGED"

        with patch("src.agentic.dashboard_api.get_db_session") as mock_get_db, \
             patch("src.execution.opportunity_lifecycle.OpportunityLifecycleManager") as MockLifecycle, \
             patch("src.agentic.event_bus.EventBus"):
            mock_db = MagicMock()
            mock_get_db.return_value.__enter__ = Mock(return_value=mock_db)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)
            mock_db.query.return_value.get.return_value = opp

            resp = dashboard_client.post("/api/unstage/42")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unstaged"
        assert data["symbol"] == "AAPL"
        assert data["id"] == 42

    def test_unstage_endpoint_rejects_terminal_state(self, dashboard_client):
        """POST /api/unstage/{id} on EXECUTED should return 400."""
        opp = Mock(spec=ScanOpportunity)
        opp.id = 99
        opp.state = "EXECUTED"

        with patch("src.agentic.dashboard_api.get_db_session") as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value.__enter__ = Mock(return_value=mock_db)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)
            mock_db.query.return_value.get.return_value = opp

            resp = dashboard_client.post("/api/unstage/99")

        assert resp.status_code == 400
        assert "Cannot unstage" in resp.json()["detail"]

    def test_unstage_endpoint_not_found(self, dashboard_client):
        """POST /api/unstage/{id} with bad ID should return 404."""
        with patch("src.agentic.dashboard_api.get_db_session") as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value.__enter__ = Mock(return_value=mock_db)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)
            mock_db.query.return_value.get.return_value = None

            resp = dashboard_client.post("/api/unstage/999")

        assert resp.status_code == 404


class TestUnstageAllEndpoint:
    """Tests for POST /api/unstage-all."""

    def test_unstage_all_expires_all_staged(self, dashboard_client):
        """POST /api/unstage-all should expire all pre-execution candidates."""
        opps = [
            Mock(spec=ScanOpportunity, id=1, symbol="AAPL", state="STAGED"),
            Mock(spec=ScanOpportunity, id=2, symbol="MSFT", state="READY"),
            Mock(spec=ScanOpportunity, id=3, symbol="TSLA", state="CONFIRMED"),
        ]

        with patch("src.agentic.dashboard_api.get_db_session") as mock_get_db, \
             patch("src.execution.opportunity_lifecycle.OpportunityLifecycleManager") as MockLifecycle, \
             patch("src.agentic.event_bus.EventBus"):
            mock_db = MagicMock()
            mock_get_db.return_value.__enter__ = Mock(return_value=mock_db)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)
            mock_db.query.return_value.filter.return_value.all.return_value = opps

            resp = dashboard_client.post("/api/unstage-all")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unstaged_all"
        assert data["count"] == 3

    def test_unstage_all_empty(self, dashboard_client):
        """POST /api/unstage-all with no candidates should return count 0."""
        with patch("src.agentic.dashboard_api.get_db_session") as mock_get_db:
            mock_db = MagicMock()
            mock_get_db.return_value.__enter__ = Mock(return_value=mock_db)
            mock_get_db.return_value.__exit__ = Mock(return_value=False)
            mock_db.query.return_value.filter.return_value.all.return_value = []

            resp = dashboard_client.post("/api/unstage-all")

        assert resp.status_code == 200
        assert resp.json()["count"] == 0


# ---------------------------------------------------------------------------
# EOD auto-unstage tests
# ---------------------------------------------------------------------------

class TestEodAutoUnstage:
    """Tests for TAADDaemon._auto_unstage_eod."""

    def _make_daemon(self):
        """Create a TAADDaemon instance with mocked config."""
        with patch("src.agentic.daemon.load_phase5_config") as mock_cfg:
            mock_cfg.return_value = MagicMock()
            from src.agentic.daemon import TAADDaemon

            return TAADDaemon(config=mock_cfg.return_value)

    def test_eod_auto_unstage_expires_remaining(self):
        """_auto_unstage_eod should expire leftover pre-exec candidates."""
        daemon = self._make_daemon()
        mock_db = MagicMock(spec=Session)

        stale_opps = [
            Mock(spec=ScanOpportunity, id=10, state="STAGED"),
            Mock(spec=ScanOpportunity, id=11, state="VALIDATING"),
        ]
        mock_db.query.return_value.filter.return_value.all.return_value = stale_opps

        with patch(
            "src.execution.opportunity_lifecycle.OpportunityLifecycleManager"
        ) as MockLifecycle:
            daemon._auto_unstage_eod(mock_db)

            lifecycle_instance = MockLifecycle.return_value
            assert lifecycle_instance.transition.call_count == 2
            # Verify each call used EXPIRED state and correct reason
            for call in lifecycle_instance.transition.call_args_list:
                assert call.args[1] == OpportunityState.EXPIRED
                assert call.kwargs["reason"] == "EOD auto-unstage"
                assert call.kwargs["actor"] == "system"

            mock_db.commit.assert_called_once()

    def test_eod_auto_unstage_skips_when_none(self):
        """_auto_unstage_eod should be a no-op with no staged candidates."""
        daemon = self._make_daemon()
        mock_db = MagicMock(spec=Session)
        mock_db.query.return_value.filter.return_value.all.return_value = []

        with patch(
            "src.execution.opportunity_lifecycle.OpportunityLifecycleManager"
        ) as MockLifecycle:
            daemon._auto_unstage_eod(mock_db)

            MockLifecycle.return_value.transition.assert_not_called()
            mock_db.commit.assert_not_called()
