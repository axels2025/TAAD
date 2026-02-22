"""Historical market context reconstruction for trade enrichment.

Reuses pure functions from src/services/market_context.py but operates on
historical data from yfinance instead of live IBKR data.
"""

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from src.data.sector_map import get_sector
from src.services.market_context import MarketContextService


# Sector to ETF mapping (reuse from MarketContextService)
SECTOR_ETFS = MarketContextService.SECTOR_ETFS

# FOMC meeting dates for ALL years (2019-2026)
# Second day of two-day meetings
FOMC_DATES: dict[int, list[date]] = {
    2019: [
        date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1), date(2019, 6, 19),
        date(2019, 7, 31), date(2019, 9, 18), date(2019, 10, 30), date(2019, 12, 11),
    ],
    2020: [
        date(2020, 1, 29), date(2020, 3, 3), date(2020, 3, 15), date(2020, 4, 29),
        date(2020, 6, 10), date(2020, 7, 29), date(2020, 9, 16), date(2020, 11, 5),
        date(2020, 12, 16),
    ],
    2021: [
        date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
        date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    ],
    2022: [
        date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15),
        date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    ],
    2023: [
        date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14),
        date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    ],
    2024: [
        date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
        date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    ],
    2025: [
        date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
        date(2025, 7, 30), date(2025, 9, 17), date(2025, 11, 5), date(2025, 12, 17),
    ],
    2026: [
        date(2026, 1, 28), date(2026, 3, 18), date(2026, 5, 6), date(2026, 6, 17),
        date(2026, 7, 29), date(2026, 9, 16), date(2026, 11, 4), date(2026, 12, 16),
    ],
}


def classify_vol_regime(vix: float) -> str:
    """Classify volatility regime based on VIX.

    Thresholds: <15 low, 15-20 normal, 20-25 elevated, >25 extreme.
    Mirrors MarketContextService._classify_vol_regime.
    """
    if vix < 15:
        return "low"
    elif vix < 20:
        return "normal"
    elif vix < 25:
        return "elevated"
    else:
        return "extreme"


def classify_market_regime(spy_change_pct: Optional[float], vix: float) -> str:
    """Classify market regime.

    Mirrors MarketContextService._classify_market_regime.
    """
    if vix > 25:
        return "volatile"
    if spy_change_pct is None:
        return "neutral"
    if spy_change_pct > 0.01:
        return "bullish"
    elif spy_change_pct < -0.01:
        return "bearish"
    else:
        return "neutral"


def is_opex_week(on_date: date) -> bool:
    """Check if date is in options expiration week (3rd Friday).

    Mirrors MarketContextService._is_opex_week.
    """
    cal = calendar.Calendar(firstweekday=calendar.SUNDAY)
    fridays = [
        d
        for d in cal.itermonthdates(on_date.year, on_date.month)
        if d.weekday() == 4 and d.month == on_date.month
    ]
    if len(fridays) < 3:
        return False

    third_friday = fridays[2]
    week_start = third_friday - timedelta(days=third_friday.weekday())
    week_end = week_start + timedelta(days=4)
    return week_start <= on_date <= week_end


def days_to_next_fomc(on_date: date) -> int:
    """Calculate days until next FOMC meeting from a historical date.

    Searches the FOMC_DATES dict for the next meeting after on_date.

    Args:
        on_date: Historical date

    Returns:
        Days to next FOMC, or 999 if none found
    """
    year = on_date.year

    # Check current year and next year
    for check_year in [year, year + 1]:
        dates = FOMC_DATES.get(check_year, [])
        for fomc_date in dates:
            if fomc_date >= on_date:
                return (fomc_date - on_date).days

    return 999


def get_historical_earnings_date(
    symbol: str, trade_date: date
) -> Optional[date]:
    """Get the next earnings date relative to a historical trade date.

    Uses yfinance to fetch historical earnings dates and finds the nearest
    upcoming one from the trade date perspective.

    Args:
        symbol: Stock symbol
        trade_date: Historical trade entry date

    Returns:
        Next earnings date after trade_date, or None if not found
    """
    try:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        earnings = ticker.earnings_dates

        if earnings is None or earnings.empty:
            return None

        # earnings_dates index is a DatetimeIndex with earnings dates
        earnings_dates = sorted([
            d.date() if hasattr(d, "date") else d
            for d in earnings.index
        ])

        # Find the first earnings date on or after trade_date
        for ed in earnings_dates:
            if ed >= trade_date:
                return ed

        return None

    except Exception as e:
        logger.debug(f"Failed to get earnings for {symbol}: {e}")
        return None


def get_sector_for_symbol(symbol: str) -> Optional[str]:
    """Get sector classification for a symbol.

    Uses the static sector map. Falls back to None for unknown symbols.

    Args:
        symbol: Stock symbol

    Returns:
        Sector name or None
    """
    sector = get_sector(symbol)
    return sector if sector != "Unknown" else None


def get_sector_etf(sector: Optional[str]) -> Optional[str]:
    """Get the sector ETF symbol for a given sector.

    Args:
        sector: Sector name (e.g. "Technology")

    Returns:
        ETF symbol (e.g. "XLK") or None
    """
    if not sector:
        return None
    return SECTOR_ETFS.get(sector)


@dataclass
class HistoricalMarketContext:
    """Complete market context reconstructed for a historical date."""

    # Indices
    spy_price: Optional[float] = None
    spy_change_pct: Optional[float] = None
    qqq_price: Optional[float] = None
    qqq_change_pct: Optional[float] = None
    iwm_price: Optional[float] = None
    iwm_change_pct: Optional[float] = None
    vix: Optional[float] = None
    vix_change_pct: Optional[float] = None

    # Sector
    sector: Optional[str] = None
    sector_etf: Optional[str] = None
    sector_change_1d: Optional[float] = None
    sector_change_5d: Optional[float] = None

    # Regime
    vol_regime: Optional[str] = None
    market_regime: Optional[str] = None

    # Calendar
    day_of_week: Optional[int] = None
    is_opex_week: Optional[bool] = None
    days_to_fomc: Optional[int] = None

    # Earnings
    earnings_date: Optional[date] = None
    days_to_earnings: Optional[int] = None
    earnings_in_dte: Optional[bool] = None
    earnings_timing: Optional[str] = None


def build_historical_context(
    symbol: str,
    trade_date: date,
    expiration: date,
    provider,
) -> HistoricalMarketContext:
    """Build complete historical market context for a trade.

    Args:
        symbol: Stock symbol
        trade_date: Trade entry date
        expiration: Option expiration date
        provider: HistoricalDataProvider instance

    Returns:
        HistoricalMarketContext with all available fields
    """
    ctx = HistoricalMarketContext()

    # --- Indices ---
    try:
        spy_bar = provider.get_index_bar("SPY", trade_date)
        if spy_bar:
            ctx.spy_price = spy_bar.close

            # Get previous day for change calculation
            spy_prev = provider.get_index_bar("SPY", trade_date - timedelta(days=1))
            if spy_prev and spy_prev.close > 0:
                ctx.spy_change_pct = round(
                    (spy_bar.close - spy_prev.close) / spy_prev.close, 6
                )
    except Exception as e:
        logger.debug(f"SPY data failed: {e}")

    try:
        qqq_bar = provider.get_index_bar("QQQ", trade_date)
        if qqq_bar:
            ctx.qqq_price = qqq_bar.close
            qqq_prev = provider.get_index_bar("QQQ", trade_date - timedelta(days=1))
            if qqq_prev and qqq_prev.close > 0:
                ctx.qqq_change_pct = round(
                    (qqq_bar.close - qqq_prev.close) / qqq_prev.close, 6
                )
    except Exception as e:
        logger.debug(f"QQQ data failed: {e}")

    try:
        iwm_bar = provider.get_index_bar("IWM", trade_date)
        if iwm_bar:
            ctx.iwm_price = iwm_bar.close
            iwm_prev = provider.get_index_bar("IWM", trade_date - timedelta(days=1))
            if iwm_prev and iwm_prev.close > 0:
                ctx.iwm_change_pct = round(
                    (iwm_bar.close - iwm_prev.close) / iwm_prev.close, 6
                )
    except Exception as e:
        logger.debug(f"IWM data failed: {e}")

    # --- VIX ---
    try:
        ctx.vix = provider.get_vix_close(trade_date)
        if ctx.vix:
            vix_prev = provider.get_vix_close(trade_date - timedelta(days=1))
            if vix_prev and vix_prev > 0:
                ctx.vix_change_pct = round((ctx.vix - vix_prev) / vix_prev, 6)
    except Exception as e:
        logger.debug(f"VIX data failed: {e}")

    # --- Sector ---
    try:
        ctx.sector = get_sector_for_symbol(symbol)
        ctx.sector_etf = get_sector_etf(ctx.sector)

        if ctx.sector_etf:
            sector_bars = provider.get_sector_etf_bars(ctx.sector_etf, trade_date, 10)
            if sector_bars is not None and len(sector_bars) >= 2:
                closes = sector_bars["Close"].values
                # 1-day change
                if len(closes) >= 2:
                    ctx.sector_change_1d = round(
                        (closes[-1] - closes[-2]) / closes[-2], 6
                    )
                # 5-day change
                if len(closes) >= 6:
                    ctx.sector_change_5d = round(
                        (closes[-1] - closes[-6]) / closes[-6], 6
                    )
    except Exception as e:
        logger.debug(f"Sector data failed for {symbol}: {e}")

    # --- Regime ---
    if ctx.vix is not None:
        ctx.vol_regime = classify_vol_regime(ctx.vix)
        ctx.market_regime = classify_market_regime(ctx.spy_change_pct, ctx.vix)

    # --- Calendar ---
    ctx.day_of_week = trade_date.weekday()
    ctx.is_opex_week = is_opex_week(trade_date)
    ctx.days_to_fomc = days_to_next_fomc(trade_date)

    # --- Earnings ---
    try:
        earnings_dt = get_historical_earnings_date(symbol, trade_date)
        if earnings_dt:
            ctx.earnings_date = earnings_dt
            ctx.days_to_earnings = (earnings_dt - trade_date).days
            ctx.earnings_in_dte = earnings_dt <= expiration
            # yfinance doesn't reliably provide BMO/AMC for historical dates
            ctx.earnings_timing = "unknown"
    except Exception as e:
        logger.debug(f"Earnings data failed for {symbol}: {e}")

    return ctx
