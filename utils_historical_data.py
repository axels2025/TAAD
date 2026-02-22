"""
Historical Data Caching System
Manages stock price history with intelligent caching to minimize IBKR API calls
"""
import logging
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional, Tuple
from ib_insync import Stock, IB, BarDataList, util 
import time

logger = logging.getLogger(__name__)

# Directory for cached stock data
STOCKS_DIR = Path("data/stocks")


def ensure_stocks_directory():
    """Create stocks directory if it doesn't exist"""
    STOCKS_DIR.mkdir(parents=True, exist_ok=True)
    logger.debug(f"Ensured directory exists: {STOCKS_DIR}")


def get_cache_file_path(symbol: str) -> Path:
    """
    Get the cache file path for a stock symbol

    Args:
        symbol: Stock symbol (e.g., "AAPL")

    Returns:
        Path to cache file
    """
    ensure_stocks_directory()
    # Clean symbol (remove any special characters)
    clean_symbol = symbol.replace(".", "_").replace("/", "_").upper()
    return STOCKS_DIR / f"{clean_symbol}.csv"


def validate_cached_data(df: pd.DataFrame, symbol: str) -> bool:
    """
    Validate cached data format and content

    Args:
        df: DataFrame to validate
        symbol: Stock symbol for logging

    Returns:
        True if valid, False if corrupted
    """
    required_columns = ['date', 'open', 'high', 'low', 'close', 'volume']

    # Check columns
    if not all(col in df.columns for col in required_columns):
        logger.warning(f"{symbol}: Missing required columns in cache")
        return False

    # Check for empty data
    if df.empty:
        logger.warning(f"{symbol}: Cache file is empty")
        return False

    # Check date format
    try:
        pd.to_datetime(df['date'])
    except Exception as e:
        logger.warning(f"{symbol}: Invalid date format in cache: {e}")
        return False

    # Check for negative prices/volumes
    numeric_cols = ['open', 'high', 'low', 'close', 'volume']
    for col in numeric_cols:
        if (df[col] < 0).any():
            logger.warning(f"{symbol}: Negative values in {col}")
            return False

    # Check for NaN values
    if df[required_columns].isna().any().any():
        logger.warning(f"{symbol}: NaN values found in cache")
        return False

    logger.debug(f"{symbol}: Cache validation passed")
    return True


def load_cached_data(symbol: str) -> Optional[pd.DataFrame]:
    """
    Load cached historical data for a symbol

    Args:
        symbol: Stock symbol

    Returns:
        DataFrame with historical data or None if not cached/corrupted
    """
    cache_file = get_cache_file_path(symbol)

    if not cache_file.exists():
        logger.debug(f"{symbol}: No cache file found")
        return None

    try:
        df = pd.read_csv(cache_file)

        # Validate data
        if not validate_cached_data(df, symbol):
            logger.warning(f"{symbol}: Cache validation failed, will re-fetch")
            # Delete corrupted cache
            cache_file.unlink()
            return None

        # Convert date to datetime
        df['date'] = pd.to_datetime(df['date'])

        # Sort by date
        df = df.sort_values('date').reset_index(drop=True)

        # Remove duplicates (keep last occurrence)
        df = df.drop_duplicates(subset=['date'], keep='last')

        logger.info(f"{symbol}: Loaded {len(df)} bars from cache (oldest: {df['date'].min()}, newest: {df['date'].max()})")
        return df

    except Exception as e:
        logger.error(f"{symbol}: Error loading cache: {e}")
        # Delete corrupted cache
        if cache_file.exists():
            cache_file.unlink()
        return None


def save_cached_data(symbol: str, df: pd.DataFrame):
    """
    Save historical data to cache

    Args:
        symbol: Stock symbol
        df: DataFrame with historical data
    """
    cache_file = get_cache_file_path(symbol)

    try:
        # Ensure date is datetime
        df['date'] = pd.to_datetime(df['date'])

        # Sort by date
        df = df.sort_values('date').reset_index(drop=True)

        # Remove duplicates
        df = df.drop_duplicates(subset=['date'], keep='last')

        # Save to CSV
        df.to_csv(cache_file, index=False)

        logger.info(f"{symbol}: Saved {len(df)} bars to cache")

    except Exception as e:
        logger.error(f"{symbol}: Error saving cache: {e}")


def bars_to_dataframe(bars: BarDataList) -> pd.DataFrame:
    """
    Convert ib_insync BarDataList to pandas DataFrame

    Args:
        bars: BarDataList from IBKR

    Returns:
        DataFrame with columns: date, open, high, low, close, volume
    """
    if not bars:
        return pd.DataFrame(columns=['date', 'open', 'high', 'low', 'close', 'volume'])

    data = []
    for bar in bars:
        data.append({
            'date': bar.date,
            'open': bar.open,
            'high': bar.high,
            'low': bar.low,
            'close': bar.close,
            'volume': bar.volume
        })

    df = pd.DataFrame(data)
    df['date'] = pd.to_datetime(df['date'])
    return df


def calculate_missing_days(cached_df: pd.DataFrame, lookback_days: int) -> Tuple[datetime, int]:
    """
    Calculate how many days of data are missing

    Args:
        cached_df: Existing cached data
        lookback_days: Total lookback period needed

    Returns:
        Tuple of (start_date, days_to_fetch)
    """
    # Get most recent date in cache
    most_recent = cached_df['date'].max()

    # Get today (market close time)
    today = datetime.now().replace(hour=16, minute=0, second=0, microsecond=0)

    # Calculate days since last cached date
    days_since_last = (today - most_recent).days

    # If data is current (within 1 day), no fetch needed
    if days_since_last <= 1:
        logger.debug(f"Cache is current (last: {most_recent.date()})")
        return None, 0

    # Calculate how many days to fetch
    # Add a small buffer to ensure we don't miss any days
    days_to_fetch = min(days_since_last + 2, lookback_days)

    # Start from day after most recent cached date
    start_date = most_recent + timedelta(days=1)

    logger.info(f"Need to fetch {days_to_fetch} days from {start_date.date()} to {today.date()}")
    return start_date, days_to_fetch


def fetch_historical_data(
    ib: IB,
    stock: Stock,
    days: int,
    end_date: datetime = None
) -> Optional[pd.DataFrame]:
    """
    Fetch historical data from IBKR

    Args:
        ib: IB connection
        stock: Stock contract
        days: Number of days to fetch
        end_date: End date for data (default: now)

    Returns:
        DataFrame with historical data or None on error
    """
    if end_date is None:
        end_date = datetime.now()

    try:
        logger.debug(f"{stock.symbol}: Fetching {days} days from IBKR (end: {end_date.date()})")

        bars = util.run(
            ib.reqHistoricalDataAsync(
                stock,
                endDateTime=end_date,
                durationStr=f'{days} D',
                barSizeSetting='1 day',
                whatToShow='TRADES',
                useRTH=True,
                formatDate=1
            )
        )

        if not bars:
            logger.warning(f"{stock.symbol}: No data returned from IBKR")
            return None

        df = bars_to_dataframe(bars)
        logger.info(f"{stock.symbol}: Fetched {len(df)} bars from IBKR")
        return df

    except Exception as e:
        logger.error(f"{stock.symbol}: Error fetching from IBKR: {e}")
        return None


def merge_historical_data(cached_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge cached data with newly fetched data

    Args:
        cached_df: Existing cached data
        new_df: Newly fetched data

    Returns:
        Merged DataFrame with duplicates removed
    """
    # Concatenate
    merged = pd.concat([cached_df, new_df], ignore_index=True)

    # Ensure date is datetime
    merged['date'] = pd.to_datetime(merged['date'])

    # Sort by date
    merged = merged.sort_values('date').reset_index(drop=True)

    # Remove duplicates (keep last occurrence - newer data is more accurate)
    merged = merged.drop_duplicates(subset=['date'], keep='last')

    logger.debug(f"Merged data: {len(cached_df)} cached + {len(new_df)} new = {len(merged)} total")
    return merged


def get_historical_data_with_cache(
    ib: IB,
    stock: Stock,
    lookback_days: int = 100
) -> Optional[pd.DataFrame]:
    """
    Get historical data with intelligent caching

    This is the main function to use for fetching historical data.
    It handles all caching logic automatically.

    Workflow:
    1. Check if cache exists
    2. If exists and current, return cached data
    3. If exists but outdated, fetch only missing days and merge
    4. If doesn't exist, fetch full lookback period and cache
    5. Handle all edge cases and errors

    Args:
        ib: IB connection
        stock: Stock contract
        lookback_days: Number of days to look back (default: 100)

    Returns:
        DataFrame with historical data or None on error
    """
    symbol = stock.symbol

    # Load cached data
    cached_df = load_cached_data(symbol)

    if cached_df is not None:
        # Cache exists and is valid
        logger.debug(f"{symbol}: Found valid cache with {len(cached_df)} bars")

        # Check if we need to fetch more data
        start_date, days_to_fetch = calculate_missing_days(cached_df, lookback_days)

        if days_to_fetch == 0:
            # Cache is current, return it
            logger.info(f"{symbol}: Using cached data (current)")
            return cached_df

        # Fetch only missing days
        logger.info(f"{symbol}: Fetching {days_to_fetch} missing days")
        new_df = fetch_historical_data(ib, stock, days_to_fetch)

        if new_df is None or new_df.empty:
            # Fetch failed, return cached data (still valid)
            logger.warning(f"{symbol}: Fetch failed, using existing cache")
            return cached_df

        # Merge cached and new data
        merged_df = merge_historical_data(cached_df, new_df)

        # Save merged data
        save_cached_data(symbol, merged_df)

        logger.info(f"{symbol}: Updated cache with {len(new_df)} new bars (total: {len(merged_df)})")
        return merged_df

    else:
        # No cache exists, fetch full lookback period
        logger.info(f"{symbol}: No cache, fetching {lookback_days} days")
        df = fetch_historical_data(ib, stock, lookback_days)

        if df is None or df.empty:
            logger.error(f"{symbol}: Failed to fetch historical data")
            return None

        # Save to cache
        save_cached_data(symbol, df)

        logger.info(f"{symbol}: Created cache with {len(df)} bars")
        return df


def clear_cache_for_symbol(symbol: str) -> bool:
    """
    Clear cached data for a specific symbol

    Args:
        symbol: Stock symbol

    Returns:
        True if cache was cleared, False if no cache existed
    """
    cache_file = get_cache_file_path(symbol)

    if cache_file.exists():
        cache_file.unlink()
        logger.info(f"{symbol}: Cache cleared")
        return True
    else:
        logger.debug(f"{symbol}: No cache to clear")
        return False


def get_cache_stats() -> dict:
    """
    Get statistics about the cache

    Returns:
        Dict with cache statistics
    """
    ensure_stocks_directory()

    cache_files = list(STOCKS_DIR.glob("*.csv"))
    total_files = len(cache_files)

    if total_files == 0:
        return {
            "total_symbols": 0,
            "total_size_mb": 0,
            "oldest_cache": None,
            "newest_cache": None
        }

    total_size = sum(f.stat().st_size for f in cache_files)
    total_size_mb = total_size / (1024 * 1024)

    # Get oldest and newest cache files by modification time
    cache_files_sorted = sorted(cache_files, key=lambda f: f.stat().st_mtime)
    oldest = cache_files_sorted[0].stem if cache_files_sorted else None
    newest = cache_files_sorted[-1].stem if cache_files_sorted else None

    return {
        "total_symbols": total_files,
        "total_size_mb": round(total_size_mb, 2),
        "oldest_cache": oldest,
        "newest_cache": newest,
        "cache_directory": str(STOCKS_DIR)
    }


def print_cache_stats():
    """Print cache statistics to console"""
    stats = get_cache_stats()

    print("\n" + "=" * 70)
    print("HISTORICAL DATA CACHE STATISTICS")
    print("=" * 70)
    print(f"Total symbols cached:  {stats['total_symbols']}")
    print(f"Total cache size:      {stats['total_size_mb']:.2f} MB")
    print(f"Cache directory:       {stats['cache_directory']}")

    if stats['total_symbols'] > 0:
        print(f"Oldest cache:          {stats['oldest_cache']}")
        print(f"Newest cache:          {stats['newest_cache']}")

    print("=" * 70 + "\n")


if __name__ == "__main__":
    # Test cache functionality
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    print_cache_stats()

    # Example: Test with a stock
    from config.ibkr_connection import create_ibkr_connection

    print("\nTesting cache with AAPL...")

    with create_ibkr_connection() as ib:
        stock = Stock('AAPL', 'SMART', 'USD')
        ib.qualifyContracts(stock)

        # First fetch (will create cache)
        print("\n1. First fetch (no cache):")
        df1 = get_historical_data_with_cache(ib, stock, lookback_days=100)
        if df1 is not None:
            print(f"   Got {len(df1)} bars")
            print(f"   Date range: {df1['date'].min()} to {df1['date'].max()}")

        # Second fetch (should use cache)
        print("\n2. Second fetch (should use cache):")
        df2 = get_historical_data_with_cache(ib, stock, lookback_days=100)
        if df2 is not None:
            print(f"   Got {len(df2)} bars")
            print(f"   Date range: {df2['date'].min()} to {df2['date'].max()}")

    print_cache_stats()
