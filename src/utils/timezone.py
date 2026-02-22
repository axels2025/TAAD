"""US market timezone utilities.

Centralizes all market-day date logic so every module uses the same
timezone source (America/New_York from market_calendar.TZ).

Usage:
    from src.utils.timezone import us_trading_date, us_eastern_now

    today = us_trading_date()          # date object in US Eastern
    now = us_eastern_now()             # datetime object in US Eastern
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

# Single source of truth — matches market_calendar.TZ
_ET = ZoneInfo("America/New_York")


def us_trading_date() -> date:
    """Current date in US Eastern time.

    Use this for all market-day logic: DTE calculations, expiration
    comparisons, daily counter resets, trading day checks.
    """
    return datetime.now(_ET).date()


def us_eastern_now() -> datetime:
    """Current datetime in US Eastern time.

    Use this when you need both date and time in market timezone
    (e.g., market hours checks, session timestamps).
    """
    return datetime.now(_ET)
