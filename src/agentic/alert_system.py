"""Alert system for the agentic daemon.

Routes alerts by level: always log, email for MEDIUM+, Slack for HIGH+.
Wraps existing Notifier service.
"""

from enum import IntEnum
from typing import Optional

from loguru import logger

from src.services.notifier import Notifier


class AlertLevel(IntEnum):
    """Alert severity levels."""

    LOW = 1       # Log only
    MEDIUM = 2    # Log + email
    HIGH = 3      # Log + email + webhook
    CRITICAL = 4  # Log + email + webhook (immediate)


# Mapping from daemon events to alert levels
EVENT_ALERT_LEVELS = {
    "heartbeat": AlertLevel.LOW,
    "scheduled_check": AlertLevel.LOW,
    "trade_executed": AlertLevel.MEDIUM,
    "position_closed": AlertLevel.MEDIUM,
    "tws_disconnected": AlertLevel.MEDIUM,
    "tws_reconnected": AlertLevel.MEDIUM,
    "anomaly_detected": AlertLevel.HIGH,
    "loss_streak": AlertLevel.HIGH,
    "margin_warning": AlertLevel.HIGH,
    "vix_spike": AlertLevel.HIGH,
    "human_review_required": AlertLevel.CRITICAL,
    "emergency_stop": AlertLevel.CRITICAL,
    "risk_limit_breach": AlertLevel.CRITICAL,
    "demotion": AlertLevel.HIGH,
    "promotion": AlertLevel.MEDIUM,
    "cost_cap_warning": AlertLevel.HIGH,
}


class AlertSystem:
    """Routes alerts based on severity using existing Notifier.

    Always logs. Sends email for MEDIUM+. Sends webhook for HIGH+.
    """

    def __init__(self):
        """Initialize alert system with existing Notifier."""
        self._notifier = Notifier()

    def alert(
        self,
        event: str,
        title: str,
        message: str,
        data: Optional[dict] = None,
        level_override: Optional[AlertLevel] = None,
    ) -> None:
        """Send an alert at the appropriate level.

        Args:
            event: Event type key (maps to default level)
            title: Alert title
            message: Alert details
            data: Optional structured data
            level_override: Override the default level for this event
        """
        level = level_override or EVENT_ALERT_LEVELS.get(event, AlertLevel.LOW)

        # Map to Notifier severity
        severity_map = {
            AlertLevel.LOW: "INFO",
            AlertLevel.MEDIUM: "WARNING",
            AlertLevel.HIGH: "CRITICAL",
            AlertLevel.CRITICAL: "EMERGENCY",
        }
        severity = severity_map.get(level, "INFO")

        self._notifier.notify(
            severity=severity,
            title=title,
            message=message,
            data=data,
        )
