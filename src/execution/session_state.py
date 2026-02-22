"""Trade session state persistence for recovery after interruption.

This module handles saving and restoring trade session state to enable
recovery if the execution is interrupted (connection loss, crashes, etc.).
"""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


@dataclass
class SessionState:
    """Represents a trade session state.

    Attributes:
        session_id: Unique session identifier (timestamp)
        timestamp: When session was created
        phase: Current phase of execution
        opportunities: List of opportunity dictionaries
        approved: List of approved opportunity indices
        executed: List of successfully executed indices
        failed: List of failed execution indices
        metadata: Additional metadata (risk limits, config, etc.)
    """

    session_id: str
    timestamp: datetime
    phase: str
    opportunities: list[dict[str, Any]]
    approved: list[int]
    executed: list[int]
    failed: list[int]
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "phase": self.phase,
            "opportunities": self.opportunities,
            "approved": self.approved,
            "executed": self.executed,
            "failed": self.failed,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionState":
        """Create from dictionary (JSON deserialization)."""
        return cls(
            session_id=data["session_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            phase=data["phase"],
            opportunities=data["opportunities"],
            approved=data["approved"],
            executed=data["executed"],
            failed=data["failed"],
            metadata=data.get("metadata", {}),
        )


class TradeSessionState:
    """Persist session state for recovery after interruption.

    Saves to: data/sessions/session_{timestamp}.json
    Complete sessions are renamed to: session_{timestamp}.complete.json
    """

    STATE_DIR = Path("data/sessions")

    def __init__(self):
        """Initialize trade session state manager."""
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.state_file = self.STATE_DIR / f"session_{self.session_id}.json"
        self.STATE_DIR.mkdir(parents=True, exist_ok=True)
        self.current_state: SessionState | None = None

        logger.info(
            f"Initialized trade session: {self.session_id}",
            extra={"session_id": self.session_id, "state_file": str(self.state_file)},
        )

    def save_state(
        self,
        phase: str,
        opportunities: list[dict[str, Any]],
        approved: list[int],
        executed: list[int],
        failed: list[int],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save current session state for recovery.

        Args:
            phase: Current execution phase (e.g., "gathering", "approval", "execution")
            opportunities: List of opportunity dictionaries
            approved: List of approved opportunity indices (0-based)
            executed: List of successfully executed indices
            failed: List of failed execution indices
            metadata: Additional metadata to save
        """
        if metadata is None:
            metadata = {}

        self.current_state = SessionState(
            session_id=self.session_id,
            timestamp=datetime.now(),
            phase=phase,
            opportunities=opportunities,
            approved=approved,
            executed=executed,
            failed=failed,
            metadata=metadata,
        )

        try:
            self.state_file.write_text(
                json.dumps(self.current_state.to_dict(), indent=2)
            )
            logger.debug(
                f"Saved session state: phase={phase}",
                extra={
                    "session_id": self.session_id,
                    "phase": phase,
                    "opportunities_count": len(opportunities),
                    "approved_count": len(approved),
                    "executed_count": len(executed),
                    "failed_count": len(failed),
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to save session state: {e}",
                extra={"session_id": self.session_id, "error": str(e)},
            )
            raise

    def load_state(self) -> SessionState | None:
        """Load current session state from file.

        Returns:
            SessionState if file exists, None otherwise
        """
        if not self.state_file.exists():
            return None

        try:
            data = json.loads(self.state_file.read_text())
            self.current_state = SessionState.from_dict(data)
            logger.info(
                f"Loaded session state: phase={self.current_state.phase}",
                extra={
                    "session_id": self.session_id,
                    "phase": self.current_state.phase,
                },
            )
            return self.current_state
        except Exception as e:
            logger.error(
                f"Failed to load session state: {e}",
                extra={"session_id": self.session_id, "error": str(e)},
            )
            return None

    @classmethod
    def find_incomplete_sessions(cls) -> list[Path]:
        """Find sessions that didn't complete.

        Returns:
            list[Path]: Paths to incomplete session files
        """
        if not cls.STATE_DIR.exists():
            return []

        incomplete = []
        for file in cls.STATE_DIR.glob("session_*.json"):
            # Skip .complete.json files
            if ".complete.json" in file.name:
                continue
            incomplete.append(file)

        if incomplete:
            logger.info(
                f"Found {len(incomplete)} incomplete sessions",
                extra={"count": len(incomplete)},
            )

        return sorted(incomplete)

    @classmethod
    def resume_session(cls, session_file: Path) -> "TradeSessionState":
        """Load and resume an incomplete session.

        Args:
            session_file: Path to session JSON file

        Returns:
            TradeSessionState with loaded state

        Raises:
            ValueError: If session file is invalid
        """
        if not session_file.exists():
            raise ValueError(f"Session file not found: {session_file}")

        try:
            data = json.loads(session_file.read_text())
            session_id = data.get("session_id")

            if not session_id:
                raise ValueError("Invalid session file: missing session_id")

            # Create instance with existing session_id
            instance = cls.__new__(cls)
            instance.session_id = session_id
            instance.state_file = session_file
            instance.STATE_DIR = cls.STATE_DIR
            instance.current_state = SessionState.from_dict(data)

            logger.info(
                f"Resumed session: {session_id}",
                extra={
                    "session_id": session_id,
                    "phase": instance.current_state.phase,
                },
            )

            return instance

        except Exception as e:
            logger.error(
                f"Failed to resume session: {e}",
                extra={"session_file": str(session_file), "error": str(e)},
            )
            raise ValueError(f"Failed to resume session: {e}") from e

    def mark_complete(self) -> None:
        """Mark session as complete (rename file).

        Complete sessions are renamed to session_{id}.complete.json
        so they are not picked up by find_incomplete_sessions().
        """
        if not self.state_file.exists():
            logger.warning(
                "Cannot mark complete - state file not found",
                extra={"session_id": self.session_id},
            )
            return

        try:
            complete_file = self.state_file.with_suffix(".complete.json")
            self.state_file.rename(complete_file)
            self.state_file = complete_file

            logger.info(
                f"Marked session as complete: {self.session_id}",
                extra={"session_id": self.session_id},
            )
        except Exception as e:
            logger.error(
                f"Failed to mark session complete: {e}",
                extra={"session_id": self.session_id, "error": str(e)},
            )

    def get_pending_executions(self) -> list[int]:
        """Get list of approved but not yet executed opportunities.

        Returns:
            list[int]: Indices of opportunities approved but not executed or failed
        """
        if not self.current_state:
            return []

        executed_or_failed = set(
            self.current_state.executed + self.current_state.failed
        )
        pending = [
            idx for idx in self.current_state.approved if idx not in executed_or_failed
        ]

        return pending

    def update_execution_status(
        self, index: int, success: bool, error_message: str | None = None
    ) -> None:
        """Update execution status for an opportunity.

        Args:
            index: Opportunity index
            success: True if execution succeeded, False if failed
            error_message: Error message if failed
        """
        if not self.current_state:
            logger.warning("No current state to update")
            return

        if success:
            if index not in self.current_state.executed:
                self.current_state.executed.append(index)
                logger.info(
                    f"Marked opportunity {index} as executed",
                    extra={"session_id": self.session_id, "index": index},
                )
        else:
            if index not in self.current_state.failed:
                self.current_state.failed.append(index)
                if error_message:
                    self.current_state.metadata[f"error_{index}"] = error_message
                logger.info(
                    f"Marked opportunity {index} as failed: {error_message}",
                    extra={
                        "session_id": self.session_id,
                        "index": index,
                        "error": error_message,
                    },
                )

        # Save updated state
        if self.current_state:
            self.save_state(
                phase=self.current_state.phase,
                opportunities=self.current_state.opportunities,
                approved=self.current_state.approved,
                executed=self.current_state.executed,
                failed=self.current_state.failed,
                metadata=self.current_state.metadata,
            )
