"""Order timing and scheduling for market hours awareness.

This module handles order timing logic based on market sessions,
including queuing orders for market open and adjusting limit prices.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

from loguru import logger

from src.services.market_calendar import MarketCalendar, MarketSession


class OrderTimingMode(Enum):
    """Order timing modes."""

    IMMEDIATE = "immediate"  # Execute immediately (reject if market closed)
    MARKET_OPEN = "market_open"  # Queue until market opens
    MANUAL_TRIGGER = "manual_trigger"  # Wait for manual approval


@dataclass
class OrderTiming:
    """Order timing information.

    Attributes:
        mode: Timing mode (IMMEDIATE, MARKET_OPEN, MANUAL_TRIGGER)
        can_execute_now: Whether order can be executed immediately
        market_session: Current market session
        execute_at: When order should be executed (None = now)
        wait_duration: How long to wait (None = execute now)
        reason: Human-readable explanation
        tif: Time-in-force (DAY, GTC, GTD, etc.)
        gtd_date: Good-til-date for GTD orders
    """

    mode: OrderTimingMode
    can_execute_now: bool
    market_session: MarketSession
    execute_at: Optional[datetime]
    wait_duration: Optional[timedelta]
    reason: str
    tif: str = "DAY"
    gtd_date: Optional[datetime] = None


class OrderTimingHandler:
    """Handles order timing and scheduling based on market hours.

    This class determines when orders should be executed based on:
    - Current market session
    - User configuration (timing mode)
    - Order type requirements
    """

    def __init__(
        self,
        default_mode: OrderTimingMode = OrderTimingMode.MARKET_OPEN,
        default_tif: str = "DAY",
        gtd_days_ahead: int = 5,
    ):
        """Initialize order timing handler.

        Args:
            default_mode: Default timing mode
            default_tif: Default time-in-force (DAY, GTC, GTD)
            gtd_days_ahead: Days ahead for GTD orders
        """
        self.default_mode = default_mode
        self.default_tif = default_tif
        self.gtd_days_ahead = gtd_days_ahead
        self.market_calendar = MarketCalendar()

    def prepare_order(
        self,
        mode: Optional[OrderTimingMode] = None,
        dt: Optional[datetime] = None,
    ) -> OrderTiming:
        """Determine order timing based on current market session.

        Args:
            mode: Override default timing mode (optional)
            dt: Current datetime (defaults to now)

        Returns:
            OrderTiming with execution information
        """
        if mode is None:
            mode = self.default_mode

        if dt is None:
            dt = datetime.now(self.market_calendar.TZ)
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.market_calendar.TZ)
            else:
                dt = dt.astimezone(self.market_calendar.TZ)

        # Get current market session
        session = self.market_calendar.get_current_session(dt)

        # Determine timing based on mode and session
        if mode == OrderTimingMode.IMMEDIATE:
            return self._immediate_mode(session, dt)
        elif mode == OrderTimingMode.MARKET_OPEN:
            return self._market_open_mode(session, dt)
        elif mode == OrderTimingMode.MANUAL_TRIGGER:
            return self._manual_trigger_mode(session, dt)
        else:
            raise ValueError(f"Unknown timing mode: {mode}")

    def _immediate_mode(self, session: MarketSession, dt: datetime) -> OrderTiming:
        """Handle IMMEDIATE timing mode.

        Args:
            session: Current market session
            dt: Current datetime

        Returns:
            OrderTiming for immediate execution
        """
        if session == MarketSession.REGULAR:
            return OrderTiming(
                mode=OrderTimingMode.IMMEDIATE,
                can_execute_now=True,
                market_session=session,
                execute_at=None,
                wait_duration=None,
                reason="Market open - executing immediately",
                tif=self.default_tif,
            )
        else:
            # Market closed - cannot execute in IMMEDIATE mode
            next_open = self.market_calendar.next_market_open(dt)
            wait = next_open - dt

            return OrderTiming(
                mode=OrderTimingMode.IMMEDIATE,
                can_execute_now=False,
                market_session=session,
                execute_at=None,
                wait_duration=wait,
                reason=f"Market {session.value} - IMMEDIATE mode requires open market",
                tif=self.default_tif,
            )

    def _market_open_mode(self, session: MarketSession, dt: datetime) -> OrderTiming:
        """Handle MARKET_OPEN timing mode.

        Args:
            session: Current market session
            dt: Current datetime

        Returns:
            OrderTiming for market open execution
        """
        if session == MarketSession.REGULAR:
            # Market already open - execute now
            return OrderTiming(
                mode=OrderTimingMode.MARKET_OPEN,
                can_execute_now=True,
                market_session=session,
                execute_at=None,
                wait_duration=None,
                reason="Market open - executing now",
                tif=self.default_tif,
            )
        else:
            # Market closed - queue for next open
            next_open = self.market_calendar.next_market_open(dt)
            wait = next_open - dt

            # For GTD orders, set expiration date
            gtd_date = None
            if self.default_tif == "GTD":
                gtd_date = next_open + timedelta(days=self.gtd_days_ahead)

            return OrderTiming(
                mode=OrderTimingMode.MARKET_OPEN,
                can_execute_now=False,
                market_session=session,
                execute_at=next_open,
                wait_duration=wait,
                reason=f"Market {session.value} - queued for next open at {next_open.strftime('%Y-%m-%d %H:%M %Z')}",
                tif=self.default_tif if self.default_tif != "DAY" else "GTC",
                gtd_date=gtd_date,
            )

    def _manual_trigger_mode(self, session: MarketSession, dt: datetime) -> OrderTiming:
        """Handle MANUAL_TRIGGER timing mode.

        Args:
            session: Current market session
            dt: Current datetime

        Returns:
            OrderTiming for manual trigger
        """
        # Never execute immediately in manual mode
        return OrderTiming(
            mode=OrderTimingMode.MANUAL_TRIGGER,
            can_execute_now=False,
            market_session=session,
            execute_at=None,
            wait_duration=None,
            reason="Manual trigger mode - waiting for user approval",
            tif=self.default_tif,
        )

    def adjust_limit_price(
        self,
        base_price: float,
        session: MarketSession,
        is_buy: bool = True,
        buffer_pct: float = 0.02,
    ) -> float:
        """Adjust limit price based on market session.

        In pre-market or after-hours, adds a buffer to improve fill probability.

        Args:
            base_price: Base limit price
            session: Current market session
            is_buy: True for buy orders (pay more), False for sell (accept less)
            buffer_pct: Buffer percentage (default 2%)

        Returns:
            Adjusted limit price
        """
        # Only adjust for pre-market and after-hours
        if session not in [MarketSession.PRE_MARKET, MarketSession.AFTER_HOURS]:
            logger.debug(
                f"No price adjustment needed for {session.value} session",
                extra={"base_price": base_price, "session": session.value},
            )
            return base_price

        # Calculate buffer
        buffer = base_price * buffer_pct

        # Adjust based on buy/sell
        if is_buy:
            adjusted = base_price + buffer  # Pay more for buy orders
        else:
            adjusted = base_price - buffer  # Accept less for sell orders

        logger.info(
            f"Adjusted limit price for {session.value}: ${base_price:.2f} -> ${adjusted:.2f}",
            extra={
                "base_price": base_price,
                "adjusted_price": adjusted,
                "buffer_pct": buffer_pct,
                "is_buy": is_buy,
                "session": session.value,
            },
        )

        return round(adjusted, 2)

    def format_timing_info(self, timing: OrderTiming) -> dict[str, str]:
        """Format order timing information for display.

        Args:
            timing: OrderTiming result

        Returns:
            Dictionary with formatted information
        """
        info = {
            "mode": timing.mode.value,
            "can_execute_now": str(timing.can_execute_now),
            "market_session": timing.market_session.value,
            "reason": timing.reason,
            "tif": timing.tif,
        }

        if timing.execute_at:
            info["execute_at"] = timing.execute_at.strftime("%Y-%m-%d %H:%M %Z")

        if timing.wait_duration:
            total_seconds = int(timing.wait_duration.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            info["wait_duration"] = f"{hours}h {minutes}m"

        if timing.gtd_date:
            info["gtd_date"] = timing.gtd_date.strftime("%Y-%m-%d")

        return info
