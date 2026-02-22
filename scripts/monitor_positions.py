"""Monitor open positions in real-time.

This script demonstrates the PositionMonitor tracking Order ID 9
and any other open positions.
"""

import os
import sys
from pathlib import Path
from time import sleep

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Ensure paper trading mode
os.environ["PAPER_TRADING"] = "true"
os.environ["IBKR_PORT"] = "7497"

from loguru import logger

from src.config.base import Config, IBKRConfig
from src.config.baseline_strategy import get_baseline_strategy
from src.execution.position_monitor import PositionMonitor
from src.tools.ibkr_client import IBKRClient

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
)


def display_position(status):
    """Display position status in readable format.

    Args:
        status: PositionStatus object
    """
    print()
    print("=" * 70)
    print(f"POSITION: {status.symbol} ${status.strike} {status.option_type}")
    print("=" * 70)
    print(f"  Position ID: {status.position_id}")
    print(f"  Contracts: {status.contracts}")
    print()
    print("PRICING:")
    print(f"  Entry Premium: ${status.entry_premium:.2f}")
    print(f"  Current Premium: ${status.current_premium:.2f}")
    print()
    print("P&L:")
    print(f"  Unrealized P&L: ${status.current_pnl:+.2f}")
    print(f"  P&L Percentage: {status.current_pnl_pct:+.1%}")
    print()
    print("TIME:")
    print(f"  Days Held: {status.days_held}")
    print(f"  Days to Expiration: {status.dte}")
    print()

    if status.delta is not None:
        print("GREEKS:")
        print(f"  Delta: {status.delta:.4f}")
        print(f"  Theta: {status.theta:.4f}")
        print(f"  Gamma: {status.gamma:.4f}")
        print(f"  Vega: {status.vega:.4f}")
        print()

    print("ALERTS:")
    if status.approaching_profit_target:
        print("  ⚠️  APPROACHING PROFIT TARGET (50%)")
    if status.approaching_stop_loss:
        print("  🔴 APPROACHING STOP LOSS (-200%)")
    if status.approaching_expiration:
        print("  ⏰ APPROACHING EXPIRATION (3 DTE)")
    if not (
        status.approaching_profit_target
        or status.approaching_stop_loss
        or status.approaching_expiration
    ):
        print("  ✓ No alerts")
    print()


def main():
    """Monitor open positions."""
    logger.info("=" * 70)
    logger.info("POSITION MONITOR - Real-Time Tracking")
    logger.info("=" * 70)
    logger.info("")

    # Connect to IBKR
    logger.info("Connecting to IBKR...")
    ibkr_config = IBKRConfig()
    ibkr_client = IBKRClient(ibkr_config)

    try:
        ibkr_client.connect()
        logger.info("✓ Connected to IBKR")
    except Exception as e:
        logger.error(f"✗ Connection failed: {e}")
        return

    logger.info("")

    # Create PositionMonitor
    logger.info("Initializing PositionMonitor...")
    config = get_baseline_strategy()
    monitor = PositionMonitor(
        ibkr_client=ibkr_client,
        config=config,
        update_interval_minutes=15,
    )
    logger.info("✓ PositionMonitor ready")
    logger.info("")

    # Get all positions
    logger.info("Retrieving open positions...")
    positions = monitor.get_all_positions()

    if not positions:
        logger.info("No open positions found")
        logger.info("")
        logger.info("This is expected if:")
        logger.info("  1. Market is not open (order not filled yet)")
        logger.info("  2. Order was cancelled")
        logger.info("  3. Position was closed")
        logger.info("")
        logger.info("Order ID 9 status: Check TWS for current status")
    else:
        logger.info(f"Found {len(positions)} open position(s)")
        logger.info("")

        # Display each position
        for i, position in enumerate(positions, 1):
            logger.info(f"Position {i}/{len(positions)}:")
            display_position(position)

    # Check for alerts
    logger.info("Checking for alerts...")
    alerts = monitor.check_alerts()

    if alerts:
        print()
        print("=" * 70)
        print("ACTIVE ALERTS")
        print("=" * 70)
        for alert in alerts:
            severity_emoji = {
                "info": "ℹ️",
                "warning": "⚠️",
                "critical": "🔴",
            }
            emoji = severity_emoji.get(alert.severity, "")
            print(f"{emoji} [{alert.severity.upper()}] {alert.message}")
            print(f"   Current: {alert.current_value}, Threshold: {alert.threshold}")
            print()
    else:
        logger.info("✓ No alerts at this time")

    logger.info("")

    # Disconnect
    logger.info("Disconnecting from IBKR...")
    ibkr_client.disconnect()
    logger.info("✓ Disconnected")
    logger.info("")
    logger.info("=" * 70)
    logger.info("Monitoring complete!")
    logger.info("=" * 70)
    logger.info("")
    logger.info("NOTE:")
    logger.info("  When market opens and Order ID 9 fills, run this script again")
    logger.info("  to see the position being tracked with real-time P&L.")


if __name__ == "__main__":
    main()
