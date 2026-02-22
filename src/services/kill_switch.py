"""Persistent kill switch for trading halt.

Provides a persistent, multi-interface trading halt mechanism that
survives process restarts. Uses a file-based approach for maximum
reliability (no database dependency).

Interfaces:
1. File flag — survives restarts (data/kill_switch.json)
2. In-memory flag — fast check, synced from file on startup
3. Signal handler — SIGTERM/SIGINT triggers halt

Check order (fast to slow):
1. In-memory flag (cached, no I/O)
2. File flag (on demand or periodic refresh)
"""

import json
import signal
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

# Default location for kill switch file
DEFAULT_HALT_FILE = Path("data/kill_switch.json")


class KillSwitch:
    """Persistent, multi-interface trading halt mechanism.

    The kill switch persists across restarts via a JSON file.
    On startup, it loads state from the file. The halt() method
    writes the file, and resume() removes it.

    Example:
        >>> ks = KillSwitch()
        >>> ks.halt("Manual override")
        >>> halted, reason = ks.is_halted()
        >>> print(f"Halted: {halted}, Reason: {reason}")
        Halted: True, Reason: Manual override
        >>> ks.resume()
    """

    def __init__(
        self,
        halt_file: Path | str | None = None,
        register_signals: bool = True,
    ):
        """Initialize kill switch.

        Args:
            halt_file: Path to halt state file. None uses default.
            register_signals: Whether to register signal handlers.
                Set to False in tests to avoid interfering with pytest.
        """
        self._halt_file = Path(halt_file) if halt_file else DEFAULT_HALT_FILE
        self._halted = False
        self._reason = ""
        self._halt_time: datetime | None = None

        # Ensure parent directory exists
        self._halt_file.parent.mkdir(parents=True, exist_ok=True)

        # Load state from file on startup
        self._load_from_file()

        # Register signal handlers
        if register_signals:
            self._register_signal_handlers()

        if self._halted:
            logger.warning(
                f"Kill switch loaded: HALTED — {self._reason} "
                f"(since {self._halt_time})"
            )
        else:
            logger.info("Kill switch loaded: trading allowed")

    def halt(self, reason: str) -> None:
        """Halt trading. Persists to file.

        Args:
            reason: Reason for halting
        """
        self._halted = True
        self._reason = reason
        self._halt_time = datetime.now()
        self._save_to_file()
        logger.critical(f"TRADING HALTED: {reason}")

    def resume(self) -> None:
        """Resume trading. Removes halt file."""
        previous_reason = self._reason
        self._halted = False
        self._reason = ""
        self._halt_time = None

        # Remove the halt file
        if self._halt_file.exists():
            self._halt_file.unlink()

        logger.info(f"Trading resumed (was halted: {previous_reason})")

    def is_halted(self) -> tuple[bool, str]:
        """Check halt status.

        Returns:
            Tuple of (halted: bool, reason: str)
        """
        return self._halted, self._reason

    def get_status(self) -> dict:
        """Get full kill switch status.

        Returns:
            Dict with halted, reason, halt_time, file_path
        """
        return {
            "halted": self._halted,
            "reason": self._reason,
            "halt_time": self._halt_time.isoformat() if self._halt_time else None,
            "file_path": str(self._halt_file),
        }

    def _load_from_file(self) -> None:
        """Load halt state from file on startup."""
        if not self._halt_file.exists():
            return

        try:
            with open(self._halt_file) as f:
                data = json.load(f)

            self._halted = data.get("halted", False)
            self._reason = data.get("reason", "")
            halt_time_str = data.get("halt_time")
            if halt_time_str:
                self._halt_time = datetime.fromisoformat(halt_time_str)

        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"Error reading kill switch file {self._halt_file}: {e}")
            # Corrupted file — treat as halted for safety
            self._halted = True
            self._reason = f"Corrupted kill switch file: {e}"

    def _save_to_file(self) -> None:
        """Save halt state to file."""
        try:
            data = {
                "halted": self._halted,
                "reason": self._reason,
                "halt_time": self._halt_time.isoformat() if self._halt_time else None,
            }
            with open(self._halt_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Error writing kill switch file: {e}")

    def _register_signal_handlers(self) -> None:
        """Register SIGTERM and SIGINT handlers for graceful halt."""
        try:
            signal.signal(signal.SIGTERM, self._signal_halt)
            # Only register SIGINT if not in interactive Python
            if not hasattr(sys, "ps1"):
                signal.signal(signal.SIGINT, self._signal_halt)
        except (OSError, ValueError):
            # Can't register signals in non-main thread
            logger.debug("Could not register signal handlers (not main thread)")

    def _signal_halt(self, signum, frame):
        """Signal handler that triggers halt."""
        sig_name = signal.Signals(signum).name
        self.halt(f"Signal {sig_name} received")
