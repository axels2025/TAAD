"""Statistical Validator for ensuring patterns are real, not noise.

Validates detected patterns using rigorous statistical tests including
t-tests, effect size calculations, and time-series cross-validation.
"""

import numpy as np
from loguru import logger
from scipy import stats
from sqlalchemy.orm import Session

from src.data.models import Trade
from src.learning.models import DetectedPattern, ValidationResult


class StatisticalValidator:
    """Validates patterns using statistical tests.

    Ensures patterns are statistically significant and not just
    random noise by applying multiple validation checks.
    """

    def __init__(
        self,
        db_session: Session,
        min_samples: int = 30,
        significance_level: float = 0.05,
        min_effect_size: float = 0.5,
        min_cv_score: float = 0.6,
    ):
        """Initialize statistical validator.

        Args:
            db_session: Database session for querying trades
            min_samples: Minimum sample size for validation
            significance_level: Maximum p-value for significance (default 0.05)
            min_effect_size: Minimum Cohen's d effect size (default 0.5)
            min_cv_score: Minimum cross-validation score (default 0.6)
        """
        self.db = db_session
        self.min_samples = min_samples
        self.significance_level = significance_level
        self.min_effect_size = min_effect_size
        self.min_cv_score = min_cv_score

    def validate_pattern(self, pattern: DetectedPattern) -> ValidationResult:
        """Run comprehensive statistical tests on a detected pattern.

        Args:
            pattern: Detected pattern to validate

        Returns:
            ValidationResult with outcome and metrics
        """
        logger.debug(f"Validating pattern: {pattern.pattern_name}")

        # 1. Sample size check
        if pattern.sample_size < self.min_samples:
            return ValidationResult(
                valid=False,
                reason=f"Insufficient samples: {pattern.sample_size} < {self.min_samples}",
            )

        # 2. Statistical significance check (p-value)
        if pattern.p_value > self.significance_level:
            return ValidationResult(
                valid=False,
                reason=f"Not statistically significant (p={pattern.p_value:.4f})",
                p_value=pattern.p_value,
            )

        # 3. Effect size check
        if abs(pattern.effect_size) < self.min_effect_size:
            return ValidationResult(
                valid=False,
                reason=f"Effect size too small ({pattern.effect_size:.3f})",
                p_value=pattern.p_value,
                effect_size=pattern.effect_size,
            )

        # 4. Cross-validation across time periods
        cv_score = self.cross_validate(pattern)
        if cv_score < self.min_cv_score:
            return ValidationResult(
                valid=False,
                reason=f"Poor cross-validation score ({cv_score:.2f})",
                p_value=pattern.p_value,
                effect_size=pattern.effect_size,
                cv_score=cv_score,
            )

        # All checks passed
        confidence = self._calculate_confidence(
            pattern.p_value, pattern.effect_size, cv_score
        )

        logger.info(
            f"✓ Pattern validated: {pattern.pattern_name} "
            f"(p={pattern.p_value:.4f}, effect={pattern.effect_size:.2f}, "
            f"cv={cv_score:.2f}, confidence={confidence:.1%})"
        )

        return ValidationResult(
            valid=True,
            reason="Pattern meets all validation criteria",
            p_value=pattern.p_value,
            effect_size=pattern.effect_size,
            cv_score=cv_score,
            confidence=confidence,
        )

    def run_t_test(self, pattern: DetectedPattern) -> tuple[float, float]:
        """Run independent samples t-test.

        Compares pattern group ROI distribution against baseline.

        Args:
            pattern: Pattern to test

        Returns:
            (t_statistic, p_value) tuple
        """
        # Get trades matching this pattern
        pattern_trades = self._get_pattern_trades(pattern)

        if len(pattern_trades) < 2:
            return (0.0, 1.0)

        # Get baseline trades (all others)
        all_trades = self.db.query(Trade).filter(Trade.exit_date.isnot(None)).all()
        pattern_ids = {t.trade_id for t in pattern_trades}
        baseline_trades = [t for t in all_trades if t.trade_id not in pattern_ids]

        if len(baseline_trades) < 2:
            return (0.0, 1.0)

        # Extract ROIs
        pattern_rois = [t.roi for t in pattern_trades if t.roi is not None]
        baseline_rois = [t.roi for t in baseline_trades if t.roi is not None]

        if len(pattern_rois) < 2 or len(baseline_rois) < 2:
            return (0.0, 1.0)

        # Two-sample t-test
        t_stat, p_value = stats.ttest_ind(pattern_rois, baseline_rois)

        return (t_stat, p_value)

    def calculate_effect_size(self, pattern: DetectedPattern) -> float:
        """Calculate Cohen's d effect size.

        Measures the standardized difference between pattern group
        and baseline performance.

        Args:
            pattern: Pattern to calculate effect size for

        Returns:
            Cohen's d effect size
        """
        pattern_trades = self._get_pattern_trades(pattern)

        if not pattern_trades:
            return 0.0

        # Get baseline
        all_trades = self.db.query(Trade).filter(Trade.exit_date.isnot(None)).all()
        pattern_ids = {t.trade_id for t in pattern_trades}
        baseline_trades = [t for t in all_trades if t.trade_id not in pattern_ids]

        # Extract ROIs
        pattern_rois = np.array([t.roi for t in pattern_trades if t.roi is not None])
        baseline_rois = np.array([t.roi for t in baseline_trades if t.roi is not None])

        if len(pattern_rois) == 0 or len(baseline_rois) == 0:
            return 0.0

        # Cohen's d = (mean1 - mean2) / pooled_std
        mean_diff = np.mean(pattern_rois) - np.mean(baseline_rois)
        pooled_std = np.sqrt((np.var(pattern_rois) + np.var(baseline_rois)) / 2)

        if pooled_std == 0:
            return 0.0

        cohen_d = mean_diff / pooled_std

        return cohen_d

    def cross_validate(self, pattern: DetectedPattern, n_folds: int = 5) -> float:
        """Time-series cross-validation.

        Splits trades into chronological folds and validates that
        pattern holds across different time periods.

        Args:
            pattern: Pattern to cross-validate
            n_folds: Number of folds for CV (default 5)

        Returns:
            Average validation score across folds
        """
        pattern_trades = self._get_pattern_trades(pattern)

        if len(pattern_trades) < n_folds:
            logger.warning(f"Not enough trades for {n_folds}-fold CV")
            return 0.0

        # Sort trades chronologically
        pattern_trades = sorted(pattern_trades, key=lambda t: t.entry_date)

        # Split into folds
        fold_size = len(pattern_trades) // n_folds
        fold_scores = []

        for i in range(n_folds):
            # Define test fold
            test_start = i * fold_size
            test_end = test_start + fold_size if i < n_folds - 1 else len(pattern_trades)
            test_trades = pattern_trades[test_start:test_end]

            # Train folds are all others
            train_trades = pattern_trades[:test_start] + pattern_trades[test_end:]

            if len(test_trades) == 0 or len(train_trades) == 0:
                continue

            # Calculate performance on train vs test
            train_roi = np.mean([t.roi for t in train_trades if t.roi is not None])
            test_roi = np.mean([t.roi for t in test_trades if t.roi is not None])

            # Score: 1.0 if test matches or exceeds train, 0.0 if opposite
            # Use scaled score based on how close they are
            if train_roi == 0:
                score = 1.0 if test_roi >= 0 else 0.0
            else:
                ratio = test_roi / train_roi
                # Score is high if test ~= train
                score = max(0.0, min(1.0, 1.0 - abs(1.0 - ratio)))

            fold_scores.append(score)

        if not fold_scores:
            return 0.0

        avg_score = np.mean(fold_scores)

        logger.debug(
            f"Cross-validation: {n_folds} folds, avg score={avg_score:.2f}"
        )

        return avg_score

    def _get_pattern_trades(self, pattern: DetectedPattern) -> list[Trade]:
        """Get trades matching the pattern criteria.

        Args:
            pattern: Pattern to match trades against

        Returns:
            List of matching trades
        """
        all_trades = self.db.query(Trade).filter(Trade.exit_date.isnot(None)).all()

        # Filter based on pattern type
        if pattern.pattern_type == "dte_bucket":
            # Parse DTE range from pattern_value
            # Format: "14-21 days"
            if "-" in pattern.pattern_value:
                range_str = pattern.pattern_value.replace(" days", "")
                min_dte, max_dte = map(int, range_str.split("-"))
                return [
                    t for t in all_trades
                    if t.dte is not None and min_dte <= t.dte <= max_dte
                ]

        elif pattern.pattern_type == "vix_regime":
            # Parse VIX range from pattern_value
            # Format: "VIX 15-20"
            if "VIX" in pattern.pattern_value:
                range_str = pattern.pattern_value.replace("VIX ", "")
                min_vix, max_vix = map(float, range_str.split("-"))
                return [
                    t for t in all_trades
                    if t.vix_at_entry is not None
                    and min_vix <= t.vix_at_entry < max_vix
                ]

        elif pattern.pattern_type == "trend_direction":
            return [
                t for t in all_trades
                if t.trend == pattern.pattern_value
            ]

        elif pattern.pattern_type == "entry_day":
            # Parse day name from pattern_name
            # Format: "entry_monday"
            day_name = pattern.pattern_value
            day_map = {
                "Monday": 0,
                "Tuesday": 1,
                "Wednesday": 2,
                "Thursday": 3,
                "Friday": 4,
            }
            day_num = day_map.get(day_name)
            if day_num is not None:
                return [
                    t for t in all_trades
                    if t.entry_date.weekday() == day_num
                ]

        # For other pattern types (delta, IV rank), return all for now
        # TODO: Add filtering once context data is linked
        return all_trades

    def _calculate_confidence(
        self, p_value: float, effect_size: float, cv_score: float
    ) -> float:
        """Calculate overall confidence from validation metrics.

        Args:
            p_value: Statistical significance
            effect_size: Cohen's d
            cv_score: Cross-validation score

        Returns:
            Confidence score between 0.0 and 1.0
        """
        # Convert metrics to confidence components
        p_confidence = max(0, 1 - (p_value / self.significance_level))
        effect_confidence = min(1.0, abs(effect_size) / 1.0)
        cv_confidence = cv_score

        # Weighted average (p-value is most important)
        confidence = (
            p_confidence * 0.4 + effect_confidence * 0.3 + cv_confidence * 0.3
        )

        return max(0.0, min(1.0, confidence))
