"""Active event detector for VIX spikes and position alerts.

Runs as an independent async background task (5-minute poll cycle).
Emits events to the EventBus when thresholds are breached:
- VIX spike: >15% *increase* from session open (drops are not risk events)
- Critical position alerts: approaching stop loss

Separate from the 15-minute SCHEDULED_CHECK — catches intraday
volatility events between Claude reasoning cycles.

VIX checks only run during market hours to avoid false spikes from
stale/default data when IBKR returns no quote outside trading hours.
"""

import asyncio
from typing import Optional

from loguru import logger

from src.agentic.event_bus import EventBus, EventType


# Default VIX returned by MarketConditionMonitor when quote fails.
# We must reject this value in spike calculations — it's synthetic.
_VIX_DEFAULT_FALLBACK = 20.0


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
        market_calendar: Optional[object] = None,
    ):
        """Initialize event detector.

        Args:
            event_bus: Event bus for emitting events
            position_monitor: PositionMonitor instance (optional)
            ibkr_client: IBKR client for VIX data (optional)
            vix_spike_threshold_pct: VIX change threshold (default 15%)
            market_calendar: MarketCalendar for market-hours gating (optional)
        """
        self.event_bus = event_bus
        self.position_monitor = position_monitor
        self.ibkr_client = ibkr_client
        self.vix_spike_threshold_pct = vix_spike_threshold_pct
        self.calendar = market_calendar

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
        """Check VIX for intraday spikes relative to session open.

        Guards against three sources of false positives:
        1. Market-hours gating — skip when market is closed (stale data).
        2. Default rejection — ignore the 20.0 fallback from _get_vix().
        3. Direction check — only VIX *increases* are risk events;
           drops mean volatility is easing and should not trigger alerts.
        """
        try:
            # Gate: only check VIX during market hours
            if self.calendar is not None:
                from datetime import datetime
                from zoneinfo import ZoneInfo

                now_et = datetime.now(ZoneInfo("America/New_York"))
                if not self.calendar.is_market_open(now_et):
                    return

            from src.services.market_conditions import MarketConditionMonitor

            monitor = MarketConditionMonitor(self.ibkr_client)
            conditions = await monitor.check_conditions()
            current_vix = conditions.vix

            if current_vix <= 0:
                return

            # Reject the hardcoded default — it's synthetic, not a real quote.
            # Using it for baseline or change detection causes false spikes
            # (e.g. baseline=31.5, default=20.0 → fake -36.5% "spike").
            if current_vix == _VIX_DEFAULT_FALLBACK:
                logger.debug(
                    "VIX check: got default fallback (20.0), skipping — "
                    "quote likely unavailable"
                )
                return

            # Set session baseline on first valid check
            if self._session_open_vix is None:
                self._session_open_vix = current_vix
                logger.info(f"EventDetector: VIX session baseline set to {current_vix:.1f}")

            self._last_vix = current_vix

            # Check for spike: only VIX INCREASES are risk events.
            # A VIX drop (current < baseline) means volatility is easing —
            # that's good news, not a risk breach.
            if self._session_open_vix > 0 and not self._vix_spike_emitted:
                if current_vix <= self._session_open_vix:
                    return  # VIX flat or falling — no risk event

                change_pct = (current_vix - self._session_open_vix) / self._session_open_vix * 100
                if change_pct >= self.vix_spike_threshold_pct:
                    logger.warning(
                        f"VIX SPIKE DETECTED: {self._session_open_vix:.1f} -> "
                        f"{current_vix:.1f} (+{change_pct:.1f}%)"
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
