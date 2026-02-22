"""Test script for placing real orders in IBKR paper trading account.

CRITICAL SAFETY:
- This script places REAL orders in the paper trading account
- Verifies PAPER_TRADING=true before executing
- Uses small position sizes (2 contracts)
- Logs every action
- Requires IBKR TWS to be running and connected

BEFORE RUNNING:
1. Start IBKR TWS Workstation
2. Login to paper trading account
3. Enable API connections in TWS (File > Global Configuration > API > Settings)
4. Verify paper trading account is active
5. Keep TWS open to observe orders

RUN WITH:
    python scripts/test_paper_trading.py

WHAT THIS DOES:
1. Connects to IBKR paper trading
2. Verifies account status
3. Gets current market data for a test symbol
4. Places 1-2 small test orders
5. Tracks order status
6. Logs complete execution details
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from time import sleep

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Ensure we're in paper trading mode
os.environ["PAPER_TRADING"] = "true"
os.environ["IBKR_PORT"] = "7497"

from loguru import logger

from src.config.base import Config, IBKRConfig
from src.execution.order_executor import OrderExecutor
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient

# Configure detailed logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="DEBUG",
)
logger.add(
    "logs/paper_trading_test.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
)


def wait_for_user_confirmation(message: str) -> bool:
    """Ask user to confirm before proceeding.

    Args:
        message: Message to display

    Returns:
        bool: True if user confirms, False otherwise
    """
    print()
    print("=" * 70)
    print(message)
    print("=" * 70)
    response = input("\nContinue? (yes/no): ").strip().lower()
    return response in ["yes", "y"]


def test_paper_trading_connection():
    """Test connection to IBKR paper trading account."""
    logger.info("=" * 70)
    logger.info("IBKR Paper Trading Connection Test")
    logger.info("=" * 70)
    logger.info("")

    # Step 1: Verify configuration
    logger.info("Step 1: Verifying paper trading configuration...")
    paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"
    ibkr_port = int(os.getenv("IBKR_PORT", "0"))

    if not paper_trading or ibkr_port != 7497:
        logger.error("✗ NOT IN PAPER TRADING MODE!")
        logger.error(f"  PAPER_TRADING={paper_trading}")
        logger.error(f"  IBKR_PORT={ibkr_port}")
        logger.error("  ABORTING - Will not place orders")
        return False

    logger.info("✓ Paper trading mode confirmed")
    logger.info(f"  PAPER_TRADING={paper_trading}")
    logger.info(f"  IBKR_PORT={ibkr_port}")
    logger.info("")

    # Step 2: Create IBKR client
    logger.info("Step 2: Creating IBKR client...")
    config = Config()
    ibkr_config = IBKRConfig()
    ibkr_client = IBKRClient(ibkr_config)
    logger.info("✓ IBKR client created")
    logger.info("")

    # Step 3: Connect to IBKR
    logger.info("Step 3: Connecting to IBKR TWS...")
    logger.info(f"  Host: {ibkr_config.host}")
    logger.info(f"  Port: {ibkr_config.port}")
    logger.info(f"  Client ID: {ibkr_config.client_id}")
    logger.info("")
    logger.info("  ⚠️  Make sure IBKR TWS is running and logged into paper account")
    logger.info("")

    try:
        ibkr_client.connect()
        logger.info("✓ Connected to IBKR successfully")
    except Exception as e:
        logger.error(f"✗ Connection failed: {e}")
        logger.error("")
        logger.error("Troubleshooting:")
        logger.error("  1. Is TWS Workstation running?")
        logger.error("  2. Are you logged into the paper trading account?")
        logger.error("  3. Is API enabled? (File > Global Configuration > API > Settings)")
        logger.error("  4. Is the port correct? (should be 7497 for paper trading)")
        logger.error("  5. Is Socket Client checkbox enabled in API settings?")
        return False

    logger.info("")

    # Step 4: Get account information
    logger.info("Step 4: Getting account information...")
    try:
        account_summary = ibkr_client.get_account_summary()

        if account_summary:
            logger.info("✓ Account information retrieved:")
            logger.info(f"  Net Liquidation: ${account_summary.get('NetLiquidation', 0):,.2f}")
            logger.info(f"  Available Funds: ${account_summary.get('AvailableFunds', 0):,.2f}")
            logger.info(f"  Buying Power: ${account_summary.get('BuyingPower', 0):,.2f}")
        else:
            logger.warning("⚠️  Could not retrieve account information")
    except Exception as e:
        logger.error(f"✗ Error getting account info: {e}")

    logger.info("")

    # Step 5: Test market data
    logger.info("Step 5: Testing market data retrieval...")
    try:
        test_symbol = "AAPL"
        logger.info(f"  Getting market data for {test_symbol}...")

        stock = ibkr_client.get_stock_contract(test_symbol)
        qualified = ibkr_client.qualify_contract(stock)

        if qualified:
            logger.info(f"✓ Contract qualified: {qualified.symbol}")

            market_data = ibkr_client.get_market_data(qualified)
            if market_data:
                logger.info(f"✓ Market data retrieved:")
                logger.info(f"  Last: ${market_data.get('last', 0):.2f}")
                logger.info(f"  Bid: ${market_data.get('bid', 0):.2f}")
                logger.info(f"  Ask: ${market_data.get('ask', 0):.2f}")
            else:
                logger.warning("⚠️  Could not get market data")
        else:
            logger.warning(f"⚠️  Could not qualify {test_symbol}")
    except Exception as e:
        logger.error(f"✗ Market data test failed: {e}")

    logger.info("")
    logger.info("=" * 70)
    logger.info("Connection test complete!")
    logger.info("=" * 70)
    logger.info("")

    return ibkr_client


def place_test_order(ibkr_client: IBKRClient):
    """Place a test order in paper trading account.

    Args:
        ibkr_client: Connected IBKR client
    """
    logger.info("=" * 70)
    logger.info("Placing Test Order in Paper Trading Account")
    logger.info("=" * 70)
    logger.info("")

    # Ask for confirmation
    if not wait_for_user_confirmation(
        "⚠️  ABOUT TO PLACE REAL ORDER IN PAPER TRADING ACCOUNT\n"
        "This will place an actual order that you'll see in TWS.\n"
        "The order is small (2 contracts) and in paper trading only."
    ):
        logger.info("User cancelled - no order placed")
        return

    logger.info("")

    # Step 1: Create OrderExecutor (NOT dry-run)
    logger.info("Step 1: Creating OrderExecutor in LIVE mode (paper trading)...")
    config = Config()
    executor = OrderExecutor(
        ibkr_client=ibkr_client,
        config=config,
        dry_run=False,  # LIVE MODE - will place real orders
    )
    logger.info("✓ OrderExecutor created (dry_run=False)")
    logger.info("  ⚠️  Orders will be placed in paper account")
    logger.info("")

    # Step 2: Create test opportunity
    logger.info("Step 2: Creating test trade opportunity...")

    # Create a conservative test trade
    opportunity = TradeOpportunity(
        symbol="AAPL",
        strike=150.0,  # Will calculate actual based on current price
        expiration=datetime.now() + timedelta(days=10),
        option_type="PUT",
        premium=0.40,  # Placeholder - will use actual market price
        contracts=2,  # Small test size
        otm_pct=0.18,
        dte=10,
        stock_price=180.0,  # Placeholder
        trend="uptrend",
        sector="Technology",
        confidence=0.85,
        reasoning="TEST ORDER for paper trading validation. Small 2-contract position to verify execution engine.",
        margin_required=1200.0,
    )

    logger.info("✓ Test opportunity created:")
    logger.info(f"  Symbol: {opportunity.symbol}")
    logger.info(f"  Contracts: {opportunity.contracts} (small test size)")
    logger.info(f"  Option Type: {opportunity.option_type}")
    logger.info(f"  Strike: ${opportunity.strike}")
    logger.info(f"  Expiration: {opportunity.expiration.strftime('%Y-%m-%d')}")
    logger.info(f"  Reasoning: {opportunity.reasoning}")
    logger.info("")

    # Step 3: Execute trade
    logger.info("Step 3: Executing trade...")
    logger.info("  ORDER TYPE: LIMIT")
    logger.info("  LIMIT PRICE: $0.40")
    logger.info("")
    logger.info("  🔴 PLACING ORDER NOW...")
    logger.info("")

    try:
        result = executor.execute_trade(
            opportunity=opportunity,
            order_type="LIMIT",
            limit_price=0.40,
        )

        logger.info("")
        logger.info("=" * 70)
        logger.info("ORDER EXECUTION RESULT")
        logger.info("=" * 70)
        logger.info(f"  Success: {result.success}")
        logger.info(f"  Order ID: {result.order_id}")
        logger.info(f"  Status: {result.status.value}")
        logger.info(f"  Dry Run: {result.dry_run}")

        if result.error_message:
            logger.error(f"  Error: {result.error_message}")

        if result.fill_price:
            logger.info(f"  Fill Price: ${result.fill_price:.2f}")
            logger.info(f"  Slippage: ${result.slippage:.2f}")

        logger.info("")
        logger.info(f"  Reasoning: {result.reasoning}")
        logger.info("")

        if result.success:
            logger.info("✓ ORDER PLACED SUCCESSFULLY!")
            logger.info("")
            logger.info("NEXT STEPS:")
            logger.info("  1. Check IBKR TWS - you should see this order")
            logger.info("  2. Look in the Orders panel in TWS")
            logger.info(f"  3. Search for Order ID: {result.order_id}")
            logger.info("  4. Verify it's in your paper trading account")
            logger.info("  5. You can cancel it manually in TWS if needed")
        else:
            logger.error("✗ ORDER FAILED")
            logger.error(f"  Reason: {result.error_message}")

    except Exception as e:
        logger.error(f"✗ Exception during order placement: {e}", exc_info=True)

    logger.info("")
    logger.info("=" * 70)


def main():
    """Main test function."""
    logger.info("")
    logger.info("╔═══════════════════════════════════════════════════════════════════╗")
    logger.info("║         IBKR PAPER TRADING TEST - REAL ORDER PLACEMENT           ║")
    logger.info("╚═══════════════════════════════════════════════════════════════════╝")
    logger.info("")
    logger.info("⚠️  WARNING: This script will place REAL orders in paper trading")
    logger.info("")

    # Test connection first
    ibkr_client = test_paper_trading_connection()

    if not ibkr_client:
        logger.error("Connection test failed - aborting")
        return

    # Wait a bit
    logger.info("")
    logger.info("Waiting 2 seconds before placing order...")
    sleep(2)

    # Place test order
    place_test_order(ibkr_client)

    # Cleanup
    logger.info("")
    logger.info("Disconnecting from IBKR...")
    ibkr_client.disconnect()
    logger.info("✓ Disconnected")
    logger.info("")
    logger.info("=" * 70)
    logger.info("Test complete!")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
