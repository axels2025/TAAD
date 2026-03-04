"""Durable PostgreSQL-backed event queue for the agentic daemon.

Events are persisted to the daemon_events table and replayed on startup.
Supports 13 event types with priority ordering. Time-based emitters
use MarketCalendar. IBKR callbacks register for fill/disconnect/reconnect.
"""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from enum import Enum
from typing import Optional

from loguru import logger
from sqlalchemy import update as sa_update
from sqlalchemy.orm import Session

from src.data.models import DaemonEvent


class EventType(str, Enum):
    """Daemon event types ordered by typical priority."""

    # Priority 1 - Critical
    EMERGENCY_STOP = "EMERGENCY_STOP"
    TWS_DISCONNECTED = "TWS_DISCONNECTED"

    # Priority 2 - High
    ORDER_FILLED = "ORDER_FILLED"
    POSITION_CLOSED = "POSITION_CLOSED"
    RISK_LIMIT_BREACH = "RISK_LIMIT_BREACH"

    # Priority 3 - Medium
    TWS_RECONNECTED = "TWS_RECONNECTED"
    MARKET_OPEN = "MARKET_OPEN"
    MARKET_CLOSE = "MARKET_CLOSE"
    POSITION_EXIT_CHECK = "POSITION_EXIT_CHECK"

    # Priority 4 - Normal
    HUMAN_OVERRIDE = "HUMAN_OVERRIDE"
    SCHEDULED_CHECK = "SCHEDULED_CHECK"

    # Priority 5 - Low
    EOD_REFLECTION = "EOD_REFLECTION"
    WEEKLY_LEARNING = "WEEKLY_LEARNING"
    HEARTBEAT = "HEARTBEAT"


# Default priorities for each event type
EVENT_PRIORITIES: dict[EventType, int] = {
    EventType.EMERGENCY_STOP: 1,
    EventType.TWS_DISCONNECTED: 1,
    EventType.ORDER_FILLED: 2,
    EventType.POSITION_CLOSED: 2,
    EventType.RISK_LIMIT_BREACH: 2,
    EventType.TWS_RECONNECTED: 3,
    EventType.MARKET_OPEN: 3,
    EventType.MARKET_CLOSE: 3,
    EventType.POSITION_EXIT_CHECK: 3,
    EventType.HUMAN_OVERRIDE: 4,
    EventType.SCHEDULED_CHECK: 4,
    EventType.EOD_REFLECTION: 5,
    EventType.WEEKLY_LEARNING: 5,
    EventType.HEARTBEAT: 5,
}


class EventBus:
    """Durable event queue backed by PostgreSQL.

    Events are persisted to daemon_events table. On startup, pending/processing
    events are replayed. New events are yielded via async stream().
    """

    def __init__(self, db_session: Session):
        """Initialize event bus.

        Args:
            db_session: SQLAlchemy session for event persistence
        """
        self.db = db_session
        self._stop_event = asyncio.Event()

    def emit(
        self,
        event_type: EventType,
        payload: Optional[dict] = None,
        priority: Optional[int] = None,
    ) -> DaemonEvent:
        """Emit a new event to the queue.

        Persists the event to daemon_events table.

        Args:
            event_type: Type of event
            payload: Optional event data
            priority: Override default priority (1=highest, 10=lowest)

        Returns:
            The persisted DaemonEvent
        """
        if priority is None:
            priority = EVENT_PRIORITIES.get(event_type, 5)

        event = DaemonEvent(
            event_type=event_type.value,
            priority=priority,
            status="pending",
            payload=payload or {},
            created_at=datetime.now(UTC),
        )
        self.db.add(event)
        self.db.commit()

        logger.info(f"Event emitted: {event_type.value} (id={event.id}, priority={priority})")
        return event

    def get_pending_events(self, limit: int = 10) -> list[DaemonEvent]:
        """Get claimable (status='pending') events ordered by priority then creation time.

        Used during steady-state polling. Only returns events that have not yet
        been claimed — avoids re-yielding events already in-flight (status='processing'),
        which would spam "already claimed" warnings every poll cycle.

        Args:
            limit: Maximum events to return

        Returns:
            List of pending DaemonEvent records
        """
        return (
            self.db.query(DaemonEvent)
            .filter(DaemonEvent.status == "pending")
            .order_by(DaemonEvent.priority, DaemonEvent.created_at)
            .limit(limit)
            .all()
        )

    def reset_stale_processing_events(self) -> int:
        """Reset 'processing' events back to 'pending' on startup.

        Called once at daemon startup to recover from a previous crash.
        Events stuck in 'processing' were claimed by a process that no longer
        exists — resetting them to 'pending' lets the new process claim them
        normally via mark_processing() (which guards on status='pending').

        Returns:
            Number of events reset
        """
        result = self.db.execute(
            sa_update(DaemonEvent)
            .where(DaemonEvent.status == "processing")
            .values(status="pending")
        )
        self.db.commit()
        if result.rowcount:
            logger.info(
                f"Reset {result.rowcount} stale 'processing' events to 'pending' (crash recovery)"
            )
        return result.rowcount

    def mark_processing(self, event: DaemonEvent) -> bool:
        """Atomically claim an event for processing.

        Uses a SQL-level UPDATE WHERE status='pending' so that two daemon
        processes running simultaneously cannot both claim the same event.

        Args:
            event: The event to claim

        Returns:
            True if this caller successfully claimed the event, False if it
            was already claimed by another process.
        """
        result = self.db.execute(
            sa_update(DaemonEvent)
            .where(DaemonEvent.id == event.id)
            .where(DaemonEvent.status == "pending")
            .values(status="processing", processed_at=datetime.now(UTC))
        )
        self.db.commit()
        if result.rowcount == 0:
            # Event was completed/failed between the poll and the claim attempt.
            # This is normal under concurrent load — log at debug, not warning.
            logger.debug(f"Event {event.id} no longer claimable (completed or failed)")
            return False
        self.db.refresh(event)
        return True

    def mark_completed(self, event: DaemonEvent) -> None:
        """Mark event as completed.

        Args:
            event: The event to mark
        """
        event.status = "completed"
        event.completed_at = datetime.now(UTC)
        self.db.commit()

    def mark_failed(self, event: DaemonEvent, error: str) -> None:
        """Mark event as failed with error message.

        Args:
            event: The event to mark
            error: Error description
        """
        event.status = "failed"
        event.error_message = error
        event.completed_at = datetime.now(UTC)
        self.db.commit()

    async def stream(
        self, poll_interval: float = 5.0, max_events: int = 10
    ) -> AsyncGenerator[DaemonEvent, None]:
        """Async generator that yields events ordered by priority.

        First replays any pending/processing events from DB, then polls
        for new events at the configured interval.

        Args:
            poll_interval: Seconds between DB polls
            max_events: Max events per poll

        Yields:
            DaemonEvent records in priority order
        """
        # Startup crash recovery: reset any events stuck in 'processing' from a
        # previous run back to 'pending' so mark_processing() can claim them cleanly.
        self.reset_stale_processing_events()

        # Replay events that were pending at startup
        pending = self.get_pending_events(limit=max_events)
        if pending:
            logger.info(f"Replaying {len(pending)} pending events from DB")
            for event in pending:
                if self._stop_event.is_set():
                    return
                yield event

        # Steady-state polling: only 'pending' events are fetched.
        # Completed/failed events are excluded; in-flight events are excluded
        # (they were claimed by mark_processing and are now 'processing').
        while not self._stop_event.is_set():
            events = self.get_pending_events(limit=max_events)
            for event in events:
                if self._stop_event.is_set():
                    return
                yield event

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=poll_interval
                )
            except asyncio.TimeoutError:
                pass  # Normal polling timeout

    def stop(self) -> None:
        """Signal the event stream to stop."""
        self._stop_event.set()

    def get_event_counts(self) -> dict[str, int]:
        """Get counts of events by status.

        Returns:
            Dictionary mapping status to count
        """
        from sqlalchemy import func as sa_func

        results = (
            self.db.query(DaemonEvent.status, sa_func.count(DaemonEvent.id))
            .group_by(DaemonEvent.status)
            .all()
        )
        return {status: count for status, count in results}
