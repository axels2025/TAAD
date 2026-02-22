"""Order execution engine with comprehensive safety mechanisms.

This module handles order placement via IBKR API with:
- Pre-flight validation
- Dry-run mode for testing
- Order status tracking
- Fill confirmation
- Slippage monitoring
- Complete audit logging

CRITICAL SAFETY:
- ALWAYS verifies PAPER_TRADING=true before placing orders
- Never places live orders during development
- Comprehensive validation before every order
- Full logging of all decisions
"""

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ib_insync import LimitOrder, MarketOrder, Order
from loguru import logger

from src.utils.position_key import generate_trade_id
from src.utils.timezone import us_eastern_now, us_trading_date

from src.config.base import Config
from src.data.models import Trade
from src.data.repositories import TradeRepository
from src.execution.risk_governor import RiskGovernor
from src.services.market_calendar import MarketCalendar, MarketSession
from src.strategies.base import TradeOpportunity
from src.tools.ibkr_client import IBKRClient


class OrderStatus(Enum):
    """Order status enumeration."""

    PENDING = "pending"
    SUBMITTED = "submitted"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"
    ERROR = "error"


@dataclass
class OrderResult:
    """Result of order execution attempt.

    Attributes:
        success: Whether order was successfully placed
        order_id: IBKR order ID (None if dry-run or failed)
        status: Current order status
        fill_price: Actual fill price (None if not filled)
        filled_quantity: Number of contracts filled
        fill_time: Time order was filled (None if not filled)
        error_message: Error description (None if successful)
        slippage: Difference between expected and actual fill
        dry_run: Whether this was a dry-run simulation
        reasoning: Why this order was placed
    """

    success: bool
    order_id: int | None = None
    status: OrderStatus = OrderStatus.PENDING
    fill_price: float | None = None
    filled_quantity: int = 0
    fill_time: datetime | None = None
    error_message: str | None = None
    slippage: float | None = None
    dry_run: bool = False
    reasoning: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "success": self.success,
            "order_id": self.order_id,
            "status": self.status.value,
            "fill_price": self.fill_price,
            "filled_quantity": self.filled_quantity,
            "fill_time": self.fill_time.isoformat() if self.fill_time else None,
            "error_message": self.error_message,
            "slippage": self.slippage,
            "dry_run": self.dry_run,
            "reasoning": self.reasoning,
        }


class OrderExecutor:
    """Execute trades via IBKR with comprehensive safety checks.

    The OrderExecutor handles all order placement with multiple safety layers:
    1. Paper trading verification (CRITICAL)
    2. Pre-flight validation
    3. Dry-run mode for testing
    4. Order status tracking
    5. Fill confirmation
    6. Slippage monitoring
    7. Complete audit logging

    Example (dry-run mode):
        >>> executor = OrderExecutor(ibkr_client, config, dry_run=True)
        >>> result = executor.execute_trade(opportunity)
        >>> if result.success:
        ...     print(f"DRY-RUN: Would place order: {result.reasoning}")

    Example (real paper trading):
        >>> executor = OrderExecutor(ibkr_client, config, dry_run=False)
        >>> result = executor.execute_trade(opportunity)
        >>> if result.success:
        ...     print(f"Order placed: {result.order_id}")
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: Config,
        trade_repository: TradeRepository | None = None,
        dry_run: bool = True,
        risk_governor: RiskGovernor | None = None,
    ):
        """Initialize order executor.

        Args:
            ibkr_client: Connected IBKR client
            config: System configuration
            trade_repository: Trade data repository (optional)
            dry_run: If True, simulate orders without placing them
            risk_governor: Risk governor for post-trade margin verification

        Raises:
            ValueError: If not in paper trading mode
        """
        self.ibkr_client = ibkr_client
        self.config = config
        self.trade_repository = trade_repository
        self.dry_run = dry_run
        self.risk_governor = risk_governor

        # CRITICAL SAFETY CHECK
        self._verify_paper_trading()

        logger.info(
            f"Initialized OrderExecutor in {'DRY-RUN' if dry_run else 'PAPER TRADING'} mode"
        )

    def _verify_paper_trading(self) -> None:
        """Verify we're in paper trading mode.

        CRITICAL SAFETY CHECK: This prevents accidentally placing live orders.

        Raises:
            ValueError: If not in paper trading mode
        """
        paper_trading = os.getenv("PAPER_TRADING", "false").lower() == "true"
        ibkr_port = int(os.getenv("IBKR_PORT", "0"))

        if not paper_trading:
            raise ValueError(
                "CRITICAL ERROR: PAPER_TRADING is not set to 'true'. "
                "This system is only for paper trading. "
                "Set PAPER_TRADING=true in .env file."
            )

        if ibkr_port != 7497:
            raise ValueError(
                f"CRITICAL ERROR: IBKR_PORT={ibkr_port} is not the paper trading port. "
                "Paper trading uses port 7497. "
                "Set IBKR_PORT=7497 in .env file."
            )

        logger.info("✓ Paper trading mode verified (PAPER_TRADING=true, PORT=7497)")

    def execute_trade(
        self,
        opportunity: TradeOpportunity,
        order_type: str = "LIMIT",
        limit_price: float | None = None,
    ) -> OrderResult:
        """Execute a trade opportunity.

        Complete workflow:
        1. Pre-flight validation
        2. Create order
        3. If dry-run: simulate and return
        4. If real: place order via IBKR
        5. Track order status
        6. Confirm fill
        7. Calculate slippage
        8. Log everything

        Args:
            opportunity: Trade opportunity to execute
            order_type: Order type ('LIMIT' or 'MARKET')
            limit_price: Limit price (required for LIMIT orders)

        Returns:
            OrderResult: Execution result with status and details

        Example:
            >>> result = executor.execute_trade(opportunity, "LIMIT", 0.40)
            >>> if result.success:
            ...     print(f"Order placed: {result.order_id}")
        """
        logger.info(
            f"{'DRY-RUN: ' if self.dry_run else ''}Executing trade: "
            f"{opportunity.symbol} ${opportunity.strike} {opportunity.option_type} "
            f"@ ${opportunity.premium}"
        )

        # Step 0: Market hours check (skip in dry-run mode)
        if not self.dry_run:
            session = MarketCalendar().get_current_session()
            if session not in (MarketSession.REGULAR, MarketSession.PRE_MARKET):
                logger.warning(
                    f"Market closed (session={session.value}), "
                    f"rejecting order for {opportunity.symbol}"
                )
                return OrderResult(
                    success=False,
                    status=OrderStatus.REJECTED,
                    error_message=f"Market closed ({session.value})",
                    dry_run=False,
                    reasoning="Order rejected: market not open",
                )

        # Step 1: Pre-flight validation
        validation_result = self._validate_trade(opportunity)
        if not validation_result["valid"]:
            logger.warning(f"Trade validation failed: {validation_result['reason']}")
            return OrderResult(
                success=False,
                status=OrderStatus.REJECTED,
                error_message=validation_result["reason"],
                dry_run=self.dry_run,
                reasoning="Pre-flight validation failed",
            )

        # Step 2: Create order
        try:
            order = self._create_order(opportunity, order_type, limit_price)
        except Exception as e:
            logger.error(f"Error creating order: {e}")
            return OrderResult(
                success=False,
                status=OrderStatus.ERROR,
                error_message=f"Order creation failed: {str(e)}",
                dry_run=self.dry_run,
                reasoning="Order creation error",
            )

        # Step 3: Dry-run mode
        if self.dry_run:
            return self._simulate_order(opportunity, order)

        # Step 4: Place real order (PAPER TRADING ONLY)
        try:
            result = self._place_order(opportunity, order)
            return result

        except Exception as e:
            logger.error(f"Error placing order: {e}", exc_info=True)
            return OrderResult(
                success=False,
                status=OrderStatus.ERROR,
                error_message=f"Order placement failed: {str(e)}",
                dry_run=False,
                reasoning="Order placement error",
            )

    def _validate_trade(self, opportunity: TradeOpportunity) -> dict:
        """Pre-flight validation checks.

        Validates:
        - IBKR connection active
        - Option contract valid
        - Premium reasonable
        - Contracts positive
        - Account has sufficient buying power

        Args:
            opportunity: Trade to validate

        Returns:
            dict: {"valid": bool, "reason": str}
        """
        # Check IBKR connection
        if not self.ibkr_client.is_connected():
            return {"valid": False, "reason": "IBKR not connected"}

        # Validate opportunity data
        if opportunity.contracts <= 0:
            return {"valid": False, "reason": "Invalid contract quantity"}

        if opportunity.premium <= 0:
            return {
                "valid": False,
                "reason": f"Invalid premium: ${opportunity.premium}",
            }

        # Check for suspiciously high premium (likely data error or ITM option)
        if opportunity.strike > 0 and opportunity.premium > opportunity.strike * 0.20:
            logger.warning(
                f"Premium ${opportunity.premium:.2f} is >{20:.0f}% of strike "
                f"${opportunity.strike:.2f} — possible data error or ITM option"
            )
            return {
                "valid": False,
                "reason": (
                    f"Premium ${opportunity.premium:.2f} suspiciously high "
                    f"(>{20}% of strike ${opportunity.strike:.2f})"
                ),
            }

        if not opportunity.symbol:
            return {"valid": False, "reason": "Missing symbol"}

        if opportunity.strike <= 0:
            return {
                "valid": False,
                "reason": f"Invalid strike: ${opportunity.strike}",
            }

        # Validate expiration is in future
        if opportunity.expiration.date() < us_trading_date():
            return {"valid": False, "reason": "Option already expired"}

        # All checks passed
        logger.debug(
            f"✓ Trade validation passed: {opportunity.symbol} "
            f"${opportunity.strike} {opportunity.option_type}"
        )
        return {"valid": True, "reason": ""}

    def _create_order(
        self,
        opportunity: TradeOpportunity,
        order_type: str,
        limit_price: float | None,
    ) -> Order:
        """Create IBKR order object.

        Args:
            opportunity: Trade opportunity
            order_type: 'LIMIT' or 'MARKET'
            limit_price: Limit price (for LIMIT orders)

        Returns:
            Order: IBKR order object

        Raises:
            ValueError: If order parameters invalid
        """
        # Selling options (opening position)
        action = "SELL"
        quantity = opportunity.contracts

        if order_type == "LIMIT":
            if limit_price is None:
                limit_price = opportunity.premium

            order = LimitOrder(
                action=action,
                totalQuantity=quantity,
                lmtPrice=limit_price,
            )
            order.tif = "DAY"  # Explicitly set Time-In-Force
            logger.debug(f"Created LIMIT order: {action} {quantity} @ ${limit_price}")

        elif order_type == "MARKET":
            order = MarketOrder(
                action=action,
                totalQuantity=quantity,
            )
            order.tif = "DAY"  # Explicitly set Time-In-Force
            logger.debug(f"Created MARKET order: {action} {quantity}")

        else:
            raise ValueError(f"Unsupported order type: {order_type}")

        return order

    def _simulate_order(
        self, opportunity: TradeOpportunity, order: Order
    ) -> OrderResult:
        """Simulate order in dry-run mode.

        Args:
            opportunity: Trade opportunity
            order: IBKR order object

        Returns:
            OrderResult: Simulated result
        """
        reasoning = (
            f"DRY-RUN: Would {order.action} {order.totalQuantity} contracts of "
            f"{opportunity.symbol} ${opportunity.strike} {opportunity.option_type} "
            f"expiring {opportunity.expiration.strftime('%Y-%m-%d')}"
        )

        if isinstance(order, LimitOrder):
            reasoning += f" @ limit ${order.lmtPrice:.2f}"
        else:
            reasoning += " @ market"

        logger.info(f"✓ {reasoning}")
        logger.info(f"  Reasoning: {opportunity.reasoning}")
        logger.info(f"  Expected premium: ${opportunity.premium:.2f}")
        logger.info(f"  Margin required: ${opportunity.margin_required:.2f}")

        return OrderResult(
            success=True,
            status=OrderStatus.PENDING,
            dry_run=True,
            reasoning=reasoning,
        )

    def _place_order(self, opportunity: TradeOpportunity, order: Order) -> OrderResult:
        """Place real order via IBKR (PAPER TRADING ONLY).

        Args:
            opportunity: Trade opportunity
            order: IBKR order object

        Returns:
            OrderResult: Execution result
        """
        # Create option contract
        contract = self.ibkr_client.get_option_contract(
            symbol=opportunity.symbol,
            expiration=opportunity.expiration.strftime("%Y%m%d"),
            strike=opportunity.strike,
            right="P" if opportunity.option_type == "PUT" else "C",
        )

        # Qualify contract
        qualified = self.ibkr_client.qualify_contract(contract)
        if not qualified:
            return OrderResult(
                success=False,
                status=OrderStatus.REJECTED,
                error_message="Failed to qualify option contract",
                reasoning="Contract qualification failed",
            )

        # Place order
        logger.info(
            f"PLACING ORDER: {order.action} {order.totalQuantity} "
            f"{opportunity.symbol} ${opportunity.strike} {opportunity.option_type}"
        )

        import asyncio
        trade = asyncio.run(self.ibkr_client.place_order(
            qualified,
            order,
            reason=f"Trade opportunity {opportunity.symbol}"
        ))

        # Wait for order to be submitted
        asyncio.run(self.ibkr_client.sleep(2))

        # Check order status
        if trade.orderStatus.status in ("PreSubmitted", "Submitted"):
            logger.info(
                f"✓ Order {trade.orderStatus.status}: Order ID {trade.order.orderId}"
            )

            # Save to database if repository available
            if self.trade_repository:
                self._save_trade_to_db(opportunity, trade)

            return OrderResult(
                success=True,
                order_id=trade.order.orderId,
                status=OrderStatus.SUBMITTED,
                reasoning=opportunity.reasoning,
            )

        elif trade.orderStatus.status == "Filled":
            fill_price = trade.orderStatus.avgFillPrice
            slippage = fill_price - opportunity.premium

            logger.info(
                f"✓ Order FILLED: Order ID {trade.order.orderId} "
                f"@ ${fill_price:.2f} (slippage: ${slippage:.2f})"
            )

            # Save to database
            if self.trade_repository:
                self._save_trade_to_db(opportunity, trade, filled=True)

            # Post-trade margin verification
            if self.risk_governor:
                self.risk_governor.verify_post_trade_margin(symbol=opportunity.symbol)

            return OrderResult(
                success=True,
                order_id=trade.order.orderId,
                status=OrderStatus.FILLED,
                fill_price=fill_price,
                filled_quantity=int(trade.orderStatus.filled),
                fill_time=us_eastern_now(),
                slippage=slippage,
                reasoning=opportunity.reasoning,
            )

        else:
            # Order rejected or error
            logger.error(
                f"✗ Order {trade.orderStatus.status}: {trade.orderStatus.status}"
            )

            return OrderResult(
                success=False,
                order_id=trade.order.orderId,
                status=OrderStatus.REJECTED,
                error_message=trade.orderStatus.status,
                reasoning=opportunity.reasoning,
            )

    def _save_trade_to_db(
        self,
        opportunity: TradeOpportunity,
        trade: any,
        filled: bool = False,
    ) -> None:
        """Save trade to database and capture entry snapshot.

        Phase 2.6 Integration: Captures comprehensive entry data including:
        - 98 fields across 9 categories
        - Technical indicators (RSI, MACD, ADX, ATR, Bollinger, S/R)
        - Market context (indices, sector, regimes, calendar)
        - Earnings data
        - All critical fields for learning engine

        Args:
            opportunity: Trade opportunity
            trade: IBKR trade object
            filled: Whether trade is already filled
        """
        try:
            # Import here to avoid circular dependency
            from src.data.database import get_db_session
            from src.data.repositories import TradeRepository
            from src.services.entry_snapshot import EntrySnapshotService

            trade_record = Trade(
                trade_id=generate_trade_id(
                    opportunity.symbol,
                    opportunity.strike,
                    opportunity.expiration.date(),
                    opportunity.option_type,
                    order_id=trade.order.orderId,
                ),
                symbol=opportunity.symbol,
                strike=opportunity.strike,
                expiration=opportunity.expiration.date(),
                option_type=opportunity.option_type,
                entry_date=us_eastern_now(),
                entry_premium=opportunity.premium,
                contracts=opportunity.contracts,
                otm_pct=opportunity.otm_pct,
                dte=opportunity.dte,
                ai_reasoning=opportunity.reasoning,
                ai_confidence=opportunity.confidence,
            )

            # Create new session for this save operation
            with get_db_session() as session:
                repo = TradeRepository(session)
                repo.create(trade_record)
                logger.info(f"✓ Trade saved to database: {trade_record.trade_id}")

                # ============================================================
                # Phase 2.6 Integration: Capture Entry Snapshot (98 Fields)
                # ============================================================
                try:
                    entry_service = EntrySnapshotService(self.ibkr_client)

                    # Capture comprehensive entry snapshot
                    snapshot = entry_service.capture_entry_snapshot(
                        trade_id=trade_record.id,
                        opportunity_id=getattr(opportunity, 'id', None),
                        symbol=opportunity.symbol,
                        strike=opportunity.strike,
                        expiration=opportunity.expiration,
                        option_type=opportunity.option_type,
                        entry_premium=opportunity.premium,
                        contracts=opportunity.contracts,
                        stock_price=getattr(opportunity, 'stock_price', 0),
                        dte=opportunity.dte,
                        source="execution",
                    )

                    # Save snapshot to database
                    entry_service.save_snapshot(snapshot, session)

                    logger.info(
                        f"✓ Entry snapshot captured (Quality: {snapshot.data_quality_score:.1%})"
                    )

                    # Log any missing critical fields
                    missing = snapshot.get_missing_critical_fields()
                    if missing:
                        logger.warning(f"Missing critical fields: {', '.join(missing)}")

                except Exception as snapshot_error:
                    # Don't fail trade save if snapshot fails - log and continue
                    logger.error(
                        f"Failed to capture entry snapshot: {snapshot_error}",
                        exc_info=True
                    )

        except Exception as e:
            logger.error(f"Error saving trade to database: {e}")

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an open order.

        Args:
            order_id: IBKR order ID

        Returns:
            bool: True if cancelled successfully
        """
        try:
            # Find the trade by order ID
            trades = self.ibkr_client.get_trades()
            target_trade = None

            for trade in trades:
                if trade.order.orderId == order_id:
                    target_trade = trade
                    break

            if not target_trade:
                logger.warning(f"Order {order_id} not found")
                return False

            # Cancel the order
            import asyncio
            success = asyncio.run(self.ibkr_client.cancel_order(
                order_id,
                reason="Manual cancellation"
            ))
            if success:
                logger.info(f"✓ Order {order_id} cancelled")
            return success

        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False
