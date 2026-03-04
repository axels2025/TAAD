"""Tests for the DaemonWatchdog process monitor.

Covers:
- PID file liveness check (_check_process)
- Heartbeat freshness check (_check_heartbeat)
- Alert debouncing and state reset
- Status JSON writing
- Stop-flag aware restart behaviour
- Plist XML generation
"""

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from src.data.database import close_database, get_session, init_database
from src.data.models import DaemonHealth
from src.services.watchdog import DaemonWatchdog

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_database():
    """Create an in-memory SQLite database for testing."""
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database) -> Session:
    """Get a database session from the in-memory database."""
    session = get_session()
    yield session
    session.close()


@pytest.fixture
def tmp_run_dir(tmp_path):
    """Create a temporary run directory for PID/status files."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    return run_dir


@pytest.fixture
def watchdog(tmp_run_dir):
    """Create a DaemonWatchdog with temporary file paths."""
    return DaemonWatchdog(
        interval=5,
        stale_warn=180,
        stale_crit=300,
        pid_file=str(tmp_run_dir / "taad.pid"),
        watchdog_pid_file=str(tmp_run_dir / "watchdog.pid"),
        status_file=str(tmp_run_dir / "watchdog_status.json"),
    )


# ---------------------------------------------------------------------------
# _check_process tests
# ---------------------------------------------------------------------------


class TestCheckProcess:
    """Test PID file liveness checks."""

    def test_alive_pid_returns_int(self, watchdog):
        """When PID file exists and process is alive, returns PID."""
        pid = os.getpid()
        watchdog.pid_file.write_text(str(pid))

        result = watchdog._check_process()
        assert result == pid

    def test_dead_pid_returns_none(self, watchdog):
        """When PID file has a non-existent PID, returns None."""
        # PID 99999999 should not exist
        watchdog.pid_file.write_text("99999999")

        result = watchdog._check_process()
        assert result is None

    def test_no_pid_file_returns_none(self, watchdog):
        """When no PID file exists, returns None."""
        result = watchdog._check_process()
        assert result is None

    def test_invalid_pid_file_returns_none(self, watchdog):
        """When PID file contains non-integer, returns None."""
        watchdog.pid_file.write_text("not-a-pid")

        result = watchdog._check_process()
        assert result is None


# ---------------------------------------------------------------------------
# _check_heartbeat tests
# ---------------------------------------------------------------------------


class TestCheckHeartbeat:
    """Test heartbeat freshness queries."""

    def test_fresh_heartbeat(self, watchdog, db_session):
        """Fresh heartbeat (30s ago) returns approximately 30."""
        now = datetime.now(UTC)
        health = DaemonHealth(
            id=1,
            status="running",
            last_heartbeat=now - timedelta(seconds=30),
        )
        db_session.add(health)
        db_session.commit()

        age = watchdog._check_heartbeat()
        assert age is not None
        assert 28 <= age <= 35  # Allow for test execution time

    def test_stale_heartbeat(self, watchdog, db_session):
        """Stale heartbeat (4 min ago) returns approximately 240."""
        now = datetime.now(UTC)
        health = DaemonHealth(
            id=1,
            status="running",
            last_heartbeat=now - timedelta(minutes=4),
        )
        db_session.add(health)
        db_session.commit()

        age = watchdog._check_heartbeat()
        assert age is not None
        assert 235 <= age <= 250

    def test_no_health_record(self, watchdog, temp_database):
        """No DaemonHealth record returns None."""
        age = watchdog._check_heartbeat()
        assert age is None

    def test_no_heartbeat_value(self, watchdog, db_session):
        """DaemonHealth exists but last_heartbeat is None."""
        health = DaemonHealth(id=1, status="stopped", last_heartbeat=None)
        db_session.add(health)
        db_session.commit()

        age = watchdog._check_heartbeat()
        assert age is None


# ---------------------------------------------------------------------------
# Alert debouncing tests
# ---------------------------------------------------------------------------


class TestAlertDebouncing:
    """Test that duplicate alerts are suppressed."""

    @patch.object(DaemonWatchdog, "_restart_daemon")
    def test_first_alert_fires(self, mock_restart, watchdog):
        """First alert of a type always fires."""
        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        watchdog._handle_daemon_down()

        mock_notifier.notify.assert_called_once()
        assert "Crashed" in mock_notifier.notify.call_args.kwargs["title"]
        mock_restart.assert_called_once()

    @patch.object(DaemonWatchdog, "_restart_daemon")
    def test_second_identical_alert_suppressed(self, mock_restart, watchdog):
        """Second consecutive identical alert is suppressed."""
        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        watchdog._handle_daemon_down()
        watchdog._handle_daemon_down()

        assert mock_notifier.notify.call_count == 1

    @patch.object(DaemonWatchdog, "_restart_daemon")
    def test_third_identical_alert_fires(self, mock_restart, watchdog):
        """Third consecutive identical alert re-fires (re-alert)."""
        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        watchdog._handle_daemon_down()
        watchdog._handle_daemon_down()
        watchdog._handle_daemon_down()

        assert mock_notifier.notify.call_count == 2

    @patch.object(DaemonWatchdog, "_restart_daemon")
    def test_different_alert_type_fires(self, mock_restart, watchdog):
        """Switching to a different alert type fires immediately."""
        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        watchdog._handle_daemon_down()
        watchdog._handle_hung_daemon(age=200.0, critical=False)

        assert mock_notifier.notify.call_count == 2

    @patch.object(DaemonWatchdog, "_restart_daemon")
    def test_state_reset_on_recovery(self, mock_restart, watchdog, tmp_run_dir):
        """After recovery (healthy), new failure triggers alert again."""
        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        # First failure
        watchdog._handle_daemon_down()
        assert mock_notifier.notify.call_count == 1

        # Recovery
        watchdog._last_alert_type = None
        watchdog._consecutive_failures = 0

        # Second failure (should fire since state was reset)
        watchdog._handle_daemon_down()
        assert mock_notifier.notify.call_count == 2


# ---------------------------------------------------------------------------
# _write_status tests
# ---------------------------------------------------------------------------


class TestWriteStatus:
    """Test status JSON file writing."""

    def test_writes_json_with_correct_keys(self, watchdog):
        """Status file contains expected keys."""
        status = {
            "overall": "healthy",
            "daemon_pid": 1234,
            "daemon_alive": True,
            "heartbeat_age_seconds": 30.5,
            "errors_today": 0,
            "checked_at": "2026-03-01T12:00:00Z",
            "watchdog_pid": os.getpid(),
        }

        watchdog._write_status(status)

        assert watchdog.status_file.exists()
        data = json.loads(watchdog.status_file.read_text())
        assert data["overall"] == "healthy"
        assert data["daemon_pid"] == 1234
        assert data["daemon_alive"] is True
        assert data["heartbeat_age_seconds"] == 30.5
        assert data["watchdog_pid"] == os.getpid()

    def test_overwrites_previous_status(self, watchdog):
        """New status overwrites the old one."""
        watchdog._write_status({"overall": "healthy"})
        watchdog._write_status({"overall": "daemon_down"})

        data = json.loads(watchdog.status_file.read_text())
        assert data["overall"] == "daemon_down"


# ---------------------------------------------------------------------------
# _check_cycle integration tests
# ---------------------------------------------------------------------------


class TestCheckCycle:
    """Test the full check cycle integrating all checks."""

    def test_healthy_cycle(self, watchdog, db_session):
        """Healthy daemon produces healthy status."""
        # Set up alive PID
        pid = os.getpid()
        watchdog.pid_file.write_text(str(pid))

        # Set up fresh heartbeat
        now = datetime.now(UTC)
        health = DaemonHealth(
            id=1,
            status="running",
            last_heartbeat=now - timedelta(seconds=10),
            errors_today=0,
        )
        db_session.add(health)
        db_session.commit()

        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        watchdog._check_cycle()

        data = json.loads(watchdog.status_file.read_text())
        assert data["overall"] == "healthy"
        assert data["daemon_alive"] is True
        mock_notifier.notify.assert_not_called()

    @patch.object(DaemonWatchdog, "_restart_daemon")
    def test_daemon_down_cycle(self, mock_restart, watchdog, temp_database):
        """Dead daemon produces daemon_down status and alert."""
        # No PID file = daemon not running
        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        watchdog._check_cycle()

        data = json.loads(watchdog.status_file.read_text())
        assert data["overall"] == "daemon_down"
        mock_notifier.notify.assert_called_once()
        mock_restart.assert_called_once()


# ---------------------------------------------------------------------------
# Stop-flag aware restart tests
# ---------------------------------------------------------------------------


class TestStopFlagRestart:
    """Test intentional stop vs crash restart behaviour."""

    def test_intentional_stop_no_restart(self, watchdog, monkeypatch, tmp_path):
        """When stop flag exists, alert but don't restart."""
        # Create stop flag in the CWD that watchdog checks
        monkeypatch.chdir(tmp_path)
        (tmp_path / "run").mkdir(exist_ok=True)
        (tmp_path / "run" / "stop_requested").touch()

        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        with patch.object(DaemonWatchdog, "_restart_daemon") as mock_restart:
            watchdog._handle_daemon_down()

            # Should alert with INFO severity, not CRITICAL
            mock_notifier.notify.assert_called_once()
            call_kwargs = mock_notifier.notify.call_args.kwargs
            assert call_kwargs["severity"] == "INFO"
            assert "Intentional" in call_kwargs["title"]

            # Should NOT restart
            mock_restart.assert_not_called()

    def test_crash_triggers_restart(self, watchdog, monkeypatch, tmp_path):
        """When no stop flag, treat as crash and restart."""
        # No stop flag present
        monkeypatch.chdir(tmp_path)

        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        with patch.object(DaemonWatchdog, "_restart_daemon") as mock_restart:
            watchdog._handle_daemon_down()

            # Should alert with CRITICAL severity
            mock_notifier.notify.assert_called_once()
            call_kwargs = mock_notifier.notify.call_args.kwargs
            assert call_kwargs["severity"] == "CRITICAL"
            assert "Crashed" in call_kwargs["title"]

            # Should restart
            mock_restart.assert_called_once()

    def test_intentional_stop_uses_different_alert_type(self, watchdog, monkeypatch, tmp_path):
        """Intentional stop uses 'daemon_stopped' alert type, not 'daemon_down'."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "run").mkdir(exist_ok=True)
        (tmp_path / "run" / "stop_requested").touch()

        mock_notifier = MagicMock()
        watchdog._notifier = mock_notifier

        with patch.object(DaemonWatchdog, "_restart_daemon"):
            watchdog._handle_daemon_down()

        # The internal alert type should be daemon_stopped
        assert watchdog._last_alert_type == "daemon_stopped"


# ---------------------------------------------------------------------------
# _generate_plist tests
# ---------------------------------------------------------------------------


class TestGeneratePlist:
    """Test launchd plist XML generation."""

    def test_plist_contains_label(self):
        """Generated plist includes the service label."""
        from src.cli.commands.daemon_commands import _generate_plist

        xml = _generate_plist(
            label="com.taad.daemon",
            program_args=["/usr/bin/python", "-m", "src.cli.main", "daemon", "start", "--fg"],
            working_directory="/opt/trading",
            log_prefix="daemon",
            keep_alive_on_crash=True,
        )

        assert "<string>com.taad.daemon</string>" in xml
        assert "<string>/usr/bin/python</string>" in xml
        assert "<string>/opt/trading</string>" in xml

    def test_keep_alive_on_crash(self):
        """KeepAlive with SuccessfulExit=false for crash-only restart."""
        from src.cli.commands.daemon_commands import _generate_plist

        xml = _generate_plist(
            label="com.taad.daemon",
            program_args=["/usr/bin/python"],
            working_directory="/opt",
            log_prefix="daemon",
            keep_alive_on_crash=True,
        )

        assert "<key>SuccessfulExit</key>" in xml
        assert "<false/>" in xml

    def test_keep_alive_always(self):
        """KeepAlive=true for always-on watchdog."""
        from src.cli.commands.daemon_commands import _generate_plist

        xml = _generate_plist(
            label="com.taad.watchdog",
            program_args=["/usr/bin/python"],
            working_directory="/opt",
            log_prefix="watchdog",
            keep_alive_always=True,
        )

        assert "<key>KeepAlive</key>" in xml
        assert "<true/>" in xml
        assert "SuccessfulExit" not in xml

    def test_throttle_interval(self):
        """ThrottleInterval is included with correct value."""
        from src.cli.commands.daemon_commands import _generate_plist

        xml = _generate_plist(
            label="com.taad.test",
            program_args=["/usr/bin/python"],
            working_directory="/opt",
            log_prefix="test",
            throttle_interval=45,
        )

        assert "<key>ThrottleInterval</key>" in xml
        assert "<integer>45</integer>" in xml

    def test_log_paths(self):
        """Log paths point to logs/ directory with correct prefix."""
        from src.cli.commands.daemon_commands import _generate_plist

        xml = _generate_plist(
            label="com.taad.test",
            program_args=["/usr/bin/python"],
            working_directory="/opt/trading",
            log_prefix="myprefix",
        )

        assert "/opt/trading/logs/myprefix-launchd.log" in xml
