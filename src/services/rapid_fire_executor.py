"""Rapid-fire parallel order execution with event-driven monitoring.

This module implements institutional-grade parallel order submission:
- Submit ALL orders in <3 seconds (not sequential)
- Event-driven fill monitoring (not polling)
- Condition-based adjustment (only when needed)
- Async monitoring of all orders simultaneously

Replaces sequential submit → wait 30s → submit → wait 30s pattern.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from loguru import logger

from src.services.adaptive_order_executor import AdaptiveOrderExecutor, LiveQuote
from src.services.premarket_validator import StagedOpportunity
from src.tools.ibkr_client import IBKRClient

# Avoid circular import — use TYPE_CHECKING for type hints only
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.execution.risk_governor import RiskGovernor


class OrderStatus(Enum):
    """Status of an order in the rapid-fire workflow."""

    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    WORKING = "WORKING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


@dataclass
class PendingOrder:
    """Track a pending order during async monitoring.

    Attributes:
        staged: Original staged opportunity
        contract: Qualified contract
        order_id: IBKR order ID
        initial_limit: Initial limit price from live quote
        current_limit: Current limit price (after adjustments)
        last_bid: Last known bid price
        last_ask: Last known ask price
        submitted_at: When order was submitted
        last_update: When status last changed
        last_status: Last known order status
        fill_price: Actual fill price (None if not filled)
        filled_qty: Quantity filled
        order_type: Type of order (Adaptive, LIMIT, etc.)
        adjustment_count: Number of price adjustments made
    """

    staged: StagedOpportunity
    contract: any  # ib_insync Contract
    order_id: int
    initial_limit: float
    current_limit: float
    last_bid: float
    last_ask: float
    submitted_at: datetime
    order_type: str
    last_update: datetime = field(default_factory=datetime.now)
    last_status: str = "Submitted"
    fill_price: float | None = None
    filled_qty: int = 0
    remaining_qty: int = 0
    adjustment_count: int = 0


@dataclass
class ExecutionSummary:
    """Summary for a single executed trade.

    Attributes:
        symbol: Stock symbol
        strike: Strike price
        order_id: IBKR order ID
        status: Final status
        order_type: Type of order used
        submitted_limit: Limit price when submitted
        fill_price: Actual fill price (None if not filled)
        fill_time: When filled (None if not filled)
        submission_time: When submitted
        adjustments_made: Number of price adjustments
        reason: Reason for rejection/skip (if applicable)
    """

    symbol: str
    strike: float
    order_id: int | None
    status: OrderStatus
    order_type: str
    submitted_limit: float
    fill_price: float | None = None
    fill_time: datetime | None = None
    submission_time: datetime = field(default_factory=datetime.now)
    adjustments_made: int = 0
    reason: str = ""


@dataclass
class ExecutionReport:
    """Complete report of rapid-fire execution batch.

    Attributes:
        started_at: When execution started
        completed_at: When execution completed
        submission_time: Time to submit all orders (seconds)
        monitoring_time: Time spent monitoring fills (seconds)
        submitted: List of successfully submitted trades
        filled: List of filled trades
        working: List of orders left working
        skipped: List of skipped trades
        failed: List of failed trades
        total_premium: Total premium from fills
    """

    started_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None
    submission_time: float = 0.0
    monitoring_time: float = 0.0
    submitted: list[ExecutionSummary] = field(default_factory=list)
    filled: list[ExecutionSummary] = field(default_factory=list)
    working: list[ExecutionSummary] = field(default_factory=list)
    skipped: list[ExecutionSummary] = field(default_factory=list)
    failed: list[ExecutionSummary] = field(default_factory=list)
    total_premium: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def total_submitted(self) -> int:
        """Total orders submitted."""
        return len(self.submitted)

    @property
    def total_filled(self) -> int:
        """Total orders filled."""
        return len(self.filled)

    @property
    def total_working(self) -> int:
        """Total orders left working."""
        return len(self.working)

    @property
    def fill_rate(self) -> float:
        """Fill rate (filled / submitted)."""
        if self.total_submitted == 0:
            return 0.0
        return self.total_filled / self.total_submitted

    @property
    def duration_seconds(self) -> float:
        """Total execution duration."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    def add_submitted(
        self,
        staged: StagedOpportunity,
        order_id: int,
        limit: float,
        order_type: str,
    ) -> None:
        """Add a submitted trade."""
        self.submitted.append(
            ExecutionSummary(
                symbol=staged.symbol,
                strike=staged.strike,
                order_id=order_id,
                status=OrderStatus.SUBMITTED,
                order_type=order_type,
                submitted_limit=limit,
            )
        )

    def add_filled(
        self,
        staged: StagedOpportunity,
        order_id: int,
        fill_price: float,
        order_type: str,
        adjustments: int = 0,
    ) -> None:
        """Add a filled trade."""
        summary = ExecutionSummary(
            symbol=staged.symbol,
            strike=staged.strike,
            order_id=order_id,
            status=OrderStatus.FILLED,
            order_type=order_type,
            submitted_limit=fill_price,
            fill_price=fill_price,
            fill_time=datetime.now(),
            adjustments_made=adjustments,
        )
        self.filled.append(summary)
        self.total_premium += fill_price * staged.staged_contracts * 100

    def add_working(
        self,
        staged: StagedOpportunity,
        order_id: int,
        current_limit: float,
        adjustments: int = 0,
    ) -> None:
        """Add an order left working."""
        self.working.append(
            ExecutionSummary(
                symbol=staged.symbol,
                strike=staged.strike,
                order_id=order_id,
                status=OrderStatus.WORKING,
                order_type="",
                submitted_limit=current_limit,
                adjustments_made=adjustments,
            )
        )

    def add_skipped(self, staged: StagedOpportunity, reason: str) -> None:
        """Add a skipped trade."""
        self.skipped.append(
            ExecutionSummary(
                symbol=staged.symbol,
                strike=staged.strike,
                order_id=None,
                status=OrderStatus.REJECTED,
                order_type="SKIPPED",
                submitted_limit=0.0,
                reason=reason,
            )
        )

    def add_failed(
        self,
        staged: StagedOpportunity,
        order_id: int | None,
        reason: str,
    ) -> None:
        """Add a failed trade."""
        self.failed.append(
            ExecutionSummary(
                symbol=staged.symbol,
                strike=staged.strike,
                order_id=order_id,
                status=OrderStatus.FAILED,
                order_type="FAILED",
                submitted_limit=0.0,
                reason=reason,
            )
        )


class RapidFireExecutor:
    """Execute all orders in parallel with event-driven monitoring.

    This replaces the sequential submit → wait 30s → submit → wait 30s pattern
    with institutional rapid-fire: submit ALL → monitor fills asynchronously.

    Key Features:
    - Parallel submission (<3 seconds for all orders)
    - Event-driven fill monitoring via orderStatusEvent
    - Condition-based adjustment: only if limit > $0.02 outside spread
    - Adaptive Algo primary, LIMIT fallback

    Example:
        >>> executor = RapidFireExecutor(ibkr_client, adaptive_executor)
        >>> report = await executor.execute_all(staged_trades)
        >>> print(f"Submitted {report.total_submitted} in {report.submission_time:.2f}s")
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        adaptive_executor: AdaptiveOrderExecutor,
        risk_governor: "RiskGovernor | None" = None,
    ):
        """Initialize rapid-fire executor.

        Args:
            ibkr_client: IBKR client for order operations
            adaptive_executor: Executor for Adaptive Algo orders
            risk_governor: Risk governor for post-trade margin verification
        """
        self.client = ibkr_client
        self.adaptive_executor = adaptive_executor
        self.risk_governor = risk_governor
        self.pending_orders: dict[int, PendingOrder] = {}
        self.max_wait = int(os.getenv("RAPID_FIRE_MAX_WAIT_SECONDS", "120"))
        self.adjustment_threshold = float(os.getenv("ADJUSTMENT_THRESHOLD", "0.02"))

        from src.config.base import get_config
        self.min_premium = get_config().premium_min

        # Register event callback for order status changes
        # Access the event directly from ib object (property doesn't support +=)
        self.client.ib.orderStatusEvent += self._on_order_status

        logger.debug(
            f"RapidFireExecutor initialized: "
            f"max_wait={self.max_wait}s, "
            f"adjustment_threshold=${self.adjustment_threshold:.2f}"
        )

    def _on_order_status(self, trade):
        """Event callback for order status changes.

        This is called automatically by ib_insync when order status changes.
        Runs in parallel for all orders - no polling needed.

        Args:
            trade: Trade object from ib_insync with updated status
        """
        order_id = trade.order.orderId

        if order_id in self.pending_orders:
            pending = self.pending_orders[order_id]
            pending.last_status = trade.orderStatus.status
            pending.last_update = datetime.now()
            pending.remaining_qty = int(trade.orderStatus.remaining)

            if trade.orderStatus.status == "Filled":
                pending.fill_price = trade.orderStatus.avgFillPrice
                pending.filled_qty = trade.orderStatus.filled
                pending.remaining_qty = 0
                logger.info(
                    f"✓ {pending.staged.symbol} FILLED @ ${pending.fill_price:.2f}"
                )
            elif trade.orderStatus.filled > 0:
                pending.filled_qty = int(trade.orderStatus.filled)

    async def execute_all(
        self,
        staged_trades: list[StagedOpportunity],
    ) -> ExecutionReport:
        """Execute all staged trades using rapid-fire parallel submission.

        Timeline:
            T+0:    Pre-qualify all contracts (batch)
            T+1:    Request live quotes for all (parallel)
            T+2:    Submit all orders (rapid-fire, ~100ms each)
            T+3:    Begin async fill monitoring
            T+??:   Condition-based adjustments (when limit > $0.02 outside spread)
            T+120:  Final status, leave unfilled as DAY orders

        Args:
            staged_trades: List of staged opportunities to execute

        Returns:
            ExecutionReport with complete execution details

        Example:
            >>> report = await executor.execute_all(staged_trades)
            >>> print(f"Fill rate: {report.fill_rate:.1%}")
        """
        report = ExecutionReport()
        submission_start = time.time()

        logger.info(
            f"🚀 RAPID FIRE: Starting parallel execution for {len(staged_trades)} trades"
        )

        # Step 1: Pre-qualify contracts in batch (parallel)
        logger.info("Step 1: Pre-qualifying all contracts in batch...")
        contracts = []
        for staged in staged_trades:
            # Get expiration in IBKR format
            exp_str = staged.expiration
            if isinstance(exp_str, str):
                exp_str = exp_str.replace("-", "")
            else:
                exp_str = exp_str.strftime("%Y%m%d")

            contract = self.client.get_option_contract(
                symbol=staged.symbol,
                expiration=exp_str,
                strike=staged.strike,
                right="P",
            )
            contracts.append(contract)

        # Batch qualify all at once
        qualified = await self.client.qualify_contracts_async(*contracts)

        if len(qualified) != len(contracts):
            logger.warning(
                f"Only {len(qualified)}/{len(contracts)} contracts qualified"
            )

        # Step 2: Request live quotes for ALL contracts (parallel)
        # Use a longer timeout at market open — 0.5s is too short for
        # most options that haven't traded yet
        quote_timeout = float(os.getenv("EXECUTION_QUOTE_TIMEOUT", "3.0"))
        logger.info(
            f"Step 2: Fetching live quotes for all contracts "
            f"(parallel, {quote_timeout}s timeout)..."
        )

        # Use IBKRClient's batch quote method (clean architecture)
        raw_quotes = await self.client.get_quotes_batch(qualified, timeout=quote_timeout)

        # Log quote validity summary
        valid_count = sum(1 for q in raw_quotes if q.is_valid)
        invalid_count = len(raw_quotes) - valid_count
        if invalid_count > 0:
            logger.warning(
                f"⚠️ {invalid_count}/{len(raw_quotes)} quotes invalid after {quote_timeout}s timeout"
            )
            # Retry invalid quotes with longer timeout
            retry_timeout = float(os.getenv("EXECUTION_QUOTE_RETRY_TIMEOUT", "5.0"))
            retry_indices = [i for i, q in enumerate(raw_quotes) if not q.is_valid]
            if retry_indices:
                retry_contracts = [qualified[i] for i in retry_indices]
                logger.info(
                    f"  Retrying {len(retry_contracts)} invalid quotes "
                    f"with {retry_timeout}s timeout..."
                )
                retry_quotes = await self.client.get_quotes_batch(
                    retry_contracts, timeout=retry_timeout
                )
                for idx, retry_quote in zip(retry_indices, retry_quotes):
                    if retry_quote.is_valid:
                        raw_quotes[idx] = retry_quote
                        logger.info(f"  ✓ Retry succeeded for {qualified[idx].symbol}")

                final_valid = sum(1 for q in raw_quotes if q.is_valid)
                logger.info(
                    f"  After retry: {final_valid}/{len(raw_quotes)} quotes valid"
                )

        # Convert Quote objects to LiveQuote objects with tradeability assessment
        quotes = [
            LiveQuote.from_quote(
                quote=raw_quote,
                limit_calc=self.adaptive_executor.limit_calc,
                min_premium=self.min_premium,
            )
            for raw_quote in raw_quotes
        ]

        # Step 3: RAPID FIRE - Submit all orders
        logger.info("Step 3: 🔥 RAPID FIRE - Submitting all orders NOW")

        # Validate list lengths before zip (detect silent truncation)
        if len(qualified) != len(staged_trades):
            logger.critical(
                f"🛑 CONTRACT QUALIFICATION MISMATCH: "
                f"{len(qualified)}/{len(staged_trades)} contracts qualified — "
                f"trades beyond index {len(qualified)} will be silently dropped!"
            )
        if len(quotes) != len(staged_trades):
            logger.critical(
                f"🛑 QUOTE COUNT MISMATCH: "
                f"{len(quotes)} quotes for {len(staged_trades)} trades — "
                f"trades beyond index {len(quotes)} will be silently dropped!"
            )

        # Track trades dropped by zip truncation
        processed_count = min(len(staged_trades), len(qualified), len(quotes))
        if processed_count < len(staged_trades):
            for i in range(processed_count, len(staged_trades)):
                dropped = staged_trades[i]
                logger.critical(
                    f"🛑 {dropped.symbol}: DROPPED — not processed due to list length mismatch"
                )
                report.add_failed(dropped, None, "Dropped: qualification/quote list mismatch")

        for staged, contract, quote in zip(staged_trades, qualified, quotes):
            if not quote.is_tradeable:
                reason = quote.reason or f"Premium ${quote.limit:.2f} < min ${self.min_premium:.2f}"
                logger.warning(
                    f"⏭️ {staged.symbol} ${staged.strike}P: SKIPPED — {reason}"
                )
                report.add_skipped(staged, reason)
                continue

            # Place order using adaptive executor
            result = await self.adaptive_executor.place_order(staged, contract, quote)

            if result.success:
                # Track as pending for monitoring
                self.pending_orders[result.order_id] = PendingOrder(
                    staged=staged,
                    contract=contract,
                    order_id=result.order_id,
                    initial_limit=quote.limit,
                    current_limit=quote.limit,
                    last_bid=quote.bid,
                    last_ask=quote.ask,
                    submitted_at=datetime.now(),
                    order_type=result.order_type,
                )
                report.add_submitted(staged, result.order_id, quote.limit, result.order_type)
            else:
                report.add_failed(staged, None, result.error_message)

        submission_time = time.time() - submission_start
        report.submission_time = submission_time

        logger.info(
            f"🚀 Rapid-fire complete: {len(self.pending_orders)} orders in {submission_time:.2f}s"
        )

        # CRITICAL SAFETY CHECK: Detect silent failures
        if len(staged_trades) > 0 and len(self.pending_orders) == 0:
            logger.critical(
                f"🛑 CRITICAL: 0 orders submitted for {len(staged_trades)} trades!"
            )
            logger.critical(
                "This indicates a systematic failure - likely market data unavailable"
            )
            logger.critical(
                "Check for TWS conflicts (Error 10197) or market data issues"
            )

            # Add warning to report
            report.warnings.append(
                f"CRITICAL FAILURE: 0 orders submitted for {len(staged_trades)} trades. "
                "Likely market data unavailable or TWS conflict."
            )

        # Step 4: Async fill monitoring with CONDITION-BASED adjustments
        monitoring_start = time.time()
        await self._monitor_and_adjust(report)
        report.monitoring_time = time.time() - monitoring_start

        report.completed_at = datetime.now()

        return report

    async def _monitor_and_adjust(self, report: ExecutionReport):
        """Monitor fills and adjust unfilled orders when condition met.

        Adjustment Condition: Only adjust if current limit is > $0.02 outside the spread.
        This is smarter than time-based adjustment — we adjust based on market, not clock.

        Args:
            report: Execution report to update with results
        """
        start_time = time.time()

        logger.info(
            f"⏱️ Monitoring {len(self.pending_orders)} orders "
            f"(max {self.max_wait}s, adjust if >${self.adjustment_threshold:.2f} outside spread)"
        )

        while self.pending_orders and (time.time() - start_time) < self.max_wait:
            await self.client.sleep(2)  # Process IB events, check every 2s

            # Process fills (handled by event callback, but double-check here)
            filled_ids = []
            for order_id, pending in self.pending_orders.items():
                if pending.last_status == "Filled":
                    report.add_filled(
                        pending.staged,
                        order_id,
                        pending.fill_price,
                        pending.order_type,
                        pending.adjustment_count,
                    )
                    filled_ids.append(order_id)

                elif pending.last_status in ("Cancelled", "Inactive", "ApiCancelled"):
                    report.add_failed(pending.staged, order_id, pending.last_status)
                    filled_ids.append(order_id)

            # Remove completed orders from pending
            for order_id in filled_ids:
                del self.pending_orders[order_id]

            # Post-trade margin verification after fills
            if filled_ids and self.risk_governor:
                filled_symbols = [
                    report.filled[-i].symbol
                    for i in range(1, len(filled_ids) + 1)
                    if i <= len(report.filled)
                ]
                symbol_str = ", ".join(filled_symbols) if filled_symbols else ""
                self.risk_governor.verify_post_trade_margin(symbol=symbol_str)

            # CONDITION-BASED ADJUSTMENT
            # Only adjust if our limit is > $0.02 outside the current spread
            await self._adjust_if_outside_spread(report)

        # Mark remaining as working (TIF=DAY)
        for order_id, pending in self.pending_orders.items():
            report.add_working(
                pending.staged, order_id, pending.current_limit, pending.adjustment_count
            )
            logger.info(
                f"⏳ {pending.staged.symbol} left working @ ${pending.current_limit:.2f} "
                f"({pending.adjustment_count} adjustments)"
            )

        # Remove only filled/cancelled/failed orders — keep working orders
        # so _on_order_status callback can still capture late fills and
        # Tier 2 can find unfilled orders to adjust
        completed_ids = [
            oid for oid, p in self.pending_orders.items()
            if p.last_status in ("Filled", "Cancelled", "Inactive", "ApiCancelled")
        ]
        for oid in completed_ids:
            del self.pending_orders[oid]

    async def _adjust_if_outside_spread(self, report: ExecutionReport):
        """Adjust orders ONLY if limit is > $0.02 outside current spread.

        This condition-based approach is smarter than time-based:
        - If market hasn't moved, no adjustment needed
        - If market moved slightly (<$0.02), no adjustment needed
        - Only adjust when we're actually outside the tradeable range

        Args:
            report: Execution report (for logging purposes)
        """
        for order_id, pending in list(self.pending_orders.items()):
            if pending.last_status == "Filled":
                continue

            # Get fresh quote
            quote = await self.client.get_quote(pending.contract, timeout=0.3)

            if not quote.is_valid:
                continue

            current_bid = quote.bid
            current_ask = quote.ask
            current_limit = pending.current_limit

            # CONDITION: Is our limit > $0.02 ABOVE the current ask?
            # (For SELL orders, being above the ask means we're too expensive)
            spread_distance = current_limit - current_ask

            if spread_distance > self.adjustment_threshold:
                # We're too far above the ask — need to lower our limit
                # New limit: between bid and mid, but not below minimum
                new_limit = self.adaptive_executor.limit_calc.calculate_sell_limit(
                    current_bid, current_ask
                )
                new_limit = max(new_limit, self.min_premium)

                if new_limit < pending.current_limit:
                    # Modify the order (cancel and replace)
                    success = await self._modify_order_price(pending, new_limit)

                    if success:
                        logger.info(
                            f"📉 {pending.staged.symbol}: Adjusted ${pending.current_limit:.2f} → "
                            f"${new_limit:.2f} (was ${spread_distance:.2f} above ask)"
                        )
                        pending.current_limit = new_limit
                        pending.last_bid = current_bid
                        pending.last_ask = current_ask
                        pending.adjustment_count += 1

    async def _modify_order_price(
        self,
        pending: PendingOrder,
        new_price: float,
    ) -> bool:
        """Modify an existing order's limit price using cancel-and-replace.

        Args:
            pending: Pending order to modify
            new_price: New limit price

        Returns:
            True if modification successful, False otherwise
        """
        try:
            # Cancel existing order
            await self.client.cancel_order(
                pending.order_id,
                reason=f"Price adjustment #{pending.adjustment_count + 1}",
            )

            await self.client.sleep(0.2)

            # Place new order with adjusted price
            new_quote = LiveQuote(
                bid=pending.last_bid,
                ask=pending.last_ask,
                limit=new_price,
                is_tradeable=True,
                reason="",
            )

            result = await self.adaptive_executor.place_order(
                pending.staged,
                pending.contract,
                new_quote,
            )

            if result.success:
                # Update pending order tracking
                old_id = pending.order_id
                pending.order_id = result.order_id
                self.pending_orders[result.order_id] = pending
                del self.pending_orders[old_id]
                return True
            else:
                logger.error(
                    f"Failed to replace order for {pending.staged.symbol}: {result.error_message}"
                )
                return False

        except Exception as e:
            logger.error(f"Error modifying order {pending.order_id}: {e}")
            return False

    def cleanup(self):
        """Clear all pending orders and unregister the event callback.

        Call this at the end of the session (after final reconciliation
        and database save) to release resources. After cleanup, the
        executor should not be reused.
        """
        self.pending_orders.clear()
        self.client.ib.orderStatusEvent -= self._on_order_status
        logger.debug("RapidFireExecutor cleanup complete")
