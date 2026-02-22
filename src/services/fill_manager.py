"""Time-boxed fill monitoring with partial fill handling and progressive limit adjustment.

Runs after Tier 1 order submission. Monitors fills for a configurable window
(default 10 minutes), handles partial fills via cancel-and-replace, and
progressively lowers limit prices to improve fill probability.

Replaces the ad-hoc 5-minute sleep + fill checking in TwoTierExecutionScheduler.
"""

import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from src.services.limit_price_calculator import LimitPriceCalculator
from src.services.rapid_fire_executor import PendingOrder


@dataclass
class FillManagerConfig:
    """Configuration for fill monitoring.

    All values loaded from environment variables with sensible defaults.

    Attributes:
        monitoring_window_seconds: Total monitoring window (default 600 = 10 min)
        check_interval_seconds: How often to check order status (default 2.0)
        max_adjustments: Maximum number of limit price adjustments (default 5)
        adjustment_increment: Dollar amount to decrease per adjustment (default $0.01)
        adjustment_interval_seconds: Seconds between adjustments (default 60)
        partial_fill_threshold: Ratio above which to cancel+replace remainder (default 0.5)
        leave_working_on_timeout: Leave unfilled as DAY orders on timeout (default True)
        min_premium_floor: Never adjust below this premium (default $0.20)
    """

    monitoring_window_seconds: int = 600
    check_interval_seconds: float = 2.0
    max_adjustments: int = 5
    adjustment_increment: float = 0.01
    adjustment_interval_seconds: int = 60
    partial_fill_threshold: float = 0.5
    leave_working_on_timeout: bool = True
    min_premium_floor: float = 0.20

    @classmethod
    def from_env(cls) -> "FillManagerConfig":
        """Load configuration from environment variables.

        Returns:
            FillManagerConfig instance with values from .env
        """
        return cls(
            monitoring_window_seconds=int(os.getenv("FILL_MONITOR_WINDOW_SECONDS", "600")),
            check_interval_seconds=float(os.getenv("FILL_CHECK_INTERVAL", "2.0")),
            max_adjustments=int(os.getenv("FILL_MAX_ADJUSTMENTS", "5")),
            adjustment_increment=float(os.getenv("FILL_ADJUSTMENT_INCREMENT", "0.01")),
            adjustment_interval_seconds=int(os.getenv("FILL_ADJUSTMENT_INTERVAL", "60")),
            partial_fill_threshold=float(os.getenv("FILL_PARTIAL_THRESHOLD", "0.5")),
            leave_working_on_timeout=os.getenv("FILL_LEAVE_WORKING", "true").lower() == "true",
            min_premium_floor=float(os.getenv("PREMIUM_FLOOR", "0.20")),
        )


@dataclass
class FillStatus:
    """Status of an individual order during fill monitoring.

    Attributes:
        order_id: IBKR order ID
        symbol: Stock symbol
        total_qty: Total contracts requested
        filled_qty: Contracts filled so far
        remaining_qty: Contracts still unfilled
        fill_price: Average fill price (None if unfilled)
        current_limit: Current limit price
        initial_limit: Original limit price at submission
        adjustments_made: Number of limit adjustments applied
        status: Current order status string
        elapsed_seconds: Time since monitoring started
        reason: Human-readable status description
    """

    order_id: int
    symbol: str
    total_qty: int
    filled_qty: int
    remaining_qty: int
    fill_price: float | None
    current_limit: float
    initial_limit: float
    adjustments_made: int
    status: str
    elapsed_seconds: float
    reason: str = ""


@dataclass
class FillReport:
    """Summary report of fill monitoring session.

    Attributes:
        started_at: When monitoring started
        completed_at: When monitoring ended
        monitoring_window: Configured window in seconds
        orders_monitored: Total orders monitored
        fully_filled: Number of orders fully filled
        partially_filled: Number with partial fills
        left_working: Number left as working DAY orders
        cancelled: Number cancelled
        total_adjustments: Total limit adjustments across all orders
        filled_orders: PendingOrder snapshots captured at fill time,
            so callers can save fill data and capture entry snapshots
            (the fill_manager removes filled orders from pending_orders)
    """

    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    monitoring_window: int = 0
    orders_monitored: int = 0
    fully_filled: int = 0
    partially_filled: int = 0
    left_working: int = 0
    cancelled: int = 0
    total_adjustments: int = 0
    filled_orders: list[PendingOrder] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Total monitoring duration."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0


class FillManager:
    """Time-boxed fill monitoring with progressive limit adjustment.

    Monitors submitted orders for a configurable window. Handles:
    - Fill detection via event-driven callbacks (checked every 2s)
    - Partial fill handling: cancel + replace for remainder
    - Progressive limit adjustment: lower by $0.01 every 60s
    - Timeout: leave working as DAY order

    Dependencies:
        - IBKRClient: For order status, cancel, replace operations
        - LimitPriceCalculator: For computing new limits
        - FillManagerConfig: For timing and threshold parameters

    Example:
        >>> manager = FillManager(ibkr_client)
        >>> report = await manager.monitor_fills(pending_orders)
        >>> print(f"Filled: {report.fully_filled}/{report.orders_monitored}")
    """

    def __init__(
        self,
        ibkr_client,
        limit_calculator: LimitPriceCalculator | None = None,
        config: FillManagerConfig | None = None,
    ):
        """Initialize fill manager.

        Args:
            ibkr_client: Connected IBKRClient instance
            limit_calculator: Limit price calculator (creates if None)
            config: Fill manager configuration (loads from env if None)
        """
        self.client = ibkr_client
        self.limit_calculator = limit_calculator or LimitPriceCalculator()
        self.config = config or FillManagerConfig.from_env()

        # Override min_premium_floor with the system-wide PREMIUM_MIN if higher.
        # The fill_manager must never adjust below the configured minimum premium.
        from src.config.base import get_config
        system_premium_min = get_config().premium_min
        if system_premium_min > self.config.min_premium_floor:
            self.config.min_premium_floor = system_premium_min

        logger.info(
            f"FillManager initialized: "
            f"window={self.config.monitoring_window_seconds}s, "
            f"max_adj={self.config.max_adjustments}, "
            f"increment=${self.config.adjustment_increment}, "
            f"floor=${self.config.min_premium_floor}"
        )

    async def monitor_fills(
        self,
        pending_orders: dict[int, PendingOrder],
    ) -> FillReport:
        """Monitor pending orders for fills with progressive adjustment.

        Main monitoring loop:
        - Every check_interval: check order statuses, detect fills
        - Detect partial fills → cancel + replace for remainder
        - Every adjustment_interval: progressive limit adjustment (−$0.01)
        - Max max_adjustments, never below min_premium_floor
        - After window: leave working as DAY order

        Args:
            pending_orders: Dict of order_id → PendingOrder from RapidFireExecutor

        Returns:
            FillReport with monitoring results
        """
        report = FillReport(
            monitoring_window=self.config.monitoring_window_seconds,
            orders_monitored=len(pending_orders),
        )

        if not pending_orders:
            report.completed_at = datetime.now()
            return report

        logger.info(
            f"Fill monitoring started: {len(pending_orders)} orders, "
            f"{self.config.monitoring_window_seconds}s window"
        )

        start_time = time.time()
        last_adjustment_time = start_time
        # Track adjustments per SYMBOL (not order_id, which changes on each
        # cancel-and-replace cycle — keying by order_id caused the counter
        # to reset every adjustment, bypassing the max_adjustments limit)
        adjustment_counts: dict[str, int] = {
            p.staged.symbol: 0 for p in pending_orders.values()
        }

        while time.time() - start_time < self.config.monitoring_window_seconds:
            elapsed = time.time() - start_time
            await self.client.sleep(self.config.check_interval_seconds)

            # Check all pending orders
            completed_ids: list[int] = []

            for order_id, pending in list(pending_orders.items()):
                # Skip already-processed orders
                if pending.last_status == "Filled":
                    completed_ids.append(order_id)
                    report.fully_filled += 1
                    # Capture fill data before removing from pending_orders
                    report.filled_orders.append(pending)
                    continue

                if pending.last_status in ("Cancelled", "Inactive", "ApiCancelled"):
                    completed_ids.append(order_id)
                    report.cancelled += 1
                    continue

                # Check for partial fills
                filled_qty, remaining_qty = await self._check_partial_fills(pending)

                if filled_qty > 0 and remaining_qty > 0:
                    # Partial fill detected
                    fill_ratio = filled_qty / pending.staged.staged_contracts

                    if fill_ratio >= self.config.partial_fill_threshold:
                        # >50% filled: cancel and replace remainder
                        logger.info(
                            f"{pending.staged.symbol}: Partial fill "
                            f"{filled_qty}/{pending.staged.staged_contracts} "
                            f"({fill_ratio:.0%}) — replacing remainder"
                        )
                        success = await self._adjust_for_remainder(
                            pending, remaining_qty, pending_orders
                        )
                        if success:
                            report.partially_filled += 1
                    else:
                        logger.debug(
                            f"{pending.staged.symbol}: Partial fill "
                            f"{filled_qty}/{pending.staged.staged_contracts} "
                            f"({fill_ratio:.0%}) — below threshold, waiting"
                        )

            # Remove completed orders
            for oid in completed_ids:
                pending_orders.pop(oid, None)

            if not pending_orders:
                logger.info("All orders filled or completed")
                break

            # Progressive limit adjustment (every adjustment_interval)
            time_since_last_adj = time.time() - last_adjustment_time
            if time_since_last_adj >= self.config.adjustment_interval_seconds:
                for order_id, pending in list(pending_orders.items()):
                    if pending.last_status in ("Filled", "Cancelled", "Inactive"):
                        continue

                    symbol = pending.staged.symbol
                    adj_count = adjustment_counts.get(symbol, 0)
                    if adj_count >= self.config.max_adjustments:
                        logger.debug(
                            f"{symbol}: Max adjustments ({self.config.max_adjustments}) "
                            f"reached, leaving order working"
                        )
                        continue

                    success = await self._progressive_adjust(
                        pending, adj_count + 1, pending_orders
                    )
                    if success:
                        adjustment_counts[symbol] = adj_count + 1
                        report.total_adjustments += 1

                last_adjustment_time = time.time()

        # Window expired — handle remaining orders
        for order_id, pending in pending_orders.items():
            if pending.last_status == "Filled":
                report.fully_filled += 1
                report.filled_orders.append(pending)
            elif pending.last_status in ("Cancelled", "Inactive"):
                report.cancelled += 1
            else:
                if self.config.leave_working_on_timeout:
                    report.left_working += 1
                    logger.info(
                        f"{pending.staged.symbol}: Left working @ "
                        f"${pending.current_limit:.2f} as DAY order"
                    )
                else:
                    # Cancel if not leaving working
                    await self.client.cancel_order(
                        order_id, reason="Fill monitoring window expired"
                    )
                    report.cancelled += 1

        report.completed_at = datetime.now()

        logger.info(
            f"Fill monitoring complete ({report.duration_seconds:.0f}s): "
            f"{report.fully_filled} filled, {report.partially_filled} partial, "
            f"{report.left_working} working, {report.cancelled} cancelled, "
            f"{report.total_adjustments} adjustments"
        )

        return report

    async def _check_partial_fills(
        self,
        pending: PendingOrder,
    ) -> tuple[int, int]:
        """Check for partial fills on a pending order.

        Reads filled_qty from the PendingOrder (updated by event callback).

        Args:
            pending: The pending order to check

        Returns:
            Tuple of (filled_qty, remaining_qty)
        """
        filled_qty = pending.filled_qty or 0
        total_qty = pending.staged.staged_contracts
        remaining_qty = total_qty - filled_qty

        return filled_qty, remaining_qty

    async def _adjust_for_remainder(
        self,
        pending: PendingOrder,
        remaining_qty: int,
        pending_orders: dict[int, PendingOrder],
    ) -> bool:
        """Cancel current order and place new one for the remaining quantity.

        Args:
            pending: The partially filled order
            remaining_qty: Number of contracts remaining
            pending_orders: Live dict of pending orders (mutated on success)

        Returns:
            True if replacement order placed successfully
        """
        try:
            old_order_id = pending.order_id

            # Cancel existing order
            cancelled = await self.client.cancel_order(
                old_order_id,
                reason=f"Partial fill — replacing for {remaining_qty} remaining contracts",
            )

            if not cancelled:
                logger.warning(
                    f"{pending.staged.symbol}: Failed to cancel order {old_order_id}"
                )
                return False

            await self.client.sleep(0.3)

            # Get fresh quote for new limit
            quote = await self.client.get_quote(pending.contract, timeout=1.0)
            if quote.is_valid:
                new_limit = self.limit_calculator.calculate_sell_limit(quote.bid, quote.ask)
            else:
                new_limit = pending.current_limit

            new_limit = max(new_limit, self.config.min_premium_floor)

            # Place new order for remainder using the adaptive executor
            # We import inline to avoid circular dependency
            from src.services.adaptive_order_executor import LiveQuote

            live_quote = LiveQuote(
                bid=quote.bid if quote.is_valid else pending.last_bid,
                ask=quote.ask if quote.is_valid else pending.last_ask,
                limit=new_limit,
                is_tradeable=True,
                reason="",
            )

            # Create a modified staged opportunity with remaining qty
            remainder_staged = pending.staged
            # We don't mutate staged_contracts since it's the original count
            # The adaptive executor uses staged.staged_contracts for qty

            # Place via adaptive executor — needs access to the executor
            # For now, use the IBKR client directly with a limit order
            from ib_insync import LimitOrder

            order = LimitOrder(
                action="SELL",
                totalQuantity=remaining_qty,
                lmtPrice=new_limit,
                tif="DAY",
                outsideRth=False,
            )

            trade = self.client.ib.placeOrder(pending.contract, order)
            await self.client.sleep(0.2)

            if trade and trade.order.orderId:
                new_id = trade.order.orderId

                # Update pending order tracking
                pending.order_id = new_id
                pending.current_limit = new_limit
                pending.adjustment_count += 1

                # Update the pending_orders dict
                pending_orders.pop(old_order_id, None)
                pending_orders[new_id] = pending

                logger.info(
                    f"{pending.staged.symbol}: Replacement order #{new_id} "
                    f"for {remaining_qty} contracts @ ${new_limit:.2f}"
                )
                return True
            else:
                logger.error(f"{pending.staged.symbol}: Failed to place replacement order")
                return False

        except Exception as e:
            logger.error(
                f"{pending.staged.symbol}: Error adjusting for remainder: {e}",
                exc_info=True,
            )
            return False

    async def _progressive_adjust(
        self,
        pending: PendingOrder,
        adjustment_number: int,
        pending_orders: dict[int, PendingOrder],
    ) -> bool:
        """Lower limit price by increment to improve fill probability.

        Args:
            pending: The pending order to adjust
            adjustment_number: Which adjustment this is (1-based)
            pending_orders: Live dict of pending orders (mutated on success)

        Returns:
            True if adjustment was applied
        """
        if adjustment_number > self.config.max_adjustments:
            return False

        current_limit = pending.current_limit
        new_limit = round(current_limit - self.config.adjustment_increment, 2)

        # Floor check
        if new_limit < self.config.min_premium_floor:
            logger.debug(
                f"{pending.staged.symbol}: Adjusted limit ${new_limit:.2f} "
                f"below floor ${self.config.min_premium_floor:.2f}, skipping"
            )
            return False

        try:
            old_id = pending.order_id

            # Cancel existing order
            cancelled = await self.client.cancel_order(
                old_id,
                reason=f"Progressive adjustment #{adjustment_number}",
            )

            if not cancelled:
                return False

            await self.client.sleep(0.2)

            # Place new order at lower limit
            from ib_insync import LimitOrder

            remaining = pending.staged.staged_contracts - (pending.filled_qty or 0)
            if remaining <= 0:
                return False

            order = LimitOrder(
                action="SELL",
                totalQuantity=remaining,
                lmtPrice=new_limit,
                tif="DAY",
                outsideRth=False,
            )

            trade = self.client.ib.placeOrder(pending.contract, order)
            await self.client.sleep(0.2)

            if trade and trade.order.orderId:
                new_id = trade.order.orderId

                pending.order_id = new_id
                pending.current_limit = new_limit
                pending.adjustment_count += 1

                pending_orders.pop(old_id, None)
                pending_orders[new_id] = pending

                logger.info(
                    f"{pending.staged.symbol}: Adjustment #{adjustment_number} "
                    f"${current_limit:.2f} → ${new_limit:.2f}"
                )
                return True
            else:
                logger.warning(f"{pending.staged.symbol}: Failed to place adjusted order")
                return False

        except Exception as e:
            logger.error(
                f"{pending.staged.symbol}: Progressive adjust error: {e}",
                exc_info=True,
            )
            return False
