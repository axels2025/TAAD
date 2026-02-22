"""
Uptrend Screener Tool
Finds stocks where Price > 20 SMA > 50 SMA
Filters by price range and market cap
"""
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from ib_insync import Stock, util
from config.ibkr_connection import create_ibkr_connection
from config import trading_config as cfg
from utils_progress import ProgressBar, StatusPrinter
from utils_historical_data import get_historical_data_with_cache

logger = logging.getLogger(__name__)


def calculate_sma(prices: pd.Series, period: int) -> float:
    """Calculate Simple Moving Average"""
    if len(prices) < period:
        return None
    return prices.tail(period).mean()


def detect_trend(price: float, sma_20: float, sma_50: float, sideways_threshold: float = 0.02):
    """
    Detect the trend of a stock based on price and SMAs

    Args:
        price: Current stock price
        sma_20: 20-period SMA
        sma_50: 50-period SMA
        sideways_threshold: Threshold for sideways detection (default 2%)

    Returns:
        tuple: (trend_type, trend_strength)
            trend_type: 'uptrend', 'downtrend', 'sideways', or 'unclear'
            trend_strength: percentage difference from SMA50
    """
    if sma_20 is None or sma_50 is None:
        return ('unclear', 0.0)

    # Calculate trend strength (distance from SMA50)
    trend_strength = ((price - sma_50) / sma_50) * 100

    # Check if SMAs are close together (sideways market)
    sma_diff_pct = abs(sma_20 - sma_50) / sma_50
    if sma_diff_pct < sideways_threshold:
        return ('sideways', trend_strength)

    # Check for uptrend: Price > SMA20 > SMA50
    if price > sma_20 > sma_50:
        return ('uptrend', trend_strength)

    # Check for downtrend: Price < SMA20 < SMA50
    if price < sma_20 < sma_50:
        return ('downtrend', trend_strength)

    # Mixed signals - unclear trend
    return ('unclear', trend_strength)


def get_market_cap(ib, stock: Stock) -> Optional[float]:
    """
    Get market capitalization for a stock

    Args:
        ib: IB connection
        stock: Stock contract

    Returns:
        Market cap in USD or None if unavailable
    """
    try:
        # Request fundamental data
        fundamentals = ib.reqFundamentalData(stock, 'ReportSnapshot')

        if fundamentals:
            # Parse XML to get market cap
            import xml.etree.ElementTree as ET
            root = ET.fromstring(fundamentals)

            # Look for market cap in various fields
            for elem in root.iter():
                if 'MarketCap' in elem.tag or 'marketCap' in elem.tag:
                    try:
                        return float(elem.text)
                    except (ValueError, TypeError):
                        pass

        # Fallback: calculate from shares outstanding
        contract_details = ib.reqContractDetails(stock)
        if contract_details:
            details = contract_details[0]

            # Get current price
            ticker = ib.reqMktData(stock, '', False, False)
            ib.sleep(1)
            price = ticker.last if ticker.last > 0 else ticker.close
            ib.cancelMktData(stock)

            # Some contracts have shares outstanding in longName or industry
            # This is a best-effort approach
            logger.debug(f"Could not get precise market cap for {stock.symbol}")
            return None

    except Exception as e:
        logger.debug(f"Error getting market cap for {stock.symbol}: {e}")
        return None


def screen_uptrend_stocks(
    symbols: List[str],
    lookback_days: int = 100,
    min_price: float = None,
    max_price: float = None,
    min_market_cap: float = None,
    trend_filter: List[str] = None
) -> List[Dict]:
    """
    Screen stocks with trend detection and filtering

    Detects trends:
    - Uptrend: Price > SMA20 > SMA50
    - Downtrend: Price < SMA20 < SMA50
    - Sideways: SMA20 and SMA50 within threshold
    - Unclear: Mixed signals

    Args:
        symbols: List of stock symbols to screen
        lookback_days: Days of historical data to fetch (default 100)
        min_price: Minimum stock price (default from config)
        max_price: Maximum stock price (default from config)
        min_market_cap: Minimum market cap in USD (default from config)
        trend_filter: List of trends to include (e.g., ['uptrend', 'sideways'])
                     Use ['all'] to include all trends (default from config)

    Returns:
        List of dicts with stock info meeting criteria
    """
    # Use config defaults if not specified
    min_price = min_price if min_price is not None else cfg.MIN_STOCK_PRICE
    max_price = max_price if max_price is not None else cfg.MAX_STOCK_PRICE
    min_market_cap = min_market_cap if min_market_cap is not None else cfg.MIN_MARKET_CAP
    trend_filter = trend_filter if trend_filter is not None else cfg.TREND_FILTER

    # Determine if we're filtering by trend
    filter_by_trend = 'all' not in trend_filter
    trend_desc = ', '.join(trend_filter) if filter_by_trend else 'all trends (no filtering)'

    logger.info(
        f"Screening {len(symbols)} symbols - "
        f"Price: ${min_price:.0f}-${max_price:.0f}, "
        f"Trend: {trend_desc}"
    )

    StatusPrinter.section(f"Screening {len(symbols)} Stocks ({trend_desc})")
    matching_stocks = []
    trend_counts = {'uptrend': 0, 'downtrend': 0, 'sideways': 0, 'unclear': 0}

    # Create progress bar
    progress = ProgressBar(total=len(symbols), prefix="Screening", width=40)

    # Create connection within this thread
    with create_ibkr_connection() as ib:
        try:
            for idx, symbol in enumerate(symbols, 1):
                try:
                    # Update progress bar - show which symbol we're working on
                    progress.update(idx, suffix=f"Processing {symbol}")
                    logger.debug(f"[{idx}/{len(symbols)}] Processing {symbol}...")

                    # Create stock contract
                    stock = Stock(symbol, 'SMART', 'USD')
                    ib.qualifyContracts(stock)
                    logger.debug(f"{symbol}: Contract qualified")

                    # Get historical data with intelligent caching
                    # This will use cached data when available, only fetching missing days
                    df = get_historical_data_with_cache(ib, stock, lookback_days=lookback_days)

                    if df is None or df.empty:
                        logger.warning(f"{symbol}: No historical data available")
                        continue

                    if len(df) < 50:
                        logger.warning(f"{symbol}: Insufficient data (got {len(df)} bars)")
                        continue

                    # Calculate SMAs
                    sma_20 = calculate_sma(df['close'], 20)
                    sma_50 = calculate_sma(df['close'], 50)
                    current_price = df['close'].iloc[-1]

                    # Rate limiting - small delay between stocks to avoid overwhelming IBKR
                    ib.sleep(0.1)

                    # Check price range filter
                    if current_price < min_price or current_price > max_price:
                        logger.debug(
                            f"{symbol}: Price ${current_price:.2f} outside range "
                            f"${min_price:.0f}-${max_price:.0f}"
                        )
                        continue

                    # Check if SMAs can be calculated
                    if sma_20 is None or sma_50 is None:
                        logger.warning(f"{symbol}: Could not calculate SMAs")
                        continue

                    # Detect trend
                    trend_type, trend_strength = detect_trend(
                        current_price, sma_20, sma_50, cfg.SIDEWAYS_SMA_THRESHOLD
                    )
                    trend_counts[trend_type] += 1

                    # Check if this trend passes the filter
                    trend_passes = not filter_by_trend or trend_type in trend_filter

                    if trend_passes:
                        # Get market cap (optional, can be slow - disabled by default)
                        market_cap = None
                        if cfg.ENABLE_MARKET_CAP_CHECK and min_market_cap > 0:
                            market_cap = get_market_cap(ib, stock)
                            if market_cap is not None and market_cap < min_market_cap:
                                logger.debug(
                                    f"{symbol}: Market cap ${market_cap:,.0f} below minimum "
                                    f"${min_market_cap:,.0f}"
                                )
                                continue
                            elif market_cap is None:
                                # If we can't get market cap, we'll include the stock
                                # (most S&P 500 stocks will be above $500M anyway)
                                logger.debug(f"{symbol}: Could not verify market cap, including anyway")
                        else:
                            # Market cap check disabled - most S&P stocks are > $500M anyway
                            logger.debug(f"{symbol}: Market cap check disabled (using price filter only)")

                        stock_info = {
                            'symbol': symbol,
                            'price': round(current_price, 2),
                            'sma_20': round(sma_20, 2),
                            'sma_50': round(sma_50, 2),
                            'trend': trend_type,
                            'trend_strength': round(trend_strength, 2),
                            'uptrend_strength': round(trend_strength, 2),  # Backward compat
                            'market_cap': round(market_cap, 0) if market_cap else None,
                            'date': df['date'].iloc[-1]
                        }
                        matching_stocks.append(stock_info)

                        cap_str = f", Cap: ${market_cap:,.0f}" if market_cap else ""
                        logger.debug(
                            f"{symbol}: {trend_type.upper()} - Price: ${current_price:.2f}, "
                            f"SMA20: ${sma_20:.2f}, SMA50: ${sma_50:.2f}, "
                            f"Strength: {trend_strength:+.1f}%{cap_str}"
                        )
                    else:
                        logger.debug(
                            f"{symbol}: {trend_type.upper()} (filtered out) - "
                            f"Price: ${current_price:.2f}, "
                            f"SMA20: ${sma_20:.2f}, SMA50: ${sma_50:.2f}"
                        )

                except Exception as e:
                    logger.error(f"Error screening {symbol}: {e}")
                    continue

        finally:
            # Always finish progress bar, even if there's an exception
            progress.finish("Complete")
            logger.debug(f"Screening loop completed. Processed {len(symbols)} symbols.")

    # Show results summary
    print()  # Blank line after progress bar

    # Display trend distribution
    trend_summary = (
        f"Trends: {trend_counts['uptrend']} uptrend, "
        f"{trend_counts['downtrend']} downtrend, "
        f"{trend_counts['sideways']} sideways, "
        f"{trend_counts['unclear']} unclear"
    )
    StatusPrinter.info(trend_summary)

    if matching_stocks:
        # Create list of symbols for easy reference
        symbol_list = ', '.join([s['symbol'] for s in matching_stocks])
        StatusPrinter.success(
            f"Found {len(matching_stocks)} stocks matching filter ({trend_desc}): {symbol_list}"
        )
        print()
        for stock in matching_stocks:
            trend_emoji = {
                'uptrend': '↗',
                'downtrend': '↘',
                'sideways': '→',
                'unclear': '?'
            }.get(stock['trend'], '?')

            StatusPrinter.result(
                f"{stock['symbol']} {trend_emoji}",
                f"${stock['price']:.2f} ({stock['trend']}, "
                f"SMA20: ${stock['sma_20']:.2f}, SMA50: ${stock['sma_50']:.2f})"
            )
    else:
        StatusPrinter.warning(
            f"No stocks found matching filter ({trend_desc}) out of {len(symbols)}"
        )
        print()
        StatusPrinter.info("Suggestions:")
        StatusPrinter.info("  • Try a different stock index")
        StatusPrinter.info("  • Use --no-trend to see all stocks regardless of trend")
        StatusPrinter.info("  • Adjust price range in config/trading_config.py")

    logger.info(
        f"Found {len(matching_stocks)} stocks matching trend filter {trend_filter} "
        f"out of {len(symbols)} ({trend_summary})"
    )
    return matching_stocks


def get_sp500_symbols() -> List[str]:
    """
    Get a sample list of S&P 500 symbols for screening
    Focused on stocks typically in $50-$150 range with >$500M market cap
    In production, you'd fetch this from a data source
    """
    # Sample of liquid S&P 500 stocks in target price range
    # These typically trade between $50-$150 with large market caps
    return [
        # Tech & Communication
        'AMD', 'INTC', 'QCOM', 'CSCO', 'ORCL', 'CRM', 'NOW', 'ADBE',
        'IBM', 'TXN', 'AMAT', 'MU', 'AVGO', 'NFLX', 'T', 'VZ',

        # Financials
        'JPM', 'BAC', 'WFC', 'C', 'GS', 'MS', 'BLK', 'SCHW',
        'AXP', 'USB', 'PNC', 'TFC', 'COF', 'BK', 'STT',

        # Healthcare
        'JNJ', 'UNH', 'PFE', 'ABBV', 'MRK', 'BMY', 'AMGN', 'GILD',
        'CVS', 'CI', 'HUM', 'ELV', 'MDT', 'ABT', 'DHR',

        # Consumer
        'WMT', 'HD', 'NKE', 'SBUX', 'TGT', 'LOW', 'DIS', 'MCD',
        'PG', 'KO', 'PEP', 'COST', 'CMCSA', 'F', 'GM',

        # Industrials & Energy
        'BA', 'CAT', 'GE', 'HON', 'UPS', 'RTX', 'LMT', 'DE',
        'XOM', 'CVX', 'COP', 'SLB', 'EOG', 'PXD', 'OXY',

        # Other sectors
        'NEE', 'DUK', 'SO', 'AEP', 'EXC', 'SRE'
    ]


if __name__ == "__main__":
    # Test the screener
    logging.basicConfig(level=logging.INFO)
    symbols = get_sp500_symbols()
    results = screen_uptrend_stocks(symbols[:5])  # Test with first 5
    print(f"\nFound {len(results)} uptrend stocks:")
    for stock in results:
        print(f"  {stock}")
