"""Active event detector for VIX spikes and position alerts.

Runs as an independent async background task (5-minute poll cycle).
Emits events to the EventBus when thresholds are breached:
- VIX spike: >15% change from session open
- Critical position alerts: approaching stop loss

Separate from the 15-minute SCHEDULED_CHECK — catches intraday
volatility events between Claude reasoning cycles.
"""

import asyncio
from typing import Optional

from loguru import logger

from src.agentic.event_bus import EventBus, EventType


class EventDetector:
    """Detect VIX spikes and critical position alerts.

    Polls IBKR every 5 minutes for VIX changes and position P&L alerts.
    Emits RISK_LIMIT_BREACH events when thresholds are crossed.
    """

    def __init__(
        self,
        event_bus: EventBus,
        position_monitor: Optional[object] = None,
        ibkr_client: Optional[object] = None,
        vix_spike_threshold_pct: float = 15.0,
    ):
        """Initialize event detector.

        Args:
            event_bus: Event bus for emitting events
            position_monitor: PositionMonitor instance (optional)
            ibkr_client: IBKR client for VIX data (optional)
            vix_spike_threshold_pct: VIX change threshold (default 15%)
        """
        self.event_bus = event_bus
        self.position_monitor = position_monitor
        self.ibkr_client = ibkr_client
        self.vix_spike_threshold_pct = vix_spike_threshold_pct

        # Session-relative VIX baseline (reset at MARKET_OPEN)
        self._session_open_vix: Optional[float] = None
        self._last_vix: Optional[float] = None
        self._vix_spike_emitted: bool = False

    async def run(self, poll_interval: int = 300) -> None:
        """Background loop — check VIX + positions every poll_interval seconds.

        Args:
            poll_interval: Seconds between checks (default 300 = 5 min)
        """
        logger.info(f"EventDetector started (poll every {poll_interval}s)")
        while True:
            try:
                if self.ibkr_client and self.ibkr_client.is_connected():
                    await self._check_vix()
                    await self._check_critical_alerts()
            except asyncio.CancelledError:
                logger.info("EventDetector cancelled")
                return
            except Exception as e:
                logger.error(f"EventDetector error: {e}")
            await asyncio.sleep(poll_interval)

    def reset_session(self) -> None:
        """Reset session VIX baseline at MARKET_OPEN."""
        self._session_open_vix = None
        self._last_vix = None
        self._vix_spike_emitted = False
        logger.info("EventDetector: session reset (VIX baseline cleared)")

    async def _check_vix(self) -> None:
        """Check VIX for intraday spikes relative to session open."""
        try:
            from src.services.market_conditions import MarketConditionMonitor

            monitor = MarketConditionMonitor(self.ibkr_client)
            conditions = await monitor.check_conditions()
            current_vix = conditions.vix

            if current_vix <= 0:
                return

            # Set session baseline on first check
            if self._session_open_vix is None:
                self._session_open_vix = current_vix
                logger.info(f"EventDetector: VIX session baseline set to {current_vix:.1f}")

            self._last_vix = current_vix

            # Check for spike relative to session open
            if self._session_open_vix > 0 and not self._vix_spike_emitted:
                change_pct = abs(current_vix - self._session_open_vix) / self._session_open_vix * 100
                if change_pct >= self.vix_spike_threshold_pct:
                    logger.warning(
                        f"VIX SPIKE DETECTED: {self._session_open_vix:.1f} -> "
                        f"{current_vix:.1f} ({change_pct:+.1f}%)"
                    )
                    self.event_bus.emit(
                        EventType.RISK_LIMIT_BREACH,
                        payload={
                            "breach_type": "vix_spike",
                            "vix_session_open": self._session_open_vix,
                            "vix_current": current_vix,
                            "change_pct": round(change_pct, 2),
                        },
                    )
                    self._vix_spike_emitted = True

        except Exception as e:
            logger.debug(f"VIX check failed: {e}")

    async def _check_critical_alerts(self) -> None:
        """Check positions for critical alerts (approaching stop loss)."""
        if not self.position_monitor:
            return

        try:
            alerts = self.position_monitor.check_alerts()
            critical_alerts = [a for a in alerts if a.severity == "critical"]

            for alert in critical_alerts:
                if alert.alert_type in ("stop_loss", "assignment_risk"):
                    logger.warning(f"Critical alert: {alert.message}")
                    self.event_bus.emit(
                        EventType.RISK_LIMIT_BREACH,
                        payload={
                            "breach_type": f"critical_{alert.alert_type}",
                            "position_id": alert.position_id,
                            "message": alert.message,
                            "current_value": alert.current_value,
                            "threshold": alert.threshold,
                        },
                    )

        except Exception as e:
            logger.debug(f"Position alert check failed: {e}")
