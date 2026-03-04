"""Exchange profile configuration for multi-market support.

Encapsulates all exchange-specific parameters (timezone, hours, holidays,
currency, IBKR routing, multiplier) into a single frozen dataclass.
The system operates in one exchange mode at a time, selected by config
or environment variable.

Usage:
    from src.config.exchange_profile import get_active_profile, get_multiplier

    profile = get_active_profile()          # reads EXCHANGE env var
    mult = get_multiplier("XJO", profile)   # 10 for XJO, 100 for BHP
"""

import os
from dataclasses import dataclass, field
from datetime import time
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class ExchangeProfile:
    """Immutable exchange configuration.

    All exchange-specific parameters are bundled here so that
    MarketCalendar, chain.py, order_manager, and the contract factory
    can be exchange-agnostic — they just read from the profile.

    Attributes:
        code: Exchange identifier ("US" or "ASX").
        timezone: Market timezone (e.g. America/New_York).
        regular_open: Regular session open time in local TZ.
        regular_close: Regular session close time in local TZ.
        pre_market_start: Pre-market start (None if no pre-market).
        after_hours_end: After-hours end (None if no after-hours).
        currency: ISO currency code ("USD" or "AUD").
        currency_symbol: Display symbol ("$" or "A$").
        ibkr_exchange: Default IBKR exchange for stocks ("SMART" or "ASX").
        ibkr_index_exchange: IBKR exchange for index contracts.
        default_multiplier: Standard option contract multiplier.
        index_symbols: Maps index symbol to its multiplier.
        equity_symbols: Known equity symbols for this exchange.
        trading_class_preferences: Preferred trading classes per symbol.
        holidays: Holiday dates as "YYYY-MM-DD" strings.
    """

    code: str
    timezone: ZoneInfo
    regular_open: time
    regular_close: time
    pre_market_start: time | None
    after_hours_end: time | None
    currency: str
    currency_symbol: str
    ibkr_exchange: str
    ibkr_index_exchange: str
    default_multiplier: int
    index_symbols: dict[str, int] = field(default_factory=dict)
    equity_symbols: frozenset[str] = field(default_factory=frozenset)
    trading_class_preferences: dict[str, list[str]] = field(default_factory=dict)
    holidays: frozenset[str] = field(default_factory=frozenset)


# ── US Profile (NYSE/CBOE) ──────────────────────────────────────────

US_PROFILE = ExchangeProfile(
    code="US",
    timezone=ZoneInfo("America/New_York"),
    regular_open=time(9, 30),
    regular_close=time(16, 0),
    pre_market_start=time(4, 0),
    after_hours_end=time(20, 0),
    currency="USD",
    currency_symbol="$",
    ibkr_exchange="SMART",
    ibkr_index_exchange="CBOE",
    default_multiplier=100,
    index_symbols={"SPX": 100, "XSP": 100, "VIX": 100},
    equity_symbols=frozenset({"SPY"}),
    trading_class_preferences={
        "SPX": ["SPXW", "SPX"],
        "XSP": ["XSPW", "XSP"],
        "SPY": ["SPY"],
    },
    # 2026 US market holidays (NYSE/NASDAQ)
    holidays=frozenset({
        "2026-01-01",  # New Year's Day
        "2026-01-19",  # Martin Luther King Jr. Day
        "2026-02-16",  # Presidents Day
        "2026-04-03",  # Good Friday
        "2026-05-25",  # Memorial Day
        "2026-07-03",  # Independence Day observed
        "2026-09-07",  # Labor Day
        "2026-11-26",  # Thanksgiving Day
        "2026-12-25",  # Christmas Day
    }),
)

# ── ASX Profile (Australian Stock Exchange) ──────────────────────────

ASX_PROFILE = ExchangeProfile(
    code="ASX",
    timezone=ZoneInfo("Australia/Sydney"),
    regular_open=time(10, 0),   # 10:00 AM AEST/AEDT
    regular_close=time(16, 0),  # 4:00 PM AEST/AEDT
    pre_market_start=None,      # No pre-market session
    after_hours_end=None,       # No after-hours session
    currency="AUD",
    currency_symbol="A$",
    ibkr_exchange="ASX",
    ibkr_index_exchange="ASX",
    default_multiplier=100,
    index_symbols={"XJO": 10},  # ASX 200 index options: multiplier = 10
    equity_symbols=frozenset({
        "BHP", "CBA", "CSL", "NAB", "WBC", "ANZ", "WES", "WOW",
        "MQG", "FMG", "RIO", "TLS", "WDS", "ALL", "GMG", "TCL",
        "REA", "COL", "SHL", "QBE", "STO", "ORG", "JHX", "CPU",
        "MIN", "TWE", "AGL", "BXB", "NCM", "NST", "S32", "IAG",
        "SUN", "MPL", "AMC", "ORI", "ASX", "XRO", "CAR", "IEL",
    }),
    trading_class_preferences={
        "XJO": ["XJO"],
    },
    # 2026 ASX market holidays
    holidays=frozenset({
        "2026-01-01",  # New Year's Day
        "2026-01-26",  # Australia Day
        "2026-04-03",  # Good Friday
        "2026-04-06",  # Easter Monday
        "2026-04-25",  # ANZAC Day (Saturday → no observed Monday)
        "2026-06-08",  # Queen's Birthday (NSW)
        "2026-12-25",  # Christmas Day
        "2026-12-28",  # Boxing Day (observed, 26 Dec is Saturday)
    }),
)

# ── Profile registry ─────────────────────────────────────────────────

PROFILES: dict[str, ExchangeProfile] = {
    "US": US_PROFILE,
    "ASX": ASX_PROFILE,
}


def get_active_profile() -> ExchangeProfile:
    """Return the active exchange profile from EXCHANGE env var.

    Defaults to "US" if not set.

    Returns:
        The matching ExchangeProfile.

    Raises:
        ValueError: If EXCHANGE env var is not a known profile.
    """
    code = os.getenv("EXCHANGE", "US").upper()
    if code not in PROFILES:
        raise ValueError(
            f"Unknown exchange '{code}'. Available: {list(PROFILES.keys())}"
        )
    return PROFILES[code]


def get_multiplier(symbol: str, profile: ExchangeProfile) -> int:
    """Get the option contract multiplier for a symbol.

    Index options may have non-standard multipliers (e.g. XJO = 10).
    Equity options use the profile's default_multiplier (typically 100).

    Args:
        symbol: Underlying symbol (e.g. "XJO", "BHP", "SPX").
        profile: Active exchange profile.

    Returns:
        Contract multiplier.
    """
    return profile.index_symbols.get(symbol, profile.default_multiplier)


def get_currency_symbol(currency: str) -> str:
    """Map ISO currency code to display symbol.

    Args:
        currency: ISO code like "USD" or "AUD".

    Returns:
        Display symbol like "$" or "A$".
    """
    return {"USD": "$", "AUD": "A$"}.get(currency, currency)
