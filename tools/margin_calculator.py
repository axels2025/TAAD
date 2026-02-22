"""
Margin Calculator Tool
Calculates margin requirements and efficiency for PUT option trades
"""
import logging
from typing import List, Dict, Optional
from ib_insync import Option
from config.ibkr_connection import get_account_summary
from utils_progress import StatusPrinter

logger = logging.getLogger(__name__)


def calculate_put_margin(
    stock_price: float,
    strike: float,
    premium: float,
    contracts: int = 5
) -> Dict:
    """
    Calculate margin requirement for selling PUT options

    For naked PUTs, IBKR typically requires:
    - Greater of:
      1. 100% of option proceeds + 20% of underlying stock value - OTM amount
      2. 100% of option proceeds + 10% of strike price

    Args:
        stock_price: Current stock price
        strike: Option strike price
        premium: Option premium per share
        contracts: Number of contracts (default 5)

    Returns:
        Dict with margin calculations
    """
    shares_per_contract = 100
    total_shares = contracts * shares_per_contract

    # Option proceeds (credit received)
    option_proceeds = premium * total_shares

    # OTM amount (for PUTs: max(0, stock_price - strike))
    otm_amount = max(0, stock_price - strike) * total_shares

    # Method 1: 20% of stock value - OTM amount
    method1 = option_proceeds + (0.20 * stock_price * total_shares) - otm_amount

    # Method 2: 10% of strike price
    method2 = option_proceeds + (0.10 * strike * total_shares)

    # Margin requirement is the greater of the two
    margin_required = max(method1, method2)

    # Minimum margin (IBKR minimum)
    min_margin = option_proceeds + (total_shares * 1.0)  # $1 per share minimum
    margin_required = max(margin_required, min_margin)

    # Calculate efficiency metrics
    return_on_margin = (option_proceeds / margin_required) * 100 if margin_required > 0 else 0
    otm_percentage = ((stock_price - strike) / stock_price) * 100

    return {
        'contracts': contracts,
        'strike': round(strike, 2),
        'premium_per_share': round(premium, 2),
        'total_premium_received': round(option_proceeds, 2),
        'margin_required': round(margin_required, 2),
        'return_on_margin': round(return_on_margin, 2),
        'otm_percentage': round(otm_percentage, 2),
        'stock_price': round(stock_price, 2)
    }


def calculate_margin_for_options(
    symbol: str,
    options: List[Dict],
    contracts: int = 5
) -> List[Dict]:
    """
    Calculate margin for multiple option opportunities

    Args:
        symbol: Stock symbol
        options: List of option dicts from options_finder
        contracts: Number of contracts per trade

    Returns:
        List of dicts with margin calculations, sorted by margin efficiency
    """
    logger.info(f"Calculating margin for {len(options)} options on {symbol}")

    margin_calcs = []

    for option in options:
        calc = calculate_put_margin(
            stock_price=option.get('stock_price') if 'stock_price' in option else 0,
            strike=option['strike'],
            premium=option['premium'],
            contracts=contracts
        )

        # Add original option info
        result = {
            **option,
            **calc
        }

        # Remove contract object if present (not serializable)
        if 'contract' in result:
            del result['contract']

        margin_calcs.append(result)

    # Sort by margin efficiency (return on margin)
    margin_calcs.sort(key=lambda x: x['return_on_margin'], reverse=True)

    logger.info(f"Calculated margin for {len(margin_calcs)} options")
    return margin_calcs


def get_portfolio_margin_capacity() -> Dict:
    """
    Get available margin from IBKR account

    Returns:
        Dict with account margin information
    """
    logger.info("Fetching account margin capacity")
    StatusPrinter.subsection("Account Margin Capacity")

    try:
        # Use the thread-safe get_account_summary helper
        account_summary = get_account_summary()

        # Extract relevant margin values
        buying_power = account_summary.get('BuyingPower', 0)
        net_liquidation = account_summary.get('NetLiquidation', 0)
        maintenance_margin = account_summary.get('MaintMarginReq', 0)
        available_funds = account_summary.get('AvailableFunds', 0)

        margin_info = {
            'net_liquidation': round(net_liquidation, 2),
            'buying_power': round(buying_power, 2),
            'available_funds': round(available_funds, 2),
            'maintenance_margin': round(maintenance_margin, 2),
            'excess_liquidity': round(available_funds - maintenance_margin, 2)
        }

        logger.info(f"Account buying power: ${buying_power:.2f}")
        StatusPrinter.result("Net Liquidation", f"${net_liquidation:,.2f}")
        StatusPrinter.result("Buying Power", f"${buying_power:,.2f}")
        StatusPrinter.result("Available Funds", f"${available_funds:,.2f}")
        return margin_info

    except Exception as e:
        logger.error(f"Error getting margin capacity: {e}")
        return {
            'net_liquidation': 0,
            'buying_power': 0,
            'available_funds': 0,
            'maintenance_margin': 0,
            'excess_liquidity': 0,
            'error': str(e)
        }


def rank_opportunities_by_margin(
    opportunities: Dict[str, List[Dict]],
    available_margin: float,
    contracts: int = 5
) -> List[Dict]:
    """
    Rank all opportunities across stocks by margin efficiency

    Args:
        opportunities: Dict mapping symbol to list of options
        available_margin: Available margin from account
        contracts: Number of contracts per trade

    Returns:
        Sorted list of best opportunities that fit within margin
    """
    StatusPrinter.subsection(f"Ranking Opportunities by Margin Efficiency")
    StatusPrinter.info(f"Available margin: ${available_margin:,.2f}")

    all_opportunities = []

    for symbol, options in opportunities.items():
        for option in options:
            # Ensure stock_price is present
            if 'stock_price' not in option:
                # Try to get from related fields
                if 'price' in option:
                    option['stock_price'] = option['price']
                else:
                    logger.warning(f"Missing stock price for {symbol}, skipping")
                    continue

            calc = calculate_put_margin(
                stock_price=option['stock_price'],
                strike=option['strike'],
                premium=option['premium'],
                contracts=contracts
            )

            # Only include if margin fits
            if calc['margin_required'] <= available_margin:
                result = {
                    'symbol': symbol,
                    **option,
                    **calc
                }
                # Remove contract object
                if 'contract' in result:
                    del result['contract']

                all_opportunities.append(result)

    # Sort by return on margin (efficiency)
    all_opportunities.sort(key=lambda x: x['return_on_margin'], reverse=True)

    logger.info(
        f"Ranked {len(all_opportunities)} opportunities "
        f"(filtered by ${available_margin:.2f} margin capacity)"
    )

    # Show results summary
    if all_opportunities:
        StatusPrinter.success(
            f"Ranked {len(all_opportunities)} opportunities by margin efficiency"
        )
        print()
        StatusPrinter.info("Top 5 opportunities:")
        for i, opp in enumerate(all_opportunities[:5], 1):
            StatusPrinter.result(
                f"{i}. {opp['symbol']} ${opp['strike']}",
                f"${opp['total_premium_received']:.2f} premium, "
                f"${opp['margin_required']:,.2f} margin ({opp['return_on_margin']:.2f}% ROI)"
            )
    else:
        StatusPrinter.warning("No opportunities fit within available margin")

    return all_opportunities


if __name__ == "__main__":
    # Test the margin calculator
    logging.basicConfig(level=logging.INFO)

    # Test calculation
    result = calculate_put_margin(
        stock_price=180.0,
        strike=150.0,
        premium=0.40,
        contracts=5
    )

    print("\nMargin Calculation:")
    for key, value in result.items():
        print(f"  {key}: {value}")
