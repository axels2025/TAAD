"""Market timezone utilities with exchange profile support.

Centralizes all market-day date logic so every module uses the same
timezone source. Supports both US and ASX markets via the active
ExchangeProfile.

Usage:
    from src.utils.timezone import trading_date, market_now

    today = trading_date()          # date in active exchange TZ
    now = market_now()              # datetime in active exchange TZ

Backward-compatible aliases:
    from src.utils.timezone import us_trading_date, us_eastern_now
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

# Single source of truth — matches market_calendar.TZ for US
_ET = ZoneInfo("America/New_York")


def trading_date() -> date:
    """Current date in the active exchange's timezone.

    Use this for all market-day logic: DTE calculations, expiration
    comparisons, daily counter resets, trading day checks.
    """
    from src.config.exchange_profile import get_active_profile

    return datetime.now(get_active_profile().timezone).date()


def market_now() -> datetime:
    """Current datetime in the active exchange's timezone.

    Use this when you need both date and time in market timezone
    (e.g., market hours checks, session timestamps).
    """
    from src.config.exchange_profile import get_active_profile

    return datetime.now(get_active_profile().timezone)


# Backward-compatible aliases — existing ~27 import sites continue working
us_trading_date = trading_date
us_eastern_now = market_now
