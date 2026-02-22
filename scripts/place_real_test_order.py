"""Place a real test order using actual available options.

This script queries the options chain to find real, tradeable options
before placing the order.
"""

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

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


def find_tradeable_option(ibkr_client: IBKRClient, symbol: str):
    """Find a real, tradeable option contract.

    Args:
        ibkr_client: Connected IBKR client
        symbol: Stock symbol

    Returns:
        dict: Option details or None
    """
    logger.info(f"Searching for tradeable {symbol} options...")

    # Get stock contract
    stock = ibkr_client.get_stock_contract(symbol)
    qualified_stock = ibkr_client.qualify_contract(stock)

    if not qualified_stock:
        logger.error(f"Could not qualify {symbol} stock")
        return None

    # Get current price
    market_data = ibkr_client.get_market_data(qualified_stock)
    if not market_data:
        logger.error("Could not get market data")
        return None

    current_price = market_data["last"]
    logger.info(f"Current {symbol} price: ${current_price:.2f}")

    # Get option chains
    try:
        chains = ibkr_client.ib.reqSecDefOptParams(
            symbol, "", qualified_stock.secType, qualified_stock.conId
        )

        if not chains:
            logger.error("No option chains found")
            return None

        logger.info(f"Found {len(chains)} option chains")

        # Get the first chain
        chain = chains[0]
        logger.info(f"Using chain: {chain.exchange}")

        # Find expirations in the next 7-14 days
        today = datetime.now().date()
        target_expirations = []

        for exp_str in chain.expirations:
            exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
            days_to_exp = (exp_date - today).days

            if 7 <= days_to_exp <= 14:
                target_expirations.append((exp_str, days_to_exp))

        if not target_expirations:
            logger.warning("No expirations in 7-14 day range, using nearest")
            # Use first available
            if chain.expirations:
                exp_str = chain.expirations[0]
                exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                days_to_exp = (exp_date - today).days
                target_expirations = [(exp_str, days_to_exp)]

        if not target_expirations:
            logger.error("No valid expirations found")
            return None

        # Use first matching expiration
        expiration_str, dte = target_expirations[0]
        logger.info(f"Selected expiration: {expiration_str} ({dte} DTE)")

        # Find a strike around 15-20% OTM
        target_strike = current_price * 0.82  # 18% OTM

        # Find closest strike
        available_strikes = sorted(chain.strikes)
        closest_strike = min(available_strikes, key=lambda x: abs(x - target_strike))

        logger.info(f"Target strike: ${target_strike:.2f}")
        logger.info(f"Closest available strike: ${closest_strike:.2f}")
        otm_pct = (current_price - closest_strike) / current_price

        logger.info(f"OTM percentage: {otm_pct:.1%}")

        # Get the option contract
        option_contract = ibkr_client.get_option_contract(
            symbol=symbol,
            expiration=expiration_str,
            strike=closest_strike,
            right="P",
        )

        # Qualify it
        qualified_option = ibkr_client.qualify_contract(option_contract)

        if not qualified_option:
            logger.error("Could not qualify option contract")
            return None

        logger.info("✓ Found valid option contract")

        # Get option quote
        ticker = ibkr_client.ib.reqMktData(qualified_option, snapshot=True)
        ibkr_client.ib.sleep(2)

        if ticker and ticker.bid and ticker.ask:
            mid_price = (ticker.bid + ticker.ask) / 2
            logger.info(f"Option quote: Bid ${ticker.bid:.2f}, Ask ${ticker.ask:.2f}, Mid ${mid_price:.2f}")
        else:
            mid_price = 0.40  # Default
            logger.warning(f"No quote available, using default ${mid_price}")

        return {
            "symbol": symbol,
            "strike": closest_strike,
            "expiration": datetime.strptime(expiration_str, "%Y%m%d"),
            "dte": dte,
            "stock_price": current_price,
            "otm_pct": otm_pct,
            "premium": mid_price,
            "bid": ticker.bid if ticker else None,
            "ask": ticker.ask if ticker else None,
        }

    except Exception as e:
        logger.error(f"Error finding tradeable option: {e}", exc_info=True)
        return None


def main():
    """Place test order with real option contract."""
    logger.info("=" * 70)
    logger.info("PLACING TEST ORDER - USING REAL OPTIONS CHAIN")
    logger.info("=" * 70)
    logger.info("")

    # Connect to IBKR
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

    # Find tradeable option
    logger.info("Step 2: Finding tradeable option...")
    option_data = find_tradeable_option(ibkr_client, "AAPL")

    if not option_data:
        logger.error("✗ Could not find tradeable option")
        ibkr_client.disconnect()
        return

    logger.info("")

    # Create OrderExecutor
    logger.info("Step 3: Creating OrderExecutor...")
    config = Config()
    executor = OrderExecutor(ibkr_client, config, dry_run=False)
    logger.info("✓ OrderExecutor ready")
    logger.info("")

    # Create opportunity
    logger.info("Step 4: Creating trade opportunity...")
    opportunity = TradeOpportunity(
        symbol=option_data["symbol"],
        strike=option_data["strike"],
        expiration=option_data["expiration"],
        option_type="PUT",
        premium=option_data["premium"],
        contracts=2,  # Small test size
        otm_pct=option_data["otm_pct"],
        dte=option_data["dte"],
        stock_price=option_data["stock_price"],
        trend="uptrend",
        confidence=0.85,
        reasoning=(
            f"TEST ORDER: {option_data['symbol']} ${option_data['strike']:.0f} PUT. "
            f"Stock @ ${option_data['stock_price']:.2f}, {option_data['otm_pct']:.1%} OTM, "
            f"{option_data['dte']} DTE. Premium ${option_data['premium']:.2f}."
        ),
        margin_required=1200.0,
    )

    logger.info("Trade details:")
    logger.info(f"  {opportunity.symbol} ${opportunity.strike:.0f} PUT")
    logger.info(f"  Expiration: {opportunity.expiration.strftime('%Y-%m-%d')}")
    logger.info(f"  Premium: ${opportunity.premium:.2f}")
    logger.info(f"  Contracts: {opportunity.contracts}")
    logger.info("")

    # Place order
    logger.info("Step 5: PLACING ORDER...")
    logger.info("  🔴 LIVE ORDER - PAPER TRADING ACCOUNT")
    logger.info("")

    try:
        # Use limit price slightly better than mid to increase fill chance
        limit_price = option_data["premium"] + 0.05 if option_data["premium"] > 0 else 0.40

        result = executor.execute_trade(
            opportunity=opportunity,
            order_type="LIMIT",
            limit_price=limit_price,
        )

        logger.info("")
        logger.info("=" * 70)
        logger.info("EXECUTION RESULT")
        logger.info("=" * 70)
        logger.info(f"Success: {result.success}")
        logger.info(f"Order ID: {result.order_id}")
        logger.info(f"Status: {result.status.value}")

        if result.error_message:
            logger.error(f"Error: {result.error_message}")

        if result.success:
            logger.info("")
            logger.info("✅ ORDER PLACED IN PAPER ACCOUNT!")
            logger.info("")
            logger.info("CHECK TWS NOW:")
            logger.info(f"  Order ID: {result.order_id}")
            logger.info(f"  Action: SELL")
            logger.info(f"  Symbol: {opportunity.symbol}")
            logger.info(f"  Strike: ${opportunity.strike:.0f}")
            logger.info(f"  Type: PUT")
            logger.info(f"  Quantity: {opportunity.contracts}")
            logger.info(f"  Limit: ${limit_price:.2f}")
            logger.info(f"  Expiration: {opportunity.expiration.strftime('%Y-%m-%d')}")
        else:
            logger.error("❌ ORDER FAILED")

    except Exception as e:
        logger.error(f"❌ Exception: {e}", exc_info=True)

    # Disconnect
    logger.info("")
    logger.info("Disconnecting...")
    ibkr_client.disconnect()
    logger.info("✓ Done")
    logger.info("")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
