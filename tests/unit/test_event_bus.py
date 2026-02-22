"""Unit tests for the agentic event bus.

Tests the EventBus class that provides a durable, priority-ordered event queue
backed by the daemon_events database table. Covers event emission, status
transitions, priority ordering, and event counting.
"""

import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from src.agentic.event_bus import EVENT_PRIORITIES, EventBus, EventType
from src.data.database import close_database, get_session, init_database
from src.data.models import Base, DaemonEvent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_database():
    """Provide an in-memory SQLite database with all tables created."""
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database):
    """Provide a SQLAlchemy session bound to the in-memory database."""
    session = get_session()
    yield session
    session.close()


@pytest.fixture
def event_bus(db_session):
    """Provide an EventBus instance wired to the test database session."""
    return EventBus(db_session=db_session)


# ---------------------------------------------------------------------------
# Tests: EventType enum
# ---------------------------------------------------------------------------


class TestEventType:
    """Tests for the EventType enumeration."""

    def test_all_event_types_defined(self):
        """Verify that all 13 event types are present in the enum."""
        expected = [
            "EMERGENCY_STOP",
            "TWS_DISCONNECTED",
            "ORDER_FILLED",
            "POSITION_CLOSED",
            "RISK_LIMIT_BREACH",
            "TWS_RECONNECTED",
            "MARKET_OPEN",
            "MARKET_CLOSE",
            "HUMAN_OVERRIDE",
            "SCHEDULED_CHECK",
            "EOD_REFLECTION",
            "WEEKLY_LEARNING",
            "HEARTBEAT",
        ]
        actual = [e.name for e in EventType]
        assert actual == expected

    def test_event_type_values_match_names(self):
        """Each EventType value should equal its name (str enum)."""
        for et in EventType:
            assert et.value == et.name

    def test_event_type_is_str_subclass(self):
        """EventType members should be usable as plain strings."""
        assert isinstance(EventType.HEARTBEAT, str)
        assert EventType.HEARTBEAT == "HEARTBEAT"


# ---------------------------------------------------------------------------
# Tests: EVENT_PRIORITIES mapping
# ---------------------------------------------------------------------------


class TestEventPriorities:
    """Tests for the EVENT_PRIORITIES default priority mapping."""

    def test_all_event_types_have_priority(self):
        """Every EventType must have a default priority entry."""
        for et in EventType:
            assert et in EVENT_PRIORITIES, f"Missing priority for {et.name}"

    def test_no_extra_priorities(self):
        """EVENT_PRIORITIES should not contain keys outside of EventType."""
        assert set(EVENT_PRIORITIES.keys()) == set(EventType)

    def test_critical_events_are_priority_1(self):
        """EMERGENCY_STOP and TWS_DISCONNECTED must be priority 1."""
        assert EVENT_PRIORITIES[EventType.EMERGENCY_STOP] == 1
        assert EVENT_PRIORITIES[EventType.TWS_DISCONNECTED] == 1

    def test_high_events_are_priority_2(self):
        """ORDER_FILLED, POSITION_CLOSED, RISK_LIMIT_BREACH must be priority 2."""
        assert EVENT_PRIORITIES[EventType.ORDER_FILLED] == 2
        assert EVENT_PRIORITIES[EventType.POSITION_CLOSED] == 2
        assert EVENT_PRIORITIES[EventType.RISK_LIMIT_BREACH] == 2

    def test_medium_events_are_priority_3(self):
        """TWS_RECONNECTED, MARKET_OPEN, MARKET_CLOSE must be priority 3."""
        assert EVENT_PRIORITIES[EventType.TWS_RECONNECTED] == 3
        assert EVENT_PRIORITIES[EventType.MARKET_OPEN] == 3
        assert EVENT_PRIORITIES[EventType.MARKET_CLOSE] == 3

    def test_normal_events_are_priority_4(self):
        """HUMAN_OVERRIDE, SCHEDULED_CHECK must be priority 4."""
        assert EVENT_PRIORITIES[EventType.HUMAN_OVERRIDE] == 4
        assert EVENT_PRIORITIES[EventType.SCHEDULED_CHECK] == 4

    def test_low_events_are_priority_5(self):
        """EOD_REFLECTION, WEEKLY_LEARNING, HEARTBEAT must be priority 5."""
        assert EVENT_PRIORITIES[EventType.EOD_REFLECTION] == 5
        assert EVENT_PRIORITIES[EventType.WEEKLY_LEARNING] == 5
        assert EVENT_PRIORITIES[EventType.HEARTBEAT] == 5

    def test_priorities_are_positive_integers(self):
        """All priority values must be positive integers."""
        for et, priority in EVENT_PRIORITIES.items():
            assert isinstance(priority, int), f"{et.name} priority is not int"
            assert priority >= 1, f"{et.name} priority is < 1"


# ---------------------------------------------------------------------------
# Tests: EventBus.emit()
# ---------------------------------------------------------------------------


class TestEmit:
    """Tests for EventBus.emit()."""

    def test_emit_returns_daemon_event(self, event_bus):
        """emit() should return a persisted DaemonEvent instance."""
        event = event_bus.emit(EventType.HEARTBEAT)
        assert isinstance(event, DaemonEvent)

    def test_emit_persists_to_database(self, event_bus, db_session):
        """Emitted event should be queryable from the database."""
        event_bus.emit(EventType.HEARTBEAT)

        rows = db_session.query(DaemonEvent).all()
        assert len(rows) == 1

    def test_emit_sets_event_type(self, event_bus):
        """event_type column should match the EventType value string."""
        event = event_bus.emit(EventType.MARKET_OPEN)
        assert event.event_type == "MARKET_OPEN"

    def test_emit_sets_default_priority(self, event_bus):
        """Priority should come from EVENT_PRIORITIES when not overridden."""
        event = event_bus.emit(EventType.EMERGENCY_STOP)
        assert event.priority == 1

        event2 = event_bus.emit(EventType.HEARTBEAT)
        assert event2.priority == 5

    def test_emit_custom_priority_overrides_default(self, event_bus):
        """An explicit priority argument should override the default."""
        event = event_bus.emit(EventType.HEARTBEAT, priority=1)
        assert event.priority == 1

    def test_emit_sets_status_to_pending(self, event_bus):
        """New events must start with status='pending'."""
        event = event_bus.emit(EventType.HEARTBEAT)
        assert event.status == "pending"

    def test_emit_stores_payload(self, event_bus):
        """Payload dict should be persisted to the event."""
        payload = {"order_id": 42, "symbol": "AAPL"}
        event = event_bus.emit(EventType.ORDER_FILLED, payload=payload)
        assert event.payload == payload

    def test_emit_empty_payload_when_none(self, event_bus):
        """When payload is None, the stored payload should be an empty dict."""
        event = event_bus.emit(EventType.HEARTBEAT, payload=None)
        assert event.payload == {}

    def test_emit_sets_created_at(self, event_bus):
        """created_at should be set to approximately now."""
        before = datetime.utcnow()
        event = event_bus.emit(EventType.HEARTBEAT)
        after = datetime.utcnow()

        assert event.created_at is not None
        assert before <= event.created_at <= after

    def test_emit_assigns_auto_increment_id(self, event_bus):
        """Each emitted event should get a unique auto-incremented id."""
        e1 = event_bus.emit(EventType.HEARTBEAT)
        e2 = event_bus.emit(EventType.HEARTBEAT)

        assert e1.id is not None
        assert e2.id is not None
        assert e2.id > e1.id

    def test_emit_multiple_event_types(self, event_bus, db_session):
        """Emitting different event types should all persist correctly."""
        event_bus.emit(EventType.EMERGENCY_STOP)
        event_bus.emit(EventType.ORDER_FILLED, payload={"fill_price": 1.25})
        event_bus.emit(EventType.HEARTBEAT)

        rows = db_session.query(DaemonEvent).all()
        assert len(rows) == 3

        types = {r.event_type for r in rows}
        assert types == {"EMERGENCY_STOP", "ORDER_FILLED", "HEARTBEAT"}

    def test_emit_complex_payload(self, event_bus):
        """Nested/complex payloads should serialize correctly via JSON column."""
        payload = {
            "positions": [
                {"symbol": "AAPL", "strike": 150.0},
                {"symbol": "MSFT", "strike": 300.0},
            ],
            "total_pnl": -250.50,
            "flags": {"urgent": True},
        }
        event = event_bus.emit(EventType.RISK_LIMIT_BREACH, payload=payload)
        assert event.payload == payload
        assert event.payload["positions"][0]["symbol"] == "AAPL"


# ---------------------------------------------------------------------------
# Tests: EventBus.get_pending_events()
# ---------------------------------------------------------------------------


class TestGetPendingEvents:
    """Tests for EventBus.get_pending_events()."""

    def test_returns_empty_list_when_no_events(self, event_bus):
        """Should return [] when the table is empty."""
        result = event_bus.get_pending_events()
        assert result == []

    def test_returns_pending_events(self, event_bus):
        """Should return events with status='pending'."""
        event_bus.emit(EventType.HEARTBEAT)
        event_bus.emit(EventType.MARKET_OPEN)

        events = event_bus.get_pending_events()
        assert len(events) == 2
        assert all(e.status == "pending" for e in events)

    def test_returns_processing_events(self, event_bus):
        """Should also return events with status='processing'."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)

        events = event_bus.get_pending_events()
        assert len(events) == 1
        assert events[0].status == "processing"

    def test_excludes_completed_events(self, event_bus):
        """Completed events should not appear in pending results."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)
        event_bus.mark_completed(event)

        events = event_bus.get_pending_events()
        assert len(events) == 0

    def test_excludes_failed_events(self, event_bus):
        """Failed events should not appear in pending results."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="something broke")

        events = event_bus.get_pending_events()
        assert len(events) == 0

    def test_orders_by_priority_ascending(self, event_bus):
        """Higher-priority (lower number) events should come first."""
        event_bus.emit(EventType.HEARTBEAT)          # priority 5
        event_bus.emit(EventType.EMERGENCY_STOP)     # priority 1
        event_bus.emit(EventType.MARKET_OPEN)        # priority 3

        events = event_bus.get_pending_events()
        priorities = [e.priority for e in events]
        assert priorities == sorted(priorities)
        assert priorities == [1, 3, 5]

    def test_orders_by_created_at_within_same_priority(self, event_bus):
        """Within the same priority, older events should come first (FIFO)."""
        e1 = event_bus.emit(EventType.HEARTBEAT)           # priority 5
        e2 = event_bus.emit(EventType.EOD_REFLECTION)      # priority 5
        e3 = event_bus.emit(EventType.WEEKLY_LEARNING)     # priority 5

        events = event_bus.get_pending_events()
        ids = [e.id for e in events]
        assert ids == [e1.id, e2.id, e3.id]

    def test_priority_then_created_at_combined(self, event_bus):
        """Mixed priorities and creation times should sort correctly."""
        # Emit in deliberate order: low priority first, high priority last
        low = event_bus.emit(EventType.HEARTBEAT)          # priority 5
        med = event_bus.emit(EventType.MARKET_OPEN)        # priority 3
        high = event_bus.emit(EventType.EMERGENCY_STOP)    # priority 1

        events = event_bus.get_pending_events()
        ids = [e.id for e in events]
        assert ids == [high.id, med.id, low.id]

    def test_limit_parameter(self, event_bus):
        """The limit parameter should cap the number of returned events."""
        for _ in range(5):
            event_bus.emit(EventType.HEARTBEAT)

        events = event_bus.get_pending_events(limit=3)
        assert len(events) == 3

    def test_default_limit_is_10(self, event_bus):
        """Default limit should be 10."""
        for _ in range(15):
            event_bus.emit(EventType.HEARTBEAT)

        events = event_bus.get_pending_events()
        assert len(events) == 10

    def test_limit_greater_than_available(self, event_bus):
        """When limit exceeds available events, return all available."""
        event_bus.emit(EventType.HEARTBEAT)
        event_bus.emit(EventType.HEARTBEAT)

        events = event_bus.get_pending_events(limit=100)
        assert len(events) == 2

    def test_mixed_statuses_returns_only_actionable(self, event_bus):
        """Only pending and processing events should be returned."""
        pending = event_bus.emit(EventType.HEARTBEAT)

        processing = event_bus.emit(EventType.MARKET_OPEN)
        event_bus.mark_processing(processing)

        completed = event_bus.emit(EventType.ORDER_FILLED)
        event_bus.mark_processing(completed)
        event_bus.mark_completed(completed)

        failed = event_bus.emit(EventType.SCHEDULED_CHECK)
        event_bus.mark_failed(failed, error="timeout")

        events = event_bus.get_pending_events()
        event_ids = {e.id for e in events}
        assert pending.id in event_ids
        assert processing.id in event_ids
        assert completed.id not in event_ids
        assert failed.id not in event_ids
        assert len(events) == 2


# ---------------------------------------------------------------------------
# Tests: EventBus.mark_processing()
# ---------------------------------------------------------------------------


class TestMarkProcessing:
    """Tests for EventBus.mark_processing()."""

    def test_sets_status_to_processing(self, event_bus):
        """Status should become 'processing'."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)
        assert event.status == "processing"

    def test_sets_processed_at_timestamp(self, event_bus):
        """processed_at should be set to approximately now."""
        event = event_bus.emit(EventType.HEARTBEAT)
        before = datetime.utcnow()
        event_bus.mark_processing(event)
        after = datetime.utcnow()

        assert event.processed_at is not None
        assert before <= event.processed_at <= after

    def test_persisted_to_database(self, event_bus, db_session):
        """Status change should be committed to the database."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)

        refreshed = db_session.get(DaemonEvent, event.id)
        assert refreshed.status == "processing"
        assert refreshed.processed_at is not None


# ---------------------------------------------------------------------------
# Tests: EventBus.mark_completed()
# ---------------------------------------------------------------------------


class TestMarkCompleted:
    """Tests for EventBus.mark_completed()."""

    def test_sets_status_to_completed(self, event_bus):
        """Status should become 'completed'."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)
        event_bus.mark_completed(event)
        assert event.status == "completed"

    def test_sets_completed_at_timestamp(self, event_bus):
        """completed_at should be set to approximately now."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)

        before = datetime.utcnow()
        event_bus.mark_completed(event)
        after = datetime.utcnow()

        assert event.completed_at is not None
        assert before <= event.completed_at <= after

    def test_persisted_to_database(self, event_bus, db_session):
        """Completed status should be committed to the database."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)
        event_bus.mark_completed(event)

        refreshed = db_session.get(DaemonEvent, event.id)
        assert refreshed.status == "completed"
        assert refreshed.completed_at is not None

    def test_completed_event_not_returned_by_get_pending(self, event_bus):
        """A completed event must not appear in get_pending_events()."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)
        event_bus.mark_completed(event)

        assert event_bus.get_pending_events() == []


# ---------------------------------------------------------------------------
# Tests: EventBus.mark_failed()
# ---------------------------------------------------------------------------


class TestMarkFailed:
    """Tests for EventBus.mark_failed()."""

    def test_sets_status_to_failed(self, event_bus):
        """Status should become 'failed'."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="connection lost")
        assert event.status == "failed"

    def test_stores_error_message(self, event_bus):
        """error_message should contain the provided error string."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="connection lost")
        assert event.error_message == "connection lost"

    def test_sets_completed_at_timestamp(self, event_bus):
        """completed_at should be set even for failed events."""
        event = event_bus.emit(EventType.HEARTBEAT)

        before = datetime.utcnow()
        event_bus.mark_failed(event, error="timeout")
        after = datetime.utcnow()

        assert event.completed_at is not None
        assert before <= event.completed_at <= after

    def test_persisted_to_database(self, event_bus, db_session):
        """Failed status and error message should be committed to the database."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="out of memory")

        refreshed = db_session.get(DaemonEvent, event.id)
        assert refreshed.status == "failed"
        assert refreshed.error_message == "out of memory"

    def test_failed_event_not_returned_by_get_pending(self, event_bus):
        """A failed event must not appear in get_pending_events()."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="boom")

        assert event_bus.get_pending_events() == []

    def test_empty_error_message(self, event_bus):
        """An empty string error should still be stored."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="")
        assert event.error_message == ""


# ---------------------------------------------------------------------------
# Tests: EventBus.get_event_counts()
# ---------------------------------------------------------------------------


class TestGetEventCounts:
    """Tests for EventBus.get_event_counts()."""

    def test_returns_empty_dict_when_no_events(self, event_bus):
        """With no events in the database, counts should be empty."""
        counts = event_bus.get_event_counts()
        assert counts == {}

    def test_counts_pending_events(self, event_bus):
        """Should count pending events."""
        event_bus.emit(EventType.HEARTBEAT)
        event_bus.emit(EventType.MARKET_OPEN)

        counts = event_bus.get_event_counts()
        assert counts["pending"] == 2

    def test_counts_processing_events(self, event_bus):
        """Should count processing events separately."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)

        counts = event_bus.get_event_counts()
        assert counts["processing"] == 1

    def test_counts_completed_events(self, event_bus):
        """Should count completed events."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_processing(event)
        event_bus.mark_completed(event)

        counts = event_bus.get_event_counts()
        assert counts["completed"] == 1

    def test_counts_failed_events(self, event_bus):
        """Should count failed events."""
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error="err")

        counts = event_bus.get_event_counts()
        assert counts["failed"] == 1

    def test_counts_multiple_statuses(self, event_bus):
        """Should report accurate counts across all statuses simultaneously."""
        # 3 pending
        event_bus.emit(EventType.HEARTBEAT)
        event_bus.emit(EventType.HEARTBEAT)
        event_bus.emit(EventType.HEARTBEAT)

        # 2 processing
        p1 = event_bus.emit(EventType.MARKET_OPEN)
        p2 = event_bus.emit(EventType.MARKET_CLOSE)
        event_bus.mark_processing(p1)
        event_bus.mark_processing(p2)

        # 1 completed
        c1 = event_bus.emit(EventType.ORDER_FILLED)
        event_bus.mark_processing(c1)
        event_bus.mark_completed(c1)

        # 1 failed
        f1 = event_bus.emit(EventType.SCHEDULED_CHECK)
        event_bus.mark_failed(f1, error="timeout")

        counts = event_bus.get_event_counts()
        assert counts["pending"] == 3
        assert counts["processing"] == 2
        assert counts["completed"] == 1
        assert counts["failed"] == 1

    def test_counts_return_type(self, event_bus):
        """Return value should be a dict mapping str -> int."""
        event_bus.emit(EventType.HEARTBEAT)
        counts = event_bus.get_event_counts()

        assert isinstance(counts, dict)
        for status, count in counts.items():
            assert isinstance(status, str)
            assert isinstance(count, int)


# ---------------------------------------------------------------------------
# Tests: EventBus.stop()
# ---------------------------------------------------------------------------


class TestStop:
    """Tests for EventBus.stop()."""

    def test_stop_sets_event(self, event_bus):
        """stop() should set the internal asyncio Event."""
        assert not event_bus._stop_event.is_set()
        event_bus.stop()
        assert event_bus._stop_event.is_set()


# ---------------------------------------------------------------------------
# Tests: Full lifecycle transitions
# ---------------------------------------------------------------------------


class TestEventLifecycle:
    """Integration-style tests for complete event lifecycle flows."""

    def test_full_success_lifecycle(self, event_bus):
        """pending -> processing -> completed is the happy path."""
        event = event_bus.emit(EventType.ORDER_FILLED, payload={"order_id": 99})

        assert event.status == "pending"
        assert event.processed_at is None
        assert event.completed_at is None

        event_bus.mark_processing(event)
        assert event.status == "processing"
        assert event.processed_at is not None
        assert event.completed_at is None

        event_bus.mark_completed(event)
        assert event.status == "completed"
        assert event.completed_at is not None

    def test_full_failure_lifecycle(self, event_bus):
        """pending -> failed captures the error without going through processing."""
        event = event_bus.emit(EventType.ORDER_FILLED)

        event_bus.mark_failed(event, error="IBKR timeout")
        assert event.status == "failed"
        assert event.error_message == "IBKR timeout"
        assert event.completed_at is not None

    def test_processing_then_failure_lifecycle(self, event_bus):
        """pending -> processing -> failed for errors discovered during processing."""
        event = event_bus.emit(EventType.MARKET_OPEN)

        event_bus.mark_processing(event)
        event_bus.mark_failed(event, error="market data unavailable")

        assert event.status == "failed"
        assert event.error_message == "market data unavailable"
        assert event.processed_at is not None
        assert event.completed_at is not None

    def test_multiple_events_different_lifecycles(self, event_bus):
        """Multiple events progressing through different lifecycle paths."""
        e1 = event_bus.emit(EventType.ORDER_FILLED)
        e2 = event_bus.emit(EventType.HEARTBEAT)
        e3 = event_bus.emit(EventType.MARKET_OPEN)

        # e1: success
        event_bus.mark_processing(e1)
        event_bus.mark_completed(e1)

        # e2: failure
        event_bus.mark_failed(e2, error="skip")

        # e3: still pending

        counts = event_bus.get_event_counts()
        assert counts.get("completed") == 1
        assert counts.get("failed") == 1
        assert counts.get("pending") == 1

        # Only e3 should be returned as pending
        pending = event_bus.get_pending_events()
        assert len(pending) == 1
        assert pending[0].id == e3.id


# ---------------------------------------------------------------------------
# Tests: Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_emit_all_event_types(self, event_bus, db_session):
        """Every EventType should be emittable without errors."""
        for et in EventType:
            event = event_bus.emit(et)
            assert event.event_type == et.value
            assert event.priority == EVENT_PRIORITIES[et]

        total = db_session.query(DaemonEvent).count()
        assert total == len(EventType)

    def test_priority_override_to_higher(self, event_bus):
        """Can override a low-priority event type to priority 1."""
        event = event_bus.emit(EventType.HEARTBEAT, priority=1)
        assert event.priority == 1

    def test_priority_override_to_lower(self, event_bus):
        """Can override a high-priority event type to priority 10."""
        event = event_bus.emit(EventType.EMERGENCY_STOP, priority=10)
        assert event.priority == 10

    def test_large_payload(self, event_bus):
        """A large payload should be stored and retrieved correctly."""
        payload = {f"key_{i}": f"value_{i}" for i in range(100)}
        event = event_bus.emit(EventType.EOD_REFLECTION, payload=payload)
        assert len(event.payload) == 100
        assert event.payload["key_50"] == "value_50"

    def test_payload_with_none_values(self, event_bus):
        """Payload can contain None values in its fields."""
        payload = {"result": None, "count": 0, "flag": False}
        event = event_bus.emit(EventType.SCHEDULED_CHECK, payload=payload)
        assert event.payload["result"] is None
        assert event.payload["count"] == 0
        assert event.payload["flag"] is False

    def test_get_pending_limit_zero(self, event_bus):
        """Requesting limit=0 should return no events."""
        event_bus.emit(EventType.HEARTBEAT)
        events = event_bus.get_pending_events(limit=0)
        assert events == []

    def test_long_error_message(self, event_bus):
        """A long error message (traceback) should be stored fully."""
        long_error = "Error: " + "x" * 5000
        event = event_bus.emit(EventType.HEARTBEAT)
        event_bus.mark_failed(event, error=long_error)
        assert event.error_message == long_error
        assert len(event.error_message) == 5007
