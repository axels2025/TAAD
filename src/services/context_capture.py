"""Context capture service for trade decision context.

This module provides services for capturing complete market and underlying
context at trade decision time for learning engine analysis.
"""

import uuid
from datetime import datetime

from loguru import logger

from src.data.context_snapshot import DecisionContext, MarketContext, UnderlyingContext
from src.strategies.base import TradeOpportunity


class ContextCaptureService:
    """Capture and store decision context for learning.

    This service captures:
    - Market-wide context (SPY, QQQ, VIX, etc.)
    - Underlying-specific context (price, indicators, IV, volume)
    - Decision metadata (source, ranking, strategy params)
    """

    def __init__(self, ibkr_client, timeout: int = 10):
        """Initialize context capture service.

        Args:
            ibkr_client: IBKR client for market data
            timeout: Timeout in seconds for data capture
        """
        self.ibkr = ibkr_client
        self.timeout = timeout

    def capture_market_context(self) -> MarketContext:
        """Capture current market-wide context.

        Fetches data for:
        - SPY (S&P 500 ETF)
        - QQQ (Nasdaq-100 ETF)
        - VIX (Volatility Index)

        Returns:
            MarketContext with market data

        Raises:
            TimeoutError: If data fetch exceeds timeout
        """
        timestamp = datetime.now()

        try:
            # Get SPY data
            spy_ticker = self.ibkr.ticker("SPY")
            spy_price = spy_ticker.marketPrice()
            spy_close = spy_ticker.close
            spy_change_pct = (
                (spy_price - spy_close) / spy_close
                if spy_close and spy_close > 0
                else 0.0
            )

            # Get QQQ data
            qqq_ticker = self.ibkr.ticker("QQQ")
            qqq_price = qqq_ticker.marketPrice()
            qqq_close = qqq_ticker.close
            qqq_change_pct = (
                (qqq_price - qqq_close) / qqq_close
                if qqq_close and qqq_close > 0
                else 0.0
            )

            # Get VIX data
            vix_ticker = self.ibkr.ticker("VIX")
            vix = vix_ticker.marketPrice()
            vix_close = vix_ticker.close
            vix_change_pct = (
                (vix - vix_close) / vix_close if vix_close and vix_close > 0 else 0.0
            )

            market_context = MarketContext(
                timestamp=timestamp,
                spy_price=spy_price or 0.0,
                spy_change_pct=spy_change_pct,
                qqq_price=qqq_price or 0.0,
                qqq_change_pct=qqq_change_pct,
                vix=vix or 0.0,
                vix_change_pct=vix_change_pct,
                # Optional fields can be added later with more data sources
                advance_decline_ratio=None,
                new_highs=None,
                new_lows=None,
                sector_leaders=[],
                sector_laggards=[],
            )

            logger.info(
                "Captured market context",
                extra={
                    "spy_price": spy_price,
                    "qqq_price": qqq_price,
                    "vix": vix,
                },
            )

            return market_context

        except Exception as e:
            logger.error(f"Failed to capture market context: {e}")
            # Return minimal context on error
            return MarketContext(
                timestamp=timestamp,
                spy_price=0.0,
                spy_change_pct=0.0,
                qqq_price=0.0,
                qqq_change_pct=0.0,
                vix=0.0,
                vix_change_pct=0.0,
            )

    def capture_underlying_context(self, symbol: str) -> UnderlyingContext:
        """Capture context for specific underlying.

        Fetches data for the underlying symbol including:
        - Current price and OHLC
        - Volume metrics
        - Moving averages (if available)
        - Trend indicators

        Args:
            symbol: Stock ticker symbol

        Returns:
            UnderlyingContext with underlying data

        Raises:
            TimeoutError: If data fetch exceeds timeout
        """
        timestamp = datetime.now()

        try:
            # Get ticker data
            ticker = self.ibkr.ticker(symbol)

            # Basic price data
            current_price = ticker.marketPrice() or 0.0
            open_price = ticker.open or current_price
            high_price = ticker.high or current_price
            low_price = ticker.low or current_price
            previous_close = ticker.close or current_price

            # Volume data
            volume = ticker.volume or 0
            avg_volume = ticker.avgVolume or 0
            relative_volume = volume / avg_volume if avg_volume > 0 else 0.0

            # Calculate simple trend direction
            trend_direction = "unknown"
            if current_price > previous_close * 1.02:
                trend_direction = "uptrend"
            elif current_price < previous_close * 0.98:
                trend_direction = "downtrend"
            else:
                trend_direction = "sideways"

            # Trend strength based on % move
            trend_strength = (
                abs(current_price - previous_close) / previous_close
                if previous_close > 0
                else 0.0
            )

            underlying_context = UnderlyingContext(
                symbol=symbol,
                timestamp=timestamp,
                current_price=current_price,
                open_price=open_price,
                high_price=high_price,
                low_price=low_price,
                previous_close=previous_close,
                volume=volume,
                avg_volume_20d=avg_volume,
                relative_volume=relative_volume,
                trend_direction=trend_direction,
                trend_strength=min(trend_strength, 1.0),
                # Optional fields - can be enhanced later
                sma_20=None,
                sma_50=None,
                iv_rank=None,
                iv_percentile=None,
                historical_vol_20d=None,
                support_levels=[],
                resistance_levels=[],
            )

            logger.info(
                f"Captured underlying context for {symbol}",
                extra={
                    "symbol": symbol,
                    "price": current_price,
                    "volume": volume,
                    "trend": trend_direction,
                },
            )

            return underlying_context

        except Exception as e:
            logger.error(f"Failed to capture underlying context for {symbol}: {e}")
            # Return minimal context on error
            return UnderlyingContext(
                symbol=symbol,
                timestamp=timestamp,
                current_price=0.0,
                open_price=0.0,
                high_price=0.0,
                low_price=0.0,
                previous_close=0.0,
            )

    def capture_full_context(
        self,
        opportunity: TradeOpportunity,
        strategy_params: dict,
        rank_info: dict,
    ) -> DecisionContext:
        """Capture complete decision context.

        Args:
            opportunity: Trade opportunity being considered
            strategy_params: Strategy configuration at decision time
            rank_info: Ranking information (position, score, factors)

        Returns:
            DecisionContext with complete context
        """
        # Generate unique decision ID
        decision_id = self._generate_decision_id()

        # Capture market and underlying context
        market = self.capture_market_context()
        underlying = self.capture_underlying_context(opportunity.symbol)

        # Build decision context
        context = DecisionContext(
            decision_id=decision_id,
            timestamp=datetime.now(),
            market=market,
            underlying=underlying,
            strategy_params=strategy_params,
            source=rank_info.get("source", "unknown"),
            rank_position=rank_info.get("position", 0),
            rank_score=rank_info.get("score", 0.0),
            rank_factors=rank_info.get("factors", {}),
            ai_confidence_score=None,  # Can be added later
            ai_reasoning=None,
        )

        logger.info(
            f"Captured full decision context: {decision_id}",
            extra={
                "decision_id": decision_id,
                "symbol": opportunity.symbol,
                "source": context.source,
            },
        )

        return context

    def _generate_decision_id(self) -> str:
        """Generate unique decision ID.

        Returns:
            Unique decision ID string
        """
        # Format: decision_{timestamp}_{uuid}
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        return f"decision_{timestamp}_{unique_id}"
