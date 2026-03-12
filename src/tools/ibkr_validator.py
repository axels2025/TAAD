"""IBKR Option Validator.

Provides IBKR-based option enrichment and validation utilities:
- Real-time bid/ask quotes
- Margin requirements (actual via whatIfOrder or estimated via Reg-T)
- Trend analysis (20-day SMA)
- IV Rank computation (52-week percentile)
- Manual opportunity enrichment with live data
"""

from datetime import datetime, timedelta
from typing import Optional

from ib_insync import Option
from loguru import logger

from src.utils.timezone import us_trading_date
from src.tools.ibkr_client import IBKRClient
from src.utils.market_data import safe_bid_ask


class IBKRValidator:
    """IBKR option validation and enrichment utilities.

    Provides methods for enriching trade opportunities with real-time
    IBKR data including quotes, margin, trend, and IV rank.
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
    ):
        """Initialize IBKR validator.

        Args:
            ibkr_client: Connected IBKR client
        """
        self.ibkr_client = ibkr_client
        logger.info("Initialized IBKRValidator")

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
                diag_msg = (
                    f"{symbol} ${strike} {expiration}: No bid/ask quotes from IBKR. "
                    f"Ticker: bid={ticker.bid}, ask={ticker.ask}, last={ticker.last}, "
                    f"close={ticker.close}, marketDataType={ticker.marketDataType}, "
                    f"halted={ticker.halted}. "
                    f"Contract ID: {qualified[0].conId}"
                )
                logger.warning(diag_msg)
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

        Uses standard Reg-T formula for naked short puts:
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
        - Sideways: Within +/-2% of SMA
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
        to validate and enrich it.

        Args:
            opportunity: Manual opportunity dict with keys:
                - symbol: Stock symbol
                - strike: Strike price
                - expiration: Expiration date (datetime.date or string)
                - option_type: PUT or CALL
                - (other fields optional - will be overwritten)

        Returns:
            Enriched opportunity dict with live IBKR data, or None if failed
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
                return None

            # Step 3: Get real-time option quotes (snapshot mode)
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
            pricing_source = "live"

            # Fallback pricing if market is closed
            if not bid or not ask:
                if hasattr(ticker, "close") and ticker.close and ticker.close > 0:
                    bid = ask = ticker.close
                    pricing_source = "close"
                elif hasattr(ticker, "last") and ticker.last and ticker.last > 0:
                    bid = ask = ticker.last
                    pricing_source = "last"
                elif hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                    if (
                        hasattr(ticker.modelGreeks, "optPrice")
                        and ticker.modelGreeks.optPrice
                        and ticker.modelGreeks.optPrice > 0
                    ):
                        bid = ask = ticker.modelGreeks.optPrice
                        pricing_source = "model"

            if not bid or not ask:
                logger.warning(f"No pricing data available for {symbol} ${strike}")
                return None

            # Step 4: Calculate metrics
            premium = (bid + ask) / 2
            spread_pct = (ask - bid) / premium if premium > 0 else 0.0

            if option_type == "PUT":
                otm_pct = (stock_price - strike) / stock_price if stock_price > 0 else 0
            else:
                otm_pct = (strike - stock_price) / stock_price if stock_price > 0 else 0
            otm_pct = max(0, otm_pct)

            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
            dte = (exp_date - us_trading_date()).days

            # Step 5: Get trend
            trend = self._check_trend(symbol)

            # Step 6: Get margin requirement
            margin_required = None
            margin_source = "unknown"

            margin_required = self._get_actual_margin(symbol, strike, exp_str, premium)
            if margin_required and margin_required > 0:
                margin_source = "ibkr"
            else:
                margin_required = self._estimate_margin_fallback(stock_price, strike, premium)
                margin_source = "estimated"

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
                    "expiration": exp_str,
                    "pricing_source": pricing_source,
                    "margin_source": margin_source,
                }
            )

            if delta is not None:
                enriched["delta"] = delta
            if iv is not None:
                enriched["iv"] = iv

            log_parts = [
                f"Enriched {symbol} ${strike}:",
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
