"""Efficient options scanner using options-first approach.

This scanner is fundamentally different from the old stock-first approach:
- Starts with a curated universe of liquid option underlyings
- Fetches option chains ONCE and caches them
- Batch qualifies options contracts
- Only checks trend for options that pass premium/OTM filters
- Minimizes API calls through aggressive caching

This is how successful scanners like Barchart work.
"""

from datetime import datetime, timedelta
from typing import Literal, Optional

from ib_insync import Option
from loguru import logger

from src.config.baseline_strategy import BaselineStrategy
from src.utils.calc import fmt_pct
from src.utils.timezone import us_trading_date
from src.tools.ibkr_client import IBKRClient
from src.tools.scanner_cache import ScannerCache
from src.utils.market_data import safe_bid_ask, safe_price


# Curated list of liquid option underlyings
# These are chosen for:
# - High option liquidity
# - Tight bid-ask spreads
# - Standard options (not mini)
# - Multiple sectors for diversification
LIQUID_UNIVERSE = [
    # Major Indices
    "SPY", "QQQ", "IWM", "DIA",

    # Mega-cap Tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA",
    "AMD", "NFLX", "CRM", "ADBE", "INTC", "AVGO", "QCOM",

    # Finance
    "JPM", "BAC", "GS", "MS", "C", "WFC", "V", "MA", "AXP",

    # Healthcare
    "UNH", "JNJ", "PFE", "ABBV", "MRK", "LLY", "TMO", "BMY",

    # Consumer
    "WMT", "HD", "NKE", "SBUX", "MCD", "TGT", "COST", "LOW",
    "KO", "PEP", "PG", "DIS",

    # Energy
    "XOM", "CVX", "COP", "SLB",

    # Industrials
    "CAT", "GE", "UPS", "DE", "BA",

    # Communications
    "T", "VZ", "CMCSA",

    # Consumer Discretionary
    "AMZN", "TSLA", "HD", "MCD", "NKE",

    # Materials
    "LIN", "APD",
]

# Remove duplicates while preserving order
LIQUID_UNIVERSE = list(dict.fromkeys(LIQUID_UNIVERSE))


class EfficientOptionScanner:
    """Options-first scanner for finding trading opportunities quickly.

    Key differences from old approach:
    1. Start with options, not stocks
    2. Aggressive caching (chains don't change intraday)
    3. Batch qualification (50 at a time)
    4. Delayed trend check (only for passing options)
    5. Minimal API calls

    Example:
        >>> scanner = EfficientOptionScanner(ibkr_client)
        >>> opportunities = scanner.scan_opportunities(
        ...     min_premium=0.30,
        ...     max_premium=1.00,
        ...     min_otm=0.15,
        ...     max_otm=0.25,
        ...     min_dte=5,
        ...     max_dte=21,
        ...     require_uptrend=True,
        ...     max_results=20
        ... )
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: BaselineStrategy | None = None,
        cache: ScannerCache | None = None,
        universe: list[str] | None = None,
    ):
        """Initialize efficient scanner.

        Args:
            ibkr_client: Connected IBKR client
            config: Strategy configuration (optional)
            cache: Scanner cache (creates default if None)
            universe: Custom universe of symbols (uses LIQUID_UNIVERSE if None)
        """
        self.ibkr_client = ibkr_client
        self.config = config or BaselineStrategy.from_env()
        self.cache = cache or ScannerCache()
        self.universe = universe or LIQUID_UNIVERSE

        logger.info(
            f"Initialized EfficientOptionScanner with {len(self.universe)} symbols"
        )

    def scan_opportunities(
        self,
        min_premium: float = 0.30,
        max_premium: Optional[float] = 1.00,
        min_otm: float = 0.15,
        max_otm: Optional[float] = 0.25,
        min_dte: int = 5,
        max_dte: Optional[int] = 21,
        require_uptrend: bool = True,
        max_results: int = 20,
        option_type: Literal["PUT", "CALL"] = "PUT",
    ) -> list[dict]:
        """Scan for option opportunities using options-first approach.

        Args:
            min_premium: Minimum premium per share
            max_premium: Maximum premium per share (None = unbounded)
            min_otm: Minimum OTM percentage (e.g., 0.15 = 15%)
            max_otm: Maximum OTM percentage (None = unbounded)
            min_dte: Minimum days to expiration
            max_dte: Maximum days to expiration (None = unbounded)
            require_uptrend: Only include stocks in uptrend
            max_results: Maximum opportunities to return
            option_type: "PUT" or "CALL"

        Returns:
            list[dict]: Opportunities sorted by margin efficiency
        """
        # Format unbounded max values as "unlimited"
        premium_max_str = f"${max_premium}" if max_premium is not None else "unlimited"
        otm_max_str = f"{max_otm:.0%}" if max_otm is not None else "unlimited"
        dte_max_str = str(max_dte) if max_dte is not None else "unlimited"

        logger.info(
            f"Scanning {len(self.universe)} symbols for {option_type} opportunities: "
            f"Premium ${min_premium}-{premium_max_str}, "
            f"OTM {min_otm:.0%}-{otm_max_str}, "
            f"DTE {min_dte}-{dte_max_str}, "
            f"Uptrend={'required' if require_uptrend else 'optional'}"
        )

        start_time = datetime.now()
        all_candidates = []
        symbols_processed = 0
        symbols_skipped = 0
        api_calls_saved = 0

        for symbol in self.universe:
            try:
                # Get stock price
                stock_price = self._get_stock_price(symbol)
                if stock_price is None:
                    logger.debug(f"{symbol}: Could not get stock price")
                    symbols_skipped += 1
                    continue

                # Get or cache option chain
                chain = self.get_or_cache_chain(symbol)
                if not chain:
                    logger.debug(f"{symbol}: No option chain available")
                    symbols_skipped += 1
                    continue

                # Check if we used cache
                if self.cache.is_chain_fresh(symbol, max_age_hours=12):
                    api_calls_saved += 1

                # Extract options matching criteria
                candidates = self._extract_matching_options(
                    symbol=symbol,
                    stock_price=stock_price,
                    chain=chain,
                    min_otm=min_otm,
                    max_otm=max_otm,
                    min_dte=min_dte,
                    max_dte=max_dte,
                    option_type=option_type,
                )

                if candidates:
                    all_candidates.extend(candidates)
                    logger.debug(
                        f"{symbol}: Found {len(candidates)} option candidates"
                    )

                symbols_processed += 1

            except Exception as e:
                logger.warning(f"{symbol}: Error during scan - {e}")
                symbols_skipped += 1
                continue

        logger.info(
            f"Extracted {len(all_candidates)} option candidates from "
            f"{symbols_processed} symbols (skipped {symbols_skipped}), "
            f"saved {api_calls_saved} API calls via cache"
        )

        if not all_candidates:
            logger.warning("No option candidates found")
            return []

        # Batch qualify contracts
        qualified_options = self.batch_qualify_options(all_candidates)
        logger.info(
            f"Qualified {len(qualified_options)}/{len(all_candidates)} options"
        )

        if not qualified_options:
            return []

        # Get premiums for qualified options
        priced_options = self.batch_get_premiums(qualified_options)
        logger.info(f"Got premiums for {len(priced_options)} options")

        # Filter by premium range (handle unbounded max_premium)
        if max_premium is not None:
            filtered_options = [
                opt
                for opt in priced_options
                if min_premium <= opt["premium"] <= max_premium
            ]
        else:
            # Unbounded max premium
            filtered_options = [
                opt for opt in priced_options if opt["premium"] >= min_premium
            ]

        logger.info(
            f"Filtered to {len(filtered_options)} options in premium range"
        )

        if not filtered_options:
            return []

        # Check trend if required
        if require_uptrend:
            with_trend = []
            for opt in filtered_options:
                trend = self.quick_trend_check(opt["symbol"])
                if trend == "uptrend":
                    opt["trend"] = trend
                    with_trend.append(opt)

            logger.info(
                f"Filtered to {len(with_trend)} options with uptrend requirement"
            )
            filtered_options = with_trend
        else:
            # Still get trend but don't filter
            for opt in filtered_options:
                opt["trend"] = self.quick_trend_check(opt["symbol"]) or "unknown"

        if not filtered_options:
            return []

        # Rank by margin efficiency
        ranked_options = self._rank_opportunities(filtered_options)

        # Limit results
        top_options = ranked_options[:max_results]

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(
            f"Scan complete in {elapsed:.1f}s: Found {len(top_options)} opportunities"
        )

        return top_options

    def get_or_cache_chain(self, symbol: str) -> dict | None:
        """Get option chain from cache or IBKR.

        Args:
            symbol: Stock symbol

        Returns:
            dict with chain info or None
        """
        # Check cache first
        if self.cache.is_chain_fresh(symbol, max_age_hours=12):
            chain = self.cache.get_chain(symbol)
            if chain:
                logger.debug(f"{symbol}: Using cached option chain")
                return chain

        # Fetch from IBKR
        try:
            stock_contract = self.ibkr_client.get_stock_contract(symbol)
            qualified_stock = self.ibkr_client.qualify_contract(stock_contract)

            if not qualified_stock:
                return None

            chains = self.ibkr_client.ib.reqSecDefOptParams(
                qualified_stock.symbol,
                "",
                qualified_stock.secType,
                qualified_stock.conId,
            )

            if not chains:
                return None

            # Select best chain (prefer SMART + matching tradingClass)
            selected_chain = self._select_best_chain(chains, symbol)

            if selected_chain:
                # Cache it
                self.cache.set_chain(symbol, selected_chain)
                logger.debug(f"{symbol}: Fetched and cached option chain")

            return selected_chain

        except Exception as e:
            logger.debug(f"{symbol}: Error getting option chain - {e}")
            return None

    def quick_trend_check(self, symbol: str) -> str | None:
        """Quick trend check using cache or simple heuristic.

        Args:
            symbol: Stock symbol

        Returns:
            Trend string or None
        """
        # Check cache
        if self.cache.is_trend_fresh(symbol, max_age_hours=24):
            trend = self.cache.get_trend(symbol)
            if trend:
                return trend

        # Simple trend check: current price vs 20-day SMA
        try:
            stock_contract = self.ibkr_client.get_stock_contract(symbol)
            qualified = self.ibkr_client.qualify_contract(stock_contract)

            if not qualified:
                return None

            # Get recent bars
            bars = self.ibkr_client.ib.reqHistoricalData(
                qualified,
                endDateTime="",
                durationStr="30 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if not bars or len(bars) < 20:
                return None

            # Calculate 20-day SMA
            closes = [bar.close for bar in bars]
            sma_20 = sum(closes[-20:]) / 20
            current_price = closes[-1]

            # Simple trend determination
            if current_price > sma_20 * 1.02:  # 2% above SMA
                trend = "uptrend"
            elif current_price < sma_20 * 0.98:  # 2% below SMA
                trend = "downtrend"
            else:
                trend = "sideways"

            # Cache the result
            self.cache.set_trend(symbol, trend)

            return trend

        except Exception as e:
            logger.debug(f"{symbol}: Error checking trend - {e}")
            return None

    def batch_qualify_options(self, candidates: list[dict]) -> list[dict]:
        """Batch qualify option contracts.

        IBKR's qualifyContracts can take multiple contracts at once,
        which is much faster than qualifying one at a time.

        Args:
            candidates: List of option candidate dicts

        Returns:
            list[dict]: Candidates with qualified contracts (conId > 0)
        """
        qualified = []
        batch_size = 50  # IBKR limit

        # Reset error counter before batch qualification
        self.ibkr_client.reset_suppressed_error_count()

        for i in range(0, len(candidates), batch_size):
            batch = candidates[i : i + batch_size]

            # Create Option contracts
            contracts = []
            for candidate in batch:
                contract = Option(
                    candidate["symbol"],
                    candidate["expiration"],
                    candidate["strike"],
                    "P" if candidate["option_type"] == "PUT" else "C",
                    candidate["exchange"],
                    tradingClass=candidate["trading_class"],
                )
                contracts.append(contract)

            # Batch qualify
            try:
                qualified_contracts = self.ibkr_client.ib.qualifyContracts(*contracts)

                # Match qualified contracts back to candidates
                for j, qualified_contract in enumerate(qualified_contracts):
                    if qualified_contract and qualified_contract.conId > 0:
                        candidate = batch[j].copy()
                        candidate["contract"] = qualified_contract
                        candidate["conId"] = qualified_contract.conId
                        qualified.append(candidate)

            except Exception as e:
                logger.warning(f"Error qualifying batch: {e}")
                continue

        # Log summary of suppressed errors
        suppressed_count = self.ibkr_client.get_suppressed_error_count()
        if suppressed_count > 0:
            logger.debug(
                f"Suppressed {suppressed_count} 'No security definition' errors "
                f"(expected during contract qualification)"
            )

        logger.debug(
            f"Batch qualified {len(qualified)}/{len(candidates)} contracts "
            f"in {(len(candidates) + batch_size - 1) // batch_size} batches"
        )

        return qualified

    def batch_get_premiums(self, qualified_options: list[dict]) -> list[dict]:
        """Get premiums for qualified options.

        Uses snapshot market data to get current bid/ask/last.

        Args:
            qualified_options: Options with qualified contracts

        Returns:
            list[dict]: Options with premium data
        """
        priced = []

        for option in qualified_options:
            try:
                contract = option["contract"]

                # Request market data snapshot (avoid competing live sessions)
                ticker = self.ibkr_client.ib.reqMktData(contract, snapshot=True)
                self.ibkr_client.ib.sleep(1.5)  # Brief wait for data

                # Extract premium (NaN-safe)
                bid, ask = safe_bid_ask(ticker)
                premium = None
                if bid and ask:
                    premium = (bid + ask) / 2
                else:
                    premium = safe_price(ticker)

                # Cancel market data
                self.ibkr_client.ib.cancelMktData(contract)

                if premium and premium > 0:
                    option_copy = option.copy()
                    option_copy["premium"] = round(premium, 2)
                    option_copy["bid"] = bid
                    option_copy["ask"] = ask
                    priced.append(option_copy)

            except Exception as e:
                logger.debug(f"Error getting premium for {option['symbol']}: {e}")
                continue

        return priced

    def _get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price.

        Args:
            symbol: Stock symbol

        Returns:
            Current price or None
        """
        return self.ibkr_client.get_stock_price(symbol)

    def _select_best_chain(self, chains: list, symbol: str) -> dict | None:
        """Select best option chain for trading.

        Args:
            chains: List of option chains from IBKR
            symbol: Stock symbol

        Returns:
            dict with chain info or None
        """
        if not chains:
            return None

        # Prefer SMART exchange with matching tradingClass
        for chain in chains:
            if chain.exchange == "SMART" and chain.tradingClass == symbol:
                return {
                    "exchange": chain.exchange,
                    "trading_class": chain.tradingClass,
                    "multiplier": chain.multiplier,
                    "expirations": chain.expirations,
                    "strikes": chain.strikes,
                }

        # Fall back to any SMART chain
        for chain in chains:
            if chain.exchange == "SMART":
                return {
                    "exchange": chain.exchange,
                    "trading_class": chain.tradingClass,
                    "multiplier": chain.multiplier,
                    "expirations": chain.expirations,
                    "strikes": chain.strikes,
                }

        # Last resort: chain with most data
        best_chain = max(chains, key=lambda c: len(c.expirations) * len(c.strikes))
        return {
            "exchange": best_chain.exchange,
            "trading_class": best_chain.tradingClass,
            "multiplier": best_chain.multiplier,
            "expirations": best_chain.expirations,
            "strikes": best_chain.strikes,
        }

    def _extract_matching_options(
        self,
        symbol: str,
        stock_price: float,
        chain: dict,
        min_otm: float,
        max_otm: Optional[float],
        min_dte: int,
        max_dte: Optional[int],
        option_type: str,
    ) -> list[dict]:
        """Extract options matching OTM and DTE criteria from chain.

        Args:
            symbol: Stock symbol
            stock_price: Current stock price
            chain: Option chain data
            min_otm: Minimum OTM percentage
            max_otm: Maximum OTM percentage (None = unbounded)
            min_dte: Minimum days to expiration
            max_dte: Maximum days to expiration (None = unbounded)
            option_type: "PUT" or "CALL"

        Returns:
            list[dict]: Matching option candidates
        """
        candidates = []

        # Filter expirations by DTE
        today = us_trading_date()
        min_date = today + timedelta(days=min_dte)
        # Use a far future date if max_dte is unbounded
        max_date = (
            today + timedelta(days=max_dte)
            if max_dte is not None
            else today + timedelta(days=365)  # 1 year max for unbounded
        )

        valid_expirations = []
        for exp_str in chain["expirations"]:
            exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
            if min_date <= exp_date <= max_date:
                valid_expirations.append(exp_str)

        if not valid_expirations:
            return []

        # Calculate strike range
        if option_type == "PUT":
            max_strike = stock_price * (1 - min_otm)
            # Use 0 if max_otm is unbounded (any strike below max_strike)
            min_strike = (
                stock_price * (1 - max_otm) if max_otm is not None else 0
            )
        else:  # CALL
            min_strike = stock_price * (1 + min_otm)
            # Use very high value if max_otm is unbounded
            max_strike = (
                stock_price * (1 + max_otm)
                if max_otm is not None
                else stock_price * 10  # 10x stock price for unbounded
            )

        # Filter strikes
        valid_strikes = [
            s for s in chain["strikes"] if min_strike <= s <= max_strike
        ]

        if not valid_strikes:
            return []

        # Limit number of strikes to avoid generating too many invalid contracts
        # IBKR doesn't tell us which strikes exist for which expirations,
        # so we need to be selective to reduce "No security definition" errors
        max_strikes_per_exp = 8  # Limit strikes to reduce qualification failures

        # Create candidates for all combinations
        for expiration in valid_expirations:
            exp_date = datetime.strptime(expiration, "%Y%m%d").date()
            dte = (exp_date - today).days

            # For each expiration, select a reasonable subset of strikes
            # Sort by how close they are to our ideal OTM range (middle of min/max)
            ideal_otm = (min_otm + max_otm) / 2
            if option_type == "PUT":
                ideal_strike = stock_price * (1 - ideal_otm)
            else:
                ideal_strike = stock_price * (1 + ideal_otm)

            # Sort strikes by distance from ideal
            sorted_strikes = sorted(valid_strikes, key=lambda s: abs(s - ideal_strike))
            selected_strikes = sorted_strikes[:max_strikes_per_exp]

            for strike in selected_strikes:
                # Calculate OTM %
                if option_type == "PUT":
                    otm_pct = (stock_price - strike) / stock_price
                else:
                    otm_pct = (strike - stock_price) / stock_price

                candidates.append(
                    {
                        "symbol": symbol,
                        "strike": strike,
                        "expiration": expiration,
                        "option_type": option_type,
                        "otm_pct": round(otm_pct, 4),
                        "dte": dte,
                        "stock_price": stock_price,
                        "exchange": chain["exchange"],
                        "trading_class": chain["trading_class"],
                    }
                )

        return candidates

    def _rank_opportunities(self, opportunities: list[dict]) -> list[dict]:
        """Rank opportunities by margin efficiency and quality.

        Args:
            opportunities: List of opportunity dicts

        Returns:
            list[dict]: Sorted opportunities (best first)
        """

        def score_opportunity(opp: dict) -> float:
            # Estimate margin requirement
            stock_price = opp["stock_price"]
            strike = opp["strike"]
            premium = opp["premium"]

            otm_amount = max(0, stock_price - strike)
            margin = (0.20 * stock_price) - otm_amount + premium
            margin = max(margin, 0.10 * stock_price)  # Minimum 10%
            margin_per_contract = margin * 100

            # Margin efficiency (premium / margin required)
            margin_efficiency = premium / max(margin, 0.01)

            # Premium quality
            premium_score = premium

            # DTE quality (prefer middle of range, ~10 days)
            dte_score = 1.0 / (1 + abs(opp["dte"] - 10))

            # Combined score
            return (
                margin_efficiency * 1000 * 0.5  # 50% weight
                + premium_score * 0.3  # 30% weight
                + dte_score * 0.2  # 20% weight
            )

        # Add margin estimates and confidence scores
        for opp in opportunities:
            stock_price = opp["stock_price"]
            strike = opp["strike"]
            premium = opp["premium"]

            otm_amount = max(0, stock_price - strike)
            margin = (0.20 * stock_price) - otm_amount + premium
            margin = max(margin, 0.10 * stock_price)
            margin_per_contract = margin * 100

            opp["margin_required"] = round(margin_per_contract, 2)
            opp["confidence"] = 0.75  # Base confidence for scanned opportunities
            opp["reasoning"] = (
                f"Scanned opportunity: ${opp['premium']:.2f} premium, "
                f"{fmt_pct(opp['otm_pct'])} OTM, {opp['dte']} DTE"
            )

        return sorted(opportunities, key=score_opportunity, reverse=True)
