"""Routes daemon decisions to existing CLI commands as library calls.

Each action type maps to an existing function. All calls go through
asyncio.to_thread() for sync functions. AutonomyGovernor.can_execute()
is checked before every action.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.agentic.autonomy_governor import AutonomyGovernor, AutonomyDecision
from src.agentic.reasoning_engine import DecisionOutput
from src.utils.timezone import utc_now


@dataclass
class ExecutionResult:
    """Result of executing a daemon action."""

    success: bool
    action: str
    message: str
    data: Optional[dict] = None
    error: Optional[str] = None


class ActionExecutor:
    """Routes DecisionOutput actions to existing trading functions.

    Uses library calls (not subprocess) via asyncio.to_thread() for sync
    functions. Checks AutonomyGovernor.can_execute() before every action.
    """

    def __init__(
        self,
        db_session: Session,
        governor: AutonomyGovernor,
        ibkr_client: Optional[Any] = None,
        exit_manager: Optional[Any] = None,
    ):
        """Initialize action executor.

        Args:
            db_session: SQLAlchemy session
            governor: Autonomy governor for gate checks
            ibkr_client: Optional IBKR client for trade execution
            exit_manager: Optional ExitManager for closing positions
        """
        self.db = db_session
        self.governor = governor
        self.ibkr_client = ibkr_client
        self.exit_manager = exit_manager
        self._pending_approvals: list[dict] = []

    async def execute(
        self,
        decision: DecisionOutput,
        context: Optional[dict] = None,
    ) -> ExecutionResult:
        """Execute a decision after autonomy gate check.

        Args:
            decision: The decision to execute
            context: Optional context for autonomy evaluation

        Returns:
            ExecutionResult with success/failure info
        """
        context = context or {}

        # Check autonomy gate
        gate = self.governor.can_execute(
            action=decision.action,
            confidence=decision.confidence,
            context=context,
        )

        if not gate.approved:
            if gate.escalation_required:
                return await self._escalate(decision, gate)
            return ExecutionResult(
                success=False,
                action=decision.action,
                message=f"Autonomy gate denied: {gate.reason}",
            )

        # Route to action handler
        try:
            handler = self._get_handler(decision.action)
            result = await handler(decision)
            return result
        except Exception as e:
            logger.error(f"Action execution failed: {e}", exc_info=True)
            return ExecutionResult(
                success=False,
                action=decision.action,
                message=f"Execution error: {e}",
                error=str(e),
            )

    def _get_handler(self, action: str):
        """Get the handler function for an action.

        Args:
            action: Action name

        Returns:
            Async handler function
        """
        handlers = {
            "MONITOR_ONLY": self._handle_monitor,
            "STAGE_CANDIDATES": self._handle_stage,
            "EXECUTE_TRADES": self._handle_execute,
            "CLOSE_POSITION": self._handle_close,
            "CLOSE_ALL_POSITIONS": self._handle_close_all,
            "ADJUST_PARAMETERS": self._handle_adjust,
            "RUN_EXPERIMENT": self._handle_experiment,
            "REQUEST_HUMAN_REVIEW": self._handle_human_review,
        }
        return handlers.get(action, self._handle_monitor)

    async def _handle_monitor(self, decision: DecisionOutput) -> ExecutionResult:
        """MONITOR_ONLY: No-op, just log."""
        logger.info(f"MONITOR_ONLY: {decision.reasoning}")
        return ExecutionResult(
            success=True,
            action="MONITOR_ONLY",
            message=decision.reasoning,
        )

    async def _handle_stage(self, decision: DecisionOutput) -> ExecutionResult:
        """STAGE_CANDIDATES: Run auto-scan pipeline to find and stage trades.

        Uses the same pipeline as the daemon's MARKET_OPEN hook and the
        dashboard's /api/auto-scan/trigger endpoint:
          scan (IBKR) → chains → scores → AI → portfolio → stage
        """
        try:
            from src.agentic.config import load_phase5_config
            from src.services.auto_select_pipeline import (
                run_auto_select_pipeline,
                run_scan_and_persist,
                stage_selected_candidates,
            )

            config = load_phase5_config()
            preset = config.auto_scan.scanner_preset

            # Step 1: Run IBKR scanner
            scan_id, opportunities = run_scan_and_persist(
                preset=preset, db=self.db
            )

            if not opportunities:
                return ExecutionResult(
                    success=True,
                    action="STAGE_CANDIDATES",
                    message="Scanner returned 0 symbols, nothing to stage",
                    data={"scan_id": scan_id, "symbols_found": 0},
                )

            # Step 2: Run auto-select pipeline (chains → scores → AI → portfolio)
            result = run_auto_select_pipeline(
                scan_id=scan_id,
                db=self.db,
                override_market_hours=False,
            )

            if not result.success:
                return ExecutionResult(
                    success=False,
                    action="STAGE_CANDIDATES",
                    message=f"Auto-select pipeline failed: {result.error}",
                    data={"scan_id": scan_id},
                )

            # Step 3: Stage selected trades
            staged_count = 0
            if result.selected:
                staged_count = stage_selected_candidates(
                    selected=result.selected,
                    opp_id_map=result.opp_id_map,
                    config_snapshot=result.config_snapshot,
                    db=self.db,
                    earnings_map=result.earnings_map,
                )

            selected_symbols = [s.symbol for s in result.selected]
            return ExecutionResult(
                success=True,
                action="STAGE_CANDIDATES",
                message=(
                    f"Auto-scan complete: {result.symbols_scanned} scanned, "
                    f"{result.best_strikes_found} strikes, "
                    f"{len(result.selected)} selected, {staged_count} staged"
                ),
                data={
                    "scan_id": scan_id,
                    "symbols_scanned": result.symbols_scanned,
                    "selected": len(result.selected),
                    "staged": staged_count,
                    "symbols": selected_symbols[:10],
                    "budget": result.available_budget,
                    "used_margin": result.used_margin,
                },
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="STAGE_CANDIDATES",
                message=f"Auto-scan failed: {e}",
                error=str(e),
            )

    async def _handle_execute(self, decision: DecisionOutput) -> ExecutionResult:
        """EXECUTE_TRADES: Execute staged/confirmed trades.

        Loads STAGED opportunities from the DB, transitions them to EXECUTING
        (preventing double-submission from concurrent events), converts them to
        StagedOpportunity format, and runs the TwoTierExecutionScheduler.
        Entry snapshots (66+ fields) are captured by the scheduler internally.
        """
        try:
            from src.data.repositories import ScanRepository
            from src.data.opportunity_state import OpportunityState
            from src.execution.opportunity_lifecycle import (
                OpportunityLifecycleManager,
            )
            from src.services.premarket_validator import StagedOpportunity
            from src.services.two_tier_execution_scheduler import (
                TwoTierExecutionScheduler,
                AutomationMode,
            )

            # Load staged opportunities from DB
            scan_repo = ScanRepository(self.db)
            opportunities = scan_repo.get_opportunities_by_state(
                OpportunityState.STAGED
            )

            if not opportunities:
                return ExecutionResult(
                    success=True,
                    action="EXECUTE_TRADES",
                    message="No staged trades to execute",
                    data={"executed_count": 0},
                )

            # Transition STAGED → EXECUTING to prevent double-submission.
            # If another event already claimed an opportunity (transition
            # fails), we skip it — dedup without query changes.
            lifecycle = OpportunityLifecycleManager(self.db)
            claimed: list = []
            for opp in opportunities:
                ok = lifecycle.transition(
                    opportunity_id=opp.id,
                    new_state=OpportunityState.EXECUTING,
                    reason="Autonomous daemon claiming for execution",
                    actor="daemon",
                )
                if ok:
                    claimed.append(opp)
                else:
                    logger.warning(
                        f"Opportunity {opp.id} ({opp.symbol}) skipped — "
                        f"already claimed or invalid state"
                    )

            if not claimed:
                return ExecutionResult(
                    success=True,
                    action="EXECUTE_TRADES",
                    message="All staged trades already claimed by another event",
                    data={"executed_count": 0},
                )

            # Convert DB models to StagedOpportunity format
            staged = [
                StagedOpportunity(
                    id=opp.id,
                    symbol=opp.symbol,
                    strike=opp.strike,
                    expiration=(
                        opp.expiration.isoformat() if opp.expiration else ""
                    ),
                    staged_stock_price=opp.stock_price or 0.0,
                    staged_limit_price=opp.staged_limit_price or 0.0,
                    staged_contracts=opp.staged_contracts or 0,
                    staged_margin=opp.staged_margin or 0.0,
                    otm_pct=opp.otm_pct or 0.0,
                    state="EXECUTING",
                )
                for opp in claimed
            ]

            scheduler = TwoTierExecutionScheduler(
                ibkr_client=self.ibkr_client,
                automation_mode=AutomationMode.AUTONOMOUS,
            )

            try:
                # run_monday_morning is async — await it directly
                report = await scheduler.run_monday_morning(staged, dry_run=False)
            except Exception as sched_err:
                # Scheduler failed — mark claimed opportunities as FAILED
                for opp in claimed:
                    lifecycle.transition(
                        opportunity_id=opp.id,
                        new_state=OpportunityState.FAILED,
                        reason=f"Scheduler error: {sched_err}",
                        actor="daemon",
                    )
                raise

            return ExecutionResult(
                success=True,
                action="EXECUTE_TRADES",
                message=(
                    f"Execution complete: {len(staged)} staged, "
                    f"{getattr(report, 'executed_count', '?')} executed"
                ),
                data={
                    "staged_count": len(staged),
                    "executed_count": getattr(report, "executed_count", 0),
                    "filled_count": getattr(report, "filled_count", 0),
                    "failed_count": getattr(report, "failed_count", 0),
                    "report": str(report),
                },
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="EXECUTE_TRADES",
                message=f"Trade execution failed: {e}",
                error=str(e),
            )

    async def _handle_close(self, decision: DecisionOutput) -> ExecutionResult:
        """CLOSE_POSITION: Close a specific open position via ExitManager."""
        position_id = (
            decision.metadata.get("position_id")
            or decision.metadata.get("trade_id")
        )

        # Fallback: resolve trade_id from reasoning text + open DB positions
        if not position_id:
            position_id = self._resolve_trade_id_from_reasoning(decision)

        if not position_id:
            return ExecutionResult(
                success=False,
                action="CLOSE_POSITION",
                message="No position_id or trade_id in metadata and could not resolve from reasoning",
            )

        # Convert DB trade_id (e.g. ALAB_150.0_20260313_C_1936967951) to
        # canonical position key (ALAB_150.0_20260313_C) that matches IBKR.
        # The DB trade_id may have a hash suffix that the IBKR-derived key
        # never includes, causing update_position() to fail with "not found".
        from src.utils.position_key import position_key_from_trade
        from src.data.models import Trade

        try:
            trade = self.db.query(Trade).filter(Trade.trade_id == position_id).first()
            if trade:
                # Already closed? Return early instead of failing at IBKR level.
                if trade.exit_date is not None:
                    logger.info(
                        f"CLOSE_POSITION skipped: {trade.symbol} ({position_id}) "
                        f"already closed at {trade.exit_date}"
                    )
                    return ExecutionResult(
                        success=True,
                        action="CLOSE_POSITION",
                        message=f"Position {trade.symbol} already closed at {trade.exit_date} — no action needed",
                        data={"position_id": position_id, "already_closed": True},
                    )

                canonical_id = position_key_from_trade(trade)
                if canonical_id != position_id:
                    logger.info(
                        f"Converted trade_id to position key: {position_id} -> {canonical_id}"
                    )
                    position_id = canonical_id
        except Exception as e:
            logger.debug(f"Could not convert trade_id to position key: {e}")

        if not self.exit_manager:
            return ExecutionResult(
                success=False,
                action="CLOSE_POSITION",
                message="No exit_manager available",
            )

        try:
            from src.execution.exit_manager import ExitDecision

            exit_decision = ExitDecision(
                should_exit=True,
                reason=decision.metadata.get("reason", "claude_decision"),
                exit_type=decision.metadata.get("exit_type", "limit"),
                limit_price=decision.metadata.get("limit_price"),
                urgency="high",
            )

            # Call execute_exit directly on the main thread.
            # It MUST run on the same thread as the ib_insync event loop
            # to avoid deadlocks with asyncio.run() in a separate thread.
            result = self.exit_manager.execute_exit(position_id, exit_decision)

            if result.success:
                return ExecutionResult(
                    success=True,
                    action="CLOSE_POSITION",
                    message=f"Closed {position_id} @ ${result.exit_price:.2f}" if result.exit_price else f"Exit order placed for {position_id}",
                    data={"position_id": position_id, "exit_price": result.exit_price},
                )
            return ExecutionResult(
                success=False,
                action="CLOSE_POSITION",
                message=f"Exit failed: {result.error_message}",
                error=result.error_message,
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="CLOSE_POSITION",
                message=f"Position close failed: {e}",
                error=str(e),
            )

    async def _handle_close_all(self, decision: DecisionOutput) -> ExecutionResult:
        """CLOSE_ALL_POSITIONS: Emergency close all open positions via ExitManager."""
        from src.data.models import Trade
        from src.utils.position_key import position_key_from_trade

        try:
            open_trades = (
                self.db.query(Trade)
                .filter(Trade.exit_date.is_(None))
                .all()
            )

            if not open_trades:
                return ExecutionResult(
                    success=True,
                    action="CLOSE_ALL_POSITIONS",
                    message="No open positions to close",
                    data={"closed_count": 0, "failed_count": 0},
                )

            if not self.exit_manager:
                return ExecutionResult(
                    success=False,
                    action="CLOSE_ALL_POSITIONS",
                    message="No exit_manager available",
                    error="exit_manager is None",
                )

            from src.execution.exit_manager import ExitDecision

            closed = 0
            failed = 0
            details: list[dict] = []

            for trade in open_trades:
                try:
                    position_key = position_key_from_trade(trade)
                    exit_decision = ExitDecision(
                        should_exit=True,
                        reason=decision.metadata.get("reason", "emergency_close_all"),
                        exit_type="market",
                        urgency="critical",
                    )
                    result = self.exit_manager.execute_exit(position_key, exit_decision)
                    if result.success:
                        closed += 1
                        details.append({
                            "trade_id": trade.trade_id,
                            "symbol": trade.symbol,
                            "status": "closed",
                            "exit_price": result.exit_price,
                        })
                    else:
                        failed += 1
                        details.append({
                            "trade_id": trade.trade_id,
                            "symbol": trade.symbol,
                            "status": "failed",
                            "error": result.error_message,
                        })
                except Exception as e:
                    failed += 1
                    details.append({
                        "trade_id": trade.trade_id,
                        "symbol": trade.symbol,
                        "status": "error",
                        "error": str(e),
                    })

            return ExecutionResult(
                success=failed == 0,
                action="CLOSE_ALL_POSITIONS",
                message=(
                    f"Emergency close: {closed} closed, {failed} failed "
                    f"(out of {len(open_trades)} positions)"
                ),
                data={
                    "closed_count": closed,
                    "failed_count": failed,
                    "total": len(open_trades),
                    "details": details,
                },
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="CLOSE_ALL_POSITIONS",
                message=f"Emergency close failed: {e}",
                error=str(e),
            )

    async def _handle_adjust(self, decision: DecisionOutput) -> ExecutionResult:
        """ADJUST_PARAMETERS: Propose strategy parameter change."""
        # Parameter changes always require human review
        return await self._escalate(
            decision,
            AutonomyDecision(
                approved=False,
                level=self.governor.level,
                reason="Parameter changes require human approval",
                escalation_required=True,
                escalation_trigger="parameter_change",
            ),
        )

    async def _handle_experiment(self, decision: DecisionOutput) -> ExecutionResult:
        """RUN_EXPERIMENT: Start a new A/B experiment."""
        try:
            from src.learning.experiment_engine import ExperimentEngine

            engine = ExperimentEngine(self.db)
            experiment_config = decision.metadata.get("experiment", {})

            result = await asyncio.to_thread(
                engine.create_experiment,
                name=experiment_config.get("name", "daemon_experiment"),
                parameter_name=experiment_config.get("parameter", "unknown"),
                control_value=experiment_config.get("control_value", ""),
                test_value=experiment_config.get("test_value", ""),
                description=decision.reasoning,
            )

            return ExecutionResult(
                success=True,
                action="RUN_EXPERIMENT",
                message=f"Experiment created: {experiment_config.get('name', '?')}",
                data={"experiment": str(result)},
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="RUN_EXPERIMENT",
                message=f"Experiment creation failed: {e}",
                error=str(e),
            )

    async def _handle_human_review(self, decision: DecisionOutput) -> ExecutionResult:
        """REQUEST_HUMAN_REVIEW: Queue for human approval."""
        return await self._escalate(
            decision,
            AutonomyDecision(
                approved=False,
                level=self.governor.level,
                reason=decision.reasoning,
                escalation_required=True,
                escalation_trigger="ai_requested",
            ),
        )

    async def _handle_emergency_stop(self, decision: DecisionOutput) -> ExecutionResult:
        """EMERGENCY_STOP: Halt all trading immediately."""
        try:
            from src.services.kill_switch import KillSwitch

            ks = KillSwitch(register_signals=False)
            ks.halt(f"Daemon emergency stop: {decision.reasoning}")

            return ExecutionResult(
                success=True,
                action="EMERGENCY_STOP",
                message=f"Trading HALTED: {decision.reasoning}",
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="EMERGENCY_STOP",
                message=f"Emergency stop failed: {e}",
                error=str(e),
            )

    async def _escalate(
        self, decision: DecisionOutput, gate: AutonomyDecision
    ) -> ExecutionResult:
        """Queue a decision for human review.

        Args:
            decision: The decision requiring review
            gate: The autonomy gate result

        Returns:
            ExecutionResult indicating escalation
        """
        approval = {
            "decision": {
                "action": decision.action,
                "confidence": decision.confidence,
                "reasoning": decision.reasoning,
                "key_factors": decision.key_factors,
                "risks_considered": decision.risks_considered,
                "metadata": decision.metadata,
            },
            "escalation_reason": gate.reason,
            "escalation_trigger": gate.escalation_trigger,
            "queued_at": utc_now().isoformat(),
            "status": "pending",
        }
        self._pending_approvals.append(approval)

        logger.info(
            f"Escalated to human: {decision.action} "
            f"(trigger={gate.escalation_trigger})"
        )

        return ExecutionResult(
            success=True,  # Escalation itself is a success
            action="REQUEST_HUMAN_REVIEW",
            message=f"Queued for human review: {gate.reason}",
            data=approval,
        )

    def _resolve_trade_id_from_reasoning(self, decision: DecisionOutput) -> Optional[str]:
        """Fallback: extract symbol from reasoning and look up trade_id from DB.

        When Claude omits trade_id from CLOSE_POSITION metadata, this method
        queries open positions and matches symbols mentioned in reasoning.

        Args:
            decision: CLOSE_POSITION decision with missing trade_id

        Returns:
            trade_id string if resolved, None otherwise
        """
        import re

        reasoning = decision.reasoning or ""
        if not reasoning:
            return None

        try:
            from src.data.models import Trade

            open_trades = (
                self.db.query(Trade)
                .filter(Trade.exit_date.is_(None))
                .all()
            )
            if not open_trades:
                return None

            for t in open_trades:
                if t.symbol and re.search(
                    r"\b" + re.escape(t.symbol.upper()) + r"\b", reasoning
                ):
                    logger.info(
                        f"Auto-resolved CLOSE_POSITION trade_id from reasoning: "
                        f"{t.symbol} -> {t.trade_id}"
                    )
                    return str(t.trade_id)
        except Exception as e:
            logger.warning(f"Could not resolve trade_id from reasoning: {e}")

        return None

    def get_pending_approvals(self) -> list[dict]:
        """Get all pending human approvals.

        Returns:
            List of pending approval dicts
        """
        return [a for a in self._pending_approvals if a["status"] == "pending"]

    def approve(self, index: int) -> Optional[dict]:
        """Approve a pending decision.

        Args:
            index: Index into pending approvals list

        Returns:
            The approved decision dict, or None
        """
        pending = self.get_pending_approvals()
        if 0 <= index < len(pending):
            pending[index]["status"] = "approved"
            pending[index]["decided_at"] = utc_now().isoformat()
            return pending[index]
        return None

    def reject(self, index: int, reason: str = "") -> Optional[dict]:
        """Reject a pending decision.

        Args:
            index: Index into pending approvals list
            reason: Rejection reason

        Returns:
            The rejected decision dict, or None
        """
        pending = self.get_pending_approvals()
        if 0 <= index < len(pending):
            pending[index]["status"] = "rejected"
            pending[index]["rejection_reason"] = reason
            pending[index]["decided_at"] = utc_now().isoformat()
            self.governor.record_override()
            return pending[index]
        return None
