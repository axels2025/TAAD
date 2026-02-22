"""Adaptive order executor with IBKR Adaptive Algo and LIMIT fallback.

This module implements institutional-grade order execution using IBKR's
Adaptive Algorithm as the primary strategy, with automatic fallback to
standard LIMIT orders when Adaptive is not supported.

The Adaptive Algo dynamically navigates the bid-ask spread for better fills,
while the LIMIT fallback provides compatibility for all option classes.
"""

import asyncio
import os
from dataclasses import dataclass
from enum import Enum

from ib_insync import Contract, LimitOrder, TagValue
from loguru import logger

from src.services.limit_price_calculator import LimitPriceCalculator
from src.services.market_calendar import MarketCalendar, MarketSession
from src.services.premarket_validator import StagedOpportunity
from src.tools.ibkr_client import IBKRClient, Quote


class OrderStatus(Enum):
    """Status of an order placement attempt."""

    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass
class LiveQuote:
    """Live market quote with tradeability assessment.

    Attributes:
        bid: Current bid price
        ask: Current ask price
        limit: Calculated limit price for selling
        is_tradeable: Whether the quote meets minimum premium requirements
        reason: Reason if not tradeable
    """

    bid: float
    ask: float
    limit: float
    is_tradeable: bool
    reason: str = ""

    @classmethod
    def from_quote(
        cls,
        quote: Quote,
        limit_calc: LimitPriceCalculator,
        min_premium: float,
    ) -> "LiveQuote":
        """Create LiveQuote from Quote object.

        Args:
            quote: Quote from IBKRClient
            limit_calc: Calculator for limit price
            min_premium: Minimum acceptable premium

        Returns:
            LiveQuote with tradeability assessment
        """
        if not quote.is_valid:
            return cls(
                bid=0,
                ask=0,
                limit=0,
                is_tradeable=False,
                reason=quote.reason,
            )

        limit = limit_calc.calculate_sell_limit(quote.bid, quote.ask)
        is_tradeable = limit >= min_premium
        reason = "" if is_tradeable else f"Premium ${limit:.2f} < min ${min_premium:.2f}"

        return cls(
            bid=quote.bid,
            ask=quote.ask,
            limit=limit,
            is_tradeable=is_tradeable,
            reason=reason,
        )


@dataclass
class OrderResult:
    """Result of an order placement attempt.

    Attributes:
        success: Whether order was placed successfully
        order_id: IBKR order ID (None if failed)
        status: Order status
        order_type: Type of order used (Adaptive, LIMIT, or LIMIT fallback)
        live_bid: Live bid price at placement
        live_ask: Live ask price at placement
        calculated_limit: Limit price calculated from live quotes
        staged_limit: Original staged limit price
        limit_deviation: Absolute difference between calculated and staged
        error_message: Error message if failed
    """

    success: bool
    order_id: int | None = None
    status: OrderStatus = OrderStatus.SUBMITTED
    order_type: str = "LIMIT"
    live_bid: float = 0.0
    live_ask: float = 0.0
    calculated_limit: float = 0.0
    staged_limit: float = 0.0
    limit_deviation: float = 0.0
    error_message: str | None = None


class AdaptiveOrderExecutor:
    """Execute orders using IBKR Adaptive Algo with LIMIT fallback.

    Primary Strategy: Adaptive Algo lets IBKR dynamically navigate the
    bid-ask spread for better fills.

    Fallback Strategy: Standard LIMIT order when Adaptive is not supported
    for specific option classes.

    Example:
        >>> executor = AdaptiveOrderExecutor(ibkr_client, limit_calculator)
        >>> quote = await executor.get_live_quote(contract)
        >>> if quote.is_tradeable:
        ...     result = await executor.place_order(staged_trade, contract, quote)
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        limit_calc: LimitPriceCalculator,
    ):
        """Initialize the adaptive order executor.

        Args:
            ibkr_client: IBKR client for order placement
            limit_calc: Calculator for limit prices from live quotes
        """
        self.client = ibkr_client
        self.limit_calc = limit_calc

        from src.config.base import get_config
        self.min_premium = get_config().premium_min
        self.use_adaptive = os.getenv("USE_ADAPTIVE_ALGO", "true").lower() == "true"
        self.max_execution_spread_pct = float(
            os.getenv("MAX_EXECUTION_SPREAD_PCT", "0.30")
        )

        logger.debug(
            f"AdaptiveOrderExecutor initialized: "
            f"use_adaptive={self.use_adaptive}, "
            f"min_premium=${self.min_premium:.2f}, "
            f"max_spread={self.max_execution_spread_pct:.0%}"
        )

    def create_adaptive_order(
        self,
        contracts: int,
        floor_price: float,
    ) -> LimitOrder:
        """Create an Adaptive Algo order with Urgent priority.

        The Adaptive Algo dynamically trades between the spread.
        'Urgent' priority means faster fills with slightly less price improvement.

        Args:
            contracts: Number of contracts to sell
            floor_price: Minimum acceptable premium (your floor)

        Returns:
            LimitOrder configured with Adaptive Algo

        Example:
            >>> order = executor.create_adaptive_order(5, 0.45)
            >>> order.algoStrategy
            'Adaptive'
        """
        order = LimitOrder(
            action="SELL",
            totalQuantity=contracts,
            lmtPrice=floor_price,  # This becomes your floor
            tif="DAY",
        )

        # Enable Adaptive Algo
        order.algoStrategy = "Adaptive"
        order.algoParams = [
            TagValue("adaptivePriority", "Urgent")  # Options: Urgent, Normal, Patient
        ]

        logger.debug(
            f"Created Adaptive order: {contracts} @ ${floor_price:.2f} (Urgent)"
        )

        return order

    def create_limit_order(
        self,
        contracts: int,
        limit_price: float,
    ) -> LimitOrder:
        """Create a standard LIMIT order (fallback).

        Used when Adaptive Algo isn't supported for the option class.

        Args:
            contracts: Number of contracts to sell
            limit_price: Limit price for the order

        Returns:
            Standard LimitOrder

        Example:
            >>> order = executor.create_limit_order(5, 0.45)
        """
        order = LimitOrder(
            action="SELL",
            totalQuantity=contracts,
            lmtPrice=limit_price,
            tif="DAY",
        )

        logger.debug(f"Created LIMIT order: {contracts} @ ${limit_price:.2f}")

        return order

    async def get_live_quote(self, contract: Contract) -> LiveQuote:
        """Fetch live bid/ask for limit price calculation.

        Uses event-driven waiting - returns immediately when valid quote
        arrives instead of waiting for fixed timeout.

        Used for:
        1. Calculating floor price for Adaptive orders
        2. Calculating limit price for fallback LIMIT orders
        3. Validating premium still meets minimum

        Args:
            contract: Qualified contract to get quote for

        Returns:
            LiveQuote with tradeability assessment

        Example:
            >>> quote = await executor.get_live_quote(contract)
            >>> if quote.is_tradeable:
            ...     print(f"Ready to trade @ ${quote.limit:.2f}")
        """
        quote = await self.client.get_quote(contract)

        return LiveQuote.from_quote(
            quote=quote,
            limit_calc=self.limit_calc,
            min_premium=self.min_premium,
        )

    async def place_order(
        self,
        staged: StagedOpportunity,
        contract: Contract,
        quote: LiveQuote,
    ) -> OrderResult:
        """Place order using Adaptive Algo, fallback to LIMIT if needed.

        Strategy:
        1. Try Adaptive Algo first (IBKR handles spread navigation)
        2. If Adaptive fails/rejected, fall back to standard LIMIT

        Args:
            staged: Staged trade opportunity
            contract: Qualified contract
            quote: Live quote with calculated limit

        Returns:
            OrderResult with placement details

        Example:
            >>> result = await executor.place_order(staged, contract, quote)
            >>> if result.success:
            ...     print(f"Order {result.order_id} placed using {result.order_type}")
        """
        # Market hours check
        session = MarketCalendar().get_current_session()
        if session not in (MarketSession.REGULAR, MarketSession.PRE_MARKET):
            logger.warning(
                f"{staged.symbol}: Market closed (session={session.value}), "
                f"rejecting order"
            )
            return OrderResult(
                success=False,
                error_message=f"Market closed ({session.value})",
                live_bid=quote.bid,
                live_ask=quote.ask,
                calculated_limit=quote.limit,
                staged_limit=staged.staged_limit_price,
            )

        if not quote.is_tradeable:
            return OrderResult(
                success=False,
                error_message=f"Not tradeable: {quote.reason}",
                live_bid=quote.bid,
                live_ask=quote.ask,
                calculated_limit=quote.limit,
                staged_limit=staged.staged_limit_price,
            )

        # Spread check — reject if bid-ask spread is too wide
        spread_pct = (
            (quote.ask - quote.bid) / quote.bid if quote.bid > 0 else 999.0
        )
        spread_ok = spread_pct <= self.max_execution_spread_pct
        logger.info(
            f"{staged.symbol}: Spread check: bid=${quote.bid:.2f}, ask=${quote.ask:.2f}, "
            f"spread={spread_pct:.0%} — {'OK' if spread_ok else 'WIDE'}"
        )
        if not spread_ok:
            return OrderResult(
                success=False,
                error_message=(
                    f"Spread {spread_pct:.0%} exceeds max "
                    f"{self.max_execution_spread_pct:.0%}"
                ),
                live_bid=quote.bid,
                live_ask=quote.ask,
                calculated_limit=quote.limit,
                staged_limit=staged.staged_limit_price,
            )

        # Price stability check — reject if live limit deviates too far from staged
        if staged.staged_limit_price > 0:
            deviation_pct = (
                abs(quote.limit - staged.staged_limit_price)
                / staged.staged_limit_price
            )
        else:
            deviation_pct = 0.0

        if deviation_pct > 0.50:
            logger.warning(
                f"{staged.symbol}: Price unstable: live limit ${quote.limit:.2f} vs "
                f"staged ${staged.staged_limit_price:.2f} ({deviation_pct:.0%} deviation) — REJECTED"
            )
            return OrderResult(
                success=False,
                error_message=(
                    f"Price unstable: live limit ${quote.limit:.2f} vs "
                    f"staged ${staged.staged_limit_price:.2f} "
                    f"({deviation_pct:.0%} deviation)"
                ),
                live_bid=quote.bid,
                live_ask=quote.ask,
                calculated_limit=quote.limit,
                staged_limit=staged.staged_limit_price,
                limit_deviation=abs(quote.limit - staged.staged_limit_price),
            )
        elif deviation_pct > 0.20:
            logger.warning(
                f"{staged.symbol}: Price deviation {deviation_pct:.0%}: "
                f"live ${quote.limit:.2f} vs staged ${staged.staged_limit_price:.2f} — proceeding with caution"
            )

        # Primary: Adaptive Algo (if enabled)
        if self.use_adaptive:
            order = self.create_adaptive_order(
                contracts=staged.staged_contracts,
                floor_price=quote.limit,  # Use live-calculated price as floor
            )
            order_type_used = "Adaptive"
        else:
            # Fallback: Standard LIMIT
            order = self.create_limit_order(
                contracts=staged.staged_contracts,
                limit_price=quote.limit,
            )
            order_type_used = "LIMIT"

        try:
            trade = await self.client.place_order(
                contract,
                order,
                reason=f"Staged trade {staged.symbol}",
            )

            # Check for immediate rejection (Adaptive not supported)
            await asyncio.sleep(0.3)

            if trade.orderStatus.status == "Inactive" and self.use_adaptive:
                logger.warning(
                    f"{staged.symbol}: Adaptive Algo rejected, falling back to LIMIT"
                )

                # Cancel the rejected Adaptive order
                await self.client.cancel_order(
                    trade.order.orderId,
                    reason="Adaptive rejected, trying LIMIT",
                )

                # Fallback to standard LIMIT
                order = self.create_limit_order(
                    contracts=staged.staged_contracts,
                    limit_price=quote.limit,
                )

                trade = await self.client.place_order(
                    contract,
                    order,
                    reason=f"Staged trade {staged.symbol} (fallback from Adaptive)",
                )

                order_type_used = "LIMIT (fallback)"

            return OrderResult(
                success=True,
                order_id=trade.order.orderId,
                status=OrderStatus.SUBMITTED,
                order_type=order_type_used,
                live_bid=quote.bid,
                live_ask=quote.ask,
                calculated_limit=quote.limit,
                staged_limit=staged.staged_limit_price,
                limit_deviation=abs(quote.limit - staged.staged_limit_price),
            )

        except Exception as e:
            logger.error(f"{staged.symbol}: Order placement failed: {e}")
            return OrderResult(
                success=False,
                error_message=str(e),
                live_bid=quote.bid,
                live_ask=quote.ask,
                calculated_limit=quote.limit,
                staged_limit=staged.staged_limit_price,
            )
