"""US equity market calendar and hours awareness.

This module provides market session detection and holiday awareness for
accurate order timing and execution scheduling.
"""

from datetime import datetime, time, timedelta
from enum import Enum
from zoneinfo import ZoneInfo


class MarketSession(Enum):
    """US equity market session types."""

    PRE_MARKET = "pre_market"  # 4:00 AM - 9:30 AM ET
    REGULAR = "regular"  # 9:30 AM - 4:00 PM ET
    AFTER_HOURS = "after_hours"  # 4:00 PM - 8:00 PM ET
    CLOSED = "closed"  # 8:00 PM - 4:00 AM ET
    HOLIDAY = "holiday"  # Market closed all day
    WEEKEND = "weekend"  # Saturday/Sunday


class MarketCalendar:
    """US equity market hours and holiday awareness.

    Handles market session detection, holiday checking, and provides
    information about next market open times.

    All times are in US Eastern Time (America/New_York).
    """

    # US Eastern timezone
    TZ = ZoneInfo("America/New_York")

    # Market hours (in Eastern Time)
    PRE_MARKET_START = time(4, 0)  # 4:00 AM ET
    REGULAR_OPEN = time(9, 30)  # 9:30 AM ET
    REGULAR_CLOSE = time(16, 0)  # 4:00 PM ET
    AFTER_HOURS_END = time(20, 0)  # 8:00 PM ET

    # 2026 US market holidays (NYSE/NASDAQ)
    # Source: NYSE Holiday Schedule
    HOLIDAYS_2026 = {
        "2026-01-01",  # New Year's Day (Thursday)
        "2026-01-19",  # Martin Luther King Jr. Day (Monday)
        "2026-02-16",  # Presidents Day (Monday)
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day (Monday)
        "2026-07-03",  # Independence Day observed (Friday, July 4 is Saturday)
        "2026-09-07",  # Labor Day (Monday)
        "2026-11-26",  # Thanksgiving Day (Thursday)
        "2026-12-25",  # Christmas Day (Friday)
    }

    def __init__(self):
        """Initialize market calendar."""
        pass

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

        if self.PRE_MARKET_START <= current_time < self.REGULAR_OPEN:
            return MarketSession.PRE_MARKET
        elif self.REGULAR_OPEN <= current_time < self.REGULAR_CLOSE:
            return MarketSession.REGULAR
        elif self.REGULAR_CLOSE <= current_time < self.AFTER_HOURS_END:
            return MarketSession.AFTER_HOURS
        else:
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
