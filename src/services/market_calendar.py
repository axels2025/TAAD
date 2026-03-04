"""Market calendar and hours awareness with exchange profile support.

This module provides market session detection and holiday awareness for
accurate order timing and execution scheduling. Supports multiple exchanges
(US, ASX) via ExchangeProfile injection.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from enum import Enum
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from src.config.exchange_profile import ExchangeProfile


class MarketSession(Enum):
    """Market session types."""

    PRE_MARKET = "pre_market"  # 4:00 AM - 9:30 AM ET (US only)
    REGULAR = "regular"  # 9:30 AM - 4:00 PM ET / 10:00 AM - 4:00 PM AEST
    AFTER_HOURS = "after_hours"  # 4:00 PM - 8:00 PM ET (US only)
    CLOSED = "closed"  # Outside all sessions
    HOLIDAY = "holiday"  # Market closed all day
    WEEKEND = "weekend"  # Saturday/Sunday


class MarketCalendar:
    """Exchange-aware market hours and holiday awareness.

    Handles market session detection, holiday checking, and provides
    information about next market open times. Parameterised by an
    ExchangeProfile — defaults to the active profile (US unless
    EXCHANGE env var is set).

    Class-level attributes provide US defaults for backward compatibility
    (code that accesses MarketCalendar.TZ without an instance). Instance
    attributes override these with profile-specific values.
    """

    # Class-level US defaults for backward compatibility
    TZ = ZoneInfo("America/New_York")
    PRE_MARKET_START: time | None = time(4, 0)
    REGULAR_OPEN = time(9, 30)
    REGULAR_CLOSE = time(16, 0)
    AFTER_HOURS_END: time | None = time(20, 0)
    HOLIDAYS_2026: set[str] | frozenset[str] = frozenset({
        "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03",
        "2026-05-25", "2026-07-03", "2026-09-07", "2026-11-26",
        "2026-12-25",
    })

    def __init__(self, profile: ExchangeProfile | None = None):
        """Initialize market calendar.

        Args:
            profile: Exchange profile. Defaults to get_active_profile().
        """
        if profile is None:
            from src.config.exchange_profile import get_active_profile
            profile = get_active_profile()

        self._profile = profile

        # Instance attributes override class defaults
        self.TZ = profile.timezone
        self.PRE_MARKET_START = profile.pre_market_start
        self.REGULAR_OPEN = profile.regular_open
        self.REGULAR_CLOSE = profile.regular_close
        self.AFTER_HOURS_END = profile.after_hours_end
        self.HOLIDAYS_2026 = profile.holidays

    def get_current_session(self, dt: datetime | None = None) -> MarketSession:
        """Determine current market session.

        Args:
            dt: Datetime to check (defaults to now). Will be converted to ET.

        Returns:
            Current MarketSession
        """
        if dt is None:
            dt = datetime.now(self.TZ)
        else:
            # Convert to ET if not already
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.TZ)
            else:
                dt = dt.astimezone(self.TZ)

        # Check if weekend
        if dt.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return MarketSession.WEEKEND

        # Check if holiday
        date_str = dt.strftime("%Y-%m-%d")
        if date_str in self.HOLIDAYS_2026:
            return MarketSession.HOLIDAY

        # Check time-based sessions
        current_time = dt.time()

        # Pre-market (skip if exchange has no pre-market, e.g. ASX)
        if (
            self.PRE_MARKET_START is not None
            and self.PRE_MARKET_START <= current_time < self.REGULAR_OPEN
        ):
            return MarketSession.PRE_MARKET

        if self.REGULAR_OPEN <= current_time < self.REGULAR_CLOSE:
            return MarketSession.REGULAR

        # After-hours (skip if exchange has no after-hours, e.g. ASX)
        if (
            self.AFTER_HOURS_END is not None
            and self.REGULAR_CLOSE <= current_time < self.AFTER_HOURS_END
        ):
            return MarketSession.AFTER_HOURS

        return MarketSession.CLOSED

    def is_market_open(self, dt: datetime | None = None) -> bool:
        """Check if regular trading session is active.

        Args:
            dt: Datetime to check (defaults to now)

        Returns:
            True if regular session is active, False otherwise
        """
        return self.get_current_session(dt) == MarketSession.REGULAR

    def is_trading_day(self, dt: datetime | None = None) -> bool:
        """Check if given date is a trading day.

        Args:
            dt: Date to check (defaults to today)

        Returns:
            True if trading day (not weekend or holiday), False otherwise
        """
        if dt is None:
            dt = datetime.now(self.TZ)
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.TZ)
            else:
                dt = dt.astimezone(self.TZ)

        # Check weekend
        if dt.weekday() >= 5:
            return False

        # Check holiday
        date_str = dt.strftime("%Y-%m-%d")
        return date_str not in self.HOLIDAYS_2026

    def next_market_open(self, dt: datetime | None = None) -> datetime:
        """Get datetime of next regular session open.

        Args:
            dt: Starting datetime (defaults to now)

        Returns:
            Datetime of next market open (9:30 AM ET)
        """
        if dt is None:
            dt = datetime.now(self.TZ)
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.TZ)
            else:
                dt = dt.astimezone(self.TZ)

        # Start checking from current date
        check_date = dt.date()
        current_time = dt.time()

        # If before market open today and today is trading day, return today's open
        if current_time < self.REGULAR_OPEN and self.is_trading_day(
            datetime.combine(check_date, current_time, self.TZ)
        ):
            return datetime.combine(check_date, self.REGULAR_OPEN, self.TZ)

        # Otherwise, find next trading day
        check_date += timedelta(days=1)
        max_days = 10  # Safety limit

        for _ in range(max_days):
            check_dt = datetime.combine(check_date, self.REGULAR_OPEN, self.TZ)
            if self.is_trading_day(check_dt):
                return check_dt
            check_date += timedelta(days=1)

        # Fallback - should never reach here
        raise ValueError("Could not find next market open within 10 days")

    def time_until_open(self, dt: datetime | None = None) -> timedelta:
        """Get time remaining until market opens.

        Args:
            dt: Starting datetime (defaults to now)

        Returns:
            Timedelta until next market open
        """
        if dt is None:
            dt = datetime.now(self.TZ)
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.TZ)
            else:
                dt = dt.astimezone(self.TZ)

        next_open = self.next_market_open(dt)
        return next_open - dt

    def next_market_close(self, dt: datetime | None = None) -> datetime:
        """Get datetime of next regular session close.

        Args:
            dt: Starting datetime (defaults to now)

        Returns:
            Datetime of next market close (4:00 PM ET)
        """
        if dt is None:
            dt = datetime.now(self.TZ)
        else:
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=self.TZ)
            else:
                dt = dt.astimezone(self.TZ)

        check_date = dt.date()
        current_time = dt.time()

        # If before market close today and today is trading day, return today's close
        if current_time < self.REGULAR_CLOSE and self.is_trading_day(
            datetime.combine(check_date, current_time, self.TZ)
        ):
            return datetime.combine(check_date, self.REGULAR_CLOSE, self.TZ)

        # Otherwise, find next trading day's close
        check_date += timedelta(days=1)
        max_days = 10

        for _ in range(max_days):
            check_dt = datetime.combine(check_date, self.REGULAR_CLOSE, self.TZ)
            if self.is_trading_day(check_dt):
                return check_dt
            check_date += timedelta(days=1)

        raise ValueError("Could not find next market close within 10 days")

    def format_session_info(self, dt: datetime | None = None) -> dict[str, str]:
        """Get formatted information about current market status.

        Args:
            dt: Datetime to check (defaults to now)

        Returns:
            Dictionary with session info for display
        """
        if dt is None:
            dt = datetime.now(self.TZ)

        session = self.get_current_session(dt)

        info = {
            "session": session.value,
            "is_open": str(self.is_market_open(dt)),
            "current_time": dt.strftime("%Y-%m-%d %H:%M:%S %Z"),
        }

        if session in [
            MarketSession.CLOSED,
            MarketSession.WEEKEND,
            MarketSession.HOLIDAY,
        ]:
            next_open = self.next_market_open(dt)
            time_until = self.time_until_open(dt)

            info["next_open"] = next_open.strftime("%Y-%m-%d %H:%M %Z")
            info["time_until_open"] = self._format_timedelta(time_until)

        return info

    def _format_timedelta(self, td: timedelta) -> str:
        """Format timedelta as human-readable string.

        Args:
            td: Timedelta to format

        Returns:
            Formatted string like "2 hours, 30 minutes" or "1 day, 3 hours"
        """
        total_seconds = int(td.total_seconds())
        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        parts = []
        if days > 0:
            parts.append(f"{days} day{'s' if days != 1 else ''}")
        if hours > 0:
            parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
        # Always show minutes if no other parts, or if minutes > 0
        if minutes > 0 or (not parts and minutes == 0):
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")

        return ", ".join(parts)
