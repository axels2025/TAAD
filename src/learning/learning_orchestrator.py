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
from src.utils.timezone import utc_now
from src.learning.alpha_decay_monitor import AlphaDecayMonitor
from src.learning.experiment_engine import ExperimentEngine
from src.learning.models import LearningReport
from src.learning.parameter_optimizer import ParameterOptimizer
from src.learning.pattern_detector import PatternDetector
from src.learning.regime_adapter import RegimeAdapter
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
        self.alpha_decay_monitor = AlphaDecayMonitor(db_session)

        # Load baseline config
        if baseline_config is None:
            baseline_config = self._load_baseline_config()

        self.optimizer = ParameterOptimizer(db_session, baseline_config)

        # D: Regime-aware adaptation
        regime_overrides = self._load_regime_overrides()
        self.regime_adapter = RegimeAdapter(
            db_session, self.experiment_engine, config_overrides=regime_overrides,
        )

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

        # Get baseline metrics (exclude stock_held and paper trades)
        closed_trades = (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .filter(
                sa.or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held")
            )
            .filter(sa.or_(Trade.trade_source.is_(None), Trade.trade_source != "paper"))
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
        logger.info("\n[1/7] Detecting patterns...")
        patterns = self.pattern_detector.detect_patterns()
        report.patterns_detected = len(patterns)

        logger.info(f"  → Detected {len(patterns)} patterns")

        # Step 2: Validate patterns and persist ALL findings
        logger.info("\n[2/7] Validating patterns...")

        # C1: Apply FDR correction across all pattern p-values before validation
        if patterns:
            self.validator.apply_fdr_correction(patterns)

        validated_patterns = []
        preliminary_patterns = []

        for pattern in patterns:
            result = self.validator.validate_pattern(pattern)

            if result.valid:
                validated_patterns.append(pattern)
                self._save_pattern(pattern)
                self._save_pattern_candidate(pattern, result)

                logger.info(
                    f"  ✓ {pattern.pattern_name}: "
                    f"{pattern.sample_size} trades, "
                    f"ROI={pattern.avg_roi:.2%}, "
                    f"confidence={pattern.confidence:.1%}"
                )
            elif result.status == "PRELIMINARY":
                preliminary_patterns.append(pattern)
                self._save_pattern_candidate(pattern, result)

                logger.info(
                    f"  ~ {pattern.pattern_name} [PRELIMINARY]: "
                    f"{pattern.sample_size} trades, "
                    f"ROI={pattern.avg_roi:.2%}, "
                    f"p={pattern.p_value:.4f} — {result.reason}"
                )
            else:
                # A1: Persist even rejected patterns as candidates for audit trail
                self._save_pattern_candidate(pattern, result)
                logger.debug(f"  ✗ {pattern.pattern_name}: {result.reason}")

        report.patterns_validated = len(validated_patterns)
        report.patterns_preliminary = len(preliminary_patterns)

        logger.info(
            f"  → Validated {len(validated_patterns)}, "
            f"Preliminary {len(preliminary_patterns)}, "
            f"Rejected {len(patterns) - len(validated_patterns) - len(preliminary_patterns)}"
        )

        # Step 3: Evaluate active experiments
        logger.info("\n[3/7] Evaluating active experiments...")
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
                    date_detected=utc_now(),
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
        logger.info("\n[4/7] Proposing parameter optimizations...")
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

        # A2: Persist ALL proposals to learning_history (not just auto-applied ones)
        for proposal in proposals:
            self._save_proposal(proposal)

        # Step 5: Auto-apply high-confidence changes
        logger.info("\n[5/7] Auto-applying high-confidence changes...")

        for proposal in proposals:
            if proposal.confidence >= self.auto_apply_threshold:
                change = self.optimizer.apply_change(proposal, approval="auto")
                report.changes_applied.append(proposal)

                logger.info(
                    f"  ✓ Applied: {proposal.parameter} = {proposal.proposed_value} "
                    f"(confidence={proposal.confidence:.1%})"
                )

        logger.info(f"  → Applied {len(report.changes_applied)} changes automatically")

        # Step 6: Alpha decay monitoring
        logger.info("\n[6/7] Running alpha decay analysis...")
        decay_report = self.alpha_decay_monitor.run_analysis()
        report.alpha_decay_health = decay_report.overall_health
        report.alpha_decay_reasons = decay_report.health_reasons

        health_style = {
            "HEALTHY": "✓", "WATCH": "~", "WARNING": "⚠", "CRITICAL": "✗",
        }.get(decay_report.overall_health, "?")

        logger.info(f"  {health_style} Strategy health: {decay_report.overall_health}")
        for reason in decay_report.health_reasons:
            logger.info(f"    • {reason}")

        if decay_report.rolling_metrics:
            for m in decay_report.rolling_metrics:
                logger.info(
                    f"  {m.window_days}d: {m.trade_count} trades, "
                    f"WR={m.win_rate:.0%}, ROI={m.avg_roi:.1%}, "
                    f"Sharpe={m.sharpe_ratio:.2f}"
                )

        # Step 7: Regime-aware adaptation analysis
        logger.info("\n[7/7] Running regime adaptation analysis...")
        try:
            # Use latest VIX from closed trades
            latest_vix = self._get_latest_vix(closed_trades)
            if latest_vix:
                regime_report = self.regime_adapter.analyse(latest_vix)
                report.regime_health = regime_report.current_regime
                report.regime_entry_signal = (
                    regime_report.term_structure.entry_signal
                    if regime_report.term_structure else "unknown"
                )

                logger.info(
                    f"  Regime: {regime_report.current_regime} (VIX={latest_vix:.1f})"
                )
                if regime_report.term_structure:
                    logger.info(
                        f"  VIX direction: {regime_report.term_structure.direction} "
                        f"→ entry signal: {regime_report.term_structure.entry_signal}"
                    )
                logger.info(
                    f"  Active regime experiments: {len(regime_report.regime_experiments)}"
                )
            else:
                logger.info("  No VIX data available — skipping regime analysis")
        except Exception as e:
            logger.warning(f"  Regime analysis failed: {e}")

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
            existing.date_last_validated = utc_now()
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
                date_last_validated=utc_now(),
                status="active",
            )

            self.db.add(pattern_model)

        # Log learning event
        learning_event = LearningHistory(
            event_type="pattern_detected",
            event_date=utc_now(),
            pattern_name=pattern.pattern_name,
            confidence=pattern.confidence,
            sample_size=pattern.sample_size,
            reasoning=f"{pattern.pattern_type}: {pattern.pattern_value}",
        )

        self.db.add(learning_event)
        self.db.commit()

    def _save_pattern_candidate(self, pattern, result) -> None:
        """Persist every detected pattern as a candidate for audit trail (A1).

        Creates a learning_history record with event_type='pattern_candidate',
        including stats and validation status/reason so nothing is lost.
        """
        import json

        details = {
            "pattern_type": pattern.pattern_type,
            "pattern_value": pattern.pattern_value,
            "win_rate": round(pattern.win_rate, 4),
            "avg_roi": round(pattern.avg_roi, 4),
            "baseline_win_rate": round(pattern.baseline_win_rate, 4) if pattern.baseline_win_rate else None,
            "baseline_roi": round(pattern.baseline_roi, 4) if pattern.baseline_roi else None,
            "p_value": round(pattern.p_value, 6),
            "effect_size": round(pattern.effect_size, 4),
            "validation_status": result.status,
            "rejection_reason": result.reason if not result.valid else None,
        }

        event = LearningHistory(
            event_type="pattern_candidate",
            event_date=utc_now(),
            pattern_name=pattern.pattern_name,
            confidence=pattern.confidence,
            sample_size=pattern.sample_size,
            reasoning=json.dumps(details),
        )
        self.db.add(event)
        self.db.commit()

    def _save_proposal(self, proposal) -> None:
        """Persist a proposal to learning_history even if not auto-applied (A2).

        Creates a learning_history record with event_type='proposal_generated'.
        """
        event = LearningHistory(
            event_type="proposal_generated",
            event_date=utc_now(),
            pattern_name=proposal.source_pattern.pattern_name if proposal.source_pattern else None,
            confidence=proposal.confidence,
            parameter_changed=proposal.parameter,
            old_value=str(proposal.current_value),
            new_value=str(proposal.proposed_value),
            reasoning=proposal.reasoning,
            expected_improvement=proposal.expected_improvement,
        )
        self.db.add(event)
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
            f"{report.patterns_preliminary} preliminary, "
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

    def _get_latest_vix(self, trades: list) -> Optional[float]:
        """Get the most recent VIX value from trade history.

        Args:
            trades: List of closed trades

        Returns:
            Latest VIX value or None
        """
        vix_trades = [
            t for t in trades
            if t.vix_at_entry is not None and t.entry_date is not None
        ]
        if not vix_trades:
            return None
        latest = max(vix_trades, key=lambda t: t.entry_date)
        return latest.vix_at_entry

    def _load_regime_overrides(self) -> dict:
        """Load regime parameter overrides from phase5.yaml config.

        Returns:
            Dict mapping regime names to parameter overrides
        """
        try:
            import yaml
            from pathlib import Path

            config_path = Path(__file__).parent.parent.parent / "config" / "phase5.yaml"
            if config_path.exists():
                with open(config_path) as f:
                    config = yaml.safe_load(f)
                regime_config = config.get("regime_adaptation", {})
                if regime_config.get("enabled", False):
                    return regime_config.get("regime_overrides", {})
            return {}
        except Exception as e:
            logger.warning(f"Could not load regime overrides: {e}")
            return {}

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
