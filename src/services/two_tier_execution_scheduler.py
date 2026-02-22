"""Two-tier execution scheduler with progressive automation modes.

This module implements Phase D: intelligent two-tier execution strategy
that preserves pre-market research value while leveraging market condition
improvements later in the session.

Key Features:
- Tier 1 (9:30 AM): Execute while pre-market research still valid
- Tier 2 (Condition-based): Retry unfilled when VIX low + spreads tight
- Progressive automation: hybrid → supervised → autonomous
- FINRA-compliant clock synchronization
- Scalable from 3-5 trades → 10-15 trades

Timeline:
09:15    Stage 1: Pre-market validation
09:30    Adaptive strike selection (or Stage 2 fallback)
09:30    TIER 1: Submit all orders (conservative)
09:35-10:30  TIER 2: Monitor conditions, retry when favorable
10:30    Final reconciliation

Usage:
    scheduler = TwoTierExecutionScheduler(
        ibkr_client=client,
        automation_mode=AutomationMode.HYBRID,  # Start with manual trigger
    )

    report = await scheduler.run_monday_morning(staged_trades)
"""

import asyncio
import os
from datetime import datetime, time
from enum import Enum
from zoneinfo import ZoneInfo

from loguru import logger
from rich.console import Console

from src.data.opportunity_state import OpportunityState
from src.services.execution_scheduler import ExecutionReport
from src.services.fill_manager import FillManager
from src.services.live_strike_selector import LiveStrikeSelector
from src.services.market_conditions import MarketConditionMonitor
from src.services.premarket_validator import PremarketValidator, StagedOpportunity, ValidationStatus
from src.services.rapid_fire_executor import RapidFireExecutor
from src.services.order_reconciliation import OrderReconciliation
from src.tools.ibkr_client import IBKRClient

console = Console()


class AutomationMode(Enum):
    """Progressive automation modes for gradual transition to autonomy.

    HYBRID: Automated prep, manual execution trigger
        - System does Stage 1/2 validation automatically
        - User reviews market conditions
        - User clicks "execute" when ready
        - Use during initial testing (weeks 1-3)

    SUPERVISED: Automated execution, manual report review
        - System executes automatically (Tier 1 + Tier 2)
        - User reviews execution report after completion
        - Use during confidence building (weeks 4-8)

    AUTONOMOUS: Fully automated, alerts only on errors
        - System runs completely autonomous
        - User receives report but no review required
        - Only intervenes if alerts triggered
        - Use after extensive testing (weeks 9+)
    """
    HYBRID = "hybrid"
    SUPERVISED = "supervised"
    AUTONOMOUS = "autonomous"


class TwoTierExecutionScheduler:
    """Two-tier execution with condition-based retry and progressive automation.

    Implements intelligent execution timing that preserves pre-market research
    value while opportunistically retrying unfilled orders when market
    conditions improve.

    Design Philosophy:
    - Tier 1: Execute at 9:30 before prices invalidate strikes/premiums
    - Tier 2: Wait for favorable conditions (VIX low, spreads tight)
    - Progressive automation for safe transition to autonomous operation

    Example (Hybrid Mode):
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=client,
            automation_mode=AutomationMode.HYBRID,
        )

        # System does prep, asks you to click "execute"
        report = await scheduler.run_monday_morning(staged_trades)

    Example (Autonomous Mode):
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=client,
            automation_mode=AutomationMode.AUTONOMOUS,
        )

        # Fully automated - you wake up to a report
        report = await scheduler.run_monday_morning(staged_trades)
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        premarket_validator: PremarketValidator | None = None,
        rapid_fire_executor: RapidFireExecutor | None = None,
        reconciler: OrderReconciliation | None = None,
        condition_monitor: MarketConditionMonitor | None = None,
        strike_selector: LiveStrikeSelector | None = None,
        fill_manager: FillManager | None = None,
        automation_mode: AutomationMode = AutomationMode.HYBRID,
        tier2_enabled: bool = True,
    ):
        """Initialize two-tier execution scheduler.

        Args:
            ibkr_client: Connected IBKRClient instance
            premarket_validator: Pre-market validator (creates if None)
            rapid_fire_executor: Rapid-fire executor (creates if None)
            reconciler: Order reconciliation (creates if None)
            condition_monitor: Market condition monitor (creates if None)
            strike_selector: Live strike selector (None = disabled)
            fill_manager: Fill manager (None = disabled, uses legacy wait)
            automation_mode: Automation mode (default: HYBRID for testing)
            tier2_enabled: Enable Tier 2 retry logic (default: True)
        """
        self.client = ibkr_client
        self.validator = premarket_validator or PremarketValidator(ibkr_client=ibkr_client)
        self.executor = rapid_fire_executor  # Will be created with adaptive executor
        self.reconciler = reconciler  # Will be created if needed
        self.conditions = condition_monitor or MarketConditionMonitor(ibkr_client)
        self.strike_selector = strike_selector
        self.fill_manager = fill_manager
        self.automation_mode = automation_mode
        self.tier2_enabled = tier2_enabled

        # Track which order IDs have been saved to database (prevents duplicates)
        self._saved_order_ids: set[int] = set()

        # Load Tier 2 configuration from environment
        self.tier2_window_start = self._parse_time(os.getenv("TIER2_WINDOW_START", "09:45"))
        self.tier2_window_end = self._parse_time(os.getenv("TIER2_WINDOW_END", "10:30"))
        self.tier2_check_interval = int(os.getenv("TIER2_CHECK_INTERVAL", "300"))  # 5 minutes
        self.tier2_limit_adjustment = float(os.getenv("TIER2_LIMIT_ADJUSTMENT", "1.1"))

        # Execution timeline
        self.stage1_time = self._parse_time(os.getenv("STAGE1_TIME", "09:15"))
        self.tier1_time = self._parse_time(os.getenv("TIER1_EXECUTION_TIME", "09:30"))
        self.reconciliation_time = self._parse_time(os.getenv("RECONCILIATION_TIME", "10:30"))

        logger.info(
            f"TwoTierExecutionScheduler initialized:\n"
            f"  Mode: {automation_mode.value}\n"
            f"  Tier 2: {'enabled' if tier2_enabled else 'disabled'}\n"
            f"  Tier 2 window: {self.tier2_window_start} - {self.tier2_window_end}\n"
            f"  Tier 2 adjustment: {self.tier2_limit_adjustment}x\n"
            f"  Check interval: {self.tier2_check_interval}s"
        )

    async def run_monday_morning(
        self,
        staged_trades: list[StagedOpportunity],
        dry_run: bool = False
    ) -> ExecutionReport:
        """Execute two-tier strategy based on automation mode.

        Timeline:
        09:15    Stage 1: Pre-market validation
        09:30    Adaptive strike selection (or Stage 2 fallback)
        09:30    Tier 1: Submit all orders
        09:35-10:30  Tier 2: Condition-based retry
        10:30    Final reconciliation

        Args:
            staged_trades: List of staged opportunities from weekend screening
            dry_run: If True, simulate without real orders

        Returns:
            ExecutionReport with complete execution details

        Raises:
        """
        logger.info(
            f"{'[DRY-RUN] ' if dry_run else ''}"
            f"Starting two-tier execution for {len(staged_trades)} trades "
            f"(mode: {self.automation_mode.value})"
        )

        # ── Stage 1: Pre-market validation (9:15 AM) ──
        await self._wait_until_time(self.stage1_time, "Stage 1 validation")
        logger.info("📊 Stage 1: Pre-market validation")

        stage1_results = self.validator.validate_premarket(staged_trades)
        ready_trades = [r.opportunity for r in stage1_results if r.passed]

        console.print(self._format_stage1_table(stage1_results))

        if not ready_trades:
            logger.warning("No trades passed Stage 1 validation")
            return ExecutionReport(
                execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                started_at=datetime.now(ZoneInfo("America/New_York")),
                completed_at=datetime.now(ZoneInfo("America/New_York")),
                dry_run=dry_run,
                warnings=["No trades passed Stage 1"],
            )

        logger.info(f"Stage 1 complete: {len(ready_trades)}/{len(staged_trades)} passed")

        # ── Strike validation: adaptive selection OR Stage 2 fallback ──
        if self.strike_selector:
            # Adaptive strike selection replaces Stage 2.
            # The strike selector validates delta, premium, OTM%, spread, and
            # liquidity — everything Stage 2 checks plus more.
            # Wait for market open so option bids are valid (not -1.0).
            await self._wait_until_time(self.tier1_time, "market open for strike selection")
            logger.info("🎯 Running adaptive strike selection (Stage 2 skipped)")

            strike_results = await self.strike_selector.select_all(ready_trades)

            for r in strike_results:
                if r.status == "ABANDONED":
                    logger.warning(
                        f"  ✗ {r.opportunity.symbol}: ABANDONED — {r.reason}"
                    )
                else:
                    logger.info(
                        f"  ✓ {r.opportunity.symbol}: {r.status} "
                        f"strike=${r.selected_strike} delta={r.selected_delta}"
                    )

            confirmed_trades = [
                r.opportunity for r in strike_results
                if r.status != "ABANDONED"
            ]

            if not confirmed_trades:
                logger.warning("No trades remain after adaptive strike selection")
                return ExecutionReport(
                    execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                    started_at=datetime.now(ZoneInfo("America/New_York")),
                    completed_at=datetime.now(ZoneInfo("America/New_York")),
                    dry_run=dry_run,
                    warnings=["All trades abandoned during strike selection"],
                )

            logger.info(
                f"Strike selection complete: "
                f"{len(confirmed_trades)}/{len(ready_trades)} confirmed"
            )
        else:
            # Fallback: Stage 2 premium validation at market open (not 9:28).
            # Options don't trade pre-market; IBKR returns bid=-1.0 before 9:30.
            await self._wait_until_time(self.tier1_time, "Stage 2 validation at market open")
            logger.info("🔄 Stage 2: Refreshing quotes and validating premiums")

            stage2_results = self.validator.validate_at_open(ready_trades)
            confirmed_trades = [
                r.opportunity
                for r in stage2_results
                if r.status in (ValidationStatus.READY, ValidationStatus.ADJUSTED)
            ]

            console.print(self._format_stage2_table(stage2_results))

            if not confirmed_trades:
                logger.warning("No trades confirmed after Stage 2")
                return ExecutionReport(
                    execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                    started_at=datetime.now(ZoneInfo("America/New_York")),
                    completed_at=datetime.now(ZoneInfo("America/New_York")),
                    dry_run=dry_run,
                    warnings=["No trades passed Stage 2"],
                )

            logger.info(
                f"Stage 2 complete: {len(confirmed_trades)}/{len(ready_trades)} confirmed"
            )

        # ── Automation mode branching ──
        if self.automation_mode == AutomationMode.HYBRID:
            return await self._run_hybrid_mode(confirmed_trades, dry_run)
        elif self.automation_mode == AutomationMode.SUPERVISED:
            return await self._run_supervised_mode(confirmed_trades, dry_run)
        else:  # AUTONOMOUS
            return await self._run_autonomous_mode(confirmed_trades, dry_run)

    async def _run_hybrid_mode(
        self,
        staged: list[StagedOpportunity],
        dry_run: bool
    ) -> ExecutionReport:
        """HYBRID mode: Automated prep, manual execution trigger.

        System shows market conditions and waits for user to type "execute".
        """
        logger.info("🔧 HYBRID MODE: Waiting for manual execution trigger")

        # Pre-flight safety checks (non-blocking for hybrid mode)
        is_safe, errors = self._pre_flight_validation(staged)
        if not is_safe:
            console.print("\n[bold red]⚠ PRE-FLIGHT VALIDATION WARNINGS:[/bold red]")
            for error in errors:
                console.print(f"  [red]❌ {error}[/red]")
            console.print("\n[yellow]Review these warnings before executing.[/yellow]\n")

        # Get current market conditions
        sample_contracts = [
            self.client.get_option_contract(
                symbol=s.symbol,
                expiration=s.expiration.replace("-", ""),
                strike=s.strike,
                right="P"
            )
            for s in staged[:5]  # Sample first 5
        ]

        conditions = await self.conditions.check_conditions(sample_contracts)

        # Present to user
        console.print(f"""
        ╔════════════════════════════════════════════════════════╗
        ║           READY FOR EXECUTION (Hybrid Mode)            ║
        ╚════════════════════════════════════════════════════════╝

        Trades Ready:    {len(staged)}
        Current Time:    {datetime.now(ZoneInfo("America/New_York")).strftime('%H:%M:%S')} ET

        Market Conditions:
          VIX:           {conditions.vix:.1f}
          SPY:           ${conditions.spy_price:.2f}
          Avg Spread:    ${conditions.avg_spread:.3f}
          Status:        {'✓ FAVORABLE' if conditions.conditions_favorable else '✗ UNFAVORABLE'}
          Reason:        {conditions.reason}

        ────────────────────────────────────────────────────────
        Type 'execute' to submit all orders now
        Type 'wait' to check again in 5 minutes
        Type 'abort' to cancel execution
        ────────────────────────────────────────────────────────
        """)

        # Wait for user decision
        command = await self._wait_for_user_input()

        if command == 'abort':
            logger.info("Execution aborted by user")
            return ExecutionReport(
                execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                started_at=datetime.now(ZoneInfo("America/New_York")),
                completed_at=datetime.now(ZoneInfo("America/New_York")),
                dry_run=dry_run,
                warnings=["Execution aborted by user"],
            )

        elif command == 'wait':
            logger.info("User requested wait - checking again in 5 minutes")
            for _ in range(60):
                await asyncio.sleep(5)
            return await self._run_hybrid_mode(staged, dry_run)  # Recursive retry

        # User typed 'execute' - proceed
        return await self._execute_tier1_and_tier2(staged, dry_run)

    async def _run_supervised_mode(
        self,
        staged: list[StagedOpportunity],
        dry_run: bool
    ) -> ExecutionReport:
        """SUPERVISED mode: Automated execution, manual report review."""
        logger.info("🤖 SUPERVISED MODE: Automated execution with post-review")

        # Pre-flight safety checks (blocking for supervised mode)
        is_safe, errors = self._pre_flight_validation(staged)
        if not is_safe:
            logger.critical("🛑 PRE-FLIGHT VALIDATION FAILED - ABORTING EXECUTION")
            for error in errors:
                logger.critical(f"  ❌ {error}")

            # Return empty report with errors
            return ExecutionReport(
                execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                started_at=datetime.now(ZoneInfo("America/New_York")),
                completed_at=datetime.now(ZoneInfo("America/New_York")),
                dry_run=dry_run,
                staged_count=len(staged),
                warnings=[
                    "EXECUTION ABORTED - Pre-flight validation failed:",
                    *errors,
                ],
            )

        # Execute automatically
        report = await self._execute_tier1_and_tier2(staged, dry_run)

        # Send report for review (user reviews after completion)
        console.print("\n" + "=" * 70)
        console.print("SUPERVISED MODE: Execution Complete - Please Review Report")
        console.print("=" * 70)
        self._send_execution_summary(report)

        return report

    def _pre_flight_validation(
        self,
        staged: list[StagedOpportunity],
    ) -> tuple[bool, list[str]]:
        """Run pre-flight safety checks before execution.

        Validates:
        - Market data health (no TWS conflicts)
        - Total margin within limits
        - Position count within limits
        - All trades have valid parameters

        Args:
            staged: List of trades to validate

        Returns:
            Tuple of (is_safe, errors)
                is_safe: True if all checks pass
                errors: List of error messages (empty if safe)
        """
        errors = []

        # Check 1: Market data health (detects TWS conflicts)
        logger.info("Pre-flight check 1: Market data health...")
        is_healthy, health_error = self.client.check_market_data_health()
        if not is_healthy:
            errors.append(f"Market data unavailable: {health_error}")
            logger.error(f"❌ {health_error}")

        # Check 2: Total margin within MAX_TOTAL_MARGIN
        logger.info("Pre-flight check 2: Margin limits...")
        total_margin = sum(opp.staged_margin for opp in staged if opp.staged_margin)
        from src.config.base import get_config
        cfg = get_config()
        max_total_margin = cfg.max_total_margin

        if total_margin > max_total_margin:
            errors.append(
                f"Total margin ${total_margin:,.0f} exceeds MAX_TOTAL_MARGIN "
                f"${max_total_margin:,.0f}"
            )
            logger.error(
                f"❌ Total margin ${total_margin:,.0f} > "
                f"${max_total_margin:,.0f} limit"
            )
        else:
            logger.info(
                f"✓ Margin check passed: ${total_margin:,.0f} / "
                f"${max_total_margin:,.0f}"
            )

        # Check 3: Position count within MAX_POSITIONS
        logger.info("Pre-flight check 3: Position count...")
        max_positions = cfg.max_positions
        if len(staged) > max_positions:
            errors.append(
                f"Trade count {len(staged)} exceeds MAX_POSITIONS {max_positions}"
            )
            logger.error(f"❌ {len(staged)} trades > {max_positions} limit")
        else:
            logger.info(f"✓ Position count: {len(staged)} / {max_positions}")

        # Check 4: All trades have valid parameters
        logger.info("Pre-flight check 4: Trade parameters...")
        invalid_trades = []
        for opp in staged:
            if not opp.staged_limit_price or opp.staged_limit_price <= 0:
                invalid_trades.append(f"{opp.symbol}: invalid limit price")
            if not opp.staged_contracts or opp.staged_contracts <= 0:
                invalid_trades.append(f"{opp.symbol}: invalid contract count")

        if invalid_trades:
            errors.append(f"Invalid trade parameters: {', '.join(invalid_trades)}")
            logger.error(f"❌ {len(invalid_trades)} trades have invalid parameters")
        else:
            logger.info(f"✓ All {len(staged)} trades have valid parameters")

        # Final result
        is_safe = len(errors) == 0

        if is_safe:
            logger.info("✅ All pre-flight checks passed")
        else:
            logger.error(f"❌ Pre-flight validation FAILED ({len(errors)} errors)")

        return is_safe, errors

    async def _run_autonomous_mode(
        self,
        staged: list[StagedOpportunity],
        dry_run: bool
    ) -> ExecutionReport:
        """AUTONOMOUS mode: Fully automated execution."""
        logger.info("🚀 AUTONOMOUS MODE: Fully automated execution")

        # Pre-flight safety checks
        is_safe, errors = self._pre_flight_validation(staged)
        if not is_safe:
            logger.critical("🛑 PRE-FLIGHT VALIDATION FAILED - ABORTING EXECUTION")
            for error in errors:
                logger.critical(f"  ❌ {error}")

            # Return empty report with errors
            return ExecutionReport(
                execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                started_at=datetime.now(ZoneInfo("America/New_York")),
                completed_at=datetime.now(ZoneInfo("America/New_York")),
                dry_run=dry_run,
                staged_count=len(staged),
                warnings=[
                    "EXECUTION ABORTED - Pre-flight validation failed:",
                    *errors,
                ],
            )

        # Execute automatically
        report = await self._execute_tier1_and_tier2(staged, dry_run)

        # Send report (no review required)
        self._send_execution_summary(report)

        return report

    async def _execute_tier1_and_tier2(
        self,
        staged: list[StagedOpportunity],
        dry_run: bool
    ) -> ExecutionReport:
        """Execute Tier 1 at 9:30, then Tier 2 based on conditions.

        Args:
            staged: List of confirmed trades ready for execution
            dry_run: If True, simulate without real orders

        Returns:
            ExecutionReport with Tier 1 + Tier 2 results
        """
        if dry_run:
            logger.info(f"DRY RUN: Would execute {len(staged)} trades")
            return ExecutionReport(
                execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
                started_at=datetime.now(ZoneInfo("America/New_York")),
                completed_at=datetime.now(ZoneInfo("America/New_York")),
                dry_run=True,
                staged_count=len(staged),
            )

        # ── TIER 1: 9:30 AM execution ──
        await self._wait_until_time(self.tier1_time, "Tier 1 execution")
        logger.info(f"🚀 TIER 1: Submitting {len(staged)} orders at market open")

        # Use RapidFireExecutor for Tier 1
        tier1_report = await self.executor.execute_all(staged)

        logger.info(
            f"Tier 1 complete: {tier1_report.total_filled}/{tier1_report.total_submitted} filled "
            f"in {tier1_report.submission_time:.1f}s"
        )

        # Save PENDING records for ALL submitted orders (crash safety)
        await self._save_pending_trades_to_db(tier1_report, staged)

        # CRITICAL: Save filled trades to database (bug fix)
        await self._save_filled_trades_to_db(tier1_report.filled, staged)

        # Mark these as saved to prevent duplicates
        for trade in tier1_report.filled:
            if trade.order_id:
                self._saved_order_ids.add(trade.order_id)

        # ── FILL MONITORING (replaces legacy 5-minute sleep) ──
        monitoring_fills = 0
        monitoring_adjustments = 0
        if self.fill_manager and self.executor.pending_orders:
            logger.info("⏱️ Starting fill monitoring...")
            fill_report = await self.fill_manager.monitor_fills(
                self.executor.pending_orders
            )
            monitoring_fills = fill_report.fully_filled
            monitoring_adjustments = fill_report.total_adjustments
            logger.info(
                f"Fill monitoring complete: "
                f"{fill_report.fully_filled} filled, "
                f"{fill_report.left_working} working, "
                f"{fill_report.total_adjustments} adjustments"
            )

            # Save fills that happened during monitoring.
            # The fill_manager removes filled orders from pending_orders,
            # so _get_newly_filled_trades() can't find them. Instead, use
            # fill_report.filled_orders which captures fill data before removal.
            if fill_report.filled_orders:
                monitoring_summaries = self._pending_orders_to_summaries(
                    fill_report.filled_orders
                )
                logger.info(
                    f"💾 Saving {len(monitoring_summaries)} trades "
                    f"that filled during monitoring..."
                )
                await self._save_filled_trades_to_db(monitoring_summaries, staged)
        else:
            # Legacy behavior: sleep 5 minutes
            for _ in range(60):
                await asyncio.sleep(5)

        # Count working orders (fill_manager removes filled ones from pending_orders)
        working_count = len([
            o for o in self.executor.pending_orders.values()
            if o.last_status in ('Submitted', 'PreSubmitted')
        ])
        # Total fills = tier1 instant fills + fills during monitoring
        total_filled_so_far = tier1_report.total_filled + monitoring_fills

        logger.info(
            f"📊 Post-monitoring status: {total_filled_so_far} filled "
            f"({tier1_report.total_filled} tier1 + {monitoring_fills} during monitoring), "
            f"{working_count} working"
        )

        # ── TIER 2: Condition-based retry ──
        tier2_adjustments = 0
        if self.tier2_enabled and working_count > 0:
            tier2_adjustments = await self._execute_tier2_when_ready()
            logger.info(f"Tier 2 complete: {tier2_adjustments} orders adjusted")

            # Save any trades that filled during Tier 2
            newly_filled = self._get_newly_filled_trades()
            if newly_filled:
                logger.info(f"💾 Saving {len(newly_filled)} trades that filled during Tier 2...")
                await self._save_filled_trades_to_db(newly_filled, staged)

        # ── Final reconciliation at 10:30 ──
        await self._wait_until_time(self.reconciliation_time, "final reconciliation")

        if self.reconciler:
            reconciliation = await self.reconciler.sync_all_orders()
            logger.info(f"Reconciliation complete: {len(reconciliation)} orders synced")

        # Final database save for any remaining fills
        newly_filled = self._get_newly_filled_trades()
        if newly_filled:
            logger.info(f"💾 Final save: {len(newly_filled)} trades that filled before reconciliation...")
            await self._save_filled_trades_to_db(newly_filled, staged)

        # Release executor resources (unregister callback, clear pending)
        self.executor.cleanup()

        # Build final report (converting rapid-fire report to ExecutionReport)
        # total_filled_so_far already includes tier1 + monitoring fills;
        # add any Tier 2 fills that _get_newly_filled_trades found
        total_fills = total_filled_so_far
        # Staged count = total submitted + skipped + failed (everything that entered the pipeline)
        total_staged = (
            tier1_report.total_submitted
            + len(tier1_report.skipped)
            + len(tier1_report.failed)
        )

        final_report = ExecutionReport(
            execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
            started_at=tier1_report.started_at,
            completed_at=datetime.now(ZoneInfo("America/New_York")),
            dry_run=False,
            staged_count=total_staged,
            executed_count=tier1_report.total_submitted,
            filled_count=total_fills,
            working_count=working_count,
            failed_count=len(tier1_report.failed) + len(tier1_report.skipped),
            total_premium=tier1_report.total_premium,
            warnings=tier1_report.warnings,
        )

        return final_report

    async def _execute_tier2_when_ready(self) -> int:
        """Wait for favorable conditions, then retry unfilled orders.

        Monitors VIX and spreads every 5 minutes. Executes when:
        - VIX < high_threshold (< 25)
        - Spreads < max_spread (< $0.08)

        Returns:
            Number of orders adjusted
        """
        logger.info(
            f"⏳ TIER 2: Monitoring conditions from "
            f"{self.tier2_window_start} to {self.tier2_window_end}"
        )

        now = datetime.now(ZoneInfo("America/New_York"))
        window_end_dt = datetime.combine(
            now.date(),
            self.tier2_window_end,
            tzinfo=ZoneInfo("America/New_York")
        )

        # Get sample contracts for spread checking
        sample_contracts = [
            pending.contract
            for pending in list(self.executor.pending_orders.values())[:5]
            if pending.last_status in ('Submitted', 'PreSubmitted')
        ]

        while datetime.now(ZoneInfo("America/New_York")) < window_end_dt:
            # Check conditions
            conditions = await self.conditions.check_conditions(sample_contracts)

            current_time = conditions.timestamp.strftime('%H:%M')
            logger.info(
                f"Conditions at {current_time}: "
                f"VIX {conditions.vix:.1f}, Spread ${conditions.avg_spread:.3f} - "
                f"{conditions.reason}"
            )

            if conditions.conditions_favorable:
                logger.info(f"✓ Conditions favorable - executing Tier 2 adjustments")
                return await self._adjust_unfilled_orders(conditions)

            # Wait before next check
            logger.debug(
                f"Conditions not favorable, checking again in "
                f"{self.tier2_check_interval}s"
            )
            await asyncio.sleep(self.tier2_check_interval)

        logger.warning("Tier 2 window expired without favorable conditions")
        return 0

    async def _adjust_unfilled_orders(self, conditions) -> int:
        """Adjust unfilled orders with slightly more aggressive limits.

        Tier 2 uses more aggressive limits to increase fill probability
        when market conditions are favorable (low VIX, tight spreads).

        Args:
            conditions: Current market conditions

        Returns:
            Number of orders adjusted
        """
        adjusted_count = 0

        for order_id, pending in self.executor.pending_orders.items():
            if pending.last_status not in ('Submitted', 'PreSubmitted'):
                continue  # Already filled or cancelled

            # Get fresh quote
            quote = await self.client.get_quote(pending.contract)

            if not quote.is_valid:
                logger.warning(f"Invalid quote for {pending.staged.symbol}, skipping")
                continue

            # Calculate base limit
            base_limit = self.executor.adaptive_executor.limit_calc.calculate_sell_limit(
                bid=quote.bid,
                ask=quote.ask,
            )

            # Apply Tier 2 adjustment (more aggressive)
            # e.g., base_limit=$0.45, tier2_limit=$0.45*1.1=$0.495
            tier2_limit = base_limit * self.tier2_limit_adjustment

            # Ensure we don't exceed ask (sanity check)
            tier2_limit = min(tier2_limit, quote.ask - 0.01)

            # Only adjust if different enough
            if abs(tier2_limit - pending.current_limit) < 0.01:
                logger.debug(f"{pending.staged.symbol}: Limit unchanged (${tier2_limit:.2f})")
                continue

            # Modify order (cancel-and-replace via RapidFireExecutor)
            pending.last_bid = quote.bid
            pending.last_ask = quote.ask
            success = await self.executor._modify_order_price(
                pending,
                tier2_limit,
            )

            if success:
                adjusted_count += 1
                adjustment_pct = (tier2_limit / pending.current_limit - 1) * 100
                logger.info(
                    f"{pending.staged.symbol}: Tier 2 adjusted "
                    f"${pending.current_limit:.2f} → ${tier2_limit:.2f} "
                    f"(+{adjustment_pct:.1f}%)"
                )

        return adjusted_count

    async def _wait_until_time(self, target: time, reason: str):
        """Wait until a specific time (ET) with precise single sleep.

        Args:
            target: Target time (ET timezone)
            reason: Human-readable reason for waiting
        """
        now = datetime.now(ZoneInfo("America/New_York"))
        target_dt = datetime.combine(
            now.date(),
            target,
            tzinfo=ZoneInfo("America/New_York")
        )

        if now >= target_dt:
            return  # Already past target

        wait_seconds = (target_dt - now).total_seconds()
        logger.info(
            f"⏰ Waiting {wait_seconds:.0f}s until "
            f"{target.strftime('%H:%M')} ET ({reason})"
        )

        # Sleep in 5-second intervals so Ctrl+C can interrupt promptly
        while wait_seconds > 0:
            chunk = min(wait_seconds, 5.0)
            await asyncio.sleep(chunk)
            wait_seconds -= chunk

    async def _wait_for_user_input(self) -> str:
        """Wait for user to type 'execute', 'wait', or 'abort'.

        Returns:
            User command as string
        """
        while True:
            try:
                # Use asyncio-compatible input
                command = await asyncio.to_thread(input, "> ")
                command = command.strip().lower()

                if command in ('execute', 'wait', 'abort'):
                    return command

                console.print(
                    "[yellow]Please type 'execute', 'wait', or 'abort'[/yellow]"
                )

            except (EOFError, KeyboardInterrupt):
                return 'abort'

    def _get_newly_filled_trades(self) -> list:
        """Extract newly filled trades from pending orders that haven't been saved yet.

        Returns:
            List of ExecutionSummary objects for trades that filled but aren't in database yet
        """
        from src.services.rapid_fire_executor import ExecutionSummary, OrderStatus

        newly_filled = []

        if not hasattr(self.executor, 'pending_orders'):
            logger.debug("No pending orders to check")
            return newly_filled

        for order_id, pending in self.executor.pending_orders.items():
            # Skip if already saved
            if order_id in self._saved_order_ids:
                continue

            # Only process filled orders
            if pending.last_status != 'Filled':
                continue

            # Create ExecutionSummary for this newly filled trade
            summary = ExecutionSummary(
                symbol=pending.staged.symbol,
                strike=pending.staged.strike,
                order_id=order_id,
                status=OrderStatus.FILLED,
                order_type=pending.order_type,
                submitted_limit=pending.initial_limit,
                fill_price=pending.fill_price,
                fill_time=pending.last_update,
                submission_time=pending.submitted_at,
                adjustments_made=pending.adjustment_count,
            )

            newly_filled.append(summary)
            # Mark as saved to prevent duplicates
            self._saved_order_ids.add(order_id)

        return newly_filled

    def _pending_orders_to_summaries(
        self,
        pending_orders: list,
    ) -> list:
        """Convert PendingOrder objects to ExecutionSummary for database save.

        Used to bridge fill_manager's filled_orders (PendingOrder) to
        _save_filled_trades_to_db which expects ExecutionSummary objects.

        Args:
            pending_orders: List of PendingOrder objects from fill_manager

        Returns:
            List of ExecutionSummary objects ready for database save
        """
        from src.services.rapid_fire_executor import ExecutionSummary, OrderStatus

        summaries = []
        for pending in pending_orders:
            # Skip if already saved
            if pending.order_id in self._saved_order_ids:
                continue

            summary = ExecutionSummary(
                symbol=pending.staged.symbol,
                strike=pending.staged.strike,
                order_id=pending.order_id,
                status=OrderStatus.FILLED,
                order_type=pending.order_type,
                submitted_limit=pending.initial_limit,
                fill_price=pending.fill_price,
                fill_time=pending.last_update,
                submission_time=pending.submitted_at,
                adjustments_made=pending.adjustment_count,
            )
            summaries.append(summary)
            self._saved_order_ids.add(pending.order_id)

        return summaries

    async def _save_pending_trades_to_db(
        self,
        report,
        staged_opportunities: list,
    ) -> None:
        """Create PENDING Trade records for all submitted orders.

        Called immediately after order submission so that even if the
        system crashes, the database shows which orders were placed.
        Later, _save_filled_trades_to_db() updates these records with
        fill data instead of creating duplicates.

        Args:
            report: RapidFireExecutor's ExecutionReport with submitted/working orders
            staged_opportunities: List of StagedOpportunity objects that were staged
        """
        # Collect all submitted orders (filled + working + still-submitted)
        all_submitted = list(report.submitted)

        if not all_submitted:
            logger.debug("No submitted orders to save as PENDING")
            return

        logger.info(f"💾 Saving {len(all_submitted)} PENDING trade records...")

        # Create lookup map: (symbol, strike) -> staged opportunity
        staged_map = {}
        for opp in staged_opportunities:
            key = (opp.symbol, float(opp.strike))
            staged_map[key] = opp

        try:
            from src.data.database import get_db_session
            from src.data.models import Trade

            with get_db_session() as session:
                for summary in all_submitted:
                    try:
                        # Skip if already saved
                        if summary.order_id in self._saved_order_ids:
                            continue

                        # Find the corresponding staged opportunity
                        key = (summary.symbol, float(summary.strike))
                        staged_opp = staged_map.get(key)

                        if not staged_opp:
                            contracts = 5
                            otm_pct = 0.0
                            dte = 0
                            expiration = summary.submission_time.date()
                        else:
                            contracts = staged_opp.staged_contracts or 5
                            otm_pct = staged_opp.otm_pct or 0.0
                            dte = getattr(staged_opp, "dte", 0) or 0
                            expiration = staged_opp.expiration

                        # Ensure expiration is a date object (StagedOpportunity stores it as string)
                        if isinstance(expiration, str):
                            from datetime import date as date_type
                            expiration = date_type.fromisoformat(expiration)

                        exp_str = (
                            expiration.strftime("%Y%m%d")
                            if hasattr(expiration, "strftime")
                            else str(expiration).replace("-", "")
                        )
                        canonical_trade_id = f"{summary.symbol}_{summary.strike}_{exp_str}_P"

                        trade_record = Trade(
                            trade_id=canonical_trade_id,
                            order_id=summary.order_id,
                            symbol=summary.symbol,
                            strike=summary.strike,
                            expiration=expiration,
                            option_type="PUT",
                            entry_date=summary.submission_time,
                            entry_premium=summary.submitted_limit,
                            contracts=contracts,
                            otm_pct=otm_pct,
                            dte=dte,
                            ai_reasoning="PENDING - awaiting fill",
                            ai_confidence=0.0,
                        )

                        session.add(trade_record)
                        self._saved_order_ids.add(summary.order_id)

                        logger.debug(
                            f"  ✓ PENDING {summary.symbol} ${summary.strike}P: "
                            f"Order={summary.order_id}, {contracts} contracts"
                        )

                    except Exception as e:
                        logger.opt(exception=True).error(
                            "  ✗ Failed to save PENDING "
                            + summary.symbol + ": " + str(e)
                        )

                session.commit()
                logger.info(
                    f"✓ PENDING records committed: {len(all_submitted)} orders tracked"
                )

        except Exception as e:
            logger.opt(exception=True).error(
                "✗ Critical error saving PENDING trades: " + str(e)
            )

    async def _save_filled_trades_to_db(
        self, filled_trades: list, staged_opportunities: list
    ) -> None:
        """Save filled trades to database with entry snapshots.

        This is critical for:
        1. Position tracking (auto-monitor needs to see open positions)
        2. Entry snapshots for learning engine
        3. Opportunity state updates (STAGED → EXECUTED)
        4. Reconciliation accuracy

        Args:
            filled_trades: List of ExecutionSummary objects with filled trades
            staged_opportunities: List of ScanOpportunity objects that were staged
        """
        if not filled_trades:
            logger.debug("No filled trades to save")
            return

        logger.info(f"💾 Saving {len(filled_trades)} filled trades to database...")

        # Create lookup map: (symbol, strike) -> staged opportunity
        staged_map = {}
        for opp in staged_opportunities:
            key = (opp.symbol, float(opp.strike))
            staged_map[key] = opp

        try:
            # Import here to avoid circular dependencies
            from src.data.database import get_db_session
            from src.data.models import Trade
            from src.services.entry_snapshot import EntrySnapshotService
            from src.execution.opportunity_lifecycle import OpportunityLifecycleManager

            snapshot_service = EntrySnapshotService(ibkr_client=self.client)

            with get_db_session() as session:
                lifecycle_manager = OpportunityLifecycleManager(session)

                for summary in filled_trades:
                    try:
                        # Find the corresponding staged opportunity
                        key = (summary.symbol, float(summary.strike))
                        staged_opp = staged_map.get(key)

                        if not staged_opp:
                            logger.warning(
                                f"  ⚠ No staged opportunity found for {summary.symbol} "
                                f"${summary.strike} - using defaults"
                            )
                            # Use defaults if no match found
                            contracts = 5
                            otm_pct = 0.0
                            dte = 0
                            expiration = summary.submission_time.date()
                        else:
                            # Extract data from staged opportunity
                            contracts = staged_opp.staged_contracts or 5
                            otm_pct = staged_opp.otm_pct or 0.0
                            dte = getattr(staged_opp, "dte", 0) or 0
                            expiration = staged_opp.expiration

                        # Ensure expiration is a date object (StagedOpportunity stores it as string)
                        if isinstance(expiration, str):
                            from datetime import date as date_type
                            expiration = date_type.fromisoformat(expiration)

                        # Check if a PENDING record already exists for this order
                        existing = None
                        if summary.order_id:
                            existing = (
                                session.query(Trade)
                                .filter(Trade.order_id == summary.order_id)
                                .first()
                            )

                        if existing:
                            # Update PENDING → filled
                            existing.entry_premium = summary.fill_price or summary.submitted_limit
                            existing.entry_date = summary.fill_time or summary.submission_time
                            existing.ai_reasoning = "Executed via two-tier scheduler"
                            existing.ai_confidence = 0.8
                            trade_record = existing
                            session.flush()
                            logger.debug(
                                f"  ↑ Updated PENDING → filled for {summary.symbol} "
                                f"(order {summary.order_id})"
                            )
                        else:
                            # Create new Trade record
                            exp_str = expiration.strftime("%Y%m%d") if hasattr(expiration, "strftime") else str(expiration).replace("-", "")
                            canonical_trade_id = f"{summary.symbol}_{summary.strike}_{exp_str}_P"
                            trade_record = Trade(
                                trade_id=canonical_trade_id,
                                order_id=summary.order_id,
                                symbol=summary.symbol,
                                strike=summary.strike,
                                expiration=expiration,
                                option_type="PUT",
                                entry_date=summary.fill_time or summary.submission_time,
                                entry_premium=summary.fill_price or summary.submitted_limit,
                                contracts=contracts,
                                otm_pct=otm_pct,
                                dte=dte,
                                ai_reasoning="Executed via two-tier scheduler",
                                ai_confidence=0.8,
                            )
                            session.add(trade_record)
                            session.flush()  # Get ID

                        logger.info(
                            f"  ✓ Saved {summary.symbol} ${summary.strike}P: "
                            f"ID={trade_record.id}, Order={summary.order_id}, "
                            f"{contracts} contracts @ ${summary.fill_price or summary.submitted_limit}"
                        )

                        # Capture entry snapshot (98+ fields for learning)
                        try:
                            # Extract strike selection data from staged opportunity
                            sel_method = getattr(staged_opp, "strike_selection_method", None) if staged_opp else None
                            orig_strike = getattr(staged_opp, "strike", None) if staged_opp else None
                            live_delta = getattr(staged_opp, "live_delta", None) if staged_opp else None

                            snapshot = snapshot_service.capture_entry_snapshot(
                                trade_id=trade_record.id,
                                opportunity_id=getattr(staged_opp, "id", None),
                                symbol=summary.symbol,
                                strike=summary.strike,
                                expiration=trade_record.expiration,
                                option_type="PUT",
                                contracts=trade_record.contracts,
                                entry_premium=trade_record.entry_premium,
                                stock_price=getattr(staged_opp, "staged_stock_price", 0) if staged_opp else 0,
                                dte=dte,
                                source="two_tier_scheduler",
                                strike_selection_method=sel_method,
                                original_strike=orig_strike,
                                live_delta_at_selection=live_delta,
                            )
                            snapshot_service.save_snapshot(snapshot, session)
                            logger.debug(f"  ✓ Entry snapshot captured for {summary.symbol}")
                        except Exception as e:
                            logger.warning(
                                f"  ⚠ Entry snapshot failed for {summary.symbol}: {e}"
                            )
                            # Continue anyway - trade is saved

                        # Update opportunity state: STAGED → EXECUTED
                        if staged_opp:
                            try:
                                lifecycle_manager.transition(
                                    opportunity_id=staged_opp.id,
                                    new_state=OpportunityState.EXECUTED,
                                    reason=f"Filled at ${summary.fill_price or summary.submitted_limit}",
                                    metadata={"trade_id": trade_record.trade_id},
                                )
                                logger.debug(
                                    f"  ✓ Updated {summary.symbol} state: STAGED → EXECUTED"
                                )
                            except Exception as e:
                                logger.warning(
                                    f"  ⚠ State update failed for {summary.symbol}: {e}"
                                )
                                # Continue anyway - trade is saved

                    except Exception as e:
                        logger.opt(exception=True).error(
                            "  ✗ Failed to save " + summary.symbol + ": " + str(e)
                        )
                        # Continue with other trades

                session.commit()
                logger.info(f"✓ Database commit successful: {len(filled_trades)} trades saved")

        except Exception as e:
            logger.opt(exception=True).error(
                "✗ Critical error saving trades to database: " + str(e)
            )
            # Don't raise - execution already happened, just log the failure

    def _send_execution_summary(self, report: ExecutionReport):
        """Send execution summary to user (console for now).

        Args:
            report: Execution report to summarize
        """
        console.print("\n" + "=" * 70)
        console.print("EXECUTION SUMMARY")
        console.print("=" * 70)
        console.print(f"  Executed:  {report.executed_count}")
        console.print(f"  Filled:    {report.filled_count}")
        console.print(f"  Working:   {report.working_count}")
        console.print(f"  Failed:    {report.failed_count}")
        console.print(f"  Premium:   ${report.total_premium:,.2f}")
        console.print("=" * 70 + "\n")

    def _format_stage1_table(self, results):
        """Format Stage 1 results as a table."""
        # Placeholder - would use Rich table in production
        return f"Stage 1: {len([r for r in results if r.passed])} passed"

    def _format_stage2_table(self, results):
        """Format Stage 2 results as a table."""
        # Placeholder - would use Rich table in production
        confirmed = len([r for r in results if r.status in ('CONFIRMED', 'ADJUSTED')])
        return f"Stage 2: {confirmed} confirmed"

    def _parse_time(self, time_str: str) -> time:
        """Parse time string to time object.

        Args:
            time_str: Time in HH:MM format (e.g., "09:30")

        Returns:
            time object
        """
        hour, minute = time_str.split(":")
        return time(int(hour), int(minute))
