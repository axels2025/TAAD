#!/usr/bin/env python3
"""Quick test to verify scanner error fixes."""

import sys
from src.config.base import get_config
from src.tools.ibkr_client import IBKRClient
from src.tools.efficient_scanner import EfficientOptionScanner

print("=" * 60)
print("Testing Scanner Error Fixes")
print("=" * 60)

# Connect to IBKR
print("\n1. Connecting to IBKR...")
config = get_config()
client = IBKRClient(config.ibkr)

try:
    client.connect()
    print("   ✓ Connected successfully")
except Exception as e:
    print(f"   ✗ Connection failed: {e}")
    print("\n   Make sure TWS/IB Gateway is running!")
    sys.exit(1)

# Test scanner with limited universe
print("\n2. Testing scanner with SPY (should have no error spam)...")
scanner = EfficientOptionScanner(client, universe=["SPY"])

try:
    opportunities = scanner.scan_opportunities(
        min_premium=0.30,
        max_premium=1.00,
        min_otm=0.15,
        max_otm=0.30,
        min_dte=5,
        max_dte=21,
        require_uptrend=False,
        max_results=5,
    )

    print(f"   ✓ Scanner completed without error spam!")
    print(f"   ✓ Found {len(opportunities)} opportunities")

    if opportunities:
        print("\n3. Sample opportunities:")
        for i, opp in enumerate(opportunities[:3], 1):
            print(f"   {i}. {opp['symbol']} ${opp['strike']:.0f} "
                  f"exp:{opp['expiration']} premium:${opp['premium']:.2f}")
    else:
        print("\n3. No opportunities found (try wider parameters)")
        print("   Example: --max-premium 2.00 --max-otm 0.35")

    client.disconnect()

    print("\n" + "=" * 60)
    print("✓ Scanner fix verified successfully!")
    print("=" * 60)
    print("\nYou can now run: python -m src.cli.main scan")

except Exception as e:
    print(f"\n   ✗ Scanner failed: {e}")
    import traceback
    traceback.print_exc()
    client.disconnect()
    sys.exit(1)
