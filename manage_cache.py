#!/usr/bin/env python3
"""
Cache Management Utility
Provides tools to view, analyze, and manage the historical data cache
"""
import sys
import argparse
from pathlib import Path
import pandas as pd
from datetime import datetime, timedelta
from utils_historical_data import (
    get_cache_stats,
    print_cache_stats,
    get_cache_file_path,
    clear_cache_for_symbol,
    load_cached_data,
    STOCKS_DIR
)


def list_cached_symbols():
    """List all cached symbols with details"""
    cache_files = sorted(STOCKS_DIR.glob("*.csv"))

    if not cache_files:
        print("\nNo cached symbols found.")
        return

    print("\n" + "=" * 90)
    print(f"{'Symbol':<10} {'Bars':<8} {'Oldest Date':<12} {'Newest Date':<12} {'Size (KB)':<10}")
    print("=" * 90)

    for cache_file in cache_files:
        symbol = cache_file.stem
        try:
            df = pd.read_csv(cache_file)
            df['date'] = pd.to_datetime(df['date'])

            bars = len(df)
            oldest = df['date'].min().strftime('%Y-%m-%d')
            newest = df['date'].max().strftime('%Y-%m-%d')
            size_kb = cache_file.stat().st_size / 1024

            print(f"{symbol:<10} {bars:<8} {oldest:<12} {newest:<12} {size_kb:<10.2f}")

        except Exception as e:
            print(f"{symbol:<10} ERROR: {e}")

    print("=" * 90 + "\n")


def view_cache_details(symbol: str):
    """View detailed information about a cached symbol"""
    df = load_cached_data(symbol)

    if df is None:
        print(f"\nNo cache found for {symbol}")
        return

    cache_file = get_cache_file_path(symbol)
    file_size_kb = cache_file.stat().st_size / 1024

    print("\n" + "=" * 70)
    print(f"CACHE DETAILS: {symbol}")
    print("=" * 70)
    print(f"File:           {cache_file}")
    print(f"Size:           {file_size_kb:.2f} KB")
    print(f"Total bars:     {len(df)}")
    print(f"Oldest date:    {df['date'].min().strftime('%Y-%m-%d')}")
    print(f"Newest date:    {df['date'].max().strftime('%Y-%m-%d')}")
    print(f"Days covered:   {(df['date'].max() - df['date'].min()).days}")

    # Calculate data freshness
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    newest_date = pd.to_datetime(df['date'].max())
    days_old = (today - newest_date).days

    print(f"Data freshness: {days_old} days old")

    # Price statistics
    print(f"\nPrice statistics:")
    print(f"  Current close: ${df['close'].iloc[-1]:.2f}")
    print(f"  Min close:     ${df['close'].min():.2f}")
    print(f"  Max close:     ${df['close'].max():.2f}")
    print(f"  Avg close:     ${df['close'].mean():.2f}")

    # Recent data
    print(f"\nMost recent 5 bars:")
    print(df[['date', 'open', 'high', 'low', 'close', 'volume']].tail().to_string(index=False))
    print("=" * 70 + "\n")


def find_stale_cache(days: int = 7):
    """Find cached symbols with data older than N days"""
    cache_files = sorted(STOCKS_DIR.glob("*.csv"))
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    stale_symbols = []

    print(f"\nSearching for cache files older than {days} days...")

    for cache_file in cache_files:
        symbol = cache_file.stem
        try:
            df = pd.read_csv(cache_file)
            df['date'] = pd.to_datetime(df['date'])

            newest_date = df['date'].max()
            days_old = (today - newest_date).days

            if days_old > days:
                stale_symbols.append({
                    'symbol': symbol,
                    'days_old': days_old,
                    'newest_date': newest_date.strftime('%Y-%m-%d')
                })

        except Exception:
            continue

    if not stale_symbols:
        print(f"\n✓ All cache files are current (within {days} days)")
        return

    print("\n" + "=" * 50)
    print(f"{'Symbol':<10} {'Days Old':<10} {'Newest Date':<15}")
    print("=" * 50)

    for item in stale_symbols:
        print(f"{item['symbol']:<10} {item['days_old']:<10} {item['newest_date']:<15}")

    print("=" * 50)
    print(f"\nFound {len(stale_symbols)} stale cache files")


def clear_all_cache():
    """Clear all cached data"""
    cache_files = list(STOCKS_DIR.glob("*.csv"))

    if not cache_files:
        print("\nNo cache files to clear.")
        return

    print(f"\nFound {len(cache_files)} cache files.")
    response = input("Are you sure you want to delete all cache files? (yes/no): ")

    if response.lower() != 'yes':
        print("Cancelled.")
        return

    for cache_file in cache_files:
        cache_file.unlink()

    print(f"\n✓ Cleared {len(cache_files)} cache files")


def export_cache_to_excel(symbol: str, output_file: str = None):
    """Export cached data to Excel file"""
    df = load_cached_data(symbol)

    if df is None:
        print(f"\nNo cache found for {symbol}")
        return

    if output_file is None:
        output_file = f"{symbol}_historical_data.xlsx"

    try:
        df.to_excel(output_file, index=False, sheet_name=symbol)
        print(f"\n✓ Exported {len(df)} bars to {output_file}")
    except Exception as e:
        print(f"\n✗ Error exporting to Excel: {e}")
        print(f"  Try installing openpyxl: pip install openpyxl")


def main():
    """Main CLI interface"""
    parser = argparse.ArgumentParser(
        description='Historical Data Cache Management',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Show cache statistics
  python3 manage_cache.py stats

  # List all cached symbols
  python3 manage_cache.py list

  # View details for a symbol
  python3 manage_cache.py view AAPL

  # Find stale cache (older than 7 days)
  python3 manage_cache.py stale

  # Clear cache for a symbol
  python3 manage_cache.py clear AAPL

  # Clear all cache
  python3 manage_cache.py clear-all

  # Export to Excel
  python3 manage_cache.py export AAPL
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Command to execute')

    # Stats command
    subparsers.add_parser('stats', help='Show cache statistics')

    # List command
    subparsers.add_parser('list', help='List all cached symbols')

    # View command
    view_parser = subparsers.add_parser('view', help='View details for a symbol')
    view_parser.add_argument('symbol', help='Stock symbol')

    # Stale command
    stale_parser = subparsers.add_parser('stale', help='Find stale cache files')
    stale_parser.add_argument('--days', type=int, default=7, help='Days threshold (default: 7)')

    # Clear command
    clear_parser = subparsers.add_parser('clear', help='Clear cache for a symbol')
    clear_parser.add_argument('symbol', help='Stock symbol')

    # Clear all command
    subparsers.add_parser('clear-all', help='Clear all cached data')

    # Export command
    export_parser = subparsers.add_parser('export', help='Export cache to Excel')
    export_parser.add_argument('symbol', help='Stock symbol')
    export_parser.add_argument('--output', help='Output filename (default: {SYMBOL}_historical_data.xlsx)')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 0

    # Execute command
    if args.command == 'stats':
        print_cache_stats()

    elif args.command == 'list':
        list_cached_symbols()

    elif args.command == 'view':
        view_cache_details(args.symbol)

    elif args.command == 'stale':
        find_stale_cache(args.days)

    elif args.command == 'clear':
        if clear_cache_for_symbol(args.symbol):
            print(f"\n✓ Cleared cache for {args.symbol}")
        else:
            print(f"\n✗ No cache found for {args.symbol}")

    elif args.command == 'clear-all':
        clear_all_cache()

    elif args.command == 'export':
        export_cache_to_excel(args.symbol, args.output)

    return 0


if __name__ == "__main__":
    sys.exit(main())
