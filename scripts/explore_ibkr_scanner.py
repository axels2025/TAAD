#!/usr/bin/env python3
"""Explore IBKR Market Scanner API parameters.

Standalone script to discover what scan types, instruments, and filters
are available via reqScannerParameters(). Outputs a summary of useful
scanner types for finding naked put candidates.

Usage:
    python scripts/explore_ibkr_scanner.py
    python scripts/explore_ibkr_scanner.py --full-xml    # Save full XML to file
"""

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from ib_insync import IB, util


def connect_ibkr(client_id: int = 20) -> IB:
    """Connect to IBKR with a unique client ID for exploration."""
    util.patchAsyncio()

    ib = IB()
    host = os.getenv("IBKR_HOST", "127.0.0.1")
    port = int(os.getenv("IBKR_PORT", "7497"))

    print(f"Connecting to IBKR at {host}:{port} (client_id={client_id})...")
    ib.connect(host, port, clientId=client_id, timeout=10)
    print("Connected.\n")
    return ib


def get_scanner_parameters(ib: IB) -> str:
    """Fetch the full scanner parameters XML from IBKR."""
    print("Fetching scanner parameters (this may take a moment)...")
    xml_str = ib.reqScannerParameters()
    print(f"Received {len(xml_str):,} bytes of XML.\n")
    return xml_str


def parse_scan_types(root: ET.Element) -> list[dict]:
    """Extract all available scan types."""
    scan_types = []
    for scan_type in root.iter("ScanType"):
        code = scan_type.findtext("scanCode", "")
        display = scan_type.findtext("displayName", "")
        instruments = scan_type.findtext("instruments", "")
        scan_types.append({
            "code": code,
            "display": display,
            "instruments": instruments,
        })
    return scan_types


def parse_instruments(root: ET.Element) -> list[dict]:
    """Extract all available instrument types."""
    instruments = []
    for inst in root.iter("Instrument"):
        inst_type = inst.findtext("type", "")
        name = inst.findtext("name", "")
        sec_type = inst.findtext("secType", "")
        instruments.append({
            "type": inst_type,
            "name": name,
            "sec_type": sec_type,
        })
    return instruments


def parse_filters(root: ET.Element) -> list[dict]:
    """Extract all available filter parameters."""
    filters = []
    for filt in root.iter("AbstractField"):
        code = filt.findtext("code", "")
        display = filt.findtext("displayName", "")
        ftype = filt.findtext("type", "")
        filters.append({
            "code": code,
            "display": display,
            "type": ftype,
        })
    return filters


def parse_locations(root: ET.Element) -> list[dict]:
    """Extract available location codes (exchanges/regions)."""
    locations = []
    for loc in root.iter("Location"):
        code = loc.findtext("locationCode", "")
        display = loc.findtext("displayName", "")
        locations.append({
            "code": code,
            "display": display,
        })
    return locations


def print_section(title: str, items: list[dict], key_field: str, detail_field: str, max_items: int = 0):
    """Print a formatted section of results."""
    print(f"\n{'=' * 70}")
    print(f"  {title} ({len(items)} total)")
    print(f"{'=' * 70}")

    display_items = items if max_items == 0 else items[:max_items]
    for item in display_items:
        key = item.get(key_field, "?")
        detail = item.get(detail_field, "")
        extra = ""
        if "instruments" in item and item["instruments"]:
            extra = f"  [{item['instruments']}]"
        print(f"  {key:<45} {detail}{extra}")

    if max_items and len(items) > max_items:
        print(f"  ... and {len(items) - max_items} more")


def print_option_relevant_scans(scan_types: list[dict]):
    """Highlight scan types most relevant for finding put candidates."""
    keywords = [
        "opt", "vol", "imp", "put", "call", "dividend", "trade_rate",
        "volume_rate", "hot", "price_range", "yield",
    ]

    relevant = []
    for st in scan_types:
        code_lower = st["code"].lower()
        display_lower = st["display"].lower()
        if any(kw in code_lower or kw in display_lower for kw in keywords):
            relevant.append(st)

    print(f"\n{'=' * 70}")
    print(f"  OPTIONS-RELEVANT SCAN TYPES ({len(relevant)} found)")
    print(f"{'=' * 70}")
    for st in relevant:
        instruments = st.get("instruments", "")
        print(f"  {st['code']:<45} {st['display']}")
        if instruments:
            print(f"    {'instruments:':<45} {instruments}")


def main():
    parser = argparse.ArgumentParser(description="Explore IBKR Scanner API parameters")
    parser.add_argument(
        "--full-xml", action="store_true",
        help="Save full XML to data/cache/ibkr_scanner_params.xml"
    )
    parser.add_argument(
        "--client-id", type=int, default=20,
        help="IBKR client ID (default: 20)"
    )
    args = parser.parse_args()

    ib = connect_ibkr(args.client_id)

    try:
        xml_str = get_scanner_parameters(ib)

        # Save full XML if requested
        if args.full_xml:
            out_path = Path("data/cache/ibkr_scanner_params.xml")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(xml_str)
            print(f"Full XML saved to {out_path}\n")

        # Parse XML
        root = ET.fromstring(xml_str)

        # Extract components
        scan_types = parse_scan_types(root)
        instruments = parse_instruments(root)
        filters = parse_filters(root)
        locations = parse_locations(root)

        # Print summary
        print_section("SCAN TYPES", scan_types, "code", "display")
        print_section("INSTRUMENTS", instruments, "type", "name")
        print_section("FILTERS", filters, "code", "display")
        print_section("LOCATIONS (exchanges/regions)", locations, "code", "display", max_items=30)

        # Highlight options-relevant scans
        print_option_relevant_scans(scan_types)

        print(f"\n{'=' * 70}")
        print(f"  SUMMARY")
        print(f"{'=' * 70}")
        print(f"  Scan types:  {len(scan_types)}")
        print(f"  Instruments: {len(instruments)}")
        print(f"  Filters:     {len(filters)}")
        print(f"  Locations:   {len(locations)}")
        print(f"\nTip: Run with --full-xml to save the complete XML for detailed inspection.")

    finally:
        ib.disconnect()
        print("\nDisconnected from IBKR.")


if __name__ == "__main__":
    main()
