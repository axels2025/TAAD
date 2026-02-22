"""Parameter Optimizer for proposing and tracking parameter changes.

Converts detected patterns into actionable parameter change proposals
and tracks configuration evolution over time.
"""

from datetime import datetime
from typing import Any, Optional

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import LearningHistory
from src.learning.models import ConfigChange, DetectedPattern, ParameterProposal


class ParameterOptimizer:
    """Optimizes strategy parameters based on learning.

    Analyzes detected patterns and proposes parameter changes to
    improve strategy performance. Tracks all changes for audit trail.
    """

    def __init__(self, db_session: Session, baseline_config: dict):
        """Initialize parameter optimizer.

        Args:
            db_session: Database session for logging changes
            baseline_config: Current strategy configuration
        """
        self.db = db_session
        self.current_config = baseline_config.copy()
        self.config_history: list[ConfigChange] = []

    def propose_changes(
        self, patterns: list[DetectedPattern]
    ) -> list[ParameterProposal]:
        """Based on detected patterns, propose parameter changes.

        Args:
            patterns: List of validated patterns

        Returns:
            List of parameter change proposals, sorted by expected improvement
        """
        logger.info(f"Analyzing {len(patterns)} patterns for optimization opportunities")

        proposals = []

        for pattern in patterns:
            if not pattern.is_significant():
                continue

            proposal = self.pattern_to_proposal(pattern)
            if proposal:
                proposals.append(proposal)

        # Sort by expected improvement (descending)
        proposals.sort(key=lambda p: p.expected_improvement, reverse=True)

        logger.info(f"Generated {len(proposals)} parameter change proposals")

        return proposals

    def pattern_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert a pattern into a parameter change proposal.

        Args:
            pattern: Detected pattern to convert

        Returns:
            ParameterProposal or None if no applicable parameter change
        """
        # Delta bucket patterns -> adjust delta range
        if pattern.pattern_type == "delta_bucket":
            return self._delta_bucket_to_proposal(pattern)

        # IV rank patterns -> adjust IV rank thresholds
        elif pattern.pattern_type == "iv_rank_bucket":
            return self._iv_rank_to_proposal(pattern)

        # DTE patterns -> adjust preferred DTE range
        elif pattern.pattern_type == "dte_bucket":
            return self._dte_bucket_to_proposal(pattern)

        # VIX regime patterns -> adjust VIX-based filtering
        elif pattern.pattern_type == "vix_regime":
            return self._vix_regime_to_proposal(pattern)

        # Trend patterns -> adjust trend requirements
        elif pattern.pattern_type == "trend_direction":
            return self._trend_to_proposal(pattern)

        # Entry day patterns -> adjust trading day preferences
        elif pattern.pattern_type == "entry_day":
            return self._entry_day_to_proposal(pattern)

        return None

    def _delta_bucket_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert delta bucket pattern to parameter proposal.

        Args:
            pattern: Delta bucket pattern

        Returns:
            Proposal to adjust delta range
        """
        # Parse delta range from pattern_value
        # E.g., "15-20%" -> (0.15, 0.20)
        if "%" in pattern.pattern_value:
            range_str = pattern.pattern_value.replace("%", "").replace("pct", "")

            if "plus" in range_str or "+" in range_str:
                # "25+" -> min=0.25, no max
                min_delta = 0.25
                max_delta = 1.0
            elif "-" in range_str:
                # "15-20" -> (0.15, 0.20)
                parts = range_str.replace("_", "-").split("-")
                min_delta = float(parts[0]) / 100.0
                max_delta = float(parts[1]) / 100.0
            else:
                return None

            # Only propose if this range significantly outperforms baseline
            if pattern.avg_roi > pattern.baseline_roi * 1.2:  # 20% better
                current_range = self.current_config.get("delta_range", (0.10, 0.25))

                return ParameterProposal(
                    parameter="delta_range",
                    current_value=current_range,
                    proposed_value=(min_delta, max_delta),
                    expected_improvement=pattern.effect_size,
                    confidence=pattern.confidence,
                    source_pattern=pattern,
                    reasoning=(
                        f"Trades with delta {pattern.pattern_value} show "
                        f"{pattern.avg_roi:.2%} ROI vs {pattern.baseline_roi:.2%} baseline "
                        f"({pattern.sample_size} trades, p={pattern.p_value:.4f})"
                    ),
                )

        return None

    def _iv_rank_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert IV rank pattern to parameter proposal.

        Args:
            pattern: IV rank bucket pattern

        Returns:
            Proposal to adjust IV rank preference
        """
        # Parse IV range
        # E.g., "50-75%" -> prefer high IV trades
        if pattern.avg_roi > pattern.baseline_roi * 1.15:  # 15% better
            return ParameterProposal(
                parameter="preferred_iv_regime",
                current_value=self.current_config.get("preferred_iv_regime", "any"),
                proposed_value=pattern.pattern_name,  # e.g., "high_iv"
                expected_improvement=pattern.effect_size,
                confidence=pattern.confidence,
                source_pattern=pattern,
                reasoning=(
                    f"{pattern.pattern_value} IV rank trades show "
                    f"{pattern.avg_roi:.2%} ROI vs {pattern.baseline_roi:.2%} baseline"
                ),
            )

        return None

    def _dte_bucket_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert DTE bucket pattern to parameter proposal.

        Args:
            pattern: DTE bucket pattern

        Returns:
            Proposal to adjust DTE range
        """
        # Parse DTE range from pattern_value
        # E.g., "14-21 days" -> (14, 21)
        if "days" in pattern.pattern_value:
            range_str = pattern.pattern_value.replace(" days", "").replace("plus", "+")

            if "+" in range_str:
                # "30+" -> min=30, no strict max
                min_dte = 30
                max_dte = 60
            elif "-" in range_str:
                parts = range_str.split("-")
                min_dte = int(parts[0])
                max_dte = int(parts[1])
            else:
                return None

            # Only propose if significantly better
            if pattern.avg_roi > pattern.baseline_roi * 1.15:  # 15% better
                current_range = self.current_config.get("dte_range", (21, 45))

                return ParameterProposal(
                    parameter="dte_range",
                    current_value=current_range,
                    proposed_value=(min_dte, max_dte),
                    expected_improvement=pattern.effect_size,
                    confidence=pattern.confidence,
                    source_pattern=pattern,
                    reasoning=(
                        f"Trades with {pattern.pattern_value} DTE show "
                        f"{pattern.avg_roi:.2%} ROI vs {pattern.baseline_roi:.2%} baseline"
                    ),
                )

        return None

    def _vix_regime_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert VIX regime pattern to parameter proposal.

        Args:
            pattern: VIX regime pattern

        Returns:
            Proposal to adjust VIX-based filtering
        """
        # Only propose changes for strong patterns
        if pattern.avg_roi > pattern.baseline_roi * 1.2:  # 20% better
            return ParameterProposal(
                parameter="preferred_vix_regime",
                current_value=self.current_config.get("preferred_vix_regime", "any"),
                proposed_value=pattern.pattern_name,  # e.g., "elevated_vix"
                expected_improvement=pattern.effect_size,
                confidence=pattern.confidence,
                source_pattern=pattern,
                reasoning=(
                    f"{pattern.pattern_value} regime shows "
                    f"{pattern.avg_roi:.2%} ROI vs {pattern.baseline_roi:.2%} baseline"
                ),
            )

        return None

    def _trend_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert trend pattern to parameter proposal.

        Args:
            pattern: Trend direction pattern

        Returns:
            Proposal to adjust trend filtering
        """
        # If one trend significantly outperforms, propose to filter for it
        if pattern.avg_roi > pattern.baseline_roi * 1.25:  # 25% better
            return ParameterProposal(
                parameter="required_trend",
                current_value=self.current_config.get("required_trend", "any"),
                proposed_value=pattern.pattern_value,  # e.g., "uptrend"
                expected_improvement=pattern.effect_size,
                confidence=pattern.confidence,
                source_pattern=pattern,
                reasoning=(
                    f"Trades in {pattern.pattern_value} show "
                    f"{pattern.avg_roi:.2%} ROI vs {pattern.baseline_roi:.2%} baseline"
                ),
            )

        return None

    def _entry_day_to_proposal(
        self, pattern: DetectedPattern
    ) -> Optional[ParameterProposal]:
        """Convert entry day pattern to parameter proposal.

        Args:
            pattern: Entry day pattern

        Returns:
            Proposal to adjust trading day preferences
        """
        # Only propose if one day significantly better
        if pattern.avg_roi > pattern.baseline_roi * 1.3:  # 30% better
            return ParameterProposal(
                parameter="preferred_entry_days",
                current_value=self.current_config.get(
                    "preferred_entry_days", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                ),
                proposed_value=[pattern.pattern_value],
                expected_improvement=pattern.effect_size,
                confidence=pattern.confidence,
                source_pattern=pattern,
                reasoning=(
                    f"Trades entered on {pattern.pattern_value} show "
                    f"{pattern.avg_roi:.2%} ROI vs {pattern.baseline_roi:.2%} baseline"
                ),
            )

        return None

    def apply_change(
        self, proposal: ParameterProposal, approval: str = "auto"
    ) -> ConfigChange:
        """Apply an approved parameter change.

        Args:
            proposal: Approved parameter proposal
            approval: Approval type ('auto', 'manual', 'experiment')

        Returns:
            ConfigChange record
        """
        # Record the change
        change = ConfigChange(
            timestamp=datetime.now(),
            parameter=proposal.parameter,
            old_value=proposal.current_value,
            new_value=proposal.proposed_value,
            reason=proposal.reasoning,
            approval_type=approval,
        )

        self.config_history.append(change)

        # Update current config
        self.current_config[proposal.parameter] = proposal.proposed_value

        # Log to database
        self._log_change_to_db(change, proposal)

        logger.info(
            f"Applied parameter change: {proposal.parameter} "
            f"{proposal.current_value} -> {proposal.proposed_value} "
            f"(approval={approval})"
        )

        return change

    def _log_change_to_db(
        self, change: ConfigChange, proposal: ParameterProposal
    ) -> None:
        """Log configuration change to database.

        Args:
            change: ConfigChange record
            proposal: Associated proposal
        """
        learning_event = LearningHistory(
            event_type="parameter_adjusted",
            event_date=change.timestamp,
            pattern_name=proposal.source_pattern.pattern_name,
            confidence=proposal.confidence,
            sample_size=proposal.source_pattern.sample_size,
            parameter_changed=change.parameter,
            old_value=str(change.old_value),
            new_value=str(change.new_value),
            reasoning=change.reason,
            expected_improvement=proposal.expected_improvement,
        )

        self.db.add(learning_event)
        self.db.commit()

    def get_current_config(self) -> dict:
        """Get current configuration.

        Returns:
            Current config dictionary
        """
        return self.current_config.copy()

    def get_config_history(self) -> list[ConfigChange]:
        """Get history of all configuration changes.

        Returns:
            List of ConfigChange records
        """
        return self.config_history.copy()

    def rollback_change(self, parameter: str) -> bool:
        """Rollback the most recent change to a parameter.

        Args:
            parameter: Parameter to rollback

        Returns:
            True if rollback successful, False otherwise
        """
        # Find most recent change to this parameter
        for change in reversed(self.config_history):
            if change.parameter == parameter:
                # Revert to old value
                self.current_config[parameter] = change.old_value

                # Log rollback
                rollback_change = ConfigChange(
                    timestamp=datetime.now(),
                    parameter=parameter,
                    old_value=change.new_value,
                    new_value=change.old_value,
                    reason=f"Rollback of change from {change.timestamp}",
                    approval_type="rollback",
                )

                self.config_history.append(rollback_change)

                logger.warning(
                    f"Rolled back parameter {parameter}: "
                    f"{change.new_value} -> {change.old_value}"
                )

                return True

        logger.error(f"No change history found for parameter {parameter}")
        return False
