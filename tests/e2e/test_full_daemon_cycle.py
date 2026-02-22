"""E2E test simulating daemon operation.

Covers:
- Create TAADDaemon with mocked dependencies
- Emit a few events
- Verify events are processed
- Verify health record is updated
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.agentic.config import (
    AutonomyConfig,
    ClaudeConfig,
    DaemonConfig,
    Phase5Config,
)
from src.agentic.daemon import TAADDaemon
from src.agentic.event_bus import EventBus, EventType
from src.agentic.reasoning_engine import DecisionOutput
from src.data.database import close_database, init_database
from src.data.models import DaemonEvent, DaemonHealth, DecisionAudit


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
def phase5_config(tmp_path):
    """Create a Phase5Config with test-friendly settings."""
    return Phase5Config(
        autonomy=AutonomyConfig(initial_level=4, max_level=4),  # L4 to avoid escalation
        claude=ClaudeConfig(daily_cost_cap_usd=100.0),
        daemon=DaemonConfig(
            pid_file=str(tmp_path / "test_taad.pid"),
            heartbeat_interval_seconds=10,
            event_poll_interval_seconds=1,
            max_events_per_cycle=5,
        ),
    )


def _mock_monitor_decision():
    """Create a MONITOR_ONLY decision output."""
    return DecisionOutput(
        action="MONITOR_ONLY",
        confidence=0.95,
        reasoning="Markets calm, monitoring only",
        key_factors=["low_vix"],
        risks_considered=["gap_risk"],
        metadata={},
    )


class TestFullDaemonCycle:
    """E2E tests simulating daemon operation."""

    def test_daemon_processes_events_and_updates_health(
        self, db_session, phase5_config
    ):
        """Create TAADDaemon, emit events, verify processing and health updates.

        This test manually initializes the daemon components and processes
        events through the pipeline without starting the full async run loop.
        """
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)

        # Initialize components
        daemon._init_components(db_session)

        # Start health monitor
        daemon.health.start()

        # Mock the reasoning engine to return MONITOR_ONLY
        daemon.reasoning = MagicMock()
        daemon.reasoning.reason.return_value = _mock_monitor_decision()
        mock_agent = MagicMock()
        mock_agent.total_input_tokens = 50
        mock_agent.total_output_tokens = 25
        mock_agent.session_cost = 0.005
        daemon.reasoning._reasoning_agent = mock_agent

        # Emit a few events
        event1 = daemon.event_bus.emit(EventType.SCHEDULED_CHECK)
        event2 = daemon.event_bus.emit(EventType.HEARTBEAT)
        event3 = daemon.event_bus.emit(EventType.SCHEDULED_CHECK)

        # Process each event through the pipeline
        for event in [event1, event2, event3]:
            asyncio.get_event_loop().run_until_complete(
                daemon._process_event(event, db_session)
            )

        # Verify events were processed
        db_session.refresh(event1)
        db_session.refresh(event2)
        db_session.refresh(event3)
        assert event1.status == "completed"
        assert event2.status == "completed"
        assert event3.status == "completed"

        # Verify audit trail
        audits = db_session.query(DecisionAudit).all()
        assert len(audits) == 3
        for audit in audits:
            assert audit.action == "MONITOR_ONLY"
            assert audit.confidence == 0.95

        # Verify health counters
        assert daemon.health._events_processed == 3
        assert daemon.health._decisions_made == 3

        # Update health in DB via heartbeat
        daemon.health.heartbeat()
        health = db_session.query(DaemonHealth).get(1)
        assert health is not None
        assert health.status == "running"
        assert health.events_processed_today == 3
        assert health.decisions_made_today == 3

        # Verify working memory updated
        assert len(daemon.memory.recent_decisions) == 3

        # Cleanup
        daemon.health.stop()

    def test_daemon_handles_processing_error(self, db_session, phase5_config):
        """Daemon should handle processing errors gracefully and continue."""
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon._init_components(db_session)
        daemon.health.start()

        # Mock reasoning to raise an error
        daemon.reasoning = MagicMock()
        daemon.reasoning.reason.side_effect = RuntimeError("Claude API timeout")
        mock_agent = MagicMock()
        mock_agent.total_input_tokens = 0
        mock_agent.total_output_tokens = 0
        mock_agent.session_cost = 0.0
        daemon.reasoning._reasoning_agent = mock_agent

        # Emit an event
        event = daemon.event_bus.emit(EventType.SCHEDULED_CHECK)

        # Process event - should not raise
        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event, db_session)
        )

        # Event should be marked as failed
        db_session.refresh(event)
        assert event.status == "failed"
        assert "Claude API timeout" in event.error_message

        # Error should be counted
        assert daemon.health._errors == 1

        # Cleanup
        daemon.health.stop()

    def test_daemon_event_priority_processing(self, db_session, phase5_config):
        """Events should be retrieved in priority order."""
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon._init_components(db_session)

        # Emit events in reverse priority order
        low = daemon.event_bus.emit(EventType.WEEKLY_LEARNING)  # priority 5
        medium = daemon.event_bus.emit(EventType.MARKET_OPEN)  # priority 3
        high = daemon.event_bus.emit(EventType.ORDER_FILLED)  # priority 2
        critical = daemon.event_bus.emit(EventType.EMERGENCY_STOP)  # priority 1

        # Get pending events - should be in priority order
        pending = daemon.event_bus.get_pending_events()
        assert len(pending) == 4
        assert pending[0].event_type == "EMERGENCY_STOP"
        assert pending[1].event_type == "ORDER_FILLED"
        assert pending[2].event_type == "MARKET_OPEN"
        assert pending[3].event_type == "WEEKLY_LEARNING"

    def test_daemon_working_memory_survives_restart(self, db_session, phase5_config):
        """Working memory should persist and reload on simulated restart."""
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon._init_components(db_session)
        daemon.health.start()

        # Mock reasoning
        daemon.reasoning = MagicMock()
        daemon.reasoning.reason.return_value = _mock_monitor_decision()
        mock_agent = MagicMock()
        mock_agent.total_input_tokens = 50
        mock_agent.total_output_tokens = 25
        mock_agent.session_cost = 0.005
        daemon.reasoning._reasoning_agent = mock_agent

        # Process an event
        event = daemon.event_bus.emit(EventType.SCHEDULED_CHECK)
        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event, db_session)
        )

        # Verify memory has 1 decision
        assert len(daemon.memory.recent_decisions) == 1
        decision_before = list(daemon.memory.recent_decisions)[0]

        daemon.health.stop()

        # Simulate restart: create new daemon with same DB session
        daemon2 = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon2._init_components(db_session)

        # Memory should have been loaded from DB
        assert len(daemon2.memory.recent_decisions) == 1
        decision_after = list(daemon2.memory.recent_decisions)[0]
        assert decision_after["action"] == decision_before["action"]

    def test_daemon_audit_records_model_and_cost(self, db_session, phase5_config):
        """DecisionAudit should include model name and cost information."""
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon._init_components(db_session)
        daemon.health.start()

        # Mock reasoning with cost tracking
        daemon.reasoning = MagicMock()
        daemon.reasoning.reason.return_value = _mock_monitor_decision()
        mock_agent = MagicMock()
        mock_agent.total_input_tokens = 500
        mock_agent.total_output_tokens = 200
        mock_agent.session_cost = 0.025
        daemon.reasoning._reasoning_agent = mock_agent

        event = daemon.event_bus.emit(EventType.SCHEDULED_CHECK)
        asyncio.get_event_loop().run_until_complete(
            daemon._process_event(event, db_session)
        )

        audit = db_session.query(DecisionAudit).first()
        assert audit is not None
        assert audit.input_tokens == 500
        assert audit.output_tokens == 200
        assert audit.cost_usd == 0.025
        assert audit.model_used == phase5_config.claude.reasoning_model

        daemon.health.stop()

    def test_daemon_no_pending_events_after_processing(self, db_session, phase5_config):
        """After processing all events, get_pending_events should return empty."""
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon._init_components(db_session)
        daemon.health.start()

        daemon.reasoning = MagicMock()
        daemon.reasoning.reason.return_value = _mock_monitor_decision()
        mock_agent = MagicMock()
        mock_agent.total_input_tokens = 50
        mock_agent.total_output_tokens = 25
        mock_agent.session_cost = 0.005
        daemon.reasoning._reasoning_agent = mock_agent

        # Emit and process 2 events
        for _ in range(2):
            event = daemon.event_bus.emit(EventType.SCHEDULED_CHECK)
            asyncio.get_event_loop().run_until_complete(
                daemon._process_event(event, db_session)
            )

        # All events should be completed
        pending = daemon.event_bus.get_pending_events()
        assert len(pending) == 0

        # Check event counts
        counts = daemon.event_bus.get_event_counts()
        assert counts.get("completed", 0) == 2
        assert counts.get("pending", 0) == 0

        daemon.health.stop()

    def test_daemon_pause_blocks_processing(self, db_session, phase5_config):
        """When paused, daemon.health.is_paused() should return True."""
        daemon = TAADDaemon(config=phase5_config, db_session=db_session)
        daemon._init_components(db_session)
        daemon.health.start()

        assert daemon.health.is_paused() is False

        daemon.health.pause()
        assert daemon.health.is_paused() is True

        daemon.health.resume()
        assert daemon.health.is_paused() is False

        daemon.health.stop()
