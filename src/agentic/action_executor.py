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
    ):
        """Initialize action executor.

        Args:
            db_session: SQLAlchemy session
            governor: Autonomy governor for gate checks
            ibkr_client: Optional IBKR client for trade execution
        """
        self.db = db_session
        self.governor = governor
        self.ibkr_client = ibkr_client
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
            "ADJUST_PARAMETERS": self._handle_adjust,
            "RUN_EXPERIMENT": self._handle_experiment,
            "REQUEST_HUMAN_REVIEW": self._handle_human_review,
            "EMERGENCY_STOP": self._handle_emergency_stop,
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
        """STAGE_CANDIDATES: Run Sunday session to find trade candidates."""
        try:
            from src.cli.commands.sunday_session import run_sunday_session, SundaySessionConfig

            session_config = SundaySessionConfig()
            result = await asyncio.to_thread(run_sunday_session, session_config)

            return ExecutionResult(
                success=True,
                action="STAGE_CANDIDATES",
                message=f"Sunday session completed: staged {result.get('staged_count', 0)} candidates",
                data=result if isinstance(result, dict) else {"result": str(result)},
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="STAGE_CANDIDATES",
                message=f"Sunday session failed: {e}",
                error=str(e),
            )

    async def _handle_execute(self, decision: DecisionOutput) -> ExecutionResult:
        """EXECUTE_TRADES: Execute staged/confirmed trades."""
        try:
            from src.services.execution_scheduler import TwoTierExecutionScheduler

            scheduler = TwoTierExecutionScheduler(
                ibkr_client=self.ibkr_client,
                db_session=self.db,
            )
            result = await asyncio.to_thread(scheduler.run_monday_morning)

            return ExecutionResult(
                success=True,
                action="EXECUTE_TRADES",
                message="Trade execution completed",
                data=result if isinstance(result, dict) else {"result": str(result)},
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="EXECUTE_TRADES",
                message=f"Trade execution failed: {e}",
                error=str(e),
            )

    async def _handle_close(self, decision: DecisionOutput) -> ExecutionResult:
        """CLOSE_POSITION: Close a specific open position."""
        try:
            from src.execution.exit_manager import ExitManager

            position_id = decision.metadata.get("position_id")
            if not position_id:
                return ExecutionResult(
                    success=False,
                    action="CLOSE_POSITION",
                    message="No position_id in metadata",
                )

            exit_manager = ExitManager(
                ibkr_client=self.ibkr_client,
                db_session=self.db,
            )
            result = await asyncio.to_thread(
                exit_manager.close_position, position_id, reason="daemon_decision"
            )

            return ExecutionResult(
                success=True,
                action="CLOSE_POSITION",
                message=f"Position {position_id} closed",
                data={"position_id": position_id},
            )
        except Exception as e:
            return ExecutionResult(
                success=False,
                action="CLOSE_POSITION",
                message=f"Position close failed: {e}",
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
            "queued_at": datetime.utcnow().isoformat(),
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
            pending[index]["decided_at"] = datetime.utcnow().isoformat()
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
            pending[index]["decided_at"] = datetime.utcnow().isoformat()
            self.governor.record_override()
            return pending[index]
        return None
