"""End-of-day learning loop for the agentic daemon.

Extends existing LearningOrchestrator with:
1. EOD reflection at 4:30 PM ET via Claude Sonnet
2. Claude-designed experiments when patterns are found
3. Outcome feedback: links closed positions to originating decisions
"""

from datetime import datetime, date, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from src.agentic.reasoning_engine import ClaudeReasoningEngine
from src.agentic.working_memory import WorkingMemory
from src.data.models import DecisionAudit, Trade
from src.learning.learning_orchestrator import LearningOrchestrator


class LearningLoop:
    """Extended learning loop with Claude-powered reflection.

    Coordinates EOD reflection, experiment design, and outcome feedback.
    Reuses existing LearningOrchestrator for pattern detection and
    experiment management.
    """

    def __init__(
        self,
        db_session: Session,
        reasoning_engine: ClaudeReasoningEngine,
        working_memory: WorkingMemory,
    ):
        """Initialize learning loop.

        Args:
            db_session: SQLAlchemy session
            reasoning_engine: Claude reasoning engine for reflection
            working_memory: Working memory for storing reflections
        """
        self.db = db_session
        self.engine = reasoning_engine
        self.memory = working_memory

        # Reuse existing learning orchestrator
        self.orchestrator = LearningOrchestrator(db_session)

    async def run_eod_reflection(self) -> dict:
        """Run end-of-day reflection via Claude Sonnet.

        Reviews today's decisions, categorizes them, and identifies
        patterns to investigate.

        Returns:
            Reflection report dict
        """
        logger.info("Running EOD reflection...")

        # Get today's decisions
        today = date.today()
        decisions = (
            self.db.query(DecisionAudit)
            .filter(sa_func.date(DecisionAudit.timestamp) == today)
            .order_by(DecisionAudit.timestamp)
            .all()
        )

        decisions_data = [
            {
                "timestamp": str(d.timestamp),
                "action": d.action,
                "confidence": d.confidence,
                "reasoning": d.reasoning,
                "executed": d.executed,
                "autonomy_approved": d.autonomy_approved,
            }
            for d in decisions
        ]

        # Get today's trades
        trades = (
            self.db.query(Trade)
            .filter(
                sa_func.date(Trade.entry_date) == today
            )
            .all()
        )

        trades_data = [
            {
                "symbol": t.symbol,
                "strike": t.strike,
                "entry_premium": t.entry_premium,
                "contracts": t.contracts,
                "profit_loss": t.profit_loss,
                "exit_reason": t.exit_reason,
            }
            for t in trades
        ]

        if not decisions_data and not trades_data:
            report = {"summary": "No decisions or trades today", "date": str(today)}
            logger.info("EOD reflection: no activity today")
            return report

        # Include guardrail summary in reflection context
        guardrail_summary = self._build_guardrail_summary(decisions)

        # Run Claude reflection
        if guardrail_summary:
            trades_data.append({"guardrail_summary": guardrail_summary})
        report = self.engine.reflect(decisions_data, trades_data)
        report["date"] = str(today)
        report["decisions_count"] = len(decisions_data)
        report["trades_count"] = len(trades_data)

        # Store in working memory
        self.memory.add_reflection(report)

        logger.info(
            f"EOD reflection complete: {len(decisions_data)} decisions, "
            f"{len(trades_data)} trades reviewed"
        )

        return report

    def run_weekly_learning(self) -> dict:
        """Run the weekly learning cycle via existing LearningOrchestrator.

        Returns:
            Learning report summary dict
        """
        logger.info("Running weekly learning cycle...")

        try:
            report = self.orchestrator.run_weekly_analysis()

            summary = {
                "timestamp": str(report.timestamp),
                "total_trades": report.total_trades_analyzed,
                "patterns_detected": report.patterns_detected,
                "patterns_validated": report.patterns_validated,
                "experiments_adopted": len(report.experiments_adopted),
                "experiments_rejected": len(report.experiments_rejected),
                "changes_applied": len(report.changes_applied),
                "baseline_win_rate": report.baseline_win_rate,
                "baseline_avg_roi": report.baseline_avg_roi,
            }

            logger.info(f"Weekly learning complete: {summary}")
            return summary

        except Exception as e:
            logger.error(f"Weekly learning failed: {e}", exc_info=True)
            return {"error": str(e)}

    def _build_guardrail_summary(self, decisions) -> dict:
        """Build a summary of guardrail activity for today's decisions.

        Args:
            decisions: List of DecisionAudit records

        Returns:
            Summary dict with block/warning counts and flagged items
        """
        blocks = 0
        warnings = 0
        flagged_guards = []

        for d in decisions:
            flags = d.guardrail_flags or []
            for flag in flags:
                if not flag.get("passed", True):
                    if flag.get("severity") == "block":
                        blocks += 1
                    elif flag.get("severity") == "warning":
                        warnings += 1
                    guard_name = flag.get("guard_name", "unknown")
                    if guard_name not in flagged_guards:
                        flagged_guards.append(guard_name)

        if blocks == 0 and warnings == 0:
            return {}

        return {
            "guardrail_blocks": blocks,
            "guardrail_warnings": warnings,
            "flagged_guards": flagged_guards,
        }

    def record_trade_outcome(self, trade_id: str) -> None:
        """Link a closed trade's outcome back to the originating decision.

        Finds the decision that led to this trade and updates working
        memory with outcome feedback.

        Args:
            trade_id: The closed trade's trade_id
        """
        try:
            trade = self.db.query(Trade).filter_by(trade_id=trade_id).first()
            if not trade or not trade.exit_date:
                return

            # Find the originating decision (if any)
            decision = (
                self.db.query(DecisionAudit)
                .filter(
                    DecisionAudit.action.in_(["EXECUTE_TRADES", "CLOSE_POSITION"]),
                    DecisionAudit.executed == True,  # noqa: E712
                    DecisionAudit.timestamp <= trade.entry_date,
                )
                .order_by(DecisionAudit.timestamp.desc())
                .first()
            )

            outcome = {
                "trade_id": trade_id,
                "symbol": trade.symbol,
                "profit_loss": trade.profit_loss,
                "roi": trade.roi,
                "exit_reason": trade.exit_reason,
                "days_held": trade.days_held,
                "linked_decision_id": decision.id if decision else None,
                "recorded_at": datetime.utcnow().isoformat(),
            }

            # Update working memory with outcome
            self.memory.add_decision(
                {
                    "type": "outcome_feedback",
                    **outcome,
                }
            )

            # Update existing orchestrator
            self.orchestrator.on_trade_closed(trade_id)

            logger.info(
                f"Outcome recorded: {trade_id} P/L=${trade.profit_loss:.2f} "
                f"(linked to decision={decision.id if decision else 'none'})"
            )

        except Exception as e:
            logger.error(f"Failed to record trade outcome: {e}")
