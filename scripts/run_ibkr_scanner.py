#!/usr/bin/env python3
"""Run an IBKR Market Scanner and display results.

Standalone script to test reqScannerSubscription() with various scan types
and filters. Returns matching stocks with key data fields.

IMPORTANT: Additional filters (market cap, volume, option volume, stock type)
must be passed via scannerSubscriptionFilterOptions (TagValue pairs), NOT via
the native ScannerSubscription fields. The native fields use different units
and don't work correctly. The XML filter tags use market cap in millions,
e.g., marketCapAbove1e6=2000 means market cap >= $2B.

Usage:
    python scripts/run_ibkr_scanner.py
    python scripts/run_ibkr_scanner.py --scan HIGH_OPT_IMP_VOLAT_OVER_HIST
    python scripts/run_ibkr_scanner.py --scan HOT_BY_OPT_VOLUME --min-price 50 --max-price 500
    python scripts/run_ibkr_scanner.py --rows 50

    # With quality filters (recommended for naked put candidates):
    python scripts/run_ibkr_scanner.py --market-cap-above 2000 --avg-volume-above 500000 --avg-opt-volume-above 1000
    python scripts/run_ibkr_scanner.py --scan HIGH_OPT_IMP_VOLAT_OVER_HIST --market-cap-above 5000

    # Presets:
    python scripts/run_ibkr_scanner.py --preset naked-put
    python scripts/run_ibkr_scanner.py --preset iv-over-hist
    python scripts/run_ibkr_scanner.py --preset hot-options
"""

import argparse
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ib_insync import IB, ScannerSubscription, TagValue, util


# Preset filter configurations for common use cases
# Note: market_cap_above is in MILLIONS (XML tag unit), so 2000 = $2B
PRESETS = {
    "naked-put": {
        "scan": "HIGH_OPT_IMP_VOLAT",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 200.0,
        "rows": 50,
        "market_cap_above": 2000,       # $2B+ (mid-cap and above)
        "avg_volume_above": 500000,     # 500K+ avg daily volume
        "avg_opt_volume_above": 1000,   # 1K+ avg option volume
    },
    "iv-over-hist": {
        "scan": "HIGH_OPT_IMP_VOLAT_OVER_HIST",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 300.0,
        "rows": 50,
        "market_cap_above": 5000,       # $5B+ (large-cap)
        "avg_volume_above": 1000000,    # 1M+ avg daily volume
        "avg_opt_volume_above": 5000,   # 5K+ avg option volume
    },
    "hot-options": {
        "scan": "HOT_BY_OPT_VOLUME",
        "instrument": "STK",
        "location": "STK.US.MAJOR",
        "min_price": 20.0,
        "max_price": 500.0,
        "rows": 50,
        "market_cap_above": 5000,       # $5B+
        "avg_volume_above": 1000000,    # 1M+
        "avg_opt_volume_above": 5000,   # 5K+
    },
}


def connect_ibkr(client_id: int = 21) -> IB:
    """Connect to IBKR."""
    util.patchAsyncio()

    ib = IB()
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7497"))

    print(f"Connecting to IBKR at {host}:{port} (client_id={client_id})...")
    ib.connect(host, port, clientId=client_id, timeout=10)
    print("Connected.\n")
    return ib


def run_scanner(
    ib: IB,
    scan_code: str = "HIGH_OPT_IMP_VOLAT",
    instrument: str = "STK",
    location: str = "STK.US.MAJOR",
    min_price: float = 20.0,
    max_price: float = 200.0,
    num_rows: int = 50,
    market_cap_above: float = 0,
    market_cap_below: float = 0,
    avg_volume_above: int = 0,
    avg_opt_volume_above: int = 0,
    stock_type: str = "",
) -> list:
    """Run a market scanner and return results.

    Args:
        ib: Connected IB instance
        scan_code: Scanner type code
        instrument: Instrument type (STK, ETF.EQ.US, etc.)
        location: Location code (STK.US.MAJOR, STK.US, etc.)
        min_price: Minimum stock price filter
        max_price: Maximum stock price filter
        num_rows: Maximum number of results to return
        market_cap_above: Min market cap in MILLIONS (e.g., 2000 = $2B)
        market_cap_below: Max market cap in MILLIONS (0 = no limit)
        avg_volume_above: Min average daily volume
        avg_opt_volume_above: Min average option volume
        stock_type: Stock type filter (CORP, ADR, ETF, etc.)

    Returns:
        List of ScanData objects
    """
    sub = ScannerSubscription(
        instrument=instrument,
        locationCode=location,
        scanCode=scan_code,
        abovePrice=min_price,
        belowPrice=max_price,
        numberOfRows=num_rows,
    )

    # Build TagValue filter options (these work correctly unlike native fields)
    filter_options = []
    if market_cap_above > 0:
        filter_options.append(TagValue("marketCapAbove1e6", str(market_cap_above)))
    if market_cap_below > 0:
        filter_options.append(TagValue("marketCapBelow1e6", str(market_cap_below)))
    if avg_volume_above > 0:
        filter_options.append(TagValue("avgVolumeAbove", str(avg_volume_above)))
    if avg_opt_volume_above > 0:
        filter_options.append(TagValue("avgOptVolumeAbove", str(avg_opt_volume_above)))
    if stock_type:
        filter_options.append(TagValue("stkTypes", stock_type))

    print(f"Running scanner: {scan_code}")
    print(f"  Instrument:    {instrument}")
    print(f"  Location:      {location}")
    print(f"  Price:         ${min_price:.0f} - ${max_price:.0f}")
    print(f"  Max rows:      {num_rows}")
    if market_cap_above > 0:
        cap_display = f"${market_cap_above/1000:.1f}B" if market_cap_above >= 1000 else f"${market_cap_above:.0f}M"
        print(f"  Market cap:    >= {cap_display}")
    if market_cap_below > 0:
        cap_display = f"${market_cap_below/1000:.1f}B" if market_cap_below >= 1000 else f"${market_cap_below:.0f}M"
        print(f"  Market cap:    <= {cap_display}")
    if avg_volume_above > 0:
        print(f"  Avg volume:    >= {avg_volume_above:,}")
    if avg_opt_volume_above > 0:
        print(f"  Avg opt vol:   >= {avg_opt_volume_above:,}")
    if stock_type:
        print(f"  Stock type:    {stock_type}")
    if filter_options:
        print(f"  Filters:       {len(filter_options)} TagValue filter(s)")
    print()

    results = ib.reqScannerData(
        sub,
        scannerSubscriptionFilterOptions=filter_options,
    )

    print(f"Scanner returned {len(results)} results.\n")
    return results


def display_results(results: list) -> None:
    """Display scanner results in a formatted table."""
    if not results:
        print("No results returned.")
        return

    # Header
    print(
        f"{'#':<4} {'Symbol':<8} {'SecType':<8} {'Exchange':<10} {'ConId':<12} "
        f"{'Rank':<6} {'Distance':<10} {'Benchmark':<10} {'Value':<12} {'Legs':<6}"
    )
    print("-" * 96)

    for i, item in enumerate(results):
        contract = item.contractDetails.contract
        symbol = contract.symbol
        sec_type = contract.secType
        exchange = contract.primaryExchange or contract.exchange
        con_id = contract.conId
        rank = item.rank
        distance = item.distance if item.distance else ""
        benchmark = item.benchmark if item.benchmark else ""
        value = item.projection if item.projection else ""
        legs = item.legsStr if item.legsStr else ""

        print(
            f"{i+1:<4} {symbol:<8} {sec_type:<8} {exchange:<10} {con_id:<12} "
            f"{rank:<6} {distance:<10} {benchmark:<10} {value:<12} {legs:<6}"
        )

    # Also print just the symbols for easy copy/paste
    symbols = [item.contractDetails.contract.symbol for item in results]
    print(f"\nSymbols ({len(symbols)}): {', '.join(symbols)}")


def display_contract_details(results: list) -> None:
    """Display extended contract details for each result."""
    if not results:
        return

    print(f"\n{'=' * 80}")
    print("  EXTENDED CONTRACT DETAILS")
    print(f"{'=' * 80}\n")

    for i, item in enumerate(results):
        cd = item.contractDetails
        c = cd.contract

        print(f"  #{i+1} {c.symbol}")
        print(f"    Long name:    {cd.longName}")
        print(f"    Industry:     {cd.industry}")
        print(f"    Category:     {cd.category}")
        print(f"    Subcategory:  {cd.subcategory}")
        print(f"    Exchange:     {c.primaryExchange or c.exchange}")
        print(f"    ConId:        {c.conId}")
        print(f"    Market cap:   {cd.stockType if cd.stockType else '?'}")
        print()


def main():
    parser = argparse.ArgumentParser(
        description="Run IBKR Market Scanner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Presets:
  naked-put     HIGH_OPT_IMP_VOLAT with quality filters ($2B+ cap, 500K+ vol)
  iv-over-hist  HIGH_OPT_IMP_VOLAT_OVER_HIST with strict filters ($5B+ cap, 1M+ vol)
  hot-options   HOT_BY_OPT_VOLUME with strict filters ($5B+ cap, 1M+ vol)

Note: --market-cap-above/below values are in MILLIONS (e.g., 2000 = $2B)

Examples:
  %(prog)s --preset naked-put
  %(prog)s --preset naked-put --rows 25 --min-price 30
  %(prog)s --scan HIGH_OPT_IMP_VOLAT --market-cap-above 2000 --avg-opt-volume-above 1000
  %(prog)s --scan HOT_BY_OPT_VOLUME --avg-volume-above 1000000
        """,
    )
    parser.add_argument(
        "--preset", choices=list(PRESETS.keys()),
        help="Use a preset configuration (overrides individual options unless explicitly set)"
    )
    parser.add_argument(
        "--scan", default=None,
        help="Scan code (default: HIGH_OPT_IMP_VOLAT)"
    )
    parser.add_argument(
        "--instrument", default=None,
        help="Instrument type (default: STK)"
    )
    parser.add_argument(
        "--location", default=None,
        help="Location code (default: STK.US.MAJOR)"
    )
    parser.add_argument(
        "--min-price", type=float, default=None,
        help="Minimum stock price (default: 20)"
    )
    parser.add_argument(
        "--max-price", type=float, default=None,
        help="Maximum stock price (default: 200)"
    )
    parser.add_argument(
        "--rows", type=int, default=None,
        help="Maximum results (default: 50)"
    )
    parser.add_argument(
        "--market-cap-above", type=float, default=None,
        help="Min market cap in MILLIONS (e.g., 2000 for $2B)"
    )
    parser.add_argument(
        "--market-cap-below", type=float, default=None,
        help="Max market cap in MILLIONS"
    )
    parser.add_argument(
        "--avg-volume-above", type=int, default=None,
        help="Min average daily stock volume"
    )
    parser.add_argument(
        "--avg-opt-volume-above", type=int, default=None,
        help="Min average daily option volume"
    )
    parser.add_argument(
        "--stock-type", default=None,
        help="Stock type filter (CORP, ADR, ETF, REIT, CEF)"
    )
    parser.add_argument(
        "--details", action="store_true",
        help="Show extended contract details (industry, category)"
    )
    parser.add_argument(
        "--client-id", type=int, default=21,
        help="IBKR client ID (default: 21)"
    )
    args = parser.parse_args()

    # Start with preset defaults if specified
    if args.preset:
        preset = PRESETS[args.preset]
    else:
        preset = {}

    # Merge: explicit CLI args override preset, which overrides script defaults
    scan_code = args.scan or preset.get("scan", "HIGH_OPT_IMP_VOLAT")
    instrument = args.instrument or preset.get("instrument", "STK")
    location = args.location or preset.get("location", "STK.US.MAJOR")
    min_price = args.min_price if args.min_price is not None else preset.get("min_price", 20.0)
    max_price = args.max_price if args.max_price is not None else preset.get("max_price", 200.0)
    rows = args.rows if args.rows is not None else preset.get("rows", 50)
    market_cap_above = args.market_cap_above if args.market_cap_above is not None else preset.get("market_cap_above", 0)
    market_cap_below = args.market_cap_below if args.market_cap_below is not None else preset.get("market_cap_below", 0)
    avg_volume_above = args.avg_volume_above if args.avg_volume_above is not None else preset.get("avg_volume_above", 0)
    avg_opt_volume_above = args.avg_opt_volume_above if args.avg_opt_volume_above is not None else preset.get("avg_opt_volume_above", 0)
    stock_type = args.stock_type or preset.get("stock_type", "")

    ib = connect_ibkr(args.client_id)

    try:
        results = run_scanner(
            ib,
            scan_code=scan_code,
            instrument=instrument,
            location=location,
            min_price=min_price,
            max_price=max_price,
            num_rows=rows,
            market_cap_above=market_cap_above,
            market_cap_below=market_cap_below,
            avg_volume_above=avg_volume_above,
            avg_opt_volume_above=avg_opt_volume_above,
            stock_type=stock_type,
        )

        display_results(results)

        if args.details:
            display_contract_details(results)

    finally:
        ib.disconnect()
        print("\nDisconnected from IBKR.")


if __name__ == "__main__":
    main()
