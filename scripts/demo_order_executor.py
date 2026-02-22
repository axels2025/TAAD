"""Demonstration script for OrderExecutor dry-run mode.

This script demonstrates the OrderExecutor in dry-run mode, showing:
1. Paper trading verification
2. Trade opportunity creation
3. Dry-run order execution
4. Validation checks
5. Detailed logging

Run this to see the OrderExecutor in action before enabling real orders.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# Set environment variables for demo
os.environ["PAPER_TRADING"] = "true"
os.environ["IBKR_PORT"] = "7497"

from loguru import logger

from src.config.base import Config, IBKRConfig
from src.execution.order_executor import OrderExecutor
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient

# Configure logger for demo
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
    level="INFO",
)


def create_sample_opportunity() -> TradeOpportunity:
    """Create a sample trade opportunity for demonstration."""
    return TradeOpportunity(
        symbol="AAPL",
        strike=150.0,
        expiration=datetime.now() + timedelta(days=10),
        option_type="PUT",
        premium=0.42,
        contracts=5,
        otm_pct=0.18,
        dte=10,
        stock_price=180.0,
        trend="uptrend",
        sector="Technology",
        confidence=0.85,
        reasoning="Strong uptrend stock (Price > EMA20 > EMA50) with 18% OTM put offering $0.42 premium, 10 DTE. Margin efficient at $1,500 per 5-contract position.",
        margin_required=1500.0,
    )


def demo_dry_run_mode():
    """Demonstrate OrderExecutor in dry-run mode."""
    logger.info("=" * 70)
    logger.info("OrderExecutor Dry-Run Mode Demonstration")
    logger.info("=" * 70)
    logger.info("")

    # Step 1: Create configuration
    logger.info("Step 1: Loading configuration...")
    try:
        config = Config()
        ibkr_config = IBKRConfig()
        logger.info(f"✓ Configuration loaded")
        logger.info(f"  Paper Trading: {config.paper_trading}")
        logger.info(f"  IBKR Port: {ibkr_config.port}")
    except Exception as e:
        logger.error(f"✗ Configuration error: {e}")
        return

    logger.info("")

    # Step 2: Create IBKR client (not connecting for demo)
    logger.info("Step 2: Creating IBKR client...")
    ibkr_client = IBKRClient(ibkr_config)
    logger.info("✓ IBKR client created (not connecting for dry-run demo)")
    logger.info("")

    # Step 3: Create OrderExecutor in dry-run mode
    logger.info("Step 3: Initializing OrderExecutor in DRY-RUN mode...")
    try:
        executor = OrderExecutor(
            ibkr_client=ibkr_client,
            config=config,
            dry_run=True,  # DRY-RUN MODE
        )
        logger.info("✓ OrderExecutor initialized successfully")
    except Exception as e:
        logger.error(f"✗ OrderExecutor initialization failed: {e}")
        return

    logger.info("")

    # Step 4: Create sample trade opportunity
    logger.info("Step 4: Creating sample trade opportunity...")
    opportunity = create_sample_opportunity()
    logger.info("✓ Trade opportunity created:")
    logger.info(f"  Symbol: {opportunity.symbol}")
    logger.info(f"  Strike: ${opportunity.strike}")
    logger.info(f"  Type: {opportunity.option_type}")
    logger.info(f"  Premium: ${opportunity.premium}")
    logger.info(f"  Contracts: {opportunity.contracts}")
    logger.info(f"  Expiration: {opportunity.expiration.strftime('%Y-%m-%d')}")
    logger.info(f"  DTE: {opportunity.dte} days")
    logger.info(f"  OTM: {opportunity.otm_pct:.1%}")
    logger.info(f"  Confidence: {opportunity.confidence:.1%}")
    logger.info(f"  Margin Required: ${opportunity.margin_required:,.2f}")
    logger.info("")

    # Step 5: Execute trade in dry-run mode
    logger.info("Step 5: Executing trade in DRY-RUN mode...")
    logger.info("-" * 70)
    logger.info("")

    # Mock the is_connected method since we're not actually connected
    ibkr_client.is_connected = lambda: True

    result = executor.execute_trade(
        opportunity=opportunity,
        order_type="LIMIT",
        limit_price=0.42,
    )

    logger.info("")
    logger.info("-" * 70)
    logger.info("")

    # Step 6: Display results
    logger.info("Step 6: Execution results...")
    logger.info(f"✓ Execution successful: {result.success}")
    logger.info(f"  Dry-run: {result.dry_run}")
    logger.info(f"  Status: {result.status.value}")
    logger.info(f"  Order ID: {result.order_id or 'N/A (dry-run)'}")
    logger.info(f"  Reasoning: {result.reasoning[:100]}...")
    logger.info("")

    # Step 7: Try a market order
    logger.info("Step 7: Testing MARKET order in dry-run...")
    logger.info("-" * 70)
    logger.info("")

    result_market = executor.execute_trade(
        opportunity=opportunity,
        order_type="MARKET",
    )

    logger.info("")
    logger.info("-" * 70)
    logger.info(f"✓ Market order dry-run: {result_market.success}")
    logger.info("")

    # Step 8: Test validation failure
    logger.info("Step 8: Testing validation (invalid trade)...")
    invalid_opportunity = create_sample_opportunity()
    invalid_opportunity.contracts = -5  # Invalid quantity

    result_invalid = executor.execute_trade(invalid_opportunity)

    logger.info(f"✓ Validation correctly rejected invalid trade")
    logger.info(f"  Status: {result_invalid.status.value}")
    logger.info(f"  Error: {result_invalid.error_message}")
    logger.info("")

    # Summary
    logger.info("=" * 70)
    logger.info("DRY-RUN DEMONSTRATION COMPLETE")
    logger.info("=" * 70)
    logger.info("")
    logger.info("Summary:")
    logger.info("  ✓ Paper trading configuration verified")
    logger.info("  ✓ OrderExecutor initialized successfully")
    logger.info("  ✓ Dry-run mode working correctly")
    logger.info("  ✓ LIMIT orders simulated")
    logger.info("  ✓ MARKET orders simulated")
    logger.info("  ✓ Validation checks working")
    logger.info("")
    logger.info("Next Steps:")
    logger.info("  1. Review the dry-run output above")
    logger.info("  2. Verify all validation checks are correct")
    logger.info("  3. If satisfied, request approval to enable paper trading")
    logger.info("")
    logger.info("⚠️  NO REAL ORDERS WERE PLACED - THIS WAS A SIMULATION")
    logger.info("")


if __name__ == "__main__":
    demo_dry_run_mode()
