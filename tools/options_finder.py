"""
PUT Options Finder Tool
Finds PUT options 15-20% OTM with premium between $0.30-$0.50
"""
import logging
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from ib_insync import Stock, Option, util
from config.ibkr_connection import create_ibkr_connection
from utils_progress import ProgressSpinner, ProgressBar, StatusPrinter

logger = logging.getLogger(__name__)


def find_put_options(
    symbol: str,
    current_price: float,
    otm_range: tuple = (0.15, 0.20),
    premium_range: tuple = (0.30, 0.50),
    min_dte: int = 30,
    max_dte: int = 60
) -> List[Dict]:
    """
    Find PUT options meeting specific criteria

    Args:
        symbol: Stock symbol
        current_price: Current stock price
        otm_range: (min, max) percentage out of the money (default 15-20%)
        premium_range: (min, max) premium in dollars (default $0.30-$0.50)
        min_dte: Minimum days to expiration
        max_dte: Maximum days to expiration

    Returns:
        List of dicts with option details
    """
    logger.info(f"Finding PUT options for {symbol} at ${current_price:.2f}")
    StatusPrinter.subsection(f"Finding PUT Options for {symbol}")

    matching_options = []

    # Create connection within this thread
    with create_ibkr_connection() as ib:
        try:
            # Create stock contract
            stock = Stock(symbol, 'SMART', 'USD')
            ib.qualifyContracts(stock)

            # Get option chain
            chains = ib.reqSecDefOptParams(
                stock.symbol,
                '',
                stock.secType,
                stock.conId
            )

            if not chains:
                logger.warning(f"No option chains found for {symbol}")
                return []

            # Use first exchange (usually SMART)
            chain = chains[0]
            logger.info(f"Found option chain for {symbol} on {chain.exchange}")
            logger.info(
                f"{symbol}: IBKR provides {len(chain.strikes)} strikes and "
                f"{len(chain.expirations)} expirations"
            )

            # Calculate target strike range (15-20% OTM for PUTs)
            min_strike = current_price * (1 - otm_range[1])  # 20% OTM
            max_strike = current_price * (1 - otm_range[0])  # 15% OTM
            logger.info(
                f"{symbol}: Target strike range: ${min_strike:.2f} - ${max_strike:.2f} "
                f"({otm_range[0]*100:.0f}%-{otm_range[1]*100:.0f}% OTM)"
            )

            # Filter expirations by DTE first
            today = datetime.now().date()
            min_date = today + timedelta(days=min_dte)
            max_date = today + timedelta(days=max_dte)

            expirations_in_range = [
                exp for exp in chain.expirations
                if min_date <= datetime.strptime(exp, '%Y%m%d').date() <= max_date
            ]

            if not expirations_in_range:
                logger.warning(f"No expirations found in {min_dte}-{max_dte} DTE range")
                return []

            logger.info(
                f"{symbol}: Found {len(expirations_in_range)} expirations in "
                f"{min_dte}-{max_dte} DTE range"
            )

            # Show which strikes from IBKR fall in our target range
            strikes_in_range = [s for s in chain.strikes if min_strike <= s <= max_strike]
            logger.info(
                f"{symbol}: {len(strikes_in_range)} strikes from IBKR fall in OTM range: "
                f"{sorted(strikes_in_range)[:10]}" +
                (f"... (+{len(strikes_in_range)-10} more)" if len(strikes_in_range) > 10 else "")
            )

            StatusPrinter.info(f"Checking {len(expirations_in_range)} expirations in {min_dte}-{max_dte} DTE range")

            # For each expiration, get actual available strikes
            # This prevents creating invalid strike/expiration combinations
            qualified_contracts = []
            total_attempted = 0
            total_qualified = 0

            # Start spinner for qualification process
            spinner = ProgressSpinner(f"Qualifying option contracts for {symbol}")
            spinner.start()

            for exp_idx, expiration in enumerate(expirations_in_range):
                spinner.update_message(
                    f"Qualifying {symbol} expiration {exp_idx+1}/{len(expirations_in_range)}"
                )
                # Get all strikes available for this stock
                strikes_in_otm_range = [
                    strike for strike in chain.strikes
                    if min_strike <= strike <= max_strike
                ]

                if not strikes_in_otm_range:
                    continue

                logger.debug(
                    f"{symbol}: Checking {len(strikes_in_otm_range)} strikes in OTM range "
                    f"for expiration {expiration}"
                )

                # Create option contracts for this expiration
                expiration_contracts = [
                    Option(symbol, expiration, strike, 'P', 'SMART')
                    for strike in strikes_in_otm_range
                ]

                total_attempted += len(expiration_contracts)

                # Qualify contracts for this expiration
                # qualifyContracts will skip invalid contracts
                try:
                    qualified_batch = ib.qualifyContracts(*expiration_contracts)

                    # Only keep successfully qualified contracts
                    valid_count = 0
                    for contract in qualified_batch:
                        # Verify contract has valid conId and required fields
                        # conId > 0 means IBKR recognized the contract as valid
                        if (contract.conId > 0 and
                            contract.lastTradeDateOrContractMonth and
                            contract.strike > 0):
                            qualified_contracts.append(contract)
                            valid_count += 1
                        else:
                            logger.debug(
                                f"{symbol}: Strike {contract.strike} not available "
                                f"for expiration {expiration} (conId={contract.conId})"
                            )

                    total_qualified += valid_count

                    if valid_count > 0:
                        logger.info(
                            f"{symbol}: Expiration {expiration} - "
                            f"{valid_count}/{len(expiration_contracts)} strikes are valid"
                        )

                    # Rate limiting between expiration batches
                    if exp_idx < len(expirations_in_range) - 1:
                        ib.sleep(0.2)

                except Exception as e:
                    logger.warning(
                        f"{symbol}: Error qualifying contracts for expiration {expiration}: {e}"
                    )
                    continue

            # Stop spinner
            spinner.stop()

            if not qualified_contracts:
                StatusPrinter.warning(
                    f"No valid option contracts found (attempted {total_attempted}, qualified 0)"
                )
                logger.warning(
                    f"{symbol}: No valid option contracts found "
                    f"(attempted {total_attempted}, qualified 0)"
                )
                return []

            StatusPrinter.success(
                f"Qualified {total_qualified}/{total_attempted} option contracts"
            )
            logger.info(
                f"{symbol}: Successfully qualified {total_qualified}/{total_attempted} "
                f"option contracts (filtered out {total_attempted - total_qualified} invalid strikes)"
            )

            # Use qualified contracts for pricing
            qualified = qualified_contracts

            # Create progress bar for market data fetching
            StatusPrinter.info(f"Fetching market data for {len(qualified)} contracts")
            progress = ProgressBar(total=len(qualified), prefix="Market Data", width=40)

            # Request market data for qualified contracts
            for opt_idx, option in enumerate(qualified, 1):
                progress.update(opt_idx, suffix=f"${option.strike}")
                try:
                    # Request market data with timeout
                    ticker = ib.reqMktData(option, '', False, False)
                    ib.sleep(0.5)  # Rate limiting

                    # Get bid/ask
                    bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
                    ask = ticker.ask if ticker.ask and ticker.ask > 0 else None

                    # Cancel market data to free resources
                    ib.cancelMktData(option)

                    if bid is None or ask is None:
                        logger.debug(
                            f"{symbol}: No market data for ${option.strike} "
                            f"exp {option.lastTradeDateOrContractMonth} - skipping"
                        )
                        continue

                    # Use mid price as premium
                    premium = (bid + ask) / 2

                    # Check if premium is in range
                    if premium_range[0] <= premium <= premium_range[1]:
                        exp_date = datetime.strptime(option.lastTradeDateOrContractMonth, '%Y%m%d').date()
                        dte = (exp_date - today).days
                        otm_pct = ((current_price - option.strike) / current_price) * 100

                        option_info = {
                            'symbol': symbol,
                            'strike': option.strike,
                            'expiration': option.lastTradeDateOrContractMonth,
                            'dte': dte,
                            'premium': round(premium, 2),
                            'bid': round(bid, 2),
                            'ask': round(ask, 2),
                            'otm_percentage': round(otm_pct, 2),
                            'contract': option
                        }
                        matching_options.append(option_info)
                        logger.info(
                            f"  Found: ${option.strike} PUT exp {option.lastTradeDateOrContractMonth} "
                            f"@ ${premium:.2f} ({otm_pct:.1f}% OTM, {dte} DTE)"
                        )

                except Exception as e:
                    # Common expected errors when a strike/exp combo doesn't have market data
                    error_msg = str(e).lower()
                    if 'no security definition' in error_msg or 'no data' in error_msg:
                        logger.debug(
                            f"{symbol}: Contract ${option.strike} exp {option.lastTradeDateOrContractMonth} "
                            f"has no market data (expected for illiquid strikes)"
                        )
                    else:
                        logger.warning(
                            f"{symbol}: Error getting data for ${option.strike} "
                            f"exp {option.lastTradeDateOrContractMonth}: {e}"
                        )
                    continue

            # Finish progress bar
            progress.finish("Complete")

        except Exception as e:
            logger.error(f"Error finding options for {symbol}: {e}")
            StatusPrinter.error(f"Error finding options: {e}")
            return []

    # Show results summary
    print()  # Blank line after progress bar
    if matching_options:
        StatusPrinter.success(f"Found {len(matching_options)} matching PUT options for {symbol}")
        print()
        for opt in matching_options[:5]:  # Show first 5
            StatusPrinter.result(
                f"${opt['strike']} {opt['expiration']}",
                f"${opt['premium']:.2f} premium ({opt['otm_percentage']:.1f}% OTM, {opt['dte']} DTE)"
            )
        if len(matching_options) > 5:
            StatusPrinter.info(f"... and {len(matching_options) - 5} more")
    else:
        StatusPrinter.warning(f"No matching PUT options found for {symbol}")
        logger.debug(
            f"{symbol}: No options matched - OTM: {otm_range[0]*100:.0f}%-{otm_range[1]*100:.0f}%, "
            f"Premium: ${premium_range[0]:.2f}-${premium_range[1]:.2f}, DTE: {min_dte}-{max_dte}"
        )

    logger.info(f"Found {len(matching_options)} matching PUT options for {symbol}")
    return matching_options


def find_options_for_stocks(
    stocks: List[Dict],
    otm_range: tuple = (0.15, 0.20),
    premium_range: tuple = (0.30, 0.50),
    min_dte: int = 30,
    max_dte: int = 60
) -> Dict[str, List[Dict]]:
    """
    Find PUT options for multiple stocks

    Args:
        stocks: List of stock dicts from uptrend screener
        otm_range: OTM percentage range
        premium_range: Premium dollar range
        min_dte: Minimum days to expiration
        max_dte: Maximum days to expiration

    Returns:
        Dict mapping symbol to list of option opportunities
    """
    StatusPrinter.section(f"Finding Options for {len(stocks)} Stocks")
    results = {}

    for idx, stock in enumerate(stocks, 1):
        StatusPrinter.step(idx, f"Processing {stock['symbol']}")
        symbol = stock['symbol']
        price = stock['price']

        options = find_put_options(
            symbol=symbol,
            current_price=price,
            otm_range=otm_range,
            premium_range=premium_range,
            min_dte=min_dte,
            max_dte=max_dte
        )

        if options:
            results[symbol] = options

    # Show final summary
    print()
    total_options = sum(len(opts) for opts in results.values())
    if results:
        StatusPrinter.success(
            f"Found {total_options} total options across {len(results)} stocks"
        )
    else:
        StatusPrinter.warning(f"No options matched criteria for any of the {len(stocks)} stocks")
        print()
        StatusPrinter.info(f"Search criteria:")
        StatusPrinter.info(f"  • OTM Range: {otm_range[0]*100:.0f}%-{otm_range[1]*100:.0f}%")
        StatusPrinter.info(f"  • Premium: ${premium_range[0]:.2f}-${premium_range[1]:.2f}")
        StatusPrinter.info(f"  • Expiration: {min_dte}-{max_dte} days")
        print()
        StatusPrinter.info("Suggestions to find opportunities:")
        StatusPrinter.info("  • Widen premium range (edit PREMIUM_MIN/MAX in config/trading_config.py)")
        StatusPrinter.info("  • Widen OTM range (edit OTM_MIN/MAX in config/trading_config.py)")
        StatusPrinter.info(f"  • Use {'monthly' if max_dte <= 20 else 'weekly'} options instead "
                          f"(edit DTE_MIN/MAX in config/trading_config.py)")
        StatusPrinter.info("  • Try scanning a different stock index")

    return results


if __name__ == "__main__":
    # Test the options finder
    logging.basicConfig(level=logging.INFO)
    test_symbol = "AAPL"
    test_price = 180.0

    options = find_put_options(test_symbol, test_price)
    print(f"\nFound {len(options)} matching PUT options for {test_symbol}:")
    for opt in options:
        print(f"  {opt}")
