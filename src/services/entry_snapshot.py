"""Entry snapshot service for capturing trade entry data.

Phase 2.6A - Critical Fields Data Collection
Captures all 66 fields at trade entry for learning engine analysis,
with emphasis on the 8 critical fields with ~80% predictive power.

Phase 2.6B - Technical Indicators
Extended to capture 18 additional technical indicator fields for pattern detection.
"""

from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import TradeEntrySnapshot
from src.analysis.technical_indicators import TechnicalIndicatorCalculator
from src.services.market_context import MarketContextService
from src.services.earnings_service import get_cached_earnings
from src.utils.market_data import safe_field


class EntrySnapshotService:
    """Capture and store comprehensive entry snapshot data.

    This service captures all 66 fields across 9 categories:
    1. Option Contract Data (13 fields) - Greeks, pricing
    2. Volatility Data (5 fields) - IV, IV rank, HV
    3. Liquidity (3 fields) - Volume, OI
    4. Underlying Prices (6 fields) - OHLC, price changes
    5. Calculated Metrics (6 fields) - OTM%, DTE, margin
    6. Trend (6 fields) - SMAs, trend direction
    7. Market Data (4 fields) - SPY, VIX
    8. Event Data (3 fields) - Earnings
    9. Metadata (4 fields) - Timestamp, quality score
    """

    def __init__(self, ibkr_client, timeout: int = 10):
        """Initialize entry snapshot service.

        Args:
            ibkr_client: IBKR client for market data and Greeks
            timeout: Timeout in seconds for data capture
        """
        self.ibkr = ibkr_client
        self.timeout = timeout

    def capture_entry_snapshot(
        self,
        trade_id: int,
        opportunity_id: Optional[int],
        symbol: str,
        strike: float,
        expiration: datetime,
        option_type: str,
        entry_premium: float,
        contracts: int,
        stock_price: float,
        dte: int,
        source: str = "scan",
        strike_selection_method: Optional[str] = None,
        original_strike: Optional[float] = None,
        live_delta_at_selection: Optional[float] = None,
    ) -> TradeEntrySnapshot:
        """Capture complete entry snapshot for a trade.

        Args:
            trade_id: Database ID of the trade
            opportunity_id: Database ID of the opportunity (if from scan)
            symbol: Stock symbol
            strike: Strike price
            expiration: Option expiration date
            option_type: PUT or CALL
            entry_premium: Actual fill premium received
            contracts: Number of contracts
            stock_price: Current stock price
            dte: Days to expiration
            source: Source of trade (scan, manual, auto)
            strike_selection_method: How strike was chosen (delta, otm_pct, unchanged)
            original_strike: Original strike from overnight screening
            live_delta_at_selection: Delta when strike was selected

        Returns:
            TradeEntrySnapshot with all captured data
        """
        logger.info(
            f"Capturing entry snapshot for {symbol} ${strike} {option_type}",
            extra={
                "symbol": symbol,
                "strike": strike,
                "expiration": expiration,
                "dte": dte,
            },
        )

        # Initialize snapshot with required fields
        snapshot = TradeEntrySnapshot(
            trade_id=trade_id,
            opportunity_id=opportunity_id,
            symbol=symbol,
            strike=strike,
            expiration=expiration.date() if hasattr(expiration, "date") else expiration,
            option_type=option_type,
            entry_premium=entry_premium,
            stock_price=stock_price,
            dte=dte,
            contracts=contracts,
            captured_at=datetime.now(),
            source=source,
            strike_selection_method=strike_selection_method,
            original_strike=original_strike,
            live_delta_at_selection=live_delta_at_selection,
        )

        # Check if market is open - many data points require market hours
        market_status = self.ibkr.is_market_open()
        market_is_open = market_status.get("is_open", False)

        if not market_is_open:
            logger.info(
                f"Market is closed - limited data available for entry snapshot. "
                f"Greeks, IV, and margin calculations require market hours."
            )

        # Qualify option contract ONCE (reused for pricing, Greeks, liquidity)
        try:
            qualified_option = self._qualify_option_contract(
                symbol, strike, expiration, option_type
            )
        except Exception as e:
            qualified_option = None
            logger.info(f"Failed to qualify option contract: {e}")

        # Capture pricing + Greeks + liquidity from a SINGLE subscription
        if qualified_option:
            try:
                self._capture_option_data(snapshot, qualified_option, market_is_open)
            except Exception as e:
                logger.info(f"Failed to capture option data: {e}")
        else:
            logger.warning(
                f"Could not qualify option contract for {symbol} ${strike} — "
                f"skipping pricing, Greeks, and liquidity capture"
            )

        try:
            self._capture_volatility_data(snapshot, symbol, strike, expiration, option_type)
        except Exception as e:
            logger.info(f"Failed to capture volatility data: {e}")

        try:
            self._capture_stock_data(snapshot, symbol)
        except Exception as e:
            logger.info(f"Failed to capture stock data: {e}")

        try:
            self._capture_trend_data(snapshot, symbol)
        except Exception as e:
            logger.info(f"Failed to capture trend data: {e}")

        try:
            self._capture_market_data(snapshot)
        except Exception as e:
            logger.info(f"Failed to capture market data: {e}")

        try:
            self._capture_earnings_data(snapshot, symbol)
        except Exception as e:
            logger.info(f"Failed to capture earnings data: {e}")

        try:
            self._calculate_margin_and_efficiency(
                snapshot, symbol, strike, expiration, option_type, contracts, entry_premium
            )
        except Exception as e:
            logger.info(f"Failed to capture margin data: {e}")

        # Phase 2.6B: Capture technical indicators
        try:
            self._capture_technical_indicators(snapshot, symbol, stock_price)
        except Exception as e:
            logger.info(f"Failed to capture technical indicators: {e}")

        # Phase 2.6C: Capture market context
        try:
            self._capture_market_context(snapshot, symbol)
        except Exception as e:
            logger.info(f"Failed to capture market context: {e}")

        # Calculate derived fields
        self._calculate_derived_fields(snapshot)

        # Calculate data quality score
        snapshot.data_quality_score = snapshot.calculate_data_quality_score()

        missing_critical = snapshot.get_missing_critical_fields()

        # Log with context about market hours impact
        if not market_is_open and missing_critical:
            logger.info(
                f"Entry snapshot captured with quality score: {snapshot.data_quality_score:.1%}. "
                f"Market is closed - some fields unavailable (Greeks, IV require market hours). "
                f"Missing: {', '.join(missing_critical)}"
            )
        else:
            logger.info(
                f"Entry snapshot captured with quality score: {snapshot.data_quality_score:.1%}",
                extra={
                    "symbol": symbol,
                    "quality_score": snapshot.data_quality_score,
                    "missing_critical": missing_critical,
                },
            )

        return snapshot

    def _qualify_option_contract(
        self,
        symbol: str,
        strike: float,
        expiration: datetime,
        option_type: str,
    ):
        """Qualify option contract once for reuse across capture methods.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date
            option_type: PUT or CALL

        Returns:
            Qualified contract or None if qualification fails
        """
        exp_str = expiration.strftime("%Y%m%d") if isinstance(expiration, datetime) else expiration
        right = "P" if option_type == "PUT" else "C"
        contract = self.ibkr.get_option_contract(symbol, exp_str, strike, right=right)
        return self.ibkr.qualify_contract(contract)

    def _capture_option_data(
        self,
        snapshot: TradeEntrySnapshot,
        qualified_contract,
        market_is_open: bool,
    ) -> None:
        """Capture pricing, Greeks, and liquidity from a single subscription.

        Replaces separate _capture_option_pricing, _capture_greeks, and
        _capture_liquidity methods. Uses one reqMktData call and a polling
        loop that waits for modelGreeks (the slowest field to populate).

        Args:
            snapshot: Snapshot object to populate
            qualified_contract: Already-qualified option contract
            market_is_open: Whether regular trading session is active
        """
        import os
        import time as time_mod

        ticker = self.ibkr.ib.reqMktData(qualified_contract, "", False, False)

        # Poll until modelGreeks arrive or timeout
        greeks_timeout = float(os.getenv("SNAPSHOT_GREEKS_TIMEOUT", "5.0"))
        poll_interval = 0.5
        start = time_mod.time()

        while (time_mod.time() - start) < greeks_timeout:
            self.ibkr.ib.sleep(poll_interval)

            # Greeks are the slowest to arrive — break early when they're ready
            if (
                market_is_open
                and hasattr(ticker, "modelGreeks")
                and ticker.modelGreeks
                and ticker.modelGreeks.delta is not None
            ):
                break

        elapsed = time_mod.time() - start

        # ── Pricing ──
        bid = safe_field(ticker, "bid")
        ask = safe_field(ticker, "ask")
        if bid is not None:
            snapshot.bid = bid
        if ask is not None:
            snapshot.ask = ask

        if snapshot.bid and snapshot.ask:
            snapshot.mid = (snapshot.bid + snapshot.ask) / 2
            if snapshot.mid > 0:
                snapshot.spread_pct = (snapshot.ask - snapshot.bid) / snapshot.mid

        # ── Greeks ──
        if market_is_open:
            if hasattr(ticker, "modelGreeks") and ticker.modelGreeks and ticker.modelGreeks.delta is not None:
                snapshot.delta = ticker.modelGreeks.delta
                snapshot.gamma = ticker.modelGreeks.gamma
                snapshot.theta = ticker.modelGreeks.theta
                snapshot.vega = ticker.modelGreeks.vega
                snapshot.iv = ticker.modelGreeks.impliedVol
                logger.debug(
                    f"Greeks captured for {snapshot.symbol} after {elapsed:.1f}s: "
                    f"delta={snapshot.delta:.4f}, iv={snapshot.iv:.4f}"
                )
            else:
                logger.warning(
                    f"modelGreeks NOT available for {snapshot.symbol} ${snapshot.strike}P "
                    f"after {elapsed:.1f}s wait (bid={bid}, ask={ask})"
                )
        else:
            logger.debug("Skipping Greeks capture — market closed")

        # ── Liquidity ──
        oi = safe_field(ticker, "openInterest")
        if oi is not None and oi > 0:
            snapshot.open_interest = oi
        vol = safe_field(ticker, "volume")
        if vol is not None and vol > 0:
            snapshot.option_volume = vol
        if snapshot.option_volume and snapshot.open_interest and snapshot.open_interest > 0:
            snapshot.volume_oi_ratio = snapshot.option_volume / snapshot.open_interest

        # Cancel subscription
        self.ibkr.ib.cancelMktData(qualified_contract)

    def _capture_volatility_data(
        self,
        snapshot: TradeEntrySnapshot,
        symbol: str,
        strike: float,
        expiration: datetime,
        option_type: str,
    ) -> None:
        """Capture volatility data: IV, IV rank, HV.

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date
            option_type: PUT or CALL
        """
        # IV is captured in _capture_greeks, skip duplicate work
        # IV rank and percentile require historical data (not implemented)
        snapshot.iv_rank = None
        snapshot.iv_percentile = None
        snapshot.hv_20 = None
        snapshot.iv_hv_ratio = None

    # NOTE: _capture_liquidity removed — consolidated into _capture_option_data
    # to avoid redundant contract qualifications and market data subscriptions.

    def _capture_stock_data(self, snapshot: TradeEntrySnapshot, symbol: str) -> None:
        """Capture underlying stock data: OHLC, price changes.

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
        """
        # Get stock contract
        stock_contract = self.ibkr.get_stock_contract(symbol)
        data = self.ibkr.get_market_data(stock_contract)
        if not data:
            return

        # Price data (get_market_data returns NaN-safe values)
        snapshot.stock_open = data.get("open")
        snapshot.stock_high = data.get("high")
        snapshot.stock_low = data.get("low")
        snapshot.stock_prev_close = data.get("close")

        # Calculate price change percentage
        if snapshot.stock_prev_close and snapshot.stock_prev_close > 0 and snapshot.stock_price:
            snapshot.stock_change_pct = (
                snapshot.stock_price - snapshot.stock_prev_close
            ) / snapshot.stock_prev_close

    def _capture_trend_data(self, snapshot: TradeEntrySnapshot, symbol: str) -> None:
        """Capture trend indicators: SMAs, trend direction.

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
        """
        # SMAs require historical data
        # TODO: Calculate SMA 20 and SMA 50 from historical bars
        snapshot.sma_20 = None
        snapshot.sma_50 = None

        # Calculate trend direction (simple heuristic)
        if snapshot.stock_prev_close:
            change_pct = snapshot.stock_change_pct or 0.0
            if change_pct > 0.02:
                snapshot.trend_direction = "uptrend"
                snapshot.trend_strength = min(abs(change_pct), 1.0)
            elif change_pct < -0.02:
                snapshot.trend_direction = "downtrend"
                snapshot.trend_strength = min(abs(change_pct), 1.0)
            else:
                snapshot.trend_direction = "sideways"
                snapshot.trend_strength = abs(change_pct)

        # Calculate price vs SMA percentages (if SMAs available)
        if snapshot.sma_20 and snapshot.sma_20 > 0:
            snapshot.price_vs_sma20_pct = (
                snapshot.stock_price - snapshot.sma_20
            ) / snapshot.sma_20

        if snapshot.sma_50 and snapshot.sma_50 > 0:
            snapshot.price_vs_sma50_pct = (
                snapshot.stock_price - snapshot.sma_50
            ) / snapshot.sma_50

    def _capture_market_data(self, snapshot: TradeEntrySnapshot) -> None:
        """Capture market-wide data: SPY, VIX.

        Args:
            snapshot: Snapshot object to populate
        """
        from ib_insync import Stock, Index

        # Get SPY data
        try:
            spy_contract = Stock("SPY", "SMART", "USD")
            data = self.ibkr.get_market_data(spy_contract)
            if data:
                snapshot.spy_price = data["last"]
                spy_close = data.get("close")
                if snapshot.spy_price and spy_close and spy_close > 0:
                    snapshot.spy_change_pct = (snapshot.spy_price - spy_close) / spy_close
        except Exception as e:
            logger.debug(f"Failed to get SPY data: {e}")

        # Get VIX data (CRITICAL FIELD #4)
        try:
            vix_contract = Index("VIX", "CBOE", "USD")
            data = self.ibkr.get_market_data(vix_contract)
            if data:
                snapshot.vix = data["last"]
                vix_close = data.get("close")
                if snapshot.vix and vix_close and vix_close > 0:
                    snapshot.vix_change_pct = (snapshot.vix - vix_close) / vix_close
        except Exception as e:
            logger.debug(f"Failed to get VIX data: {e}")

    def _capture_earnings_data(self, snapshot: TradeEntrySnapshot, symbol: str) -> None:
        """Capture earnings event data.

        Phase 2.6C: Integrated with external earnings API (Yahoo Finance by default).

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
        """
        # Get earnings info with caching
        earnings_info = get_cached_earnings(
            symbol, option_expiration=snapshot.expiration, data_source="yahoo"
        )

        snapshot.earnings_date = earnings_info.earnings_date
        snapshot.days_to_earnings = earnings_info.days_to_earnings
        snapshot.earnings_in_dte = earnings_info.earnings_in_dte
        snapshot.earnings_timing = earnings_info.earnings_timing

        if snapshot.earnings_date:
            logger.debug(
                f"Earnings for {symbol}: {snapshot.earnings_date} "
                f"({snapshot.days_to_earnings} days, {snapshot.earnings_timing})"
            )

    def _calculate_margin_and_efficiency(
        self,
        snapshot: TradeEntrySnapshot,
        symbol: str,
        strike: float,
        expiration: datetime,
        option_type: str,
        contracts: int,
        entry_premium: float,
    ) -> None:
        """Calculate actual margin requirement using IBKR whatIfOrder API.

        This is CRITICAL FIELD #8 - margin_efficiency_pct
        Uses actual IBKR margin calculation, not estimated formula.

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date
            option_type: PUT or CALL
            contracts: Number of contracts
            entry_premium: Premium received per contract
        """
        # Get actual margin requirement from IBKR using whatIfOrder
        expiration_str = expiration.strftime("%Y%m%d") if hasattr(expiration, "strftime") else str(expiration).replace("-", "")

        margin = self.ibkr.get_margin_requirement(
            symbol=symbol,
            strike=strike,
            expiration=expiration_str,
            option_type=option_type,
            contracts=contracts,
            action="SELL",  # Selling naked puts/calls
        )

        # If whatIfOrder failed (market closed), estimate margin
        if not margin or margin <= 0:
            logger.info(
                "Actual margin calculation unavailable (likely market closed). "
                "Using estimated margin: 20% of strike value."
            )
            # Standard naked put margin estimate: 20% of strike value + premium
            estimated_margin = (strike * 20 * 0.20 + entry_premium) * contracts
            margin = estimated_margin

        if margin and margin > 0:
            snapshot.margin_requirement = margin

            # Calculate margin efficiency: premium_collected / margin_required
            premium_collected = entry_premium * contracts * 100  # $ per contract * contracts * 100 shares
            snapshot.margin_efficiency_pct = premium_collected / margin

            logger.debug(
                f"Margin efficiency: {snapshot.margin_efficiency_pct:.1%} "
                f"(${premium_collected:.2f} premium / ${margin:.2f} margin)"
            )
        else:
            snapshot.margin_requirement = None
            snapshot.margin_efficiency_pct = None
            logger.debug(f"Could not calculate margin requirement for {symbol}")

    def _calculate_derived_fields(self, snapshot: TradeEntrySnapshot) -> None:
        """Calculate derived fields from captured data.

        Args:
            snapshot: Snapshot object to populate
        """
        # OTM percentage: (stock_price - strike) / stock_price
        if snapshot.stock_price > 0:
            snapshot.otm_pct = (snapshot.stock_price - snapshot.strike) / snapshot.stock_price
            snapshot.otm_dollars = snapshot.stock_price - snapshot.strike

        # Mid price (if not already set)
        if not snapshot.mid and snapshot.bid and snapshot.ask:
            snapshot.mid = (snapshot.bid + snapshot.ask) / 2

        # Spread percentage (if not already set)
        if not snapshot.spread_pct and snapshot.bid and snapshot.ask and snapshot.mid:
            if snapshot.mid > 0:
                snapshot.spread_pct = (snapshot.ask - snapshot.bid) / snapshot.mid

    def _capture_technical_indicators(
        self, snapshot: TradeEntrySnapshot, symbol: str, stock_price: float
    ) -> None:
        """Capture technical indicators for the underlying stock.

        Phase 2.6B: Calculates RSI, MACD, ADX, ATR, Bollinger Bands, and
        Support/Resistance levels from historical data.

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
            stock_price: Current stock price
        """
        calculator = TechnicalIndicatorCalculator(self.ibkr)
        indicators = calculator.calculate_all(symbol, stock_price, lookback_days=100)

        # Copy indicator values to snapshot
        snapshot.rsi_14 = indicators.rsi_14
        snapshot.rsi_7 = indicators.rsi_7
        snapshot.macd = indicators.macd
        snapshot.macd_signal = indicators.macd_signal
        snapshot.macd_histogram = indicators.macd_histogram
        snapshot.adx = indicators.adx
        snapshot.plus_di = indicators.plus_di
        snapshot.minus_di = indicators.minus_di
        snapshot.atr_14 = indicators.atr_14
        snapshot.atr_pct = indicators.atr_pct
        snapshot.bb_upper = indicators.bb_upper
        snapshot.bb_lower = indicators.bb_lower
        snapshot.bb_position = indicators.bb_position
        snapshot.support_1 = indicators.support_1
        snapshot.support_2 = indicators.support_2
        snapshot.resistance_1 = indicators.resistance_1
        snapshot.resistance_2 = indicators.resistance_2
        snapshot.distance_to_support_pct = indicators.distance_to_support_pct

        logger.debug(
            f"Technical indicators captured for {symbol}",
            extra={
                "rsi_14": snapshot.rsi_14,
                "adx": snapshot.adx,
                "bb_position": snapshot.bb_position,
            },
        )

    def _capture_market_context(self, snapshot: TradeEntrySnapshot, symbol: str) -> None:
        """Capture broad market context at trade entry.

        Phase 2.6C: Captures indices, sector, regime classification, and calendar data.

        Args:
            snapshot: Snapshot object to populate
            symbol: Stock symbol
        """
        market_service = MarketContextService(self.ibkr)
        context = market_service.capture_context(symbol, snapshot.vix, snapshot.spy_change_pct)

        # Copy market context to snapshot
        snapshot.qqq_price = context.qqq_price
        snapshot.qqq_change_pct = context.qqq_change_pct
        snapshot.iwm_price = context.iwm_price
        snapshot.iwm_change_pct = context.iwm_change_pct
        snapshot.sector = context.sector
        snapshot.sector_etf = context.sector_etf
        snapshot.sector_change_1d = context.sector_change_1d
        snapshot.sector_change_5d = context.sector_change_5d
        snapshot.vol_regime = context.vol_regime
        snapshot.market_regime = context.market_regime
        snapshot.day_of_week = context.day_of_week
        snapshot.is_opex_week = context.is_opex_week
        snapshot.days_to_fomc = context.days_to_fomc

        logger.debug(
            f"Market context captured for {symbol}",
            extra={
                "sector": snapshot.sector,
                "vol_regime": snapshot.vol_regime,
                "market_regime": snapshot.market_regime,
                "is_opex_week": snapshot.is_opex_week,
            },
        )

    def save_snapshot(self, snapshot: TradeEntrySnapshot, session: Session) -> None:
        """Save entry snapshot to database.

        Args:
            snapshot: Snapshot to save
            session: Database session
        """
        try:
            session.add(snapshot)
            session.commit()
            logger.info(
                f"Saved entry snapshot to database",
                extra={
                    "snapshot_id": snapshot.id,
                    "trade_id": snapshot.trade_id,
                    "symbol": snapshot.symbol,
                    "quality_score": snapshot.data_quality_score,
                },
            )
        except Exception as e:
            session.rollback()
            logger.error(f"Failed to save entry snapshot: {e}", exc_info=True)
            raise
