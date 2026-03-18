"""External process watchdog for the TAAD daemon.

Monitors daemon liveness via PID file and heartbeat freshness via
the daemon_health database table. Sends alerts through the Notifier
when the daemon is down, hung, or experiencing error spikes.

Restart behaviour:
- If the daemon was stopped intentionally (via dashboard STOP), a
  ``run/stop_requested`` flag file exists. The watchdog alerts but
  does NOT restart.
- If the daemon dies unexpectedly (crash, OOM, etc.) and no stop
  flag is present, the watchdog automatically restarts it.
"""

import json
import os
import signal
import sys
import time
from datetime import UTC, datetime

from src.utils.timezone import utc_now
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from src.services.notifier import Notifier


class DaemonWatchdog:
    """Monitors daemon health and sends alerts on failure.

    Checks three things each cycle:
    1. PID file liveness (is the process alive?)
    2. Heartbeat freshness (is the daemon responding?)
    3. Error count (is the daemon misbehaving?)

    Alert debouncing prevents notification spam: the same alert type
    is not re-sent until state changes or 3 consecutive failures
    accumulate (triggering a re-alert).

    Args:
        interval: Seconds between check cycles
        stale_warn: Heartbeat age (seconds) to trigger WARNING
        stale_crit: Heartbeat age (seconds) to trigger CRITICAL
        error_threshold: Errors today to trigger WARNING
        pid_file: Path to daemon PID file
        watchdog_pid_file: Path to watchdog's own PID file
        status_file: Path to write status JSON for dashboard
    """

    def __init__(
        self,
        interval: int = 60,
        stale_warn: int = 180,
        stale_crit: int = 300,
        error_threshold: int = 10,
        pid_file: str = "run/taad.pid",
        watchdog_pid_file: str = "run/watchdog.pid",
        status_file: str = "run/watchdog_status.json",
    ):
        self.interval = interval
        self.stale_warn = stale_warn
        self.stale_crit = stale_crit
        self.error_threshold = error_threshold
        self.pid_file = Path(pid_file)
        self.watchdog_pid_file = Path(watchdog_pid_file)
        self.status_file = Path(status_file)

        self._running = False
        self._notifier: "Notifier | None" = None  # Lazy-loaded

        # Alert debouncing state
        self._last_alert_type: str | None = None
        self._consecutive_failures = 0

    def run(self) -> None:
        """Main watchdog loop.

        Writes PID file, registers signal handlers, and loops
        _check_cycle() at the configured interval. Sleeps in
        1-second increments for responsive shutdown.
        """
        self._running = True

        # Write our own PID file
        self.watchdog_pid_file.parent.mkdir(parents=True, exist_ok=True)
        self.watchdog_pid_file.write_text(str(os.getpid()))

        self._register_signals()

        logger.info(
            f"Watchdog started (pid={os.getpid()}, "
            f"interval={self.interval}s, "
            f"stale_warn={self.stale_warn}s, "
            f"stale_crit={self.stale_crit}s)"
        )

        try:
            while self._running:
                try:
                    self._check_cycle()
                except Exception as e:
                    logger.error(f"Watchdog check cycle failed: {e}")
                    self._write_status({
                        "overall": "check_error",
                        "error": str(e),
                        "checked_at": _utcnow_iso(),
                    })

                # Sleep in 1-second increments for responsive shutdown
                for _ in range(self.interval):
                    if not self._running:
                        break
                    time.sleep(1)
        finally:
            self._cleanup()

    def _check_cycle(self) -> None:
        """Run all checks, build status dict, write JSON, handle failures."""
        pid = self._check_process()
        heartbeat_age = self._check_heartbeat()
        errors = self._check_errors()

        status = {
            "overall": "healthy",
            "daemon_pid": pid,
            "daemon_alive": pid is not None,
            "heartbeat_age_seconds": round(heartbeat_age, 1) if heartbeat_age is not None else None,
            "errors_today": errors,
            "checked_at": _utcnow_iso(),
            "watchdog_pid": os.getpid(),
        }

        # Determine overall status and trigger alerts
        if pid is None:
            status["overall"] = "daemon_down"
            self._handle_daemon_down()
        elif heartbeat_age is None:
            status["overall"] = "no_heartbeat"
            self._handle_no_heartbeat()
        elif heartbeat_age > self.stale_crit:
            status["overall"] = "hung_critical"
            self._handle_hung_daemon(heartbeat_age, critical=True)
        elif heartbeat_age > self.stale_warn:
            status["overall"] = "hung_warning"
            self._handle_hung_daemon(heartbeat_age, critical=False)
        else:
            # Healthy — reset alert state
            if self._last_alert_type is not None:
                logger.info("Daemon recovered — back to healthy")
            self._last_alert_type = None
            self._consecutive_failures = 0

        # Error spike (independent of process/heartbeat checks)
        if errors is not None and errors >= self.error_threshold:
            status["error_spike"] = True
            self._handle_error_spike(errors)

        self._write_status(status)

        level = "DEBUG" if status["overall"] == "healthy" else "WARNING"
        logger.log(level, f"Watchdog check: {status['overall']} (pid={pid}, hb_age={heartbeat_age})")

    def _check_process(self) -> int | None:
        """Check if the daemon process is alive via PID file.

        Returns:
            PID if alive, None if dead or no PID file.
        """
        if not self.pid_file.exists():
            return None

        try:
            pid = int(self.pid_file.read_text().strip())
            os.kill(pid, 0)
            return pid
        except (ValueError, ProcessLookupError, PermissionError):
            return None

    def _check_heartbeat(self) -> float | None:
        """Query daemon_health table for heartbeat age.

        Returns:
            Age in seconds since last heartbeat, or None if no record.
        """
        try:
            from src.data.database import get_db_session
            from src.data.models import DaemonHealth

            with get_db_session() as db:
                health = db.query(DaemonHealth).get(1)
                if not health or not health.last_heartbeat:
                    return None

                # Compute age — last_heartbeat is stored as naive UTC
                now = utc_now()
                hb = health.last_heartbeat
                if hb.tzinfo is not None:
                    hb = hb.replace(tzinfo=None)
                age = (now - hb).total_seconds()
                return float(age)
        except Exception as e:
            logger.error(f"Heartbeat check failed: {e}")
            return None

    def _check_errors(self) -> int | None:
        """Read errors_today from daemon_health.

        Returns:
            Error count, or None if unavailable.
        """
        try:
            from src.data.database import get_db_session
            from src.data.models import DaemonHealth

            with get_db_session() as db:
                health = db.query(DaemonHealth).get(1)
                if not health:
                    return None
                return health.errors_today or 0
        except Exception as e:
            logger.error(f"Error check failed: {e}")
            return None

    # ── Alert handlers ──────────────────────────────────────

    def _handle_daemon_down(self) -> None:
        """Handle daemon process not running.

        If ``run/stop_requested`` exists, the stop was intentional (dashboard
        STOP) — alert at INFO and do NOT restart.  Otherwise treat it as an
        unexpected crash and attempt automatic restart.
        """
        stop_flag = Path("run/stop_requested")
        if stop_flag.exists():
            self._send_alert(
                alert_type="daemon_stopped",
                severity="INFO",
                title="Daemon Stopped (Intentional)",
                message="Daemon was stopped via dashboard. Use START to restart.",
            )
            return

        # Unexpected crash — alert and restart
        self._send_alert(
            alert_type="daemon_down",
            severity="CRITICAL",
            title="Daemon Crashed — Restarting",
            message="Daemon process died unexpectedly. Attempting automatic restart.",
        )
        self._restart_daemon()

    def _restart_daemon(self) -> None:
        """Restart the daemon as a background subprocess."""
        import subprocess

        try:
            venv_bin = os.path.dirname(sys.executable)
            exe = os.path.join(venv_bin, "nakedtrader")
            log_file = os.path.join(os.getcwd(), "logs", "daemon_restart.log")
            os.makedirs(os.path.dirname(log_file), exist_ok=True)

            with open(log_file, "a") as log_f:
                subprocess.Popen(
                    [exe, "daemon", "start", "--fg"],
                    stdout=log_f,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            logger.info("Daemon restart initiated by watchdog")
        except Exception as e:
            logger.error(f"Watchdog failed to restart daemon: {e}")

    def _handle_no_heartbeat(self) -> None:
        """Alert when daemon has no heartbeat record."""
        self._send_alert(
            alert_type="no_heartbeat",
            severity="WARNING",
            title="No Daemon Heartbeat",
            message=(
                "The daemon_health table has no heartbeat record. "
                "The daemon may not have started properly."
            ),
        )

    def _handle_hung_daemon(self, age: float, critical: bool) -> None:
        """Alert when daemon heartbeat is stale."""
        severity = "CRITICAL" if critical else "WARNING"
        alert_type = "hung_critical" if critical else "hung_warning"
        self._send_alert(
            alert_type=alert_type,
            severity=severity,
            title="Daemon Heartbeat Stale",
            message=(
                f"Last heartbeat was {age:.0f} seconds ago "
                f"(threshold: {'CRITICAL' if critical else 'WARNING'}). "
                f"The daemon may be hung or deadlocked."
            ),
            data={"heartbeat_age_seconds": round(age)},
        )

    def _handle_error_spike(self, errors: int) -> None:
        """Alert when error count exceeds threshold."""
        self._send_alert(
            alert_type="error_spike",
            severity="WARNING",
            title="Daemon Error Spike",
            message=f"Daemon has {errors} errors today (threshold: {self.error_threshold}).",
            data={"errors_today": errors},
        )

    # ── Alert debouncing ────────────────────────────────────

    def _send_alert(
        self,
        alert_type: str,
        severity: str,
        title: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        """Send alert with debouncing.

        Same alert type is not re-sent until:
        - State changes to a different alert type, or
        - 3 consecutive failures of the same type (re-alert)
        """
        if alert_type == self._last_alert_type:
            self._consecutive_failures += 1
            if self._consecutive_failures < 3:
                return  # Suppress duplicate
            # Re-alert on 3rd consecutive failure, then reset counter
            self._consecutive_failures = 0
        else:
            self._last_alert_type = alert_type
            self._consecutive_failures = 1

        notifier = self._get_notifier()
        if notifier:
            try:
                notifier.notify(
                    severity=severity,
                    title=title,
                    message=message,
                    data=data,
                )
            except Exception as e:
                logger.error(f"Failed to send watchdog alert: {e}")

    def _get_notifier(self) -> "Notifier | None":
        """Lazy-load the Notifier instance."""
        if self._notifier is None:
            try:
                from src.services.notifier import Notifier
                self._notifier = Notifier()
            except Exception as e:
                logger.error(f"Failed to initialize Notifier: {e}")
        return self._notifier

    # ── Status file ─────────────────────────────────────────

    def _write_status(self, status: dict) -> None:
        """Write watchdog status JSON for dashboard consumption."""
        try:
            self.status_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.status_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(status, indent=2))
            tmp.rename(self.status_file)
        except Exception as e:
            logger.error(f"Failed to write watchdog status: {e}")

    # ── Signal handling & cleanup ───────────────────────────

    def _register_signals(self) -> None:
        """Register SIGTERM/SIGINT for graceful shutdown."""
        try:
            signal.signal(signal.SIGTERM, self._handle_signal)
            if not hasattr(sys, "ps1"):
                signal.signal(signal.SIGINT, self._handle_signal)
        except (OSError, ValueError):
            logger.debug("Could not register signal handlers")

    def _handle_signal(self, signum: int, frame: object) -> None:
        """Signal handler: request graceful shutdown."""
        sig_name = signal.Signals(signum).name
        logger.info(f"Watchdog received {sig_name}, shutting down")
        self._running = False

    def _cleanup(self) -> None:
        """Remove PID and status files on exit."""
        logger.info("Watchdog shutting down")
        try:
            if self.watchdog_pid_file.exists():
                self.watchdog_pid_file.unlink()
        except Exception:
            pass


def _utcnow_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")
