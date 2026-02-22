"""Options chain search and filtering tool.

This module searches IBKR options chains to find options matching
the strategy's criteria for strike, premium, and expiration.
"""

from datetime import datetime, timedelta

from loguru import logger

from src.config.baseline_strategy import BaselineStrategy
from src.utils.market_data import safe_bid_ask, safe_price
from src.utils.timezone import us_trading_date
from src.tools.ibkr_client import IBKRClient


class OptionsFinder:
    """Find and filter options from IBKR options chains.

    Searches options chains based on:
    - OTM percentage (e.g., 15-20% below stock price)
    - Premium range (e.g., $0.30-$0.50)
    - Days to expiration (e.g., 7-14 days)

    Example:
        >>> from src.tools.ibkr_client import IBKRClient
        >>> client = IBKRClient(config)
        >>> client.connect()
        >>> finder = OptionsFinder(client)
        >>> options = finder.find_put_options(
        ...     symbol="AAPL",
        ...     stock_price=150.0,
        ...     otm_range=(0.15, 0.20),
        ...     premium_range=(0.30, 0.50),
        ...     dte_range=(7, 14)
        ... )
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: BaselineStrategy | None = None,
    ):
        """Initialize options finder.

        Args:
            ibkr_client: Connected IBKR client
            config: Strategy configuration (optional)
        """
        self.ibkr_client = ibkr_client
        self.config = config

        logger.info("Initialized OptionsFinder")

    def _get_option_chains(self, stock_contract) -> list:
        """Get available option chains for a stock.

        Returns list of OptionChain objects with:
        - exchange: str
        - underlyingConId: int
        - tradingClass: str
        - multiplier: str
        - expirations: set[str]
        - strikes: set[float]

        Args:
            stock_contract: Qualified stock contract

        Returns:
            list: Available option chains
        """
        try:
            chains = self.ibkr_client.ib.reqSecDefOptParams(
                stock_contract.symbol,
                "",
                stock_contract.secType,
                stock_contract.conId,
            )

            if chains:
                # Log available chains for debugging
                for chain in chains:
                    logger.debug(
                        f"{stock_contract.symbol} chain: exchange={chain.exchange}, "
                        f"tradingClass={chain.tradingClass}, "
                        f"expirations={len(chain.expirations)}, "
                        f"strikes={len(chain.strikes)}"
                    )

            return chains

        except Exception as e:
            logger.error(f"Error getting option chains: {e}")
            return []

    def _select_best_chain(self, chains: list, symbol: str) -> dict | None:
        """Select the best option chain for trading.

        Prefers:
        1. SMART exchange (for best execution)
        2. tradingClass matching symbol (standard options vs weeklies)
        3. Chain with most strikes/expirations

        Args:
            chains: List of option chains
            symbol: Stock symbol

        Returns:
            dict with chain info or None
        """
        if not chains:
            return None

        # Try to find SMART exchange chain with matching tradingClass
        for chain in chains:
            if chain.exchange == "SMART" and chain.tradingClass == symbol:
                return {
                    "exchange": chain.exchange,
                    "trading_class": chain.tradingClass,
                    "multiplier": chain.multiplier,
                    "expirations": chain.expirations,
                    "strikes": chain.strikes,
                }

        # Fall back to any SMART exchange chain
        for chain in chains:
            if chain.exchange == "SMART":
                return {
                    "exchange": chain.exchange,
                    "trading_class": chain.tradingClass,
                    "multiplier": chain.multiplier,
                    "expirations": chain.expirations,
                    "strikes": chain.strikes,
                }

        # Fall back to first chain with most data
        best_chain = max(chains, key=lambda c: len(c.expirations) * len(c.strikes))
        return {
            "exchange": best_chain.exchange,
            "trading_class": best_chain.tradingClass,
            "multiplier": best_chain.multiplier,
            "expirations": best_chain.expirations,
            "strikes": best_chain.strikes,
        }

    def _filter_expirations_by_dte_from_chain(
        self, chain: dict, dte_range: tuple[int, int]
    ) -> list[str]:
        """Filter expirations from a specific chain by DTE.

        Args:
            chain: Selected chain dictionary
            dte_range: (min_dte, max_dte) tuple

        Returns:
            list[str]: Filtered expiration dates in YYYYMMDD format
        """
        today = us_trading_date()
        min_date = today + timedelta(days=dte_range[0])
        max_date = today + timedelta(days=dte_range[1])

        matching = []
        for expiration in chain["expirations"]:
            exp_date = datetime.strptime(expiration, "%Y%m%d").date()
            if min_date <= exp_date <= max_date:
                matching.append(expiration)

        return sorted(matching)

    def _get_option_quote(
        self,
        symbol: str,
        expiration: str,
        strike: float,
        right: str,
        stock_price: float,
        exchange: str,
        trading_class: str,
    ) -> dict | None:
        """Get option quote with premium.

        Args:
            symbol: Stock symbol
            expiration: Expiration date (YYYYMMDD)
            strike: Strike price
            right: Option right ('P' or 'C')
            stock_price: Current stock price
            exchange: Exchange from chain
            trading_class: Trading class from chain

        Returns:
            dict: Option data or None
        """
        try:
            # Create option contract WITH trading_class
            option_contract = self.ibkr_client.get_option_contract(
                symbol=symbol,
                expiration=expiration,
                strike=strike,
                right=right,
                exchange=exchange,
                trading_class=trading_class,
            )

            # Qualify contract
            qualified = self.ibkr_client.qualify_contract(option_contract)
            if not qualified or qualified.conId == 0:
                logger.debug(
                    f"Could not qualify {symbol} {strike}{right} {expiration} "
                    f"(exchange={exchange}, tradingClass={trading_class})"
                )
                return None

            # Request market data (snapshot mode to avoid competing sessions)
            ticker = self.ibkr_client.ib.reqMktData(qualified, snapshot=True)
            self.ibkr_client.ib.sleep(2)

            # Extract premium (NaN-safe)
            bid, ask = safe_bid_ask(ticker)
            premium = None
            if bid and ask:
                premium = (bid + ask) / 2
            else:
                premium = safe_price(ticker)

            # Cancel market data
            self.ibkr_client.ib.cancelMktData(qualified)

            if not premium or premium <= 0:
                logger.debug(f"No valid premium for {symbol} {strike}{right}")
                return None

            # Calculate metrics
            exp_date = datetime.strptime(expiration, "%Y%m%d").date()
            dte = (exp_date - us_trading_date()).days
            if right == "P":
                otm_pct = (stock_price - strike) / stock_price
            else:
                otm_pct = (strike - stock_price) / stock_price

            return {
                "symbol": symbol,
                "strike": strike,
                "expiration": datetime.strptime(expiration, "%Y%m%d"),
                "option_type": "PUT" if right == "P" else "CALL",
                "premium": round(premium, 2),
                "bid": bid,
                "ask": ask,
                "dte": dte,
                "otm_pct": round(otm_pct, 4),
                "margin_required": self._estimate_margin(stock_price, strike, premium),
                "exchange": exchange,
                "trading_class": trading_class,
            }

        except Exception as e:
            logger.debug(f"Error getting quote for {symbol} {strike} {right}: {e}")
            return None

    def find_put_options(
        self,
        symbol: str,
        stock_price: float,
        otm_range: tuple[float, float] = (0.15, 0.20),
        premium_range: tuple[float, float] = (0.30, 0.50),
        dte_range: tuple[int, int] = (7, 14),
        max_results: int = 5,
    ) -> list[dict]:
        """Find put options matching criteria.

        Args:
            symbol: Stock ticker symbol
            stock_price: Current stock price
            otm_range: OTM percentage range (e.g., 0.15-0.20 for 15-20%)
            premium_range: Premium range in dollars per share
            dte_range: Days to expiration range
            max_results: Maximum options to return

        Returns:
            list[dict]: List of matching options with metadata
        """
        logger.info(
            f"Searching put options for {symbol} @ ${stock_price:.2f}: "
            f"OTM {otm_range}, Premium {premium_range}, DTE {dte_range}"
        )

        # Get and qualify stock contract
        stock_contract = self.ibkr_client.get_stock_contract(symbol)
        qualified_stock = self.ibkr_client.qualify_contract(stock_contract)

        if not qualified_stock:
            logger.warning(f"Could not qualify contract for {symbol}")
            return []

        # Get option chains - ONCE
        chains = self._get_option_chains(qualified_stock)
        if not chains:
            logger.warning(f"No option chains found for {symbol}")
            return []

        # Select best chain to use
        selected_chain = self._select_best_chain(chains, symbol)
        if not selected_chain:
            logger.warning(f"Could not select option chain for {symbol}")
            return []

        logger.info(
            f"{symbol}: Using chain exchange={selected_chain['exchange']}, "
            f"tradingClass={selected_chain['trading_class']}"
        )

        # Filter expirations by DTE from the SELECTED chain
        target_expirations = self._filter_expirations_by_dte_from_chain(
            selected_chain, dte_range
        )
        if not target_expirations:
            logger.warning(f"No expirations in DTE range {dte_range} for {symbol}")
            return []

        # Calculate strike range
        max_strike = stock_price * (1 - otm_range[0])
        min_strike = stock_price * (1 - otm_range[1])

        # Filter strikes from the SELECTED chain
        valid_strikes = [
            s for s in selected_chain["strikes"] if min_strike <= s <= max_strike
        ]

        if not valid_strikes:
            logger.warning(
                f"No strikes in range ${min_strike:.2f}-${max_strike:.2f} for {symbol}"
            )
            return []

        logger.info(
            f"{symbol}: Found {len(target_expirations)} expirations, "
            f"{len(valid_strikes)} strikes in range"
        )

        # Get quotes for valid combinations
        matching_options = []
        for expiration in target_expirations:
            for strike in valid_strikes:
                option_data = self._get_option_quote(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    right="P",
                    stock_price=stock_price,
                    exchange=selected_chain["exchange"],
                    trading_class=selected_chain["trading_class"],
                )

                if option_data is None:
                    continue

                # Filter by premium
                if premium_range[0] <= option_data["premium"] <= premium_range[1]:
                    matching_options.append(option_data)

        if not matching_options:
            logger.info(f"No matching options found for {symbol}")
            return []

        # Rank and return
        ranked = self._rank_options(matching_options)
        top_options = ranked[:max_results]

        logger.info(f"Found {len(top_options)} matching put options for {symbol}")
        return top_options

    def find_call_options(
        self,
        symbol: str,
        stock_price: float,
        otm_range: tuple[float, float] = (0.15, 0.20),
        premium_range: tuple[float, float] = (0.30, 0.50),
        dte_range: tuple[int, int] = (7, 14),
        max_results: int = 5,
    ) -> list[dict]:
        """Find call options matching criteria.

        Similar to find_put_options but for calls.

        Args:
            symbol: Stock ticker symbol
            stock_price: Current stock price
            otm_range: OTM percentage range
            premium_range: Premium range
            dte_range: Days to expiration range
            max_results: Maximum options to return

        Returns:
            list[dict]: List of matching options
        """
        logger.info(f"Searching call options for {symbol} @ ${stock_price:.2f}")

        stock_contract = self.ibkr_client.get_stock_contract(symbol)
        qualified_stock = self.ibkr_client.qualify_contract(stock_contract)

        if not qualified_stock:
            return []

        chains = self._get_option_chains(qualified_stock)
        if not chains:
            return []

        selected_chain = self._select_best_chain(chains, symbol)
        if not selected_chain:
            return []

        logger.info(
            f"{symbol}: Using chain exchange={selected_chain['exchange']}, "
            f"tradingClass={selected_chain['trading_class']}"
        )

        target_expirations = self._filter_expirations_by_dte_from_chain(
            selected_chain, dte_range
        )
        if not target_expirations:
            return []

        # For calls, OTM means above stock price
        min_strike = stock_price * (1 + otm_range[0])
        max_strike = stock_price * (1 + otm_range[1])

        valid_strikes = [
            s for s in selected_chain["strikes"] if min_strike <= s <= max_strike
        ]

        if not valid_strikes:
            return []

        matching_options = []
        for expiration in target_expirations:
            for strike in valid_strikes:
                option_data = self._get_option_quote(
                    symbol=symbol,
                    expiration=expiration,
                    strike=strike,
                    right="C",
                    stock_price=stock_price,
                    exchange=selected_chain["exchange"],
                    trading_class=selected_chain["trading_class"],
                )

                if option_data is None:
                    continue

                if premium_range[0] <= option_data["premium"] <= premium_range[1]:
                    matching_options.append(option_data)

        ranked_options = self._rank_options(matching_options)
        return ranked_options[:max_results]

    def _estimate_margin(
        self, stock_price: float, strike: float, premium: float
    ) -> float:
        """Estimate margin requirement for naked option.

        Simplified calculation. Actual margin can vary by broker.

        Args:
            stock_price: Current stock price
            strike: Option strike
            premium: Option premium

        Returns:
            float: Estimated margin requirement
        """
        # Simplified margin calculation for naked put
        # Real calculation: 20% of stock value - OTM amount + premium
        otm_amount = max(0, stock_price - strike)
        margin = (0.20 * stock_price) - otm_amount + premium

        # Minimum margin: 10% of stock value
        min_margin = 0.10 * stock_price

        return round(max(margin, min_margin) * 100, 2)  # Per contract

    def _rank_options(self, options: list[dict]) -> list[dict]:
        """Rank options by quality.

        Ranking factors:
        - Premium (higher better)
        - Margin efficiency (premium/margin ratio)
        - DTE positioning (middle of range preferred)

        Args:
            options: List of option dictionaries

        Returns:
            list[dict]: Sorted options (best first)
        """

        def score_option(opt: dict) -> float:
            # Premium quality (40%)
            premium_score = opt["premium"]

            # Margin efficiency (40%)
            margin_efficiency = opt["premium"] / max(opt["margin_required"], 1)

            # DTE quality (20%) - prefer middle of range
            dte_score = 1.0 / (1 + abs(opt["dte"] - 10))

            return (
                premium_score * 0.4 + margin_efficiency * 1000 * 0.4 + dte_score * 0.2
            )

        return sorted(options, key=score_option, reverse=True)
