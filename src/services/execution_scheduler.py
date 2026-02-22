"""Execution scheduler for Monday morning automated trading.

This module orchestrates the complete Monday morning execution workflow:
1. Wake up at 9:15 AM ET
2. Stage 1: Pre-market validation (stock price check)
3. Wait for market open
4. Stage 2: Market-open validation (premium check)
5. Execute CONFIRMED trades with fill monitoring
6. Generate execution report

The scheduler coordinates all Phase 4 components:
- PremarketValidator for two-stage validation
- LimitPriceCalculator for price calculations
- Order placement via IBKR
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from ib_insync import LimitOrder
from loguru import logger

from src.services.adaptive_order_executor import AdaptiveOrderExecutor
from src.services.limit_price_calculator import LimitPriceCalculator
from src.services.premarket_validator import (
    OpenCheckResult,
    PremarketCheckResult,
    PremarketValidator,
    StagedOpportunity,
)
from src.services.rapid_fire_executor import RapidFireExecutor


class ExecutionStatus(Enum):
    """Status of a single trade execution."""

    PENDING = "PENDING"
    EXECUTING = "EXECUTING"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    WORKING = "WORKING"  # Order placed but not filled
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    SKIPPED = "SKIPPED"  # Skipped due to validation failure
    ERROR = "ERROR"


@dataclass
class ExecutionConfig:
    """Configuration for execution scheduler.

    All values loaded from environment variables with sensible defaults.

    Attributes:
        fill_wait_seconds: Wait time between fill checks (default 30)
        price_adjustment_increment: $ to adjust limit per attempt (default 0.01)
        max_price_adjustments: Max number of price adjustments (default 2)
        premarket_wakeup_minutes: Minutes before open to start (default 15)
        dry_run_default: Default dry_run mode (default True)
    """

    fill_wait_seconds: int = 30
    price_adjustment_increment: float = 0.01
    max_price_adjustments: int = 2
    premarket_wakeup_minutes: int = 15
    dry_run_default: bool = True
    use_rapid_fire: bool = True  # Use rapid-fire parallel execution

    @classmethod
    def from_env(cls) -> "ExecutionConfig":
        """Load configuration from the central Config singleton.

        Shared values (price adjustments) come from ``get_config()``.
        Execution-specific timing values stay as ``os.getenv``.
        """
        from src.config.base import get_config

        cfg = get_config()
        return cls(
            fill_wait_seconds=int(os.getenv("EXECUTION_FILL_WAIT_SECONDS", "30")),
            price_adjustment_increment=cfg.price_adjustment_increment,
            max_price_adjustments=cfg.max_price_adjustments,
            premarket_wakeup_minutes=int(os.getenv("PREMARKET_WAKEUP_MINUTES", "15")),
            dry_run_default=os.getenv("DRY_RUN_DEFAULT", "true").lower() == "true",
            use_rapid_fire=os.getenv("USE_RAPID_FIRE", "true").lower() == "true",
        )


@dataclass
class TradeExecutionResult:
    """Result of executing a single trade.

    Contains all details about the execution attempt.

    Attributes:
        opportunity: The opportunity that was executed
        status: Execution status
        order_id: IBKR order ID (None if dry-run or failed)
        limit_price: Limit price used for the order
        fill_price: Actual fill price (None if not filled)
        contracts_filled: Number of contracts filled
        contracts_requested: Number of contracts requested
        adjustments_made: Number of price adjustments attempted
        final_limit: Final limit price after adjustments
        fill_time: Time of fill (None if not filled)
        error_message: Error message if failed
        dry_run: Whether this was a dry run
        order_type: Type of order used (Adaptive, LIMIT, or LIMIT fallback)
        live_bid: Live bid at order placement
        live_ask: Live ask at order placement
        calculated_limit: Limit calculated from live quotes
        staged_limit: Original staged limit price
        limit_deviation: Deviation between calculated and staged limit
    """

    opportunity: StagedOpportunity
    status: ExecutionStatus
    order_id: int | None = None
    limit_price: float = 0.0
    fill_price: float | None = None
    contracts_filled: int = 0
    contracts_requested: int = 0
    adjustments_made: int = 0
    final_limit: float = 0.0
    fill_time: datetime | None = None
    error_message: str | None = None
    dry_run: bool = False
    order_type: str | None = None
    live_bid: float | None = None
    live_ask: float | None = None
    calculated_limit: float | None = None
    staged_limit: float | None = None
    limit_deviation: float | None = None

    @property
    def is_success(self) -> bool:
        """Check if execution was successful (filled or working)."""
        return self.status in (ExecutionStatus.FILLED, ExecutionStatus.WORKING)

    @property
    def premium_received(self) -> float:
        """Calculate premium received (0 if not filled)."""
        if self.fill_price and self.contracts_filled > 0:
            return self.fill_price * 100 * self.contracts_filled
        return 0.0


@dataclass
class ExecutionReport:
    """Complete report of Monday morning execution.

    Summarizes all validation and execution results.

    Attributes:
        execution_date: Date of execution
        started_at: When execution started
        completed_at: When execution completed
        dry_run: Whether this was a dry run
        premarket_results: Stage 1 validation results
        open_results: Stage 2 validation results
        execution_results: Individual trade execution results
        staged_count: Number of trades staged
        validated_count: Number that passed Stage 1
        confirmed_count: Number that passed Stage 2
        executed_count: Number executed (or attempted)
        filled_count: Number filled
        working_count: Number left working
        failed_count: Number failed/skipped
        total_premium: Total premium from fills
        total_margin: Total margin used
        warnings: Any warnings generated
    """

    execution_date: datetime
    started_at: datetime
    completed_at: datetime | None = None
    dry_run: bool = False
    premarket_results: list[PremarketCheckResult] = field(default_factory=list)
    open_results: list[OpenCheckResult] = field(default_factory=list)
    execution_results: list[TradeExecutionResult] = field(default_factory=list)
    staged_count: int = 0
    validated_count: int = 0
    confirmed_count: int = 0
    executed_count: int = 0
    filled_count: int = 0
    working_count: int = 0
    failed_count: int = 0
    total_premium: float = 0.0
    total_margin: float = 0.0
    warnings: list[str] = field(default_factory=list)

    @property
    def duration_seconds(self) -> float:
        """Calculate execution duration in seconds."""
        if self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return 0.0

    @property
    def success_rate(self) -> float:
        """Calculate success rate (filled / executed)."""
        if self.executed_count == 0:
            return 0.0
        return self.filled_count / self.executed_count


class IBKRClientProtocol(Protocol):
    """Protocol for IBKR client dependency injection."""

    def get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price."""
        ...

    def get_option_quote(
        self, symbol: str, strike: float, expiration: str, right: str
    ) -> dict | None:
        """Get option quote."""
        ...

    def place_order(self, contract, order) -> int | None:
        """Place an order."""
        ...

    def get_order_status(self, order_id: int) -> dict | None:
        """Get order status."""
        ...

    def cancel_order(self, order_id: int) -> bool:
        """Cancel an order."""
        ...


class ExecutionScheduler:
    """Orchestrate automated trade execution at market open.

    The ExecutionScheduler handles the complete Monday morning workflow:
    1. Pre-market validation (Stage 1 - 9:15 AM)
    2. Market-open validation (Stage 2 - 9:30 AM)
    3. Order execution with fill monitoring

    Example:
        >>> scheduler = ExecutionScheduler(ibkr_client)
        >>> opportunities = get_staged_opportunities()
        >>> report = scheduler.run_monday_morning(opportunities, dry_run=True)
        >>> print(f"Executed {report.executed_count} trades")
    """

    def __init__(
        self,
        ibkr_client: IBKRClientProtocol | None = None,
        validator: PremarketValidator | None = None,
        limit_calculator: LimitPriceCalculator | None = None,
        adaptive_executor: AdaptiveOrderExecutor | None = None,
        rapid_fire_executor: RapidFireExecutor | None = None,
        config: ExecutionConfig | None = None,
        risk_governor: any = None,
    ):
        """Initialize the execution scheduler.

        Args:
            ibkr_client: IBKR client for quotes and orders
            validator: PremarketValidator instance
            limit_calculator: LimitPriceCalculator instance
            adaptive_executor: AdaptiveOrderExecutor instance (optional)
            rapid_fire_executor: RapidFireExecutor instance (optional)
            config: Execution configuration
            risk_governor: RiskGovernor for post-trade margin verification
        """
        self.ibkr_client = ibkr_client
        self.validator = validator or PremarketValidator(ibkr_client=ibkr_client)
        self.limit_calculator = limit_calculator or LimitPriceCalculator()
        self.config = config or ExecutionConfig.from_env()
        self.risk_governor = risk_governor

        # Initialize adaptive executor if ibkr_client is provided
        if adaptive_executor:
            self.adaptive_executor = adaptive_executor
        elif ibkr_client and hasattr(ibkr_client, 'get_quote'):
            # Only create if ibkr_client supports new wrapper methods
            self.adaptive_executor = AdaptiveOrderExecutor(
                ibkr_client=ibkr_client,  # type: ignore
                limit_calc=self.limit_calculator,
            )
        else:
            self.adaptive_executor = None

        # Initialize rapid-fire executor if enabled
        if rapid_fire_executor:
            self.rapid_fire_executor = rapid_fire_executor
        elif self.adaptive_executor and self.config.use_rapid_fire:
            self.rapid_fire_executor = RapidFireExecutor(
                ibkr_client=ibkr_client,  # type: ignore
                adaptive_executor=self.adaptive_executor,
                risk_governor=self.risk_governor,
            )
        else:
            self.rapid_fire_executor = None

        logger.debug(
            f"ExecutionScheduler initialized: "
            f"fill_wait={self.config.fill_wait_seconds}s, "
            f"adjustments={self.config.max_price_adjustments}, "
            f"increment=${self.config.price_adjustment_increment}, "
            f"adaptive_executor={'enabled' if self.adaptive_executor else 'disabled'}, "
            f"rapid_fire={'enabled' if self.rapid_fire_executor else 'disabled'}"
        )

    def run_monday_morning(
        self,
        staged_opportunities: list[StagedOpportunity],
        dry_run: bool = True,
    ) -> ExecutionReport:
        """Run the complete Monday morning execution workflow.

        Timeline:
        1. Stage 1: Pre-market validation (check stock prices)
        2. Stage 2: Market-open validation (check premiums)
        3. Execute CONFIRMED trades in priority order

        Args:
            staged_opportunities: List of staged opportunities to process
            dry_run: If True, simulate orders without placing them

        Returns:
            ExecutionReport with full execution details
        """
        started_at = datetime.now()
        logger.info(
            f"{'[DRY-RUN] ' if dry_run else ''}"
            f"Starting Monday morning execution for {len(staged_opportunities)} trades"
        )

        report = ExecutionReport(
            execution_date=started_at.date(),
            started_at=started_at,
            dry_run=dry_run,
            staged_count=len(staged_opportunities),
        )

        if not staged_opportunities:
            report.completed_at = datetime.now()
            report.warnings.append("No staged opportunities to execute")
            logger.warning("No staged opportunities provided")
            return report

        # Stage 1: Pre-market validation
        logger.info("=== STAGE 1: Pre-market validation ===")
        premarket_results = self.validator.validate_premarket(staged_opportunities)
        report.premarket_results = premarket_results

        # Filter for Stage 2
        ready_opportunities = [
            r.opportunity for r in premarket_results if r.passed
        ]
        report.validated_count = len(ready_opportunities)

        if not ready_opportunities:
            report.completed_at = datetime.now()
            report.warnings.append("No opportunities passed pre-market validation")
            logger.warning("No opportunities passed Stage 1, nothing to execute")
            return report

        logger.info(
            f"Stage 1 complete: {report.validated_count}/{report.staged_count} passed"
        )

        # Stage 2: Market-open validation
        logger.info("=== STAGE 2: Market-open validation ===")
        open_results = self.validator.validate_at_open(ready_opportunities)
        report.open_results = open_results

        # Filter for execution
        confirmed_opportunities = [
            r.opportunity for r in open_results if r.passed
        ]
        report.confirmed_count = len(confirmed_opportunities)

        if not confirmed_opportunities:
            report.completed_at = datetime.now()
            report.warnings.append("No opportunities passed market-open validation")
            logger.warning("No opportunities passed Stage 2, nothing to execute")
            return report

        logger.info(
            f"Stage 2 complete: {report.confirmed_count}/{report.validated_count} confirmed"
        )

        # Execute confirmed trades
        logger.info("=== EXECUTING TRADES ===")

        # Use rapid-fire parallel execution if available
        if self.rapid_fire_executor and not dry_run:
            logger.info(
                f"🚀 Using RAPID-FIRE parallel execution for "
                f"{len(confirmed_opportunities)} trades"
            )

            import asyncio
            rf_report = asyncio.run(
                self.rapid_fire_executor.execute_all(confirmed_opportunities)
            )

            # Convert rapid-fire report to execution results
            execution_results: list[TradeExecutionResult] = []

            # Map filled orders
            for summary in rf_report.filled:
                execution_results.append(
                    TradeExecutionResult(
                        opportunity=next(
                            (o for o in confirmed_opportunities if o.symbol == summary.symbol),
                            confirmed_opportunities[0],
                        ),
                        status=ExecutionStatus.FILLED,
                        order_id=summary.order_id,
                        limit_price=summary.submitted_limit,
                        fill_price=summary.fill_price,
                        contracts_filled=summary.fill_time,  # Placeholder
                        contracts_requested=0,  # Will be filled from opportunity
                        final_limit=summary.fill_price,
                        fill_time=summary.fill_time,
                        order_type=summary.order_type,
                        dry_run=False,
                    )
                )
                report.filled_count += 1
                report.total_premium += rf_report.total_premium

            # Map working orders
            for summary in rf_report.working:
                execution_results.append(
                    TradeExecutionResult(
                        opportunity=next(
                            (o for o in confirmed_opportunities if o.symbol == summary.symbol),
                            confirmed_opportunities[0],
                        ),
                        status=ExecutionStatus.WORKING,
                        order_id=summary.order_id,
                        limit_price=summary.submitted_limit,
                        contracts_requested=0,
                        final_limit=summary.submitted_limit,
                        order_type=summary.order_type,
                        dry_run=False,
                    )
                )
                report.working_count += 1

            # Map failed/skipped
            for summary in rf_report.failed + rf_report.skipped:
                execution_results.append(
                    TradeExecutionResult(
                        opportunity=next(
                            (o for o in confirmed_opportunities if o.symbol == summary.symbol),
                            confirmed_opportunities[0],
                        ),
                        status=ExecutionStatus.FAILED,
                        order_id=summary.order_id,
                        limit_price=0.0,
                        contracts_requested=0,
                        error_message=summary.reason,
                        order_type=summary.order_type,
                        dry_run=False,
                    )
                )
                report.failed_count += 1

            report.executed_count = len(execution_results)
            report.execution_results = execution_results

            logger.info(
                f"🚀 Rapid-fire complete: submitted in {rf_report.submission_time:.2f}s, "
                f"monitored for {rf_report.monitoring_time:.2f}s"
            )

        else:
            # Fall back to sequential execution (legacy or dry-run)
            execution_results: list[TradeExecutionResult] = []

            for i, opp in enumerate(confirmed_opportunities, 1):
                logger.info(f"Executing trade {i}/{len(confirmed_opportunities)}: {opp.symbol}")

                result = self._execute_single_trade(opp, dry_run=dry_run)
                execution_results.append(result)

                if result.status == ExecutionStatus.FILLED:
                    report.filled_count += 1
                    report.total_premium += result.premium_received
                elif result.status == ExecutionStatus.WORKING:
                    report.working_count += 1
                else:
                    report.failed_count += 1

                report.executed_count += 1

            report.execution_results = execution_results

        report.completed_at = datetime.now()

        # Log summary
        logger.info(
            f"Execution complete: {report.filled_count} filled, "
            f"{report.working_count} working, {report.failed_count} failed"
        )

        return report

    def _execute_single_trade(
        self,
        opportunity: StagedOpportunity,
        dry_run: bool = True,
    ) -> TradeExecutionResult:
        """Execute a single trade.

        Steps:
        1. Get effective strike and limit price
        2. Place LIMIT SELL order
        3. Wait for fill
        4. If not filled, adjust limit and retry
        5. Return result

        Args:
            opportunity: The opportunity to execute
            dry_run: If True, simulate without placing real order

        Returns:
            TradeExecutionResult with execution details
        """
        effective_strike = opportunity.adjusted_strike or opportunity.strike
        effective_limit = opportunity.adjusted_limit_price or opportunity.staged_limit_price
        contracts = opportunity.staged_contracts

        logger.info(
            f"  {opportunity.symbol} ${effective_strike:.0f}P: "
            f"limit=${effective_limit:.2f}, contracts={contracts}"
        )

        if dry_run:
            # Simulate successful execution
            return TradeExecutionResult(
                opportunity=opportunity,
                status=ExecutionStatus.FILLED,
                limit_price=effective_limit,
                fill_price=effective_limit,
                contracts_filled=contracts,
                contracts_requested=contracts,
                final_limit=effective_limit,
                fill_time=datetime.now(),
                dry_run=True,
            )

        # Real execution (requires IBKR client)
        if not self.ibkr_client:
            logger.error(f"Cannot execute {opportunity.symbol}: No IBKR client")
            return TradeExecutionResult(
                opportunity=opportunity,
                status=ExecutionStatus.ERROR,
                limit_price=effective_limit,
                contracts_requested=contracts,
                error_message="No IBKR client connected",
                dry_run=False,
            )

        # Place order and monitor
        try:
            result = self._place_and_monitor_order(
                opportunity=opportunity,
                strike=effective_strike,
                limit_price=effective_limit,
                contracts=contracts,
            )
            return result

        except Exception as e:
            logger.error(f"Error executing {opportunity.symbol}: {e}")
            return TradeExecutionResult(
                opportunity=opportunity,
                status=ExecutionStatus.ERROR,
                limit_price=effective_limit,
                contracts_requested=contracts,
                error_message=str(e),
                dry_run=False,
            )

    def _place_and_monitor_order(
        self,
        opportunity: StagedOpportunity,
        strike: float,
        limit_price: float,
        contracts: int,
    ) -> TradeExecutionResult:
        """Place order and monitor for fill.

        This is the core execution logic:
        1. Get LIVE quote (not using stale staged price)
        2. Place order using AdaptiveOrderExecutor if available
        3. Wait for fill
        4. If not filled, adjust limit and retry
        5. Leave working after max adjustments

        Args:
            opportunity: The opportunity
            strike: Strike price
            limit_price: Initial limit price (from staging, may be stale)
            contracts: Number of contracts

        Returns:
            TradeExecutionResult with order type tracking
        """
        import asyncio

        current_limit = limit_price
        adjustments = 0
        order_id = None
        trade = None
        order_type = None
        live_bid = None
        live_ask = None
        calculated_limit = None

        # Get expiration in IBKR format (YYYYMMDD)
        exp_str = opportunity.expiration
        if isinstance(exp_str, str):
            # Convert from ISO format "2026-02-06" to IBKR format "20260206"
            exp_str = exp_str.replace("-", "")
        else:
            # Convert datetime to IBKR format
            exp_str = exp_str.strftime("%Y%m%d")

        # Create and qualify the option contract
        contract = self.ibkr_client.get_option_contract(
            symbol=opportunity.symbol,
            expiration=exp_str,
            strike=strike,
            right="P",
        )

        qualified = self.ibkr_client.qualify_contract(contract)
        if not qualified:
            logger.error(f"  Failed to qualify contract for {opportunity.symbol}")
            return TradeExecutionResult(
                opportunity=opportunity,
                status=ExecutionStatus.FAILED,
                order_id=None,
                limit_price=limit_price,
                contracts_requested=contracts,
                adjustments_made=0,
                final_limit=current_limit,
                dry_run=False,
            )

        # Use AdaptiveOrderExecutor if available (Phase A implementation)
        if self.adaptive_executor:
            logger.info(
                f"  Placing order with Adaptive Algo: {opportunity.symbol} ${strike}P x {contracts}"
            )

            # Get LIVE quote (event-driven, not stale)
            quote = asyncio.run(self.adaptive_executor.get_live_quote(qualified))

            if not quote.is_tradeable:
                logger.warning(
                    f"  {opportunity.symbol}: Not tradeable - {quote.reason}"
                )
                return TradeExecutionResult(
                    opportunity=opportunity,
                    status=ExecutionStatus.REJECTED,
                    order_id=None,
                    limit_price=limit_price,
                    contracts_requested=contracts,
                    error_message=quote.reason,
                    order_type="REJECTED",
                    live_bid=quote.bid,
                    live_ask=quote.ask,
                    calculated_limit=quote.limit,
                    staged_limit=opportunity.staged_limit_price,
                    limit_deviation=abs(quote.limit - opportunity.staged_limit_price),
                    dry_run=False,
                )

            # Track live quote info
            live_bid = quote.bid
            live_ask = quote.ask
            calculated_limit = quote.limit
            current_limit = quote.limit

            logger.info(
                f"  Live quote: bid=${quote.bid:.2f}, ask=${quote.ask:.2f}, "
                f"limit=${quote.limit:.2f} (staged=${limit_price:.2f})"
            )

            # Place order using adaptive executor
            result = asyncio.run(
                self.adaptive_executor.place_order(opportunity, qualified, quote)
            )

            if not result.success:
                logger.error(f"  Order failed: {result.error_message}")
                return TradeExecutionResult(
                    opportunity=opportunity,
                    status=ExecutionStatus.FAILED,
                    order_id=None,
                    limit_price=calculated_limit,
                    contracts_requested=contracts,
                    error_message=result.error_message,
                    order_type=result.order_type,
                    live_bid=live_bid,
                    live_ask=live_ask,
                    calculated_limit=calculated_limit,
                    staged_limit=opportunity.staged_limit_price,
                    limit_deviation=result.limit_deviation,
                    dry_run=False,
                )

            order_id = result.order_id
            order_type = result.order_type

            # Get the trade object for monitoring
            trades = [t for t in self.ibkr_client.get_trades() if t.order.orderId == order_id]
            if trades:
                trade = trades[0]
            else:
                logger.error(f"  Could not find trade for order {order_id}")
                return TradeExecutionResult(
                    opportunity=opportunity,
                    status=ExecutionStatus.FAILED,
                    order_id=order_id,
                    limit_price=calculated_limit,
                    contracts_requested=contracts,
                    error_message="Trade not found after placement",
                    order_type=order_type,
                    live_bid=live_bid,
                    live_ask=live_ask,
                    calculated_limit=calculated_limit,
                    staged_limit=opportunity.staged_limit_price,
                    limit_deviation=result.limit_deviation,
                    dry_run=False,
                )

        else:
            # Legacy execution (no AdaptiveOrderExecutor)
            logger.info(
                f"  Placing order (legacy): {opportunity.symbol} ${strike}P "
                f"@ ${current_limit:.2f} x {contracts}"
            )

            order = LimitOrder(
                action="SELL",
                totalQuantity=contracts,
                lmtPrice=current_limit,
            )
            order.tif = "DAY"

            trade = asyncio.run(self.ibkr_client.place_order(
                qualified,
                order,
                reason=f"Staged trade {opportunity.symbol} (legacy path)"
            ))
            order_id = trade.order.orderId
            order_type = "LIMIT (legacy)"

        # Wait for initial submission
        asyncio.run(self.ibkr_client.sleep(2))

        # Monitor for fill with adjustments
        while adjustments <= self.config.max_price_adjustments:
            # Wait for configured fill time
            asyncio.run(self.ibkr_client.sleep(self.config.fill_wait_seconds))

            # Check if filled
            if trade.orderStatus.status == "Filled":
                fill_price = trade.orderStatus.avgFillPrice
                logger.info(
                    f"  ✓ FILLED @ ${fill_price:.2f} "
                    f"(slippage: ${fill_price - (calculated_limit or limit_price):+.2f})"
                )
                return TradeExecutionResult(
                    opportunity=opportunity,
                    status=ExecutionStatus.FILLED,
                    order_id=order_id,
                    limit_price=calculated_limit or limit_price,
                    fill_price=fill_price,
                    contracts_filled=contracts,
                    contracts_requested=contracts,
                    adjustments_made=adjustments,
                    final_limit=fill_price,
                    order_type=order_type,
                    live_bid=live_bid,
                    live_ask=live_ask,
                    calculated_limit=calculated_limit,
                    staged_limit=opportunity.staged_limit_price,
                    limit_deviation=abs((calculated_limit or limit_price) - opportunity.staged_limit_price),
                    dry_run=False,
                )

            # Not filled - adjust price if we haven't hit max adjustments
            if adjustments < self.config.max_price_adjustments:
                new_limit = current_limit - self.config.price_adjustment_increment
                logger.info(
                    f"  Not filled, adjusting limit ${current_limit:.2f} → ${new_limit:.2f}"
                )

                # Cancel existing order
                asyncio.run(self.ibkr_client.cancel_order(
                    trade.order.orderId,
                    reason=f"Price adjustment {adjustments + 1}"
                ))
                asyncio.run(self.ibkr_client.sleep(1))

                # Place new order with adjusted price
                current_limit = new_limit
                order = LimitOrder(
                    action="SELL",
                    totalQuantity=contracts,
                    lmtPrice=current_limit,
                )
                order.tif = "DAY"

                trade = asyncio.run(self.ibkr_client.place_order(
                    qualified,
                    order,
                    reason=f"Price adjustment {adjustments + 1} for {opportunity.symbol}"
                ))
                order_id = trade.order.orderId
                adjustments += 1
                asyncio.run(self.ibkr_client.sleep(2))
            else:
                # Max adjustments reached, leave order working
                logger.info("  Max adjustments reached, leaving order working")
                break

        # Return working status (order still active but not filled)
        return TradeExecutionResult(
            opportunity=opportunity,
            status=ExecutionStatus.WORKING,
            order_id=order_id,
            limit_price=calculated_limit or limit_price,
            contracts_requested=contracts,
            adjustments_made=adjustments,
            final_limit=current_limit,
            order_type=order_type,
            live_bid=live_bid,
            live_ask=live_ask,
            calculated_limit=calculated_limit,
            staged_limit=opportunity.staged_limit_price,
            limit_deviation=abs((calculated_limit or limit_price) - opportunity.staged_limit_price) if calculated_limit else None,
            dry_run=False,
        )

    def validate_only_premarket(
        self,
        opportunities: list[StagedOpportunity],
    ) -> list[PremarketCheckResult]:
        """Run only Stage 1 pre-market validation.

        For manual triggering via validate-staged command.

        Args:
            opportunities: Staged opportunities to validate

        Returns:
            List of PremarketCheckResult
        """
        return self.validator.validate_premarket(opportunities)

    def validate_only_at_open(
        self,
        opportunities: list[StagedOpportunity],
    ) -> list[OpenCheckResult]:
        """Run only Stage 2 market-open validation.

        For manual triggering via validate-staged --at-open command.

        Args:
            opportunities: Ready opportunities (from Stage 1)

        Returns:
            List of OpenCheckResult
        """
        return self.validator.validate_at_open(opportunities)

    def get_execution_summary(self, report: ExecutionReport) -> str:
        """Generate a text summary of execution report.

        Args:
            report: The execution report

        Returns:
            Formatted summary string
        """
        lines = [
            "",
            "=" * 60,
            f"  EXECUTION REPORT - {report.execution_date.strftime('%Y-%m-%d')}",
            f"  {'[DRY-RUN]' if report.dry_run else '[LIVE]'}",
            "=" * 60,
            "",
            f"  Staged:    {report.staged_count}",
            f"  Validated: {report.validated_count} (passed Stage 1)",
            f"  Confirmed: {report.confirmed_count} (passed Stage 2)",
            f"  Executed:  {report.executed_count}",
            "",
            f"  Filled:    {report.filled_count}",
            f"  Working:   {report.working_count}",
            f"  Failed:    {report.failed_count}",
            "",
            f"  Total Premium: ${report.total_premium:,.2f}",
            f"  Duration:      {report.duration_seconds:.1f}s",
            "",
        ]

        if report.warnings:
            lines.append("  Warnings:")
            for warning in report.warnings:
                lines.append(f"    - {warning}")
            lines.append("")

        lines.append("=" * 60)

        return "\n".join(lines)
