"""Earnings date service with external API integration.

Phase 2.6C - Market Context & Events
Fetches earnings dates and timing information for symbols.
"""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional, Tuple

from loguru import logger

from src.utils.timezone import us_trading_date


@dataclass
class EarningsInfo:
    """Earnings information for a symbol.

    Attributes:
        earnings_date: Next earnings report date
        days_to_earnings: Days until next earnings
        earnings_timing: "BMO" (before market open) or "AMC" (after market close)
        earnings_in_dte: True if earnings before option expiration
    """

    earnings_date: Optional[date] = None
    days_to_earnings: Optional[int] = None
    earnings_timing: Optional[str] = None  # "BMO" or "AMC"
    earnings_in_dte: Optional[bool] = None


class EarningsService:
    """Fetches earnings dates from external sources.

    Supports multiple data sources:
    - Yahoo Finance (free, via yfinance library)
    - Financial Modeling Prep API (requires API key)
    """

    def __init__(self, data_source: str = "yahoo"):
        """Initialize earnings service.

        Args:
            data_source: Source for earnings data ('yahoo' or 'fmp')
        """
        self.data_source = data_source

    def get_earnings_info(
        self,
        symbol: str,
        option_expiration: Optional[date] = None,
    ) -> EarningsInfo:
        """Get earnings information for a symbol.

        Args:
            symbol: Stock symbol
            option_expiration: Option expiration date to check if earnings falls within DTE

        Returns:
            EarningsInfo with earnings date and derived fields
        """
        info = EarningsInfo()

        try:
            if self.data_source == "yahoo":
                earnings_date, timing = self._fetch_from_yahoo(symbol)
            else:
                earnings_date, timing = self._fetch_from_fmp(symbol)

            if earnings_date:
                info.earnings_date = earnings_date
                info.earnings_timing = timing
                info.days_to_earnings = (earnings_date - us_trading_date()).days

                if option_expiration:
                    info.earnings_in_dte = earnings_date <= option_expiration

        except Exception as e:
            logger.debug(f"Failed to fetch earnings for {symbol}: {e}")

        return info

    def _fetch_from_yahoo(
        self, symbol: str
    ) -> Tuple[Optional[date], Optional[str]]:
        """Fetch earnings date from Yahoo Finance.

        Uses yfinance library to access Yahoo Finance data.

        Args:
            symbol: Stock symbol

        Returns:
            Tuple of (earnings_date, timing) or (None, None)
        """
        try:
            import yfinance as yf

            ticker = yf.Ticker(symbol)
            calendar = ticker.calendar

            if calendar is not None and not calendar.empty:
                # Yahoo provides earnings date in various formats
                if "Earnings Date" in calendar.index:
                    earnings_dates = calendar.loc["Earnings Date"]

                    # Handle multiple potential earnings dates
                    if hasattr(earnings_dates, "__iter__") and len(earnings_dates) > 0:
                        next_earnings = earnings_dates[0]

                        # Convert to date object
                        if isinstance(next_earnings, datetime):
                            earnings_date = next_earnings.date()
                        elif isinstance(next_earnings, date):
                            earnings_date = next_earnings
                        else:
                            # Try parsing string
                            earnings_date = datetime.strptime(
                                str(next_earnings), "%Y-%m-%d"
                            ).date()

                        # Yahoo doesn't always provide timing (BMO/AMC)
                        # Default to AMC (most common)
                        timing = "AMC"

                        return earnings_date, timing

        except ImportError:
            logger.warning(
                "yfinance library not installed. Install with: pip install yfinance"
            )
        except Exception as e:
            logger.debug(f"Yahoo earnings fetch failed for {symbol}: {e}")

        return None, None

    def _fetch_from_fmp(self, symbol: str) -> Tuple[Optional[date], Optional[str]]:
        """Fetch earnings date from Financial Modeling Prep API.

        Requires FMP_API_KEY environment variable.

        Args:
            symbol: Stock symbol

        Returns:
            Tuple of (earnings_date, timing) or (None, None)
        """
        try:
            import os
            import requests

            api_key = os.getenv("FMP_API_KEY")
            if not api_key:
                logger.debug(
                    "FMP_API_KEY not found in environment. "
                    "Get a free key at https://financialmodelingprep.com/developer/docs/"
                )
                return None, None

            # FMP earnings calendar endpoint
            url = f"https://financialmodelingprep.com/api/v3/earning_calendar"
            params = {"symbol": symbol, "apikey": api_key}

            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()

            data = response.json()

            if data and len(data) > 0:
                # Get next earnings (data should be sorted by date)
                next_earnings = data[0]

                earnings_date_str = next_earnings.get("date")
                if earnings_date_str:
                    earnings_date = datetime.strptime(earnings_date_str, "%Y-%m-%d").date()

                    # FMP provides timing in "time" field
                    time_str = next_earnings.get("time", "").upper()
                    if "BMO" in time_str or "BEFORE" in time_str:
                        timing = "BMO"
                    elif "AMC" in time_str or "AFTER" in time_str:
                        timing = "AMC"
                    else:
                        timing = "AMC"  # Default

                    return earnings_date, timing

        except ImportError:
            logger.warning(
                "requests library not installed. Install with: pip install requests"
            )
        except Exception as e:
            logger.debug(f"FMP earnings fetch failed for {symbol}: {e}")

        return None, None


# Module-level cache for earnings data (to avoid repeated API calls)
_earnings_cache = {}


def get_cached_earnings(
    symbol: str, option_expiration: Optional[date] = None, data_source: str = "yahoo"
) -> EarningsInfo:
    """Get earnings info with caching.

    Caches results for 24 hours to reduce API calls.

    Args:
        symbol: Stock symbol
        option_expiration: Option expiration date
        data_source: Data source to use ('yahoo' or 'fmp')

    Returns:
        EarningsInfo
    """
    cache_key = f"{symbol}:{data_source}"

    # Check cache
    if cache_key in _earnings_cache:
        cached_info, cached_time = _earnings_cache[cache_key]
        # Cache valid for 24 hours
        if (datetime.now() - cached_time).total_seconds() < 86400:
            # Update earnings_in_dte if expiration provided
            if option_expiration and cached_info.earnings_date:
                cached_info.earnings_in_dte = cached_info.earnings_date <= option_expiration
            return cached_info

    # Fetch fresh data
    service = EarningsService(data_source=data_source)
    info = service.get_earnings_info(symbol, option_expiration)

    # Cache result
    _earnings_cache[cache_key] = (info, datetime.now())

    return info
