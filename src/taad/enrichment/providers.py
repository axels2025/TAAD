"""Historical data providers for trade enrichment.

Provides stock/index/VIX OHLCV data from yfinance (primary) and IBKR (supplement).
Uses a fallback chain pattern so enrichment works even without TWS connection.
"""

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class OHLCV:
    """Single bar of OHLCV data."""

    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass
class OptionSnapshot:
    """Historical option data snapshot."""

    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    spread_pct: Optional[float] = None
    volume: Optional[int] = None
    open_interest: Optional[int] = None
    iv: Optional[float] = None
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    rho: Optional[float] = None
    source: str = "unknown"


class HistoricalDataProvider(ABC):
    """Abstract base class for historical market data providers."""

    @abstractmethod
    def get_stock_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Get OHLCV bar for a stock on a specific date."""
        ...

    @abstractmethod
    def get_historical_bars(
        self, symbol: str, end_date: date, lookback_days: int = 130
    ) -> Optional[pd.DataFrame]:
        """Get historical daily bars as a DataFrame with columns: Open, High, Low, Close, Volume."""
        ...

    @abstractmethod
    def get_vix_close(self, on_date: date) -> Optional[float]:
        """Get VIX closing value on a specific date."""
        ...

    @abstractmethod
    def get_index_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Get OHLCV bar for an index/ETF (SPY, QQQ, IWM) on a specific date."""
        ...

    @abstractmethod
    def get_sector_etf_bars(
        self, etf: str, end_date: date, lookback_days: int = 10
    ) -> Optional[pd.DataFrame]:
        """Get historical bars for a sector ETF."""
        ...

    def get_option_snapshot(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[OptionSnapshot]:
        """Get historical option data. Override in providers that support this."""
        return None


class YFinanceProvider(HistoricalDataProvider):
    """Historical data provider using Yahoo Finance (yfinance).

    Downloads bulk data per symbol and caches it in memory to minimize
    API calls. Handles rate limiting with delays and exponential backoff.
    """

    def __init__(self, delay: float = 0.3):
        """Initialize provider.

        Args:
            delay: Seconds to wait between yfinance downloads
        """
        self._cache: dict[str, pd.DataFrame] = {}
        self._delay = delay
        self._last_download = 0.0

    def _rate_limit(self) -> None:
        """Enforce rate limiting between downloads."""
        elapsed = time.time() - self._last_download
        if elapsed < self._delay:
            time.sleep(self._delay - elapsed)
        self._last_download = time.time()

    def _download(
        self, ticker: str, start: date, end: date, max_retries: int = 3
    ) -> Optional[pd.DataFrame]:
        """Download data from yfinance with retry and caching.

        Args:
            ticker: Yahoo Finance ticker symbol
            start: Start date
            end: End date (exclusive)
            max_retries: Maximum retry attempts on failure

        Returns:
            DataFrame with OHLCV data or None
        """
        import yfinance as yf

        cache_key = f"{ticker}_{start}_{end}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        for attempt in range(max_retries):
            try:
                self._rate_limit()
                df = yf.download(
                    ticker,
                    start=start.isoformat(),
                    end=end.isoformat(),
                    progress=False,
                    auto_adjust=True,
                )
                if df is not None and not df.empty:
                    # Flatten MultiIndex columns if present (yfinance sometimes returns these)
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = df.columns.get_level_values(0)
                    self._cache[cache_key] = df
                    return df
                return None
            except Exception as e:
                wait = (2**attempt) * self._delay
                logger.debug(
                    f"yfinance download failed for {ticker} (attempt {attempt + 1}): {e}, "
                    f"retrying in {wait:.1f}s"
                )
                time.sleep(wait)

        logger.warning(f"yfinance download failed for {ticker} after {max_retries} retries")
        return None

    def _ensure_bars(self, symbol: str, on_date: date, lookback_days: int = 10) -> Optional[pd.DataFrame]:
        """Ensure we have bars covering a date range, downloading if needed.

        Downloads 6 months of data at once to minimize API calls.
        """
        # Check if any cached data covers our date
        for key, df in self._cache.items():
            if key.startswith(f"{symbol}_") and not df.empty:
                idx = df.index
                if hasattr(idx[0], "date"):
                    dates = [d.date() if hasattr(d, "date") else d for d in idx]
                else:
                    dates = list(idx)
                if dates and dates[0] <= on_date <= dates[-1] + timedelta(days=5):
                    return df

        # Download 6 months of data centered around the date
        start = on_date - timedelta(days=200)
        end = on_date + timedelta(days=5)
        return self._download(symbol, start, end)

    def _get_bar_on_date(self, df: pd.DataFrame, on_date: date) -> Optional[OHLCV]:
        """Extract a single bar from a DataFrame, falling back to nearest prior trading day."""
        if df is None or df.empty:
            return None

        # Normalize index to dates
        dates = []
        for d in df.index:
            if hasattr(d, "date"):
                dates.append(d.date())
            else:
                dates.append(d)

        # Try exact date, then look backwards up to 5 days (weekends/holidays)
        for offset in range(6):
            target = on_date - timedelta(days=offset)
            if target in dates:
                idx = dates.index(target)
                row = df.iloc[idx]
                return OHLCV(
                    date=target,
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=int(row.get("Volume", 0)),
                )

        return None

    def get_stock_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Get stock OHLCV bar on a specific date."""
        df = self._ensure_bars(symbol, on_date)
        return self._get_bar_on_date(df, on_date)

    def get_historical_bars(
        self, symbol: str, end_date: date, lookback_days: int = 130
    ) -> Optional[pd.DataFrame]:
        """Get historical daily bars as DataFrame."""
        start = end_date - timedelta(days=lookback_days + 60)  # Extra for weekends/holidays
        end = end_date + timedelta(days=5)
        df = self._download(symbol, start, end)

        if df is None or df.empty:
            return None

        # Filter to dates <= end_date
        mask = df.index <= pd.Timestamp(end_date)
        filtered = df[mask]

        return filtered.tail(lookback_days) if len(filtered) > lookback_days else filtered

    def get_vix_close(self, on_date: date) -> Optional[float]:
        """Get VIX closing value on a specific date."""
        bar = self.get_stock_bar("^VIX", on_date)
        return bar.close if bar else None

    def get_index_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Get index/ETF bar (SPY, QQQ, IWM)."""
        return self.get_stock_bar(symbol, on_date)

    def get_sector_etf_bars(
        self, etf: str, end_date: date, lookback_days: int = 10
    ) -> Optional[pd.DataFrame]:
        """Get historical bars for a sector ETF."""
        return self.get_historical_bars(etf, end_date, lookback_days)


class IBKRHistoricalProvider(HistoricalDataProvider):
    """Historical data provider using IBKR TWS connection.

    Requires an active TWS/Gateway connection. Gracefully returns None
    if not connected.
    """

    def __init__(self, ibkr_client=None):
        """Initialize with optional IBKR client.

        Args:
            ibkr_client: IBKR client instance (or None if not available)
        """
        self.ibkr = ibkr_client

    def _is_connected(self) -> bool:
        """Check if IBKR client is available and connected."""
        if self.ibkr is None:
            return False
        try:
            return self.ibkr.ib.isConnected()
        except Exception:
            return False

    def get_stock_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Get stock bar from IBKR historical data."""
        if not self._is_connected():
            return None

        try:
            from ib_insync import Stock

            stock = Stock(symbol, "SMART", "USD")
            qualified = self.ibkr.ib.qualifyContracts(stock)
            if not qualified:
                return None

            bars = self.ibkr.ib.reqHistoricalData(
                qualified[0],
                endDateTime=on_date.strftime("%Y%m%d 23:59:59"),
                durationStr="5 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if not bars:
                return None

            # Find exact date or closest prior
            for bar in reversed(bars):
                bar_date = bar.date if isinstance(bar.date, date) else bar.date.date()
                if bar_date <= on_date:
                    return OHLCV(
                        date=bar_date,
                        open=bar.open,
                        high=bar.high,
                        low=bar.low,
                        close=bar.close,
                        volume=int(bar.volume),
                    )

            return None
        except Exception as e:
            logger.debug(f"IBKR historical data failed for {symbol}: {e}")
            return None

    def get_historical_bars(
        self, symbol: str, end_date: date, lookback_days: int = 130
    ) -> Optional[pd.DataFrame]:
        """Get historical bars from IBKR."""
        if not self._is_connected():
            return None

        try:
            from ib_insync import Stock

            stock = Stock(symbol, "SMART", "USD")
            qualified = self.ibkr.ib.qualifyContracts(stock)
            if not qualified:
                return None

            duration = f"{lookback_days + 30} D"
            bars = self.ibkr.ib.reqHistoricalData(
                qualified[0],
                endDateTime=end_date.strftime("%Y%m%d 23:59:59"),
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if not bars:
                return None

            data = {
                "Open": [b.open for b in bars],
                "High": [b.high for b in bars],
                "Low": [b.low for b in bars],
                "Close": [b.close for b in bars],
                "Volume": [int(b.volume) for b in bars],
            }
            dates = [b.date if isinstance(b.date, date) else b.date.date() for b in bars]
            df = pd.DataFrame(data, index=pd.DatetimeIndex(dates))

            return df.tail(lookback_days) if len(df) > lookback_days else df

        except Exception as e:
            logger.debug(f"IBKR historical bars failed for {symbol}: {e}")
            return None

    def get_vix_close(self, on_date: date) -> Optional[float]:
        """Get VIX close from IBKR."""
        if not self._is_connected():
            return None

        try:
            from ib_insync import Index

            vix = Index("VIX", "CBOE")
            qualified = self.ibkr.ib.qualifyContracts(vix)
            if not qualified:
                return None

            bars = self.ibkr.ib.reqHistoricalData(
                qualified[0],
                endDateTime=on_date.strftime("%Y%m%d 23:59:59"),
                durationStr="5 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if bars:
                return bars[-1].close

            return None
        except Exception as e:
            logger.debug(f"IBKR VIX data failed: {e}")
            return None

    def get_index_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        """Get index/ETF bar from IBKR."""
        return self.get_stock_bar(symbol, on_date)

    def get_sector_etf_bars(
        self, etf: str, end_date: date, lookback_days: int = 10
    ) -> Optional[pd.DataFrame]:
        """Get sector ETF bars from IBKR."""
        return self.get_historical_bars(etf, end_date, lookback_days)


class FallbackChainProvider(HistoricalDataProvider):
    """Tries multiple providers in order, returning the first successful result.

    Default chain: YFinanceProvider (free, no connection needed) -> IBKRHistoricalProvider
    """

    def __init__(self, providers: list[HistoricalDataProvider]):
        """Initialize with ordered list of providers.

        Args:
            providers: List of providers to try in order
        """
        self.providers = providers

    def get_stock_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        for provider in self.providers:
            try:
                result = provider.get_stock_bar(symbol, on_date)
                if result is not None:
                    logger.debug(
                        f"Stock bar for {symbol} on {on_date} from {provider.__class__.__name__}"
                    )
                    return result
            except Exception as e:
                logger.debug(f"{provider.__class__.__name__} failed for {symbol}: {e}")
        return None

    def get_historical_bars(
        self, symbol: str, end_date: date, lookback_days: int = 130
    ) -> Optional[pd.DataFrame]:
        for provider in self.providers:
            try:
                result = provider.get_historical_bars(symbol, end_date, lookback_days)
                if result is not None and not result.empty:
                    logger.debug(
                        f"Historical bars for {symbol} from {provider.__class__.__name__} "
                        f"({len(result)} bars)"
                    )
                    return result
            except Exception as e:
                logger.debug(f"{provider.__class__.__name__} failed for {symbol}: {e}")
        return None

    def get_vix_close(self, on_date: date) -> Optional[float]:
        for provider in self.providers:
            try:
                result = provider.get_vix_close(on_date)
                if result is not None:
                    return result
            except Exception as e:
                logger.debug(f"{provider.__class__.__name__} VIX failed: {e}")
        return None

    def get_index_bar(self, symbol: str, on_date: date) -> Optional[OHLCV]:
        for provider in self.providers:
            try:
                result = provider.get_index_bar(symbol, on_date)
                if result is not None:
                    return result
            except Exception as e:
                logger.debug(f"{provider.__class__.__name__} index failed: {e}")
        return None

    def get_sector_etf_bars(
        self, etf: str, end_date: date, lookback_days: int = 10
    ) -> Optional[pd.DataFrame]:
        for provider in self.providers:
            try:
                result = provider.get_sector_etf_bars(etf, end_date, lookback_days)
                if result is not None and not result.empty:
                    return result
            except Exception as e:
                logger.debug(f"{provider.__class__.__name__} sector ETF failed: {e}")
        return None

    def get_option_snapshot(
        self,
        symbol: str,
        strike: float,
        expiry: date,
        put_call: str,
        on_date: date,
    ) -> Optional[OptionSnapshot]:
        for provider in self.providers:
            try:
                result = provider.get_option_snapshot(symbol, strike, expiry, put_call, on_date)
                if result is not None:
                    return result
            except Exception as e:
                logger.debug(f"{provider.__class__.__name__} option snapshot failed: {e}")
        return None
