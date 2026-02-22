"""Heartbeat and status tracking for the daemon process.

Updates daemon_health table every 60 seconds. Manages PID file.
External monitors can query the health table to check liveness.
"""

import os
import signal
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import DaemonHealth


class HealthMonitor:
    """Daemon health monitoring with heartbeat and PID file.

    Writes heartbeat to daemon_health table every interval.
    Manages PID file at run/taad.pid. Registers SIGTERM handler
    for graceful shutdown.
    """

    def __init__(
        self,
        db_session: Session,
        pid_file: str = "run/taad.pid",
        heartbeat_interval: int = 60,
    ):
        """Initialize health monitor.

        Args:
            db_session: SQLAlchemy session
            pid_file: Path to PID file
            heartbeat_interval: Seconds between heartbeats
        """
        self.db = db_session
        self.pid_file = Path(pid_file)
        self.heartbeat_interval = heartbeat_interval
        self._started_at: Optional[datetime] = None
        self._events_processed = 0
        self._decisions_made = 0
        self._errors = 0
        self._shutdown_requested = False

    def start(self) -> None:
        """Start health monitoring: write PID file and initial heartbeat."""
        self._started_at = datetime.utcnow()
        pid = os.getpid()

        # Write PID file
        self.pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.pid_file.write_text(str(pid))
        logger.info(f"PID file written: {self.pid_file} (pid={pid})")

        # Register signal handlers
        self._register_signals()

        # Initial heartbeat
        self._update_health(
            pid=pid,
            status="running",
            message="Daemon started",
        )

    def heartbeat(self, message: Optional[str] = None) -> None:
        """Send a heartbeat update.

        Args:
            message: Optional status message
        """
        uptime = 0
        if self._started_at:
            uptime = int((datetime.utcnow() - self._started_at).total_seconds())

        self._update_health(
            pid=os.getpid(),
            status="running",
            message=message or "Heartbeat OK",
            uptime_seconds=uptime,
        )

    def record_event(self, event_type: str) -> None:
        """Record that an event was processed.

        Args:
            event_type: Type of event processed
        """
        self._events_processed += 1

    def record_decision(self) -> None:
        """Record that a decision was made."""
        self._decisions_made += 1

    def record_error(self) -> None:
        """Record an error."""
        self._errors += 1

    def pause(self) -> None:
        """Mark daemon as paused."""
        self._update_health(status="paused", message="Daemon paused by user")

    def resume(self) -> None:
        """Mark daemon as running again."""
        self._update_health(status="running", message="Daemon resumed")

    def stop(self) -> None:
        """Clean shutdown: update status and remove PID file."""
        self._update_health(
            status="stopped",
            message="Daemon stopped gracefully",
        )

        if self.pid_file.exists():
            self.pid_file.unlink()
            logger.info(f"PID file removed: {self.pid_file}")

    def is_paused(self) -> bool:
        """Check if daemon is paused (by user via CLI).

        Returns:
            True if paused
        """
        row = self.db.query(DaemonHealth).get(1)
        if row:
            # Refresh from DB in case CLI changed it
            self.db.refresh(row)
            return row.status == "paused"
        return False

    @property
    def shutdown_requested(self) -> bool:
        """Check if shutdown has been requested via signal."""
        return self._shutdown_requested

    def get_status(self) -> dict:
        """Get current daemon health status.

        Returns:
            Status dictionary
        """
        row = self.db.query(DaemonHealth).get(1)
        if not row:
            return {"status": "unknown", "message": "No health record found"}

        return {
            "pid": row.pid,
            "status": row.status,
            "last_heartbeat": str(row.last_heartbeat) if row.last_heartbeat else None,
            "uptime_seconds": row.uptime_seconds,
            "events_processed_today": row.events_processed_today,
            "decisions_made_today": row.decisions_made_today,
            "errors_today": row.errors_today,
            "autonomy_level": row.autonomy_level,
            "message": row.message,
            "started_at": str(row.started_at) if row.started_at else None,
        }

    def _update_health(
        self,
        pid: Optional[int] = None,
        status: Optional[str] = None,
        message: Optional[str] = None,
        uptime_seconds: Optional[int] = None,
    ) -> None:
        """Update or create the daemon_health row.

        Uses single-row upsert pattern (id=1).
        """
        try:
            row = self.db.query(DaemonHealth).get(1)
            if row is None:
                row = DaemonHealth(id=1)
                self.db.add(row)

            if pid is not None:
                row.pid = pid
            if status is not None:
                row.status = status
            if message is not None:
                row.message = message
            if uptime_seconds is not None:
                row.uptime_seconds = uptime_seconds

            row.last_heartbeat = datetime.utcnow()
            row.events_processed_today = self._events_processed
            row.decisions_made_today = self._decisions_made
            row.errors_today = self._errors
            row.started_at = self._started_at

            self.db.commit()
        except Exception as e:
            logger.error(f"Health update failed: {e}")
            try:
                self.db.rollback()
            except Exception:
                pass

    def _register_signals(self) -> None:
        """Register SIGTERM/SIGINT for graceful shutdown."""
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            if not hasattr(sys, "ps1"):
                signal.signal(signal.SIGINT, self._handle_signal)
        except (OSError, ValueError):
            logger.debug("Could not register signal handlers")

    def _handle_signal(self, signum, frame):
        """Signal handler: request graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Received {sig_name}, requesting graceful shutdown")
        self._shutdown_requested = True

    @staticmethod
    def is_daemon_running(pid_file: str = "run/taad.pid") -> Optional[int]:
        """Check if daemon is running by reading PID file.

        Args:
            pid_file: Path to PID file

        Returns:
            PID if running, None if not
        """
        path = Path(pid_file)
        if not path.exists():
            return None

        try:
            pid = int(path.read_text().strip())
            # Check if process is actually running
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            return None
