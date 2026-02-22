"""Stock screening tool for finding trading candidates.

This module implements stock screening based on technical indicators,
price, volume, and trend analysis to find candidates for the strategy.
"""

from typing import Literal

import pandas as pd
from ib_insync import Stock
from loguru import logger

from src.config.baseline_strategy import BaselineStrategy
from src.tools.ibkr_client import IBKRClient
from src.tools.stock_universe import StockUniverseManager


class StockScreener:
    """Screen stocks based on technical and fundamental criteria.

    The screener identifies stocks matching the strategy's requirements:
    - Price range (e.g., $20-$500)
    - Volume threshold (e.g., 1M+ shares daily)
    - Trend filter (uptrend: Price > 20 EMA > 50 EMA, or sideways)

    Example:
        >>> from src.tools.ibkr_client import IBKRClient
        >>> from src.config.base import IBKRConfig
        >>> config = IBKRConfig()
        >>> client = IBKRClient(config)
        >>> client.connect()
        >>> screener = StockScreener(client)
        >>> stocks = screener.scan_stocks(
        ...     trend_filter="uptrend",
        ...     min_price=20.0,
        ...     max_price=500.0
        ... )
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: BaselineStrategy | None = None,
        universe_manager: StockUniverseManager | None = None,
    ):
        """Initialize stock screener.

        Args:
            ibkr_client: Connected IBKR client
            config: Strategy configuration (optional)
            universe_manager: Stock universe manager (optional, creates default if None)
        """
        self.ibkr_client = ibkr_client
        self.config = config
        self.universe_manager = universe_manager or StockUniverseManager()

        # Backward compatibility: keep old hardcoded universe as fallback
        self.default_universe = self._get_default_universe()

        logger.info("Initialized StockScreener with tiered universe system")

    def scan_stocks(
        self,
        trend_filter: Literal["uptrend", "downtrend", "sideways", "any"] = "uptrend",
        min_price: float = 20.0,
        max_price: float = 500.0,
        min_volume: int = 1_000_000,
        max_results: int = 20,
        symbols: list[str] | None = None,
        use_cache: bool = True,
        cache_max_age_hours: int = 24,
        universe_tier: Literal["tier1", "tier2", "tier3", "tier4", "all"] = "tier1",
    ) -> list[dict]:
        """Scan for stocks matching criteria with caching support.

        Args:
            trend_filter: Trend requirement (uptrend, downtrend, sideways, any)
            min_price: Minimum stock price
            max_price: Maximum stock price
            min_volume: Minimum daily volume
            max_results: Maximum results to return
            symbols: Custom symbol list (overrides universe_tier if provided)
            use_cache: Use cached results for recently scanned stocks
            cache_max_age_hours: Max age of cached results in hours
            universe_tier: Which tier to scan (tier1=50 stocks, tier2=250, etc.)

        Returns:
            list[dict]: List of stocks matching criteria with metadata

        Example:
            >>> # Scan top 50 liquid stocks (default)
            >>> stocks = screener.scan_stocks(trend_filter="uptrend")
            >>>
            >>> # Scan full S&P 500
            >>> stocks = screener.scan_stocks(
            ...     trend_filter="uptrend",
            ...     universe_tier="tier2",
            ...     max_results=50
            ... )
        """
        logger.info(
            f"Scanning stocks: tier={universe_tier}, trend={trend_filter}, "
            f"price={min_price}-{max_price}, volume>={min_volume:,}, "
            f"max_results={max_results}, cache={use_cache}"
        )

        # Get universe
        if symbols:
            symbol_list = symbols
        else:
            symbol_list = self.universe_manager.get_universe(universe_tier)

        # Get only unscanned symbols if using cache
        if use_cache:
            symbols_to_scan = self.universe_manager.get_unscanned_symbols(
                symbol_list,
                max_age_hours=cache_max_age_hours
            )
            logger.info(f"Need to scan {len(symbols_to_scan)}/{len(symbol_list)} symbols (rest cached)")
        else:
            symbols_to_scan = symbol_list

        matching_stocks = []

        # First, add cached results that still match criteria
        if use_cache:
            for symbol in symbol_list:
                if symbol in symbols_to_scan:
                    continue  # Will scan fresh

                cached = self.universe_manager.get_cached_result(symbol, cache_max_age_hours)
                if cached and self._matches_criteria(cached, trend_filter, min_price, max_price, min_volume):
                    matching_stocks.append(cached)

            logger.info(f"Added {len(matching_stocks)} cached results")

        # Scan remaining symbols
        scanned_count = 0

        for symbol in symbols_to_scan:
            try:
                stock_data = self._analyze_stock(
                    symbol=symbol,
                    trend_filter=trend_filter,
                    min_price=min_price,
                    max_price=max_price,
                    min_volume=min_volume,
                )

                scanned_count += 1

                # Cache the result (even if doesn't match, to avoid re-scanning)
                if use_cache:
                    self.universe_manager.mark_scanned(
                        symbol,
                        stock_data or {"no_match": True},
                        scan_type=f"{trend_filter}_{min_price}_{max_price}"
                    )

                if stock_data:
                    matching_stocks.append(stock_data)

                    if len(matching_stocks) >= max_results:
                        logger.info(f"Reached max_results limit of {max_results}")
                        break

            except Exception as e:
                logger.debug(f"Error analyzing {symbol}: {e}")
                continue

        logger.info(
            f"Found {len(matching_stocks)} stocks matching criteria "
            f"(scanned {scanned_count} fresh, used cached results, "
            f"total universe: {len(symbol_list)})"
        )

        return matching_stocks

    def _matches_criteria(
        self,
        stock_data: dict,
        trend_filter: str,
        min_price: float,
        max_price: float,
        min_volume: int,
    ) -> bool:
        """Check if cached stock data still matches criteria.

        Args:
            stock_data: Cached stock data
            trend_filter: Required trend
            min_price: Minimum price
            max_price: Maximum price
            min_volume: Minimum volume

        Returns:
            bool: True if matches all criteria
        """
        # Cached result was a non-match
        if stock_data.get("no_match"):
            return False

        # Check all criteria
        try:
            price = stock_data.get("price", 0)
            volume = stock_data.get("volume", 0)
            trend = stock_data.get("trend", "")

            if not (min_price <= price <= max_price):
                return False

            if volume < min_volume:
                return False

            if trend_filter != "any" and trend != trend_filter:
                return False

            return True

        except Exception:
            return False

    def _analyze_stock(
        self,
        symbol: str,
        trend_filter: str,
        min_price: float,
        max_price: float,
        min_volume: int,
    ) -> dict | None:
        """Analyze a single stock against criteria.

        Args:
            symbol: Stock ticker symbol
            trend_filter: Required trend
            min_price: Minimum price
            max_price: Maximum price
            min_volume: Minimum volume

        Returns:
            dict: Stock data if matches, None otherwise
        """
        # Get current market data
        contract = self.ibkr_client.get_stock_contract(symbol)
        qualified_contract = self.ibkr_client.qualify_contract(contract)

        if not qualified_contract:
            return None

        market_data = self.ibkr_client.get_market_data(qualified_contract)

        if not market_data:
            return None

        price = market_data["last"]
        volume = market_data.get("volume", 0)

        # Filter by price range
        if not (min_price <= price <= max_price):
            logger.debug(
                f"{symbol}: Price ${price:.2f} outside range "
                f"${min_price}-${max_price}"
            )
            return None

        # Filter by volume - but be smart about market closed
        # When market is closed, volume data is incomplete/stale
        # Heuristic: If stock price > $100 but volume < 100K, market is likely closed
        # In this case, skip volume check (assume these liquid stocks have adequate volume)
        market_likely_closed = (price > 100 and volume < 100_000)

        if not market_likely_closed and volume < min_volume:
            logger.debug(f"{symbol}: Volume {volume:,} below minimum {min_volume:,}")
            return None
        elif market_likely_closed:
            logger.debug(
                f"{symbol}: Market likely closed (price=${price:.2f}, volume={volume:,}), "
                "skipping volume check for liquid stock"
            )

        # Get historical data for trend analysis
        bars = self._get_historical_data(qualified_contract, days=60)

        if bars is None or len(bars) < 50:
            logger.debug(f"{symbol}: Insufficient historical data")
            return None

        # Calculate trend
        trend_data = self._calculate_trend(bars)
        trend = trend_data["trend"]
        trend_score = trend_data["trend_score"]

        # Filter by trend
        if trend_filter != "any" and trend != trend_filter:
            logger.debug(
                f"{symbol}: Trend '{trend}' does not match filter '{trend_filter}'"
            )
            return None

        # Get sector (if available)
        sector = self._get_sector(qualified_contract)

        logger.info(
            f"{symbol}: Matched - ${price:.2f}, {trend} (score: {trend_score:.2f}), "
            f"volume: {volume:,}"
        )

        return {
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "trend": trend,
            "trend_score": trend_score,
            "ema_20": trend_data["ema_20"],
            "ema_50": trend_data["ema_50"],
            "sector": sector,
            "last_updated": pd.Timestamp.now(),
        }

    def _get_historical_data(
        self, contract: Stock, days: int = 60
    ) -> pd.DataFrame | None:
        """Get historical price data for trend calculation.

        Args:
            contract: Stock contract
            days: Number of days of history

        Returns:
            DataFrame with OHLCV data or None
        """
        try:
            bars = self.ibkr_client.ib.reqHistoricalData(
                contract,
                endDateTime="",
                durationStr=f"{days} D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if not bars:
                return None

            df = pd.DataFrame(bars)
            return df

        except Exception as e:
            logger.warning(f"Error getting historical data: {e}")
            return None

    def _calculate_trend(self, bars: pd.DataFrame) -> dict:
        """Calculate trend based on EMAs.

        Trend Rules:
            - Uptrend: Price > EMA20 > EMA50
            - Downtrend: Price < EMA20 < EMA50
            - Sideways: Otherwise

        Args:
            bars: DataFrame with 'close' column

        Returns:
            dict: Trend classification and metrics
        """
        # Calculate EMAs
        close_prices = bars["close"]
        ema_20 = close_prices.ewm(span=20, adjust=False).mean().iloc[-1]
        ema_50 = close_prices.ewm(span=50, adjust=False).mean().iloc[-1]
        current_price = close_prices.iloc[-1]

        # Determine trend
        if current_price > ema_20 > ema_50:
            trend = "uptrend"
            # Strength: how far above EMA50 (as %)
            trend_score = min(1.0, (current_price - ema_50) / ema_50 * 10)
        elif current_price < ema_20 < ema_50:
            trend = "downtrend"
            trend_score = min(1.0, (ema_50 - current_price) / ema_50 * 10)
        else:
            trend = "sideways"
            # Sideways strength: how close EMAs are
            ema_diff = abs(ema_20 - ema_50) / ema_50
            trend_score = 1.0 - min(1.0, ema_diff * 10)

        return {
            "trend": trend,
            "trend_score": round(trend_score, 2),
            "ema_20": round(ema_20, 2),
            "ema_50": round(ema_50, 2),
            "current_price": round(current_price, 2),
        }

    def _get_sector(self, contract: Stock) -> str | None:
        """Get stock sector classification.

        Args:
            contract: Stock contract

        Returns:
            Sector name or None
        """
        try:
            # Request fundamental data
            fundamentals = self.ibkr_client.ib.reqFundamentalData(
                contract, reportType="ReportSnapshot"
            )

            if fundamentals:
                # Parse XML to extract sector
                # This is simplified - real implementation would parse XML
                return "Technology"  # Placeholder

            return None

        except Exception as e:
            logger.debug(f"Could not get sector: {e}")
            return None

    def _get_default_universe(self) -> list[str]:
        """Get default stock universe for scanning.

        Returns a curated list of liquid stocks across major sectors.
        In production, this could be expanded or made configurable.

        Returns:
            list[str]: List of ticker symbols
        """
        return [
            # Technology
            "AAPL",
            "MSFT",
            "GOOGL",
            "AMZN",
            "META",
            "NVDA",
            "TSLA",
            "AMD",
            "INTC",
            # Finance
            "JPM",
            "BAC",
            "WFC",
            "GS",
            "MS",
            "C",
            # Healthcare
            "JNJ",
            "UNH",
            "PFE",
            "ABBV",
            "MRK",
            # Consumer
            "WMT",
            "HD",
            "NKE",
            "SBUX",
            "MCD",
            # Energy
            "XOM",
            "CVX",
            # Industrials
            "BA",
            "CAT",
            "GE",
            # Communications
            "DIS",
            "NFLX",
            "T",
            "VZ",
            # Add more as needed
        ]
