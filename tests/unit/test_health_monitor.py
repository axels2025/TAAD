"""Tests for HealthMonitor heartbeat and PID file management.

Covers:
- start() writes PID file and creates health record
- heartbeat() updates last_heartbeat
- record_event/decision/error increment counters
- pause/resume update status
- stop() removes PID file
- is_daemon_running() checks PID file
- is_paused() reads from DB
"""

import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agentic.health_monitor import HealthMonitor
from src.data.database import close_database, init_database
from src.data.models import DaemonHealth


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
def tmp_pid_dir(tmp_path):
    """Provide a temporary directory for PID files."""
    return tmp_path


@pytest.fixture
def monitor(db_session, tmp_pid_dir):
    """Create a HealthMonitor with a temporary PID file path."""
    pid_file = str(tmp_pid_dir / "test_taad.pid")
    hm = HealthMonitor(
        db_session=db_session,
        pid_file=pid_file,
        heartbeat_interval=10,
    )
    yield hm
    # Cleanup PID file if still present
    pid_path = Path(pid_file)
    if pid_path.exists():
        pid_path.unlink()


class TestStart:
    """Tests for start() method."""

    def test_start_writes_pid_file(self, monitor):
        """start() should create a PID file with the current process PID."""
        monitor.start()

        pid_path = monitor.pid_file
        assert pid_path.exists()
        assert pid_path.read_text().strip() == str(os.getpid())

    def test_start_creates_health_record(self, monitor, db_session):
        """start() should create an initial health record in the database."""
        monitor.start()

        row = db_session.query(DaemonHealth).get(1)
        assert row is not None
        assert row.status == "running"
        assert row.pid == os.getpid()
        assert row.message == "Daemon started"

    def test_start_sets_started_at(self, monitor):
        """start() should set the internal _started_at timestamp."""
        assert monitor._started_at is None
        monitor.start()
        assert monitor._started_at is not None
        assert isinstance(monitor._started_at, datetime)

    def test_start_creates_pid_directory(self, tmp_path):
        """start() should create the PID file directory if it doesn't exist."""
        from src.data.database import close_database, init_database, get_session

        engine = init_database(database_url="sqlite:///:memory:")
        session = get_session()

        nested_dir = tmp_path / "deep" / "nested" / "dir"
        pid_file = str(nested_dir / "test.pid")
        hm = HealthMonitor(db_session=session, pid_file=pid_file)

        hm.start()

        assert nested_dir.exists()
        assert Path(pid_file).exists()

        # Cleanup
        Path(pid_file).unlink()
        session.close()
        close_database()


class TestHeartbeat:
    """Tests for heartbeat() method."""

    def test_heartbeat_updates_last_heartbeat(self, monitor, db_session):
        """heartbeat() should update the last_heartbeat timestamp in DB."""
        monitor.start()

        # Record initial heartbeat time
        row = db_session.query(DaemonHealth).get(1)
        initial_heartbeat = row.last_heartbeat

        # Send heartbeat
        monitor.heartbeat(message="Test heartbeat")

        db_session.refresh(row)
        assert row.last_heartbeat >= initial_heartbeat
        assert row.message == "Test heartbeat"
        assert row.status == "running"

    def test_heartbeat_default_message(self, monitor, db_session):
        """heartbeat() with no message should use 'Heartbeat OK'."""
        monitor.start()
        monitor.heartbeat()

        row = db_session.query(DaemonHealth).get(1)
        assert row.message == "Heartbeat OK"

    def test_heartbeat_calculates_uptime(self, monitor, db_session):
        """heartbeat() should calculate and store uptime_seconds."""
        monitor.start()
        monitor.heartbeat()

        row = db_session.query(DaemonHealth).get(1)
        # Uptime should be >= 0 (may be 0 if test runs fast)
        assert row.uptime_seconds >= 0


class TestRecordCounters:
    """Tests for record_event, record_decision, record_error."""

    def test_record_event_increments_counter(self, monitor, db_session):
        """record_event() should increment the events counter."""
        monitor.start()

        assert monitor._events_processed == 0
        monitor.record_event("SCHEDULED_CHECK")
        assert monitor._events_processed == 1
        monitor.record_event("MARKET_OPEN")
        assert monitor._events_processed == 2

    def test_record_decision_increments_counter(self, monitor, db_session):
        """record_decision() should increment the decisions counter."""
        monitor.start()

        assert monitor._decisions_made == 0
        monitor.record_decision()
        assert monitor._decisions_made == 1
        monitor.record_decision()
        monitor.record_decision()
        assert monitor._decisions_made == 3

    def test_record_error_increments_counter(self, monitor, db_session):
        """record_error() should increment the errors counter."""
        monitor.start()

        assert monitor._errors == 0
        monitor.record_error()
        assert monitor._errors == 1

    def test_counters_persist_to_db_on_heartbeat(self, monitor, db_session):
        """Counters should be written to DB when heartbeat() is called."""
        monitor.start()

        monitor.record_event("test")
        monitor.record_event("test")
        monitor.record_decision()
        monitor.record_error()

        # Trigger DB write via heartbeat
        monitor.heartbeat()

        row = db_session.query(DaemonHealth).get(1)
        assert row.events_processed_today == 2
        assert row.decisions_made_today == 1
        assert row.errors_today == 1


class TestPauseResume:
    """Tests for pause() and resume() methods."""

    def test_pause_updates_status(self, monitor, db_session):
        """pause() should set status to 'paused'."""
        monitor.start()
        monitor.pause()

        row = db_session.query(DaemonHealth).get(1)
        assert row.status == "paused"
        assert row.message == "Daemon paused by user"

    def test_resume_updates_status(self, monitor, db_session):
        """resume() should set status back to 'running'."""
        monitor.start()
        monitor.pause()
        monitor.resume()

        row = db_session.query(DaemonHealth).get(1)
        assert row.status == "running"
        assert row.message == "Daemon resumed"

    def test_pause_resume_cycle(self, monitor, db_session):
        """Multiple pause/resume cycles should work correctly."""
        monitor.start()

        for _ in range(3):
            monitor.pause()
            row = db_session.query(DaemonHealth).get(1)
            assert row.status == "paused"

            monitor.resume()
            db_session.refresh(row)
            assert row.status == "running"


class TestStop:
    """Tests for stop() method."""

    def test_stop_removes_pid_file(self, monitor):
        """stop() should remove the PID file."""
        monitor.start()
        assert monitor.pid_file.exists()

        monitor.stop()
        assert not monitor.pid_file.exists()

    def test_stop_updates_status(self, monitor, db_session):
        """stop() should set status to 'stopped' in DB."""
        monitor.start()
        monitor.stop()

        row = db_session.query(DaemonHealth).get(1)
        assert row.status == "stopped"
        assert row.message == "Daemon stopped gracefully"

    def test_stop_when_no_pid_file(self, monitor, db_session):
        """stop() should not raise if PID file doesn't exist."""
        monitor.start()
        # Remove PID file manually
        monitor.pid_file.unlink()

        # stop() should not raise
        monitor.stop()

        row = db_session.query(DaemonHealth).get(1)
        assert row.status == "stopped"


class TestIsDaemonRunning:
    """Tests for the static is_daemon_running() method."""

    def test_returns_pid_when_running(self, monitor):
        """is_daemon_running() should return PID when daemon is running."""
        monitor.start()

        pid = HealthMonitor.is_daemon_running(pid_file=str(monitor.pid_file))

        # The current process PID should be returned since we wrote it
        assert pid == os.getpid()

    def test_returns_none_when_no_pid_file(self, tmp_path):
        """is_daemon_running() should return None when PID file doesn't exist."""
        pid = HealthMonitor.is_daemon_running(
            pid_file=str(tmp_path / "nonexistent.pid")
        )
        assert pid is None

    def test_returns_none_for_stale_pid(self, tmp_path):
        """is_daemon_running() should return None if PID file has dead process."""
        pid_file = tmp_path / "stale.pid"
        # Write a PID that definitely doesn't exist (very large number)
        pid_file.write_text("9999999")

        pid = HealthMonitor.is_daemon_running(pid_file=str(pid_file))
        assert pid is None

    def test_returns_none_for_invalid_pid_content(self, tmp_path):
        """is_daemon_running() should return None if PID file has invalid content."""
        pid_file = tmp_path / "bad.pid"
        pid_file.write_text("not_a_number")

        pid = HealthMonitor.is_daemon_running(pid_file=str(pid_file))
        assert pid is None


class TestIsPaused:
    """Tests for is_paused() method."""

    def test_is_paused_returns_true_when_paused(self, monitor, db_session):
        """is_paused() should return True when status is 'paused' in DB."""
        monitor.start()
        monitor.pause()

        assert monitor.is_paused() is True

    def test_is_paused_returns_false_when_running(self, monitor, db_session):
        """is_paused() should return False when status is 'running'."""
        monitor.start()

        assert monitor.is_paused() is False

    def test_is_paused_returns_false_when_no_record(self, db_session, tmp_pid_dir):
        """is_paused() should return False when no health record exists."""
        pid_file = str(tmp_pid_dir / "fresh.pid")
        hm = HealthMonitor(db_session=db_session, pid_file=pid_file)

        # Don't call start() so no record exists
        assert hm.is_paused() is False

    def test_is_paused_reads_from_db(self, monitor, db_session):
        """is_paused() should read current state from DB (not cached)."""
        monitor.start()

        # Directly modify DB to simulate CLI pause
        row = db_session.query(DaemonHealth).get(1)
        row.status = "paused"
        db_session.commit()

        assert monitor.is_paused() is True


class TestShutdownRequested:
    """Tests for shutdown_requested property."""

    def test_initially_false(self, monitor):
        """shutdown_requested should be False initially."""
        assert monitor.shutdown_requested is False

    def test_set_by_signal_handler(self, monitor):
        """shutdown_requested should be True after _handle_signal is called."""
        import signal

        monitor._handle_signal(signal.SIGTERM, None)
        assert monitor.shutdown_requested is True


class TestGetStatus:
    """Tests for get_status() method."""

    def test_returns_status_dict(self, monitor, db_session):
        """get_status() should return a dictionary with health info."""
        monitor.start()
        monitor.heartbeat()

        status = monitor.get_status()

        assert status["status"] == "running"
        assert status["pid"] == os.getpid()
        assert "last_heartbeat" in status
        assert "uptime_seconds" in status
        assert "events_processed_today" in status
        assert "decisions_made_today" in status
        assert "errors_today" in status

    def test_returns_unknown_when_no_record(self, db_session, tmp_pid_dir):
        """get_status() returns 'unknown' status when no health record exists."""
        pid_file = str(tmp_pid_dir / "fresh.pid")
        hm = HealthMonitor(db_session=db_session, pid_file=pid_file)

        status = hm.get_status()
        assert status["status"] == "unknown"
