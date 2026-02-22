"""Autonomy governor for graduated autonomous trading.

4 levels of autonomy (L1 Recommend -> L4 Autonomous) with 9 mandatory
human review triggers that ALWAYS escalate regardless of level.

Promotion requires consecutive clean days + performance thresholds.
Demotion is immediate on override, loss streak, or anomaly.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.agentic.config import AutonomyConfig
from src.data.models import DecisionAudit, Trade


class AutonomyLevel(IntEnum):
    """Trading autonomy levels."""

    L1_RECOMMEND = 1    # Recommend only, human must approve all
    L2_NOTIFY = 2       # Execute routine trades, notify human
    L3_SUPERVISED = 3   # Execute most trades, escalate edge cases
    L4_AUTONOMOUS = 4   # Full autonomy, human reviews daily summary


@dataclass
class AutonomyDecision:
    """Result of an autonomy gate check."""

    approved: bool
    level: int
    reason: str
    escalation_required: bool = False
    escalation_trigger: Optional[str] = None


# 9 mandatory human review triggers that ALWAYS escalate
MANDATORY_ESCALATION_TRIGGERS = {
    "first_trade_of_day": "First trade of the trading day",
    "new_symbol": "Trading a symbol for the first time",
    "loss_exceeds_threshold": "Single-trade loss exceeds 2x premium",
    "margin_utilization_high": "Margin utilization above 60%",
    "vix_spike": "VIX above 30 or 20%+ daily increase",
    "consecutive_losses": "3+ consecutive losing trades",
    "parameter_change": "AI wants to change strategy parameters",
    "stale_data": "Market data is stale (>5 minutes old)",
    "low_confidence": "AI confidence below 0.6",
}


class AutonomyGovernor:
    """Enforces graduated autonomy with mandatory escalation triggers.

    Checks whether the daemon can execute a given action at the current
    autonomy level, and enforces mandatory review triggers.
    """

    def __init__(self, db_session: Session, config: Optional[AutonomyConfig] = None):
        """Initialize autonomy governor.

        Args:
            db_session: SQLAlchemy session
            config: Autonomy configuration (uses defaults if None)
        """
        self.db = db_session
        self.config = config or AutonomyConfig()
        self._level = self.config.initial_level
        self._consecutive_clean_days = 0
        self._trades_at_current_level = 0
        self._last_override_time: Optional[datetime] = None

    @property
    def level(self) -> int:
        """Current autonomy level."""
        return self._level

    @level.setter
    def level(self, value: int) -> None:
        """Set autonomy level with bounds checking."""
        old = self._level
        self._level = max(1, min(self.config.max_level, value))
        if old != self._level:
            logger.info(f"Autonomy level changed: L{old} -> L{self._level}")

    def can_execute(
        self,
        action: str,
        confidence: float,
        context: Optional[dict] = None,
    ) -> AutonomyDecision:
        """Check if the daemon can execute an action at current autonomy level.

        Args:
            action: The proposed action (e.g., "EXECUTE_TRADES")
            confidence: Claude's confidence score (0.0-1.0)
            context: Optional context for trigger evaluation

        Returns:
            AutonomyDecision with approval/escalation info
        """
        context = context or {}

        # Check mandatory escalation triggers first (always apply)
        trigger = self._check_mandatory_triggers(action, confidence, context)
        if trigger:
            return AutonomyDecision(
                approved=False,
                level=self._level,
                reason=f"Mandatory escalation: {MANDATORY_ESCALATION_TRIGGERS.get(trigger, trigger)}",
                escalation_required=True,
                escalation_trigger=trigger,
            )

        # Check minimal footprint conditions
        footprint_issue = self._check_minimal_footprint(context)
        if footprint_issue:
            return AutonomyDecision(
                approved=False,
                level=self._level,
                reason=f"Minimal footprint: {footprint_issue}",
                escalation_required=True,
                escalation_trigger="minimal_footprint",
            )

        # Level-based gating
        if action in ("MONITOR_ONLY", "REQUEST_HUMAN_REVIEW"):
            # These always pass
            return AutonomyDecision(
                approved=True,
                level=self._level,
                reason="No-op or escalation action always allowed",
            )

        if self._level == AutonomyLevel.L1_RECOMMEND:
            # L1: Nothing executes without human approval
            return AutonomyDecision(
                approved=False,
                level=self._level,
                reason="L1: All actions require human approval",
                escalation_required=True,
                escalation_trigger="l1_approval_required",
            )

        if self._level == AutonomyLevel.L2_NOTIFY:
            # L2: Execute routine trades, escalate non-routine
            if action in ("STAGE_CANDIDATES", "EXECUTE_TRADES", "CLOSE_POSITION"):
                if confidence >= 0.7:
                    return AutonomyDecision(
                        approved=True,
                        level=self._level,
                        reason=f"L2: Routine action with confidence {confidence:.2f}",
                    )
                else:
                    return AutonomyDecision(
                        approved=False,
                        level=self._level,
                        reason=f"L2: Confidence {confidence:.2f} below 0.7 threshold",
                        escalation_required=True,
                        escalation_trigger="low_confidence",
                    )
            # Non-routine actions need approval at L2
            return AutonomyDecision(
                approved=False,
                level=self._level,
                reason=f"L2: Action {action} requires human approval",
                escalation_required=True,
            )

        if self._level == AutonomyLevel.L3_SUPERVISED:
            # L3: Execute most trades, escalate edge cases
            if confidence >= 0.5:
                return AutonomyDecision(
                    approved=True,
                    level=self._level,
                    reason=f"L3: Action approved with confidence {confidence:.2f}",
                )
            return AutonomyDecision(
                approved=False,
                level=self._level,
                reason=f"L3: Confidence {confidence:.2f} below 0.5 threshold",
                escalation_required=True,
                escalation_trigger="low_confidence",
            )

        if self._level == AutonomyLevel.L4_AUTONOMOUS:
            # L4: Full autonomy
            return AutonomyDecision(
                approved=True,
                level=self._level,
                reason=f"L4: Autonomous execution (confidence={confidence:.2f})",
            )

        return AutonomyDecision(
            approved=False,
            level=self._level,
            reason=f"Unknown level {self._level}",
            escalation_required=True,
        )

    def _check_mandatory_triggers(
        self, action: str, confidence: float, context: dict
    ) -> Optional[str]:
        """Check all 9 mandatory escalation triggers.

        Returns trigger name if any trigger fires, None otherwise.
        """
        # 1. Low confidence
        if confidence < 0.6:
            return "low_confidence"

        # 2. First trade of day
        if action in ("EXECUTE_TRADES", "STAGE_CANDIDATES"):
            if context.get("is_first_trade_of_day", False):
                return "first_trade_of_day"

        # 3. New symbol
        if context.get("is_new_symbol", False):
            return "new_symbol"

        # 4. Loss exceeds threshold
        if context.get("loss_exceeds_threshold", False):
            return "loss_exceeds_threshold"

        # 5. Margin utilization high
        margin_util = context.get("margin_utilization", 0.0)
        if margin_util > 0.60:
            return "margin_utilization_high"

        # 6. VIX spike
        vix = context.get("vix", 0.0)
        vix_change = context.get("vix_change_pct", 0.0)
        if vix > 30 or vix_change > 0.20:
            return "vix_spike"

        # 7. Consecutive losses
        consecutive_losses = context.get("consecutive_losses", 0)
        if consecutive_losses >= self.config.demotion_loss_streak:
            return "consecutive_losses"

        # 8. Parameter change
        if action == "ADJUST_PARAMETERS":
            return "parameter_change"

        # 9. Stale data
        if context.get("data_stale", False):
            return "stale_data"

        return None

    def _check_minimal_footprint(self, context: dict) -> Optional[str]:
        """Check minimal footprint conditions.

        Returns issue description if action should be deferred, None otherwise.
        """
        if context.get("data_stale", False):
            return "Market data is stale, deferring to MONITOR_ONLY"

        if context.get("margin_utilization", 0.0) > 0.80:
            return "Margin utilization above 80%, deferring new trades"

        return None

    def record_trade_outcome(self, win: bool) -> None:
        """Record a trade outcome for promotion/demotion tracking.

        Args:
            win: Whether the trade was profitable
        """
        self._trades_at_current_level += 1

        if not win:
            # Check for demotion on loss streak
            recent_losses = self._count_recent_consecutive_losses()
            if recent_losses >= self.config.demotion_loss_streak:
                self._demote("Loss streak of {recent_losses}")

    def check_promotion(self) -> bool:
        """Check if promotion criteria are met.

        Returns:
            True if promoted, False otherwise
        """
        if self._level >= self.config.max_level:
            return False

        if self._trades_at_current_level < self.config.promotion_min_trades:
            return False

        if self._consecutive_clean_days < self.config.promotion_clean_days:
            return False

        # Check win rate
        win_rate = self._calculate_recent_win_rate()
        if win_rate < self.config.promotion_min_win_rate:
            return False

        self._promote()
        return True

    def record_clean_day(self) -> None:
        """Record a day with no errors or overrides."""
        self._consecutive_clean_days += 1
        logger.debug(f"Clean day #{self._consecutive_clean_days}")

    def record_override(self) -> None:
        """Record a human override (immediate demotion)."""
        self._last_override_time = datetime.utcnow()
        self._demote("Human override")

    def _promote(self) -> None:
        """Promote to next autonomy level."""
        old = self._level
        self.level = self._level + 1
        self._trades_at_current_level = 0
        self._consecutive_clean_days = 0
        logger.info(f"Autonomy PROMOTED: L{old} -> L{self._level}")

    def _demote(self, reason: str) -> None:
        """Demote to previous autonomy level.

        Args:
            reason: Reason for demotion
        """
        old = self._level
        self.level = max(1, self._level - 1)
        self._trades_at_current_level = 0
        self._consecutive_clean_days = 0
        logger.warning(f"Autonomy DEMOTED: L{old} -> L{self._level} ({reason})")

    def _count_recent_consecutive_losses(self) -> int:
        """Count consecutive recent losing trades."""
        try:
            recent = (
                self.db.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .order_by(Trade.exit_date.desc())
                .limit(10)
                .all()
            )
            count = 0
            for trade in recent:
                if trade.profit_loss is not None and trade.profit_loss < 0:
                    count += 1
                else:
                    break
            return count
        except Exception:
            return 0

    def _calculate_recent_win_rate(self, days: int = 30) -> float:
        """Calculate win rate over recent trades."""
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)
            trades = (
                self.db.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .filter(Trade.exit_date >= cutoff)
                .all()
            )
            if not trades:
                return 0.0
            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            return wins / len(trades)
        except Exception:
            return 0.0
