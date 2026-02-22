"""Experiment Engine for running controlled A/B tests.

Manages experiments comparing control (baseline) vs test (variant) values
for strategy parameters to validate improvements statistically.
"""

import random
from datetime import datetime, timedelta
from typing import Any, Optional

from loguru import logger
from scipy import stats
from sqlalchemy.orm import Session

from src.data.models import Experiment, Trade
from src.learning.models import ExperimentResult


class ExperimentEngine:
    """Manages A/B experiments to test improvements.

    Implements controlled testing where most trades use baseline
    parameters (control group) while a small percentage tests
    new parameters (test group).
    """

    def __init__(
        self,
        db_session: Session,
        control_pct: float = 0.80,
        min_samples_per_group: int = 30,
    ):
        """Initialize experiment engine.

        Args:
            db_session: Database session
            control_pct: Percentage allocated to control group (default 80%)
            min_samples_per_group: Minimum trades per group for valid test
        """
        self.db = db_session
        self.control_pct = control_pct
        self.test_pct = 1.0 - control_pct
        self.min_samples = min_samples_per_group
        self.active_experiments: list[Experiment] = []
        self._load_active_experiments()

    def _load_active_experiments(self) -> None:
        """Load active experiments from database."""
        active = (
            self.db.query(Experiment)
            .filter(Experiment.status == "active")
            .all()
        )
        self.active_experiments = active
        logger.info(f"Loaded {len(active)} active experiments")

    def create_experiment(
        self,
        name: str,
        hypothesis: str,
        parameter: str,
        control_value: Any,
        test_value: Any,
        min_samples: int = 30,
        max_duration_days: int = 30,
    ) -> Experiment:
        """Create a new A/B experiment.

        Args:
            name: Short experiment name
            hypothesis: What we're testing
            parameter: Parameter being tested
            control_value: Baseline value
            test_value: New value to test
            min_samples: Minimum trades needed per group
            max_duration_days: Maximum experiment duration

        Returns:
            Created Experiment object
        """
        experiment_id = self._generate_experiment_id()

        exp = Experiment(
            experiment_id=experiment_id,
            name=name,
            description=hypothesis,
            parameter_name=parameter,
            control_value=str(control_value),
            test_value=str(test_value),
            status="active",
            start_date=datetime.now(),
            end_date=None,
            control_trades=0,
            test_trades=0,
        )

        self.db.add(exp)
        self.db.commit()
        self.db.refresh(exp)

        self.active_experiments.append(exp)

        logger.info(
            f"Created experiment '{name}': {parameter} "
            f"control={control_value} vs test={test_value}"
        )

        return exp

    def assign_trade(
        self, opportunity: dict, baseline_params: dict
    ) -> tuple[dict, str, Optional[str]]:
        """Assign opportunity to control or test group.

        Args:
            opportunity: Trade opportunity to assign
            baseline_params: Baseline strategy parameters

        Returns:
            (parameters_to_use, group_name, experiment_id) tuple
        """
        if not self.active_experiments:
            return (baseline_params, "control", None)

        # Random assignment based on control_pct
        if random.random() < self.control_pct:
            # Control group - use baseline
            return (baseline_params, "control", None)
        else:
            # Test group - select an active experiment
            exp = self._select_active_experiment()
            if exp:
                # Apply experiment parameter
                test_params = baseline_params.copy()
                test_params[exp.parameter_name] = self._parse_value(exp.test_value)

                return (test_params, "test", exp.experiment_id)
            else:
                # No suitable experiment, fallback to control
                return (baseline_params, "control", None)

    def record_trade_assignment(
        self, trade_id: str, experiment_id: Optional[str], group: str
    ) -> None:
        """Record that a trade was assigned to an experiment group.

        Args:
            trade_id: Trade identifier
            experiment_id: Experiment ID (if test group)
            group: 'control' or 'test'
        """
        if group == "test" and experiment_id:
            exp = (
                self.db.query(Experiment)
                .filter(Experiment.experiment_id == experiment_id)
                .first()
            )
            if exp:
                exp.test_trades += 1
                self.db.commit()
                logger.debug(
                    f"Trade {trade_id} assigned to experiment {experiment_id} (test group)"
                )
        else:
            # Control group - increment count on all active experiments
            for exp in self.active_experiments:
                exp.control_trades += 1
            self.db.commit()
            logger.debug(f"Trade {trade_id} assigned to control group")

    def record_outcome(
        self,
        trade_id: str,
        experiment_id: Optional[str],
        group: str,
        outcome: float,
    ) -> None:
        """Record trade outcome for experiment tracking.

        Args:
            trade_id: Trade identifier
            experiment_id: Experiment ID (if test group)
            group: 'control' or 'test'
            outcome: Profit/loss or ROI
        """
        logger.debug(
            f"Recording outcome for trade {trade_id}: "
            f"group={group}, outcome={outcome:.2f}"
        )
        # Outcomes are tracked via Trade.profit_loss and Trade.is_experiment flag
        # This method is for future real-time tracking if needed

    def evaluate_experiment(self, exp: Experiment) -> ExperimentResult:
        """Evaluate if experiment should be adopted or rejected.

        Args:
            exp: Experiment to evaluate

        Returns:
            ExperimentResult with decision and metrics
        """
        logger.info(f"Evaluating experiment: {exp.name}")

        # Check if we have enough data
        if exp.control_trades < self.min_samples:
            return ExperimentResult(
                decision="INSUFFICIENT_DATA",
                reason=f"Need {self.min_samples - exp.control_trades} more control trades",
            )

        if exp.test_trades < self.min_samples:
            return ExperimentResult(
                decision="INSUFFICIENT_DATA",
                reason=f"Need {self.min_samples - exp.test_trades} more test trades",
            )

        # Get actual trade outcomes
        control_roi, control_trades = self._get_group_roi(exp, "control")
        test_roi, test_trades = self._get_group_roi(exp, "test")

        if len(control_trades) < self.min_samples or len(test_trades) < self.min_samples:
            return ExperimentResult(
                decision="INSUFFICIENT_DATA",
                reason="Not enough completed trades in one or both groups",
            )

        # Statistical comparison
        p_value = self._compare_groups(control_trades, test_trades)
        effect_size = test_roi - control_roi

        # Decision criteria
        # 1. Must be statistically significant (p < 0.05)
        # 2. Must have meaningful improvement (>0.5% ROI improvement)

        if p_value < 0.05 and effect_size > 0.005:  # 0.5% improvement
            decision = "ADOPT"
            recommendation = (
                f"Adopt {exp.parameter_name}={exp.test_value} "
                f"(improves ROI by {effect_size:.2%})"
            )
            reason = f"Statistically significant improvement (p={p_value:.4f})"

            # Update experiment status
            exp.status = "adopted"
            exp.end_date = datetime.now()

        elif p_value < 0.05 and effect_size < -0.005:  # 0.5% worse
            decision = "REJECT"
            recommendation = f"Keep {exp.parameter_name}={exp.control_value}"
            reason = f"Test variant performs worse (ROI decreased by {abs(effect_size):.2%})"

            # Update experiment status
            exp.status = "rejected"
            exp.end_date = datetime.now()

        else:
            # No significant difference or effect too small
            decision = "REJECT"
            recommendation = f"Keep {exp.parameter_name}={exp.control_value}"
            reason = "No significant improvement detected"

            exp.status = "rejected"
            exp.end_date = datetime.now()

        # Save updated experiment
        exp.p_value = p_value
        exp.effect_size = effect_size
        exp.control_win_rate = self._calculate_win_rate(control_trades)
        exp.test_win_rate = self._calculate_win_rate(test_trades)
        exp.control_avg_roi = control_roi
        exp.test_avg_roi = test_roi

        self.db.commit()

        logger.info(
            f"Experiment '{exp.name}': {decision} "
            f"(control ROI={control_roi:.2%}, test ROI={test_roi:.2%}, p={p_value:.4f})"
        )

        return ExperimentResult(
            decision=decision,
            p_value=p_value,
            effect_size=effect_size,
            control_roi=control_roi,
            test_roi=test_roi,
            recommendation=recommendation,
            reason=reason,
        )

    def _select_active_experiment(self) -> Optional[Experiment]:
        """Select an active experiment for trade assignment.

        Returns:
            Experiment to use, or None if none suitable
        """
        if not self.active_experiments:
            return None

        # For now, randomly select from active experiments
        # TODO: Implement smarter selection (priority, rotation, etc.)
        return random.choice(self.active_experiments)

    def _get_group_roi(
        self, exp: Experiment, group: str
    ) -> tuple[float, list[Trade]]:
        """Get average ROI for experiment group.

        Args:
            exp: Experiment
            group: 'control' or 'test'

        Returns:
            (avg_roi, trades_list) tuple
        """
        if group == "test":
            # Test group trades have experiment_id set
            trades = (
                self.db.query(Trade)
                .filter(Trade.experiment_id == exp.experiment_id)
                .filter(Trade.is_experiment == True)
                .filter(Trade.exit_date.isnot(None))
                .all()
            )
        else:
            # Control group: trades during experiment period without experiment flag
            trades = (
                self.db.query(Trade)
                .filter(Trade.entry_date >= exp.start_date)
                .filter(Trade.is_experiment == False)
                .filter(Trade.exit_date.isnot(None))
                .all()
            )

        if not trades:
            return (0.0, [])

        rois = [t.roi for t in trades if t.roi is not None]
        avg_roi = sum(rois) / len(rois) if rois else 0.0

        return (avg_roi, trades)

    def _compare_groups(
        self, control_trades: list[Trade], test_trades: list[Trade]
    ) -> float:
        """Compare control and test groups using t-test.

        Args:
            control_trades: Control group trades
            test_trades: Test group trades

        Returns:
            P-value from t-test
        """
        control_rois = [t.roi for t in control_trades if t.roi is not None]
        test_rois = [t.roi for t in test_trades if t.roi is not None]

        if len(control_rois) < 2 or len(test_rois) < 2:
            return 1.0  # Cannot compute

        # Independent samples t-test
        t_stat, p_value = stats.ttest_ind(test_rois, control_rois)

        return p_value

    def _calculate_win_rate(self, trades: list[Trade]) -> float:
        """Calculate win rate for trades.

        Args:
            trades: List of trades

        Returns:
            Win rate (0.0-1.0)
        """
        if not trades:
            return 0.0

        wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
        return wins / len(trades)

    def _generate_experiment_id(self) -> str:
        """Generate unique experiment ID.

        Returns:
            Experiment ID string
        """
        import uuid

        return f"exp_{datetime.now().strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"

    def _parse_value(self, value_str: str) -> Any:
        """Parse value string to appropriate type.

        Args:
            value_str: String representation of value

        Returns:
            Parsed value
        """
        # Try to parse as float
        try:
            return float(value_str)
        except ValueError:
            pass

        # Try to parse as int
        try:
            return int(value_str)
        except ValueError:
            pass

        # Return as string
        return value_str

    def get_active_experiments(self) -> list[Experiment]:
        """Get list of currently active experiments.

        Returns:
            List of active Experiment objects
        """
        return self.active_experiments.copy()

    def stop_experiment(self, experiment_id: str, reason: str = "Manual stop") -> bool:
        """Stop an active experiment.

        Args:
            experiment_id: Experiment to stop
            reason: Reason for stopping

        Returns:
            True if stopped, False if not found
        """
        exp = (
            self.db.query(Experiment)
            .filter(Experiment.experiment_id == experiment_id)
            .first()
        )

        if not exp:
            return False

        exp.status = "stopped"
        exp.end_date = datetime.now()
        exp.decision = reason

        self.db.commit()

        # Remove from active list
        self.active_experiments = [
            e for e in self.active_experiments if e.experiment_id != experiment_id
        ]

        logger.info(f"Stopped experiment {experiment_id}: {reason}")

        return True
