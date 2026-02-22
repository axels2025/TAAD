"""Learning Orchestrator coordinates all learning components.

Runs the weekly learning cycle: detect patterns, validate them,
evaluate experiments, propose changes, and auto-apply improvements.
"""

from datetime import datetime
from typing import Optional

import sqlalchemy as sa
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import LearningHistory, Pattern as PatternModel, Trade
from src.learning.experiment_engine import ExperimentEngine
from src.learning.models import LearningReport
from src.learning.parameter_optimizer import ParameterOptimizer
from src.learning.pattern_detector import PatternDetector
from src.learning.statistical_validator import StatisticalValidator


class LearningOrchestrator:
    """Orchestrates the learning cycle.

    Coordinates pattern detection, validation, experimentation,
    and parameter optimization in a cohesive learning loop.
    """

    def __init__(
        self,
        db_session: Session,
        baseline_config: Optional[dict] = None,
        auto_apply_threshold: float = 0.90,
    ):
        """Initialize learning orchestrator.

        Args:
            db_session: Database session
            baseline_config: Baseline strategy configuration
            auto_apply_threshold: Confidence threshold for auto-applying changes (default 0.90)
        """
        self.db = db_session
        self.auto_apply_threshold = auto_apply_threshold

        # Initialize components
        self.pattern_detector = PatternDetector(db_session)
        self.validator = StatisticalValidator(db_session)
        self.experiment_engine = ExperimentEngine(db_session)

        # Load baseline config
        if baseline_config is None:
            baseline_config = self._load_baseline_config()

        self.optimizer = ParameterOptimizer(db_session, baseline_config)

        logger.info("Learning orchestrator initialized")

    def run_weekly_analysis(self) -> LearningReport:
        """Run the weekly learning cycle.

        Executes the full learning workflow:
        1. Detect patterns from trade history
        2. Validate patterns statistically
        3. Evaluate active experiments
        4. Propose parameter optimizations
        5. Auto-apply high-confidence changes

        Returns:
            LearningReport summarizing the cycle
        """
        logger.info("=" * 60)
        logger.info("Starting weekly learning cycle")
        logger.info("=" * 60)

        report = LearningReport(timestamp=datetime.now())

        # Get baseline metrics (exclude stock_held trades with incomplete P&L)
        closed_trades = (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .filter(
                sa.or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held")
            )
            .all()
        )
        report.total_trades_analyzed = len(closed_trades)

        if closed_trades:
            wins = sum(1 for t in closed_trades if t.profit_loss and t.profit_loss > 0)
            report.baseline_win_rate = wins / len(closed_trades)

            rois = [t.roi for t in closed_trades if t.roi is not None]
            report.baseline_avg_roi = sum(rois) / len(rois) if rois else 0.0

        logger.info(
            f"Baseline: {report.total_trades_analyzed} trades, "
            f"{report.baseline_win_rate:.1%} win rate, "
            f"{report.baseline_avg_roi:.2%} avg ROI"
        )

        # Step 1: Detect patterns
        logger.info("\n[1/5] Detecting patterns...")
        patterns = self.pattern_detector.detect_patterns()
        report.patterns_detected = len(patterns)

        logger.info(f"  → Detected {len(patterns)} patterns")

        # Step 2: Validate patterns
        logger.info("\n[2/5] Validating patterns...")
        validated_patterns = []

        for pattern in patterns:
            result = self.validator.validate_pattern(pattern)

            if result.valid:
                validated_patterns.append(pattern)
                self._save_pattern(pattern)

                logger.info(
                    f"  ✓ {pattern.pattern_name}: "
                    f"{pattern.sample_size} trades, "
                    f"ROI={pattern.avg_roi:.2%}, "
                    f"confidence={pattern.confidence:.1%}"
                )
            else:
                logger.debug(f"  ✗ {pattern.pattern_name}: {result.reason}")

        report.patterns_validated = len(validated_patterns)

        logger.info(f"  → Validated {len(validated_patterns)} patterns")

        # Step 3: Evaluate active experiments
        logger.info("\n[3/5] Evaluating active experiments...")
        active_experiments = self.experiment_engine.get_active_experiments()

        for exp in active_experiments:
            result = self.experiment_engine.evaluate_experiment(exp)

            if result.decision == "ADOPT":
                report.experiments_adopted.append(exp)
                logger.info(
                    f"  ✓ ADOPT: {exp.name} "
                    f"(test ROI={result.test_roi:.2%} vs control {result.control_roi:.2%})"
                )

                # Apply the adopted experiment parameter
                from src.learning.models import DetectedPattern, ParameterProposal

                # Create a pseudo-pattern for the experiment result
                pseudo_pattern = DetectedPattern(
                    pattern_type="experiment",
                    pattern_name=exp.name,
                    pattern_value=exp.test_value,
                    sample_size=exp.test_trades,
                    win_rate=result.test_roi,  # Approximate
                    avg_roi=result.test_roi,
                    baseline_win_rate=result.control_roi,
                    baseline_roi=result.control_roi,
                    p_value=result.p_value or 0.0,
                    effect_size=result.effect_size or 0.0,
                    confidence=0.95,  # High confidence for adopted experiments
                    date_detected=datetime.now(),
                )

                proposal = ParameterProposal(
                    parameter=exp.parameter_name,
                    current_value=exp.control_value,
                    proposed_value=exp.test_value,
                    expected_improvement=result.effect_size or 0.0,
                    confidence=0.95,
                    source_pattern=pseudo_pattern,
                    reasoning=result.recommendation,
                )

                self.optimizer.apply_change(proposal, approval="experiment")

            elif result.decision == "REJECT":
                report.experiments_rejected.append(exp)
                logger.info(f"  ✗ REJECT: {exp.name} ({result.reason})")

            else:
                logger.debug(f"  ⋯ CONTINUE: {exp.name} ({result.decision})")

        logger.info(
            f"  → Adopted {len(report.experiments_adopted)}, "
            f"Rejected {len(report.experiments_rejected)}"
        )

        # Step 4: Propose new optimizations
        logger.info("\n[4/5] Proposing parameter optimizations...")
        proposals = self.optimizer.propose_changes(validated_patterns)
        report.proposals = proposals

        logger.info(f"  → Generated {len(proposals)} proposals")

        for i, proposal in enumerate(proposals[:5], 1):  # Show top 5
            logger.info(
                f"  {i}. {proposal.parameter}: "
                f"{proposal.current_value} -> {proposal.proposed_value} "
                f"(confidence={proposal.confidence:.1%}, "
                f"improvement={proposal.expected_improvement:.2%})"
            )

        # Step 5: Auto-apply high-confidence changes
        logger.info("\n[5/5] Auto-applying high-confidence changes...")

        for proposal in proposals:
            if proposal.confidence >= self.auto_apply_threshold:
                change = self.optimizer.apply_change(proposal, approval="auto")
                report.changes_applied.append(proposal)

                logger.info(
                    f"  ✓ Applied: {proposal.parameter} = {proposal.proposed_value} "
                    f"(confidence={proposal.confidence:.1%})"
                )

        logger.info(f"  → Applied {len(report.changes_applied)} changes automatically")

        # Save learning report
        self._save_report(report)

        logger.info("=" * 60)
        logger.info("Weekly learning cycle complete")
        logger.info("=" * 60)

        return report

    def on_trade_closed(self, trade_id: str) -> None:
        """Called when a trade closes - update experiment tracking.

        Args:
            trade_id: ID of closed trade
        """
        trade = self.db.query(Trade).filter_by(trade_id=trade_id).first()

        if not trade:
            logger.warning(f"Trade {trade_id} not found")
            return

        if trade.is_experiment and trade.experiment_id:
            # Experiment trade
            self.experiment_engine.record_outcome(
                trade_id=trade_id,
                experiment_id=trade.experiment_id,
                group="test",
                outcome=trade.profit_loss or 0.0,
            )

            logger.debug(
                f"Recorded experiment outcome: {trade_id} "
                f"(experiment={trade.experiment_id}, P/L={trade.profit_loss})"
            )
        else:
            # Control group trade
            self.experiment_engine.record_outcome(
                trade_id=trade_id,
                experiment_id=None,
                group="control",
                outcome=trade.profit_loss or 0.0,
            )

            logger.debug(f"Recorded control outcome: {trade_id} (P/L={trade.profit_loss})")

    def _save_pattern(self, pattern) -> None:
        """Save pattern to database.

        Args:
            pattern: DetectedPattern to save
        """
        # Check if pattern already exists
        existing = (
            self.db.query(PatternModel)
            .filter(PatternModel.pattern_name == pattern.pattern_name)
            .filter(PatternModel.pattern_type == pattern.pattern_type)
            .first()
        )

        if existing:
            # Update existing pattern
            existing.sample_size = pattern.sample_size
            existing.win_rate = pattern.win_rate
            existing.avg_roi = pattern.avg_roi
            existing.confidence = pattern.confidence
            existing.p_value = pattern.p_value
            existing.date_last_validated = datetime.now()
        else:
            # Create new pattern
            pattern_model = PatternModel(
                pattern_type=pattern.pattern_type,
                pattern_name=pattern.pattern_name,
                pattern_value=pattern.pattern_value,
                sample_size=pattern.sample_size,
                win_rate=pattern.win_rate,
                avg_roi=pattern.avg_roi,
                confidence=pattern.confidence,
                p_value=pattern.p_value,
                market_regime=pattern.market_regime,
                date_detected=pattern.date_detected,
                date_last_validated=datetime.now(),
                status="active",
            )

            self.db.add(pattern_model)

        # Log learning event
        learning_event = LearningHistory(
            event_type="pattern_detected",
            event_date=datetime.now(),
            pattern_name=pattern.pattern_name,
            confidence=pattern.confidence,
            sample_size=pattern.sample_size,
            reasoning=f"{pattern.pattern_type}: {pattern.pattern_value}",
        )

        self.db.add(learning_event)
        self.db.commit()

    def _save_report(self, report: LearningReport) -> None:
        """Save learning report summary to database.

        Args:
            report: LearningReport to save
        """
        # Log report as learning event
        summary = (
            f"Weekly analysis: {report.patterns_detected} patterns detected, "
            f"{report.patterns_validated} validated, "
            f"{len(report.experiments_adopted)} experiments adopted, "
            f"{len(report.changes_applied)} changes applied"
        )

        learning_event = LearningHistory(
            event_type="weekly_analysis",
            event_date=report.timestamp,
            reasoning=summary,
            sample_size=report.total_trades_analyzed,
        )

        self.db.add(learning_event)
        self.db.commit()

        logger.info(f"Saved learning report: {summary}")

    def _load_baseline_config(self) -> dict:
        """Load baseline strategy configuration.

        Returns:
            Baseline config dictionary
        """
        # Load from baseline strategy config
        try:
            from src.config.baseline_strategy import BaselineStrategy

            strategy = BaselineStrategy.from_env()

            return {
                "delta_range": (0.10, 0.25),
                "dte_range": strategy.dte_range,
                "otm_range": strategy.otm_range,
                "premium_min": strategy.premium_range[0],
                "required_trend": "any",
                "preferred_iv_regime": "any",
                "preferred_vix_regime": "any",
                "preferred_entry_days": [
                    "Monday",
                    "Tuesday",
                    "Wednesday",
                    "Thursday",
                    "Friday",
                ],
            }
        except Exception as e:
            logger.warning(f"Could not load baseline config: {e}")
            return {}

    def get_learning_summary(self, days: int = 30) -> dict:
        """Get summary of learning activity over past N days.

        Args:
            days: Number of days to look back

        Returns:
            Summary dictionary
        """
        from datetime import timedelta

        cutoff = datetime.now() - timedelta(days=days)

        # Query learning history
        events = (
            self.db.query(LearningHistory)
            .filter(LearningHistory.event_date >= cutoff)
            .all()
        )

        # Query patterns
        patterns = (
            self.db.query(PatternModel)
            .filter(PatternModel.date_detected >= cutoff)
            .all()
        )

        summary = {
            "period_days": days,
            "total_events": len(events),
            "patterns_detected": len(patterns),
            "parameter_changes": len(
                [e for e in events if e.event_type == "parameter_adjusted"]
            ),
            "weekly_analyses": len(
                [e for e in events if e.event_type == "weekly_analysis"]
            ),
            "active_patterns": len(
                [p for p in patterns if p.status == "active"]
            ),
        }

        return summary
