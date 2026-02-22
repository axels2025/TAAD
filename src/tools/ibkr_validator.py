"""IBKR Option Validator.

Validates Barchart scan results with real-time IBKR data:
- Verifies current bid/ask spreads
- Calculates margin requirements
- Checks trend conditions
- Enriches data with real-time quotes

This is the second step in our two-step screening workflow:
1. Barchart: Fast market scan (entire universe, single API call)
2. IBKR: Accurate validation (top candidates, real-time data)
"""

from datetime import datetime, timedelta
from typing import Optional

from ib_insync import Option
from loguru import logger

from src.tools.barchart_scanner import BarchartScanResult, BarchartScanOutput
from src.utils.timezone import us_trading_date
from src.tools.ibkr_client import IBKRClient
from src.tools.validation_report import ValidationReport
from src.utils.market_data import safe_bid_ask
from src.config.naked_put_options_config import (
    NakedPutScreenerConfig,
    get_naked_put_config,
)


class ValidatedOption:
    """Option that passed IBKR validation.

    Represents a trading opportunity that has been validated with
    real-time IBKR data and meets all criteria.
    """

    def __init__(
        self,
        barchart_result: BarchartScanResult,
        ibkr_bid: float,
        ibkr_ask: float,
        spread_pct: float,
        margin_required: float,
        margin_efficiency: float,
        trend: str,
        stock_price: float,
        iv_rank: float | None = None,
    ):
        """Initialize validated option.

        Args:
            barchart_result: Original Barchart scan result
            ibkr_bid: Real-time bid from IBKR
            ibkr_ask: Real-time ask from IBKR
            spread_pct: Bid-ask spread as percentage
            margin_required: Estimated margin requirement ($)
            margin_efficiency: Premium/margin ratio
            trend: Trend classification (uptrend, downtrend, sideways, unknown)
            stock_price: Current stock price from IBKR
            iv_rank: IV Rank (0.0-1.0) computed from IBKR historical IV data
        """
        self.barchart_result = barchart_result
        self.symbol = barchart_result.underlying_symbol
        self.strike = barchart_result.strike
        self.expiration = barchart_result.expiration_date
        self.ibkr_bid = ibkr_bid
        self.ibkr_ask = ibkr_ask
        self.spread_pct = spread_pct
        self.margin_required = margin_required
        self.margin_efficiency = margin_efficiency
        self.trend = trend
        self.stock_price = stock_price
        self.premium = (ibkr_bid + ibkr_ask) / 2
        self.iv_rank = iv_rank

        # Calculate OTM %
        self.otm_pct = (self.stock_price - self.strike) / self.stock_price

        # Calculate DTE
        exp_date = datetime.strptime(self.expiration, "%Y-%m-%d").date()
        self.dte = (exp_date - us_trading_date()).days

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization or display.

        Returns:
            dict: Option data ready for display or storage
        """
        return {
            "symbol": self.symbol,
            "strike": self.strike,
            "expiration": self.expiration,
            "dte": self.dte,
            "premium": round(self.premium, 2),
            "bid": round(self.ibkr_bid, 2),
            "ask": round(self.ibkr_ask, 2),
            "spread_pct": round(self.spread_pct, 4),
            "otm_pct": round(self.otm_pct, 4),
            "stock_price": round(self.stock_price, 2),
            "margin_required": round(self.margin_required, 2),
            "margin_efficiency": round(self.margin_efficiency, 4),
            "trend": self.trend,
            "delta": self.barchart_result.delta,
            "iv": self.barchart_result.volatility,
            "volume": self.barchart_result.volume,
            "open_interest": self.barchart_result.open_interest,
        }


class IBKRValidator:
    """Validates Barchart scan results against real-time IBKR data.

    Filters out options that don't meet spread and margin criteria,
    and enriches data with real-time quotes and trend analysis.

    This validator is critical because:
    - Barchart data may be slightly delayed
    - Real-time spreads can differ significantly from delayed quotes
    - Margin requirements need accurate current prices
    - Trend analysis requires recent price history
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: Optional[NakedPutScreenerConfig] = None,
    ):
        """Initialize IBKR validator.

        Args:
            ibkr_client: Connected IBKR client
            config: Screener configuration. Required for validation, optional for enrichment only.
        """
        self.ibkr_client = ibkr_client
        self.config = config  # Store as-is, don't auto-load

        logger.info("Initialized IBKRValidator")

    def validate_scan_results(
        self,
        scan_output: BarchartScanOutput,
        max_candidates: Optional[int] = None,
    ) -> list[ValidatedOption]:
        """Validate Barchart scan results with IBKR real-time data.

        This is the core validation workflow:
        1. Take top N candidates from Barchart (limit API calls)
        2. Get real-time stock prices from IBKR
        3. Get real-time option quotes from IBKR
        4. Calculate spreads and margin requirements
        5. Check trend if required
        6. Filter and rank results

        Args:
            scan_output: Results from BarchartScanner
            max_candidates: Maximum number to validate (None uses config default)

        Returns:
            list[ValidatedOption]: Validated and sorted options (best first)

        Raises:
            ValueError: If config is not set (required for validation)

        Example:
            >>> validator = IBKRValidator(ibkr_client, config)
            >>> validated = validator.validate_scan_results(barchart_results)
            >>> print(f"{len(validated)} options passed validation")
            >>> for v in validated[:5]:
            ...     print(f"{v.symbol} ${v.strike}: {v.margin_efficiency:.2%}")
        """
        validated, _report = self.validate_scan_results_with_report(
            scan_output, max_candidates
        )
        return validated

    def validate_scan_results_with_report(
        self,
        scan_output: BarchartScanOutput,
        max_candidates: Optional[int] = None,
    ) -> tuple[list[ValidatedOption], ValidationReport]:
        """Validate Barchart scan results with detailed reporting.

        Same as validate_scan_results but also returns a detailed report
        of all rejections with actual values and recommendations.

        Args:
            scan_output: Results from BarchartScanner
            max_candidates: Maximum number to validate (None uses config default)

        Returns:
            tuple: (validated_options, validation_report)

        Raises:
            ValueError: If config is not set (required for validation)

        Example:
            >>> validator = IBKRValidator(ibkr_client, config)
            >>> validated, report = validator.validate_scan_results_with_report(results)
            >>> report.display_summary(console)
            >>> if report.rejected_margin > 0:
            ...     print("Adjust MIN_MARGIN_EFFICIENCY in .env")
        """
        if self.config is None:
            raise ValueError(
                "Config is required for validation. "
                "Create IBKRValidator with config parameter for validation operations."
            )

        if max_candidates is None:
            max_candidates = self.config.validation.max_candidates_to_validate

        # Initialize report
        report = ValidationReport(
            total_candidates=min(max_candidates, len(scan_output.results)),
            max_spread_pct=self.config.validation.max_spread_pct,
            min_margin_efficiency=self.config.validation.min_margin_efficiency,
            require_uptrend=self.config.validation.require_uptrend,
        )

        validated = []

        # Limit candidates to validate (avoid excessive IBKR API calls)
        candidates = scan_output.results[:max_candidates]

        logger.info(
            f"Validating {len(candidates)} Barchart candidates with IBKR "
            f"(max spread: {self.config.validation.max_spread_pct:.0%}, "
            f"min margin eff: {self.config.validation.min_margin_efficiency:.2%})"
        )

        for i, result in enumerate(candidates, 1):
            if i % 10 == 0:
                logger.debug(f"Validated {i}/{len(candidates)} candidates...")

            try:
                validated_option = self._validate_single(result)

                if validated_option is None:
                    # No data from IBKR - collect diagnostic data
                    # Try to get stock price separately for diagnostics
                    stock_price = self._get_stock_price(result.underlying_symbol)

                    # Try to get option quote separately for diagnostics
                    option_data = self._get_option_quote(
                        result.underlying_symbol,
                        result.strike,
                        result.expiration_date,
                    )

                    bid = option_data[0] if option_data else None
                    ask = option_data[1] if option_data else None

                    # Try to get trend if we have stock price
                    trend = None
                    if stock_price:
                        try:
                            trend = self._check_trend(result.underlying_symbol)
                        except Exception:
                            pass

                    # Calculate margin and efficiency if we have enough data
                    margin_efficiency = None
                    if option_data and stock_price:
                        mid = (bid + ask) / 2 if bid and ask else None
                        if mid and mid > 0:
                            margin_required = self._estimate_margin(stock_price, result.strike, mid)
                            if margin_required > 0:
                                margin_efficiency = (mid * 100) / margin_required

                    # Determine specific failure reason
                    if stock_price is None:
                        reason = "no_stock_price"
                    elif option_data is None:
                        reason = "no_option_quotes"
                    else:
                        reason = "no_data"  # Shouldn't happen but fallback

                    report.add_rejection(
                        symbol=result.underlying_symbol,
                        strike=result.strike,
                        expiration=result.expiration_date,
                        dte=(
                            datetime.strptime(result.expiration_date, "%Y-%m-%d").date()
                            - us_trading_date()
                        ).days,
                        reason=reason,
                        stock_price=stock_price,
                        bid=bid,
                        ask=ask,
                        spread_pct=(ask - bid) / ((bid + ask) / 2) if bid and ask and (bid + ask) > 0 else None,
                        margin_efficiency=margin_efficiency,
                        trend=trend,
                    )
                    continue

                # Check spread
                if validated_option.spread_pct > self.config.validation.max_spread_pct:
                    logger.debug(
                        f"{validated_option.symbol}: Spread too wide "
                        f"({validated_option.spread_pct:.1%})"
                    )
                    report.add_rejection(
                        symbol=validated_option.symbol,
                        strike=validated_option.strike,
                        expiration=validated_option.expiration,
                        dte=validated_option.dte,
                        reason="spread",
                        spread_pct=validated_option.spread_pct,
                        margin_efficiency=validated_option.margin_efficiency,
                        trend=validated_option.trend,
                        stock_price=validated_option.stock_price,
                        bid=validated_option.ibkr_bid,
                        ask=validated_option.ibkr_ask,
                    )
                    continue

                # Check margin efficiency
                if (
                    validated_option.margin_efficiency
                    < self.config.validation.min_margin_efficiency
                ):
                    logger.debug(
                        f"{validated_option.symbol}: Margin efficiency too low "
                        f"({validated_option.margin_efficiency:.2%})"
                    )
                    report.add_rejection(
                        symbol=validated_option.symbol,
                        strike=validated_option.strike,
                        expiration=validated_option.expiration,
                        dte=validated_option.dte,
                        reason="margin",
                        spread_pct=validated_option.spread_pct,
                        margin_efficiency=validated_option.margin_efficiency,
                        trend=validated_option.trend,
                        stock_price=validated_option.stock_price,
                        bid=validated_option.ibkr_bid,
                        ask=validated_option.ibkr_ask,
                    )
                    continue

                # Check trend if required
                if self.config.validation.require_uptrend:
                    if validated_option.trend != "uptrend":
                        logger.debug(
                            f"{validated_option.symbol}: Not in uptrend "
                            f"(trend: {validated_option.trend})"
                        )
                        report.add_rejection(
                            symbol=validated_option.symbol,
                            strike=validated_option.strike,
                            expiration=validated_option.expiration,
                            dte=validated_option.dte,
                            reason="trend",
                            spread_pct=validated_option.spread_pct,
                            margin_efficiency=validated_option.margin_efficiency,
                            trend=validated_option.trend,
                            stock_price=validated_option.stock_price,
                            bid=validated_option.ibkr_bid,
                            ask=validated_option.ibkr_ask,
                        )
                        continue

                # Check IV Rank (if available)
                iv_rank_min = self.config.validation.iv_rank_min
                if validated_option.iv_rank is not None:
                    if validated_option.iv_rank < iv_rank_min:
                        logger.debug(
                            f"{validated_option.symbol}: IV Rank too low "
                            f"({validated_option.iv_rank:.0%} < {iv_rank_min:.0%})"
                        )
                        report.add_rejection(
                            symbol=validated_option.symbol,
                            strike=validated_option.strike,
                            expiration=validated_option.expiration,
                            dte=validated_option.dte,
                            reason="iv_rank",
                            spread_pct=validated_option.spread_pct,
                            margin_efficiency=validated_option.margin_efficiency,
                            trend=validated_option.trend,
                            stock_price=validated_option.stock_price,
                            bid=validated_option.ibkr_bid,
                            ask=validated_option.ibkr_ask,
                        )
                        continue
                else:
                    logger.warning(
                        f"{validated_option.symbol}: IV Rank unavailable "
                        f"(historical IV data missing) — passing through"
                    )

                validated.append(validated_option)

            except Exception as e:
                logger.warning(
                    f"Error validating {result.underlying_symbol} "
                    f"${result.strike}: {e}"
                )
                report.add_rejection(
                    symbol=result.underlying_symbol,
                    strike=result.strike,
                    expiration=result.expiration_date,
                    dte=(
                        datetime.strptime(result.expiration_date, "%Y-%m-%d").date()
                        - us_trading_date()
                    ).days,
                    reason="no_data",
                )
                continue

        # Update report with final counts
        report.passed_count = len(validated)

        logger.info(
            f"Validation complete: {len(validated)} passed, "
            f"rejected: {report.rejected_no_data} no data, {report.rejected_spread} spread, "
            f"{report.rejected_margin} margin, {report.rejected_trend} trend, "
            f"{report.rejected_iv_rank} iv_rank"
        )

        # Sort by margin efficiency (best opportunities first)
        validated.sort(key=lambda x: x.margin_efficiency, reverse=True)

        return validated, report

    def _validate_single(self, result: BarchartScanResult) -> Optional[ValidatedOption]:
        """Validate a single option with IBKR.

        Args:
            result: Barchart scan result to validate

        Returns:
            ValidatedOption if successful, None if validation fails
        """
        # Get real-time stock price
        stock_price = self._get_stock_price(result.underlying_symbol)
        if stock_price is None:
            logger.debug(f"{result.underlying_symbol}: Could not get stock price")
            return None

        # Get real-time option quote
        option_data = self._get_option_quote(
            result.underlying_symbol,
            result.strike,
            result.expiration_date,
        )

        if option_data is None:
            logger.warning(
                f"{result.underlying_symbol} ${result.strike} exp {result.expiration_date}: "
                "Could not get option quote from IBKR - contract may not exist or market closed"
            )
            return None

        bid, ask = option_data

        # Calculate spread
        mid = (bid + ask) / 2
        spread_pct = (ask - bid) / mid if mid > 0 else 1.0

        # Calculate margin requirement
        margin_required = self._estimate_margin(stock_price, result.strike, mid)

        # Calculate margin efficiency
        margin_efficiency = (mid * 100) / margin_required if margin_required > 0 else 0

        # Check trend (cached to avoid excessive API calls)
        trend = self._check_trend(result.underlying_symbol)

        # Compute IV Rank from historical IV data
        iv_rank = self._compute_iv_rank(result.underlying_symbol)

        return ValidatedOption(
            barchart_result=result,
            ibkr_bid=bid,
            ibkr_ask=ask,
            spread_pct=spread_pct,
            margin_required=margin_required,
            margin_efficiency=margin_efficiency,
            trend=trend,
            stock_price=stock_price,
            iv_rank=iv_rank,
        )

    def _get_stock_price(self, symbol: str) -> Optional[float]:
        """Get current stock price from IBKR.

        Args:
            symbol: Stock symbol

        Returns:
            Current price or None if unavailable
        """
        return self.ibkr_client.get_stock_price(symbol)

    def _get_option_quote(
        self,
        symbol: str,
        strike: float,
        expiration: str,
    ) -> Optional[tuple[float, float]]:
        """Get real-time bid/ask for option from IBKR.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date (YYYY-MM-DD format)

        Returns:
            Tuple of (bid, ask) or None if unavailable
        """
        try:
            # Convert date format YYYY-MM-DD to YYYYMMDD
            exp_formatted = expiration.replace("-", "")

            contract = Option(
                symbol=symbol,
                lastTradeDateOrContractMonth=exp_formatted,
                strike=strike,
                right="P",  # Put option
                exchange="SMART",
                currency="USD",
            )

            qualified = self.ibkr_client.ib.qualifyContracts(contract)
            if not qualified or not qualified[0].conId:
                logger.debug(
                    f"{symbol} ${strike} {expiration}: Contract qualification failed - "
                    "contract may not exist or invalid strike/expiration"
                )
                return None

            ticker = self.ibkr_client.ib.reqMktData(qualified[0], snapshot=True)
            self.ibkr_client.ib.sleep(2.5)  # Increased for delayed data to populate

            bid, ask = safe_bid_ask(ticker)

            # Diagnostic logging for why quotes are missing
            if bid is None and ask is None:
                # Log detailed diagnostics
                diag_msg = (
                    f"{symbol} ${strike} {expiration}: No bid/ask quotes from IBKR. "
                    f"Ticker: bid={ticker.bid}, ask={ticker.ask}, last={ticker.last}, "
                    f"close={ticker.close}, marketDataType={ticker.marketDataType}, "
                    f"halted={ticker.halted}. "
                    f"Contract ID: {qualified[0].conId}"
                )
                logger.warning(diag_msg)

                # Also print to console for immediate visibility
                print(f"  [DEBUG] {diag_msg}")
            elif bid is None:
                logger.warning(
                    f"{symbol} ${strike} {expiration}: No bid price (ask={ask}). "
                    "Contract may be illiquid."
                )
            elif ask is None:
                logger.warning(
                    f"{symbol} ${strike} {expiration}: No ask price (bid={bid}). "
                    "Contract may be illiquid."
                )

            self.ibkr_client.ib.cancelMktData(qualified[0])

            if bid is None or ask is None:
                return None

            return (bid, ask)

        except Exception as e:
            logger.warning(f"Error getting option quote for {symbol} ${strike} {expiration}: {e}")
            return None

    def _get_actual_margin(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        premium: float,
    ) -> Optional[float]:
        """Get actual margin requirement from IBKR.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date (YYYY-MM-DD format)
            premium: Option premium (mid price)

        Returns:
            Actual margin in dollars, or None if unavailable
        """
        try:
            # Create and qualify option contract
            from ib_insync import Option

            exp_formatted = expiration.replace("-", "")  # Convert to YYYYMMDD
            contract = Option(
                symbol=symbol,
                lastTradeDateOrContractMonth=exp_formatted,
                strike=strike,
                right="P",
                exchange="SMART",
                currency="USD",
            )

            qualified = self.ibkr_client.ib.qualifyContracts(contract)
            if not qualified or not qualified[0].conId:
                logger.debug(f"Could not qualify contract for {symbol} ${strike}")
                return None

            # Get actual margin via whatIfOrder (with retry logic)
            return self.ibkr_client.get_actual_margin(qualified[0])

        except Exception as e:
            logger.debug(f"Error getting actual margin for {symbol} ${strike}: {e}")
            return None

    def _estimate_margin_fallback(
        self,
        stock_price: float,
        strike: float,
        premium: float,
    ) -> float:
        """Fallback margin estimate when whatIfOrder unavailable.

        Uses standard Reg-T formula. Note: Actual IBKR margin may be
        50-100% higher for volatile stocks.

        Uses standard IBKR margin formula for naked short puts:
        Margin = max(
            20% of stock price - OTM amount + premium,
            10% of stock price
        )

        Args:
            stock_price: Current stock price
            strike: Strike price
            premium: Option premium

        Returns:
            Estimated margin requirement per contract ($)
        """
        otm_amount = max(0, stock_price - strike)
        margin = (0.20 * stock_price - otm_amount + premium) * 100
        min_margin = 0.10 * stock_price * 100

        return max(margin, min_margin)

    def _check_trend(self, symbol: str) -> str:
        """Quick trend check using 20-day SMA.

        Uses simple trend detection:
        - Uptrend: Price > 2% above 20-day SMA
        - Downtrend: Price > 2% below 20-day SMA
        - Sideways: Within ±2% of SMA
        - Unknown: Insufficient data

        Args:
            symbol: Stock symbol

        Returns:
            Trend classification: uptrend, downtrend, sideways, or unknown
        """
        try:
            stock = self.ibkr_client.get_stock_contract(symbol)
            qualified = self.ibkr_client.qualify_contract(stock)
            if not qualified:
                return "unknown"

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
                return "unknown"

            closes = [bar.close for bar in bars]
            sma_20 = sum(closes[-20:]) / 20
            current = closes[-1]

            # Trend classification
            if current > sma_20 * 1.02:
                return "uptrend"
            elif current < sma_20 * 0.98:
                return "downtrend"
            return "sideways"

        except Exception as e:
            logger.debug(f"Error checking trend for {symbol}: {e}")
            return "unknown"

    def _compute_iv_rank(self, symbol: str) -> Optional[float]:
        """Compute IV Rank from IBKR 1-year historical IV data.

        IV Rank = (current_iv - 52wk_low_iv) / (52wk_high_iv - 52wk_low_iv)

        Uses IBKR's OPTION_IMPLIED_VOLATILITY historical data to get
        the 52-week IV range, then calculates where current IV sits.

        Args:
            symbol: Stock symbol

        Returns:
            IV Rank as float (0.0-1.0), or None if data unavailable
        """
        try:
            stock = self.ibkr_client.get_stock_contract(symbol)
            qualified = self.ibkr_client.qualify_contract(stock)
            if not qualified:
                logger.debug(f"{symbol}: Could not qualify contract for IV Rank")
                return None

            # Request 1 year of historical implied volatility
            bars = self.ibkr_client.ib.reqHistoricalData(
                qualified,
                endDateTime="",
                durationStr="1 Y",
                barSizeSetting="1 day",
                whatToShow="OPTION_IMPLIED_VOLATILITY",
                useRTH=True,
                formatDate=1,
            )

            if not bars or len(bars) < 20:
                logger.debug(
                    f"{symbol}: Insufficient IV history ({len(bars) if bars else 0} bars, need 20+)"
                )
                return None

            iv_values = [bar.close for bar in bars if bar.close > 0]
            if len(iv_values) < 20:
                logger.debug(f"{symbol}: Too few valid IV values ({len(iv_values)})")
                return None

            current_iv = iv_values[-1]
            low_iv = min(iv_values)
            high_iv = max(iv_values)

            if high_iv <= low_iv:
                logger.debug(f"{symbol}: IV range is zero (high={high_iv}, low={low_iv})")
                return None

            iv_rank = (current_iv - low_iv) / (high_iv - low_iv)

            logger.debug(
                f"{symbol}: IV Rank={iv_rank:.0%} "
                f"(current={current_iv:.2%}, low={low_iv:.2%}, high={high_iv:.2%}, "
                f"{len(iv_values)} days)"
            )

            return iv_rank

        except Exception as e:
            logger.debug(f"{symbol}: Error computing IV Rank: {e}")
            return None

    def enrich_manual_opportunity(self, opportunity: dict) -> Optional[dict]:
        """Enrich a manual opportunity with live IBKR data.

        Takes a manually entered opportunity and fetches real-time IBKR data
        to validate and enrich it. This ensures manual entries are validated
        against real market data, not just what the user typed.

        Args:
            opportunity: Manual opportunity dict with keys:
                - symbol: Stock symbol
                - strike: Strike price
                - expiration: Expiration date (datetime.date or string)
                - option_type: PUT or CALL
                - (other fields optional - will be overwritten)

        Returns:
            Enriched opportunity dict with live IBKR data, or None if failed

        Example:
            >>> manual_opp = {"symbol": "SLV", "strike": 80, "expiration": "2026-01-30", "option_type": "PUT"}
            >>> enriched = validator.enrich_manual_opportunity(manual_opp)
            >>> print(enriched["otm_pct"])  # Calculated from live stock price
            0.192
        """
        try:
            symbol = opportunity["symbol"]
            strike = opportunity["strike"]
            expiration = opportunity["expiration"]
            option_type = opportunity.get("option_type", "PUT")

            # Convert expiration to string format if needed
            if isinstance(expiration, datetime):
                exp_str = expiration.strftime("%Y-%m-%d")
            elif hasattr(expiration, "strftime"):  # datetime.date
                exp_str = expiration.strftime("%Y-%m-%d")
            else:
                exp_str = str(expiration)

            logger.info(
                f"Enriching manual opportunity: {symbol} ${strike} {option_type} exp {exp_str}"
            )

            # Step 1: Get current stock price
            stock_price = self._get_stock_price(symbol)
            if not stock_price:
                logger.warning(f"Could not get stock price for {symbol}")
                logger.warning(f"  → Enrichment failed at Step 1: Stock price lookup")
                return None

            # Step 2: Get option contract
            right = "P" if option_type == "PUT" else "C"
            exp_ibkr = exp_str.replace("-", "")  # Convert to YYYYMMDD

            contract = self.ibkr_client.get_option_contract(
                symbol=symbol,
                expiration=exp_ibkr,
                strike=strike,
                right=right,
            )

            qualified = self.ibkr_client.qualify_contract(contract)
            if not qualified:
                logger.warning(
                    f"Could not qualify option contract: {symbol} ${strike} {option_type}"
                )
                logger.warning(
                    f"  → Enrichment failed at Step 2: Option contract qualification"
                )
                logger.warning(
                    f"  → Contract details: symbol={symbol}, exp={exp_ibkr}, strike={strike}, right={right}"
                )
                return None

            # Step 3: Get real-time option quotes (snapshot mode to avoid competing sessions)
            ticker = self.ibkr_client.ib.reqMktData(qualified, "", True, False)
            self.ibkr_client.ib.sleep(2)  # Wait for data

            bid = ticker.bid if ticker.bid and ticker.bid > 0 else None
            ask = ticker.ask if ticker.ask and ticker.ask > 0 else None

            # Capture Greeks from model (available even when market closed)
            delta = None
            iv = None
            if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                if hasattr(ticker.modelGreeks, "delta"):
                    delta = ticker.modelGreeks.delta
                if hasattr(ticker.modelGreeks, "impliedVol"):
                    iv = ticker.modelGreeks.impliedVol

            self.ibkr_client.ib.cancelMktData(qualified)

            # Determine pricing source
            pricing_source = "live"  # live market bid/ask

            # Fallback pricing if market is closed (bid/ask unavailable)
            if not bid or not ask:
                # Fallback 1: Use close price (last traded price)
                if hasattr(ticker, "close") and ticker.close and ticker.close > 0:
                    bid = ask = ticker.close
                    pricing_source = "close"
                    logger.info(
                        f"Using close price ${ticker.close:.2f} for {symbol} ${strike} (market closed)"
                    )

                # Fallback 2: Use last price
                elif hasattr(ticker, "last") and ticker.last and ticker.last > 0:
                    bid = ask = ticker.last
                    pricing_source = "last"
                    logger.info(
                        f"Using last price ${ticker.last:.2f} for {symbol} ${strike} (market closed)"
                    )

                # Fallback 3: Use model/theoretical price
                elif hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                    if (
                        hasattr(ticker.modelGreeks, "optPrice")
                        and ticker.modelGreeks.optPrice
                        and ticker.modelGreeks.optPrice > 0
                    ):
                        bid = ask = ticker.modelGreeks.optPrice
                        pricing_source = "model"
                        logger.info(
                            f"Using model price ${ticker.modelGreeks.optPrice:.2f} for {symbol} ${strike} (market closed)"
                        )

            # If still no pricing available, fail
            if not bid or not ask:
                logger.warning(f"No pricing data available for {symbol} ${strike}")
                logger.warning(
                    f"  → Enrichment failed at Step 3: No bid/ask, close, last, or model price"
                )
                logger.warning(f"  → Ticker data: {ticker}")
                return None

            # Step 4: Calculate metrics
            premium = (bid + ask) / 2
            spread_pct = (ask - bid) / premium if premium > 0 else 0.0

            # Calculate OTM percentage
            if option_type == "PUT":
                otm_pct = (stock_price - strike) / stock_price if stock_price > 0 else 0
            else:  # CALL
                otm_pct = (strike - stock_price) / stock_price if stock_price > 0 else 0
            otm_pct = max(0, otm_pct)

            # Calculate DTE
            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - us_trading_date()).days

            # Step 5: Get trend
            trend = self._check_trend(symbol)

            # Step 6: Get margin requirement (try IBKR whatIfOrder first, fall back to estimation)
            margin_required = None
            margin_source = "unknown"

            # Try to get actual margin from IBKR (works during AND after market hours)
            margin_required = self._get_actual_margin(symbol, strike, exp_str, premium)
            if margin_required and margin_required > 0:
                margin_source = "ibkr"
                logger.debug(f"Got actual margin from IBKR: ${margin_required:.2f}")
            else:
                # Fall back to estimation if IBKR failed
                margin_required = self._estimate_margin_fallback(stock_price, strike, premium)
                margin_source = "estimated"
                logger.debug(f"Using estimated margin: ${margin_required:.2f}")

            margin_efficiency = (
                (premium * 100) / margin_required if margin_required > 0 else 0
            )

            # Create enriched opportunity
            enriched = opportunity.copy()
            enriched.update(
                {
                    "stock_price": stock_price,
                    "bid": bid,
                    "ask": ask,
                    "premium": premium,
                    "spread_pct": spread_pct,
                    "otm_pct": otm_pct,
                    "dte": dte,
                    "trend": trend,
                    "margin_required": margin_required,
                    "margin_efficiency": margin_efficiency,
                    "expiration": exp_str,  # Normalize to string
                    "pricing_source": pricing_source,  # Track where price came from
                    "margin_source": margin_source,  # Track if margin is real or estimated
                }
            )

            # Add Greeks if available
            if delta is not None:
                enriched["delta"] = delta
            if iv is not None:
                enriched["iv"] = iv

            # Build log message
            log_parts = [
                f"✓ Enriched {symbol} ${strike}:",
                f"stock=${stock_price:.2f}",
                f"OTM={otm_pct*100:.1f}%",
                f"premium=${premium:.2f}",
                f"margin=${margin_required:.0f}({margin_source})",
                f"trend={trend}",
                f"price_src={pricing_source}",
            ]
            if delta is not None:
                log_parts.append(f"delta={delta:.3f}")
            if iv is not None:
                log_parts.append(f"IV={iv:.2%}")

            logger.info(" ".join(log_parts))

            return enriched

        except Exception as e:
            logger.error(f"Error enriching manual opportunity: {e}", exc_info=True)
            return None
