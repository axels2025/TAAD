"""Place a test order in IBKR paper trading account (non-interactive).

This script will immediately place a test order without prompting.
Use only when you're ready to place the order.
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

# Configure logging
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
)
logger.add(
    "logs/paper_trading_test.log",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {message}",
    level="DEBUG",
)


def main():
    """Place test order in paper trading account."""
    logger.info("=" * 70)
    logger.info("PLACING TEST ORDER IN PAPER TRADING ACCOUNT")
    logger.info("=" * 70)
    logger.info("")

    # Step 1: Connect to IBKR
    logger.info("Step 1: Connecting to IBKR...")
    ibkr_config = IBKRConfig()
    ibkr_client = IBKRClient(ibkr_config)

    try:
        ibkr_client.connect()
        logger.info("✓ Connected to IBKR")
    except Exception as e:
        logger.error(f"✗ Connection failed: {e}")
        return

    logger.info("")

    # Step 2: Get current market data for AAPL
    logger.info("Step 2: Getting current AAPL price...")
    try:
        stock = ibkr_client.get_stock_contract("AAPL")
        qualified = ibkr_client.qualify_contract(stock)
        market_data = ibkr_client.get_market_data(qualified)

        if market_data:
            current_price = market_data["last"]
            logger.info(f"✓ AAPL current price: ${current_price:.2f}")

            # Calculate appropriate strike (18% OTM)
            strike_price = round(current_price * 0.82 / 5) * 5  # Round to nearest $5
            logger.info(f"  Calculated strike (18% OTM): ${strike_price:.2f}")
        else:
            logger.warning("Could not get market data, using default strike")
            current_price = 252.0
            strike_price = 210.0
    except Exception as e:
        logger.error(f"Error getting market data: {e}")
        current_price = 252.0
        strike_price = 210.0

    logger.info("")

    # Step 3: Create OrderExecutor
    logger.info("Step 3: Creating OrderExecutor (LIVE MODE - paper trading)...")
    config = Config()
    executor = OrderExecutor(
        ibkr_client=ibkr_client,
        config=config,
        dry_run=False,  # LIVE MODE
    )
    logger.info("✓ OrderExecutor created (dry_run=False)")
    logger.info("")

    # Step 4: Create test opportunity
    logger.info("Step 4: Creating test trade opportunity...")
    opportunity = TradeOpportunity(
        symbol="AAPL",
        strike=strike_price,
        expiration=datetime.now() + timedelta(days=10),
        option_type="PUT",
        premium=0.40,
        contracts=2,  # Small test size
        otm_pct=0.18,
        dte=10,
        stock_price=current_price,
        trend="uptrend",
        sector="Technology",
        confidence=0.85,
        reasoning=(
            f"TEST ORDER: AAPL ${strike_price:.0f} PUT with 10 DTE. "
            f"Stock @ ${current_price:.2f}, 18% OTM. "
            "Small 2-contract position for execution engine validation."
        ),
        margin_required=1200.0,
    )

    logger.info("✓ Test opportunity:")
    logger.info(f"  Symbol: {opportunity.symbol}")
    logger.info(f"  Current Price: ${current_price:.2f}")
    logger.info(f"  Strike: ${opportunity.strike:.2f}")
    logger.info(f"  Type: {opportunity.option_type}")
    logger.info(f"  Contracts: {opportunity.contracts}")
    logger.info(f"  Expiration: {opportunity.expiration.strftime('%Y-%m-%d')}")
    logger.info(f"  DTE: {opportunity.dte}")
    logger.info("")

    # Step 5: Place the order
    logger.info("Step 5: PLACING ORDER...")
    logger.info("  🔴 EXECUTING ORDER NOW...")
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
        logger.info(f"Success: {result.success}")
        logger.info(f"Order ID: {result.order_id}")
        logger.info(f"Status: {result.status.value}")
        logger.info(f"Dry Run: {result.dry_run}")

        if result.error_message:
            logger.error(f"Error: {result.error_message}")

        if result.fill_price:
            logger.info(f"Fill Price: ${result.fill_price:.2f}")
            logger.info(f"Slippage: ${result.slippage:.2f}")

        logger.info("")
        logger.info(f"Reasoning: {result.reasoning}")
        logger.info("")

        if result.success:
            logger.info("✅ ORDER PLACED SUCCESSFULLY!")
            logger.info("")
            logger.info("NEXT STEPS:")
            logger.info("  1. Check IBKR TWS - you should see this order")
            logger.info("  2. Look in the Orders panel in TWS")
            logger.info(f"  3. Order ID: {result.order_id}")
            logger.info(f"  4. Order: SELL 2 {opportunity.symbol} ${opportunity.strike} PUT")
            logger.info("  5. You can cancel it manually in TWS if needed")
            logger.info("")
            logger.info("Order Details for TWS:")
            logger.info(f"  Symbol: {opportunity.symbol}")
            logger.info(f"  Action: SELL")
            logger.info(f"  Quantity: {opportunity.contracts}")
            logger.info(f"  Type: {opportunity.option_type}")
            logger.info(f"  Strike: ${opportunity.strike:.2f}")
            logger.info(f"  Expiration: {opportunity.expiration.strftime('%Y%m%d')}")
            logger.info(f"  Order Type: LIMIT @ $0.40")
        else:
            logger.error("❌ ORDER FAILED")
            logger.error(f"Reason: {result.error_message}")
            logger.error("")
            logger.error("Possible reasons:")
            logger.error("  1. Option contract doesn't exist at this strike/expiration")
            logger.error("  2. Market is closed for this option")
            logger.error("  3. Insufficient permissions")
            logger.error("  4. Option chain not loaded in TWS")

    except Exception as e:
        logger.error(f"❌ Exception during order placement: {e}", exc_info=True)

    logger.info("")

    # Cleanup
    logger.info("Disconnecting from IBKR...")
    ibkr_client.disconnect()
    logger.info("✓ Disconnected")
    logger.info("")
    logger.info("=" * 70)
    logger.info("Test complete!")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
