"""Market context capture for trade analysis.

Phase 2.6C - Market Context & Events
Captures broad market context including indices, sector data, regime classification,
and calendar events for pattern detection.
"""

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

from ib_insync import Stock, Index
from loguru import logger

from src.utils.timezone import us_trading_date


@dataclass
class MarketContext:
    """Broad market context at trade time.

    Captures market-wide conditions that may affect trade outcomes.
    """
    # Additional indices
    qqq_price: Optional[float] = None
    qqq_change_pct: Optional[float] = None
    iwm_price: Optional[float] = None
    iwm_change_pct: Optional[float] = None

    # Sector
    sector: Optional[str] = None
    sector_etf: Optional[str] = None
    sector_change_1d: Optional[float] = None
    sector_change_5d: Optional[float] = None

    # Regime classification
    vol_regime: Optional[str] = None  # low, normal, elevated, extreme
    market_regime: Optional[str] = None  # bullish, bearish, neutral, volatile

    # Calendar
    day_of_week: Optional[int] = None  # 0=Monday, 4=Friday
    is_opex_week: Optional[bool] = None
    days_to_fomc: Optional[int] = None


class MarketContextService:
    """Captures broad market context at trade entry.

    Provides additional data beyond individual stock metrics to help
    learning engine detect patterns related to market conditions.
    """

    # Sector to ETF mapping (SPDR sector ETFs)
    SECTOR_ETFS = {
        "Technology": "XLK",
        "Healthcare": "XLV",
        "Financials": "XLF",
        "Consumer Discretionary": "XLY",
        "Consumer Staples": "XLP",
        "Energy": "XLE",
        "Utilities": "XLU",
        "Materials": "XLB",
        "Industrials": "XLI",
        "Real Estate": "XLRE",
        "Communication Services": "XLC",
        "Communications": "XLC",  # Alias
        "Information Technology": "XLK",  # Alias
    }

    # 2026 FOMC meeting dates (second day of two-day meetings)
    # Update annually from https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
    FOMC_DATES_2026 = [
        date(2026, 1, 29),
        date(2026, 3, 18),
        date(2026, 5, 6),
        date(2026, 6, 17),
        date(2026, 7, 29),
        date(2026, 9, 16),
        date(2026, 11, 4),
        date(2026, 12, 16),
    ]

    def __init__(self, ibkr_client):
        """Initialize market context service.

        Args:
            ibkr_client: IBKR client for market data
        """
        self.ibkr = ibkr_client

    def capture_context(
        self, symbol: str, vix: float, spy_change_pct: Optional[float] = None
    ) -> MarketContext:
        """Capture complete market context.

        Args:
            symbol: Stock symbol (for sector lookup)
            vix: Current VIX value
            spy_change_pct: SPY change % (if already captured)

        Returns:
            MarketContext with all available data
        """
        ctx = MarketContext()

        # Capture additional index prices
        try:
            self._capture_indices(ctx)
        except Exception as e:
            logger.debug(f"Failed to capture index prices: {e}")

        # Determine sector
        try:
            ctx.sector = self._get_sector(symbol)
            ctx.sector_etf = self.SECTOR_ETFS.get(ctx.sector)
            if ctx.sector_etf:
                self._capture_sector_performance(ctx)
        except Exception as e:
            logger.debug(f"Failed to capture sector data: {e}")

        # Classify regimes
        try:
            ctx.vol_regime = self._classify_vol_regime(vix)
            ctx.market_regime = self._classify_market_regime(spy_change_pct, vix)
        except Exception as e:
            logger.debug(f"Failed to classify regimes: {e}")

        # Calendar data
        try:
            today = us_trading_date()
            ctx.day_of_week = today.weekday()  # 0=Monday, 6=Sunday
            ctx.is_opex_week = self._is_opex_week(today)
            ctx.days_to_fomc = self._days_to_next_fomc(today)
        except Exception as e:
            logger.debug(f"Failed to capture calendar data: {e}")

        return ctx

    def _capture_indices(self, ctx: MarketContext) -> None:
        """Capture QQQ and IWM prices and changes.

        Args:
            ctx: MarketContext object to populate
        """
        # QQQ (Nasdaq 100 ETF)
        try:
            qqq = Stock("QQQ", "SMART", "USD")
            data = self.ibkr.get_market_data(qqq)
            if data:
                ctx.qqq_price = data["last"]
                qqq_close = data.get("close")
                if ctx.qqq_price and qqq_close and qqq_close > 0:
                    ctx.qqq_change_pct = (ctx.qqq_price - qqq_close) / qqq_close
        except Exception as e:
            logger.debug(f"Failed to get QQQ data: {e}")

        # IWM (Russell 2000 ETF)
        try:
            iwm = Stock("IWM", "SMART", "USD")
            data = self.ibkr.get_market_data(iwm)
            if data:
                ctx.iwm_price = data["last"]
                iwm_close = data.get("close")
                if ctx.iwm_price and iwm_close and iwm_close > 0:
                    ctx.iwm_change_pct = (ctx.iwm_price - iwm_close) / iwm_close
        except Exception as e:
            logger.debug(f"Failed to get IWM data: {e}")

    def _get_sector(self, symbol: str) -> Optional[str]:
        """Get sector for a symbol using IBKR fundamental data.

        Args:
            symbol: Stock symbol

        Returns:
            Sector name or None if not available
        """
        try:
            stock = Stock(symbol, "SMART", "USD")
            qualified = self.ibkr.ib.qualifyContracts(stock)
            if not qualified:
                return None

            # Request fundamental data (includes sector)
            contract = qualified[0]
            fundamentals = self.ibkr.ib.reqFundamentalData(contract, "ReportsFinSummary")

            # Parse XML to extract sector (simplified - may need more robust parsing)
            if fundamentals and "<sector>" in fundamentals.lower():
                import xml.etree.ElementTree as ET
                root = ET.fromstring(fundamentals)
                sector_elem = root.find(".//sector")
                if sector_elem is not None and sector_elem.text:
                    return sector_elem.text

            return None

        except Exception as e:
            logger.debug(f"Failed to get sector for {symbol}: {e}")
            return None

    def _capture_sector_performance(self, ctx: MarketContext) -> None:
        """Capture sector ETF performance (1-day and 5-day).

        Args:
            ctx: MarketContext object to populate (must have sector_etf set)
        """
        if not ctx.sector_etf:
            return

        try:
            etf = Stock(ctx.sector_etf, "SMART", "USD")
            data = self.ibkr.get_market_data(etf)
            if not data:
                return

            current_price = data["last"]
            prev_close = data.get("close")

            # 1-day change
            if current_price and prev_close and prev_close > 0:
                ctx.sector_change_1d = (current_price - prev_close) / prev_close

            # 5-day change (requires historical data)
            # Need to qualify for reqHistoricalData
            qualified = self.ibkr.qualify_contract(etf)
            if qualified:
                bars = self.ibkr.ib.reqHistoricalData(
                    qualified,
                    endDateTime="",
                    durationStr="10 D",
                    barSizeSetting="1 day",
                    whatToShow="TRADES",
                    useRTH=True,
                    formatDate=1,
                )

                if bars and len(bars) >= 5:
                    price_5d_ago = bars[-6].close if len(bars) >= 6 else bars[0].close
                    if current_price and price_5d_ago > 0:
                        ctx.sector_change_5d = (current_price - price_5d_ago) / price_5d_ago

        except Exception as e:
            logger.debug(f"Failed to capture sector performance: {e}")

    def _classify_vol_regime(self, vix: float) -> str:
        """Classify volatility regime based on VIX.

        Thresholds based on historical VIX levels:
        - <15: Low volatility (typical bull market)
        - 15-20: Normal volatility
        - 20-25: Elevated volatility
        - >25: Extreme volatility (fear/panic)

        Args:
            vix: Current VIX value

        Returns:
            Volatility regime: "low", "normal", "elevated", or "extreme"
        """
        if vix < 15:
            return "low"
        elif vix < 20:
            return "normal"
        elif vix < 25:
            return "elevated"
        else:
            return "extreme"

    def _classify_market_regime(
        self, spy_change_pct: Optional[float], vix: float
    ) -> str:
        """Classify market regime based on price action and volatility.

        Args:
            spy_change_pct: SPY daily change percentage
            vix: Current VIX value

        Returns:
            Market regime: "bullish", "bearish", "neutral", or "volatile"
        """
        # High volatility overrides other signals
        if vix > 25:
            return "volatile"

        # No SPY data available
        if spy_change_pct is None:
            return "neutral"

        # Classify based on SPY movement
        if spy_change_pct > 0.01:  # >1% up
            return "bullish"
        elif spy_change_pct < -0.01:  # >1% down
            return "bearish"
        else:
            return "neutral"

    def _is_opex_week(self, today: date) -> bool:
        """Check if current week is options expiration week (3rd Friday).

        Args:
            today: Current date

        Returns:
            True if in OpEx week, False otherwise
        """
        # Find 3rd Friday of the month
        cal = calendar.Calendar(firstweekday=calendar.SUNDAY)
        fridays = [
            d
            for d in cal.itermonthdates(today.year, today.month)
            if d.weekday() == 4 and d.month == today.month
        ]

        if len(fridays) < 3:
            return False

        third_friday = fridays[2]

        # Check if today is within the week of 3rd Friday (Mon-Fri)
        # Week starts on Monday before 3rd Friday
        week_start = third_friday - timedelta(days=(third_friday.weekday()))
        week_end = week_start + timedelta(days=4)  # Friday

        return week_start <= today <= week_end

    def _days_to_next_fomc(self, today: date) -> int:
        """Calculate days until next FOMC meeting.

        Args:
            today: Current date

        Returns:
            Number of days to next FOMC meeting, or 999 if none this year
        """
        for fomc_date in self.FOMC_DATES_2026:
            if fomc_date >= today:
                return (fomc_date - today).days

        # No upcoming FOMC this year
        return 999
