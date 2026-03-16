"""Statistical Validator for ensuring patterns are real, not noise.

Validates detected patterns using rigorous statistical tests including
t-tests, effect size calculations, walk-forward cross-validation,
Benjamini-Hochberg FDR correction, and adaptive thresholds.
"""

import math

import numpy as np
from loguru import logger
from scipy import stats
import sqlalchemy as sa
from sqlalchemy.orm import Session

from src.data.models import Trade
from src.learning.account_filter import get_learning_account_filter
from src.learning.models import DetectedPattern, ValidationResult


class StatisticalValidator:
    """Validates patterns using statistical tests.

    Ensures patterns are statistically significant and not just
    random noise by applying multiple validation checks.

    Phase C enhancements:
    - C1: Benjamini-Hochberg FDR correction across all pattern tests
    - C2: Adaptive validation thresholds that scale with dataset size
    - C3: Walk-forward cross-validation (replaces static k-fold)
    """

    def __init__(
        self,
        db_session: Session,
        min_samples: int = 30,
        significance_level: float = 0.05,
        min_effect_size: float = 0.5,
        min_cv_score: float = 0.6,
        adaptive_thresholds: bool = True,
        fdr_correction: bool = True,
    ):
        """Initialize statistical validator.

        Args:
            db_session: Database session for querying trades
            min_samples: Minimum sample size for validation
            significance_level: Maximum p-value for significance (default 0.05)
            min_effect_size: Minimum Cohen's d effect size (default 0.5)
            min_cv_score: Minimum cross-validation score (default 0.6)
            adaptive_thresholds: Scale thresholds by dataset size (default True)
            fdr_correction: Apply Benjamini-Hochberg FDR correction (default True)
        """
        self.db = db_session
        self.min_samples = min_samples
        self.significance_level = significance_level
        self.min_effect_size = min_effect_size
        self.min_cv_score = min_cv_score
        self.adaptive_thresholds = adaptive_thresholds
        self.fdr_correction = fdr_correction

    def validate_pattern(self, pattern: DetectedPattern) -> ValidationResult:
        """Run comprehensive statistical tests on a detected pattern.

        Returns VALIDATED (all 4 gates pass), PRELIMINARY (directional signal
        with relaxed thresholds), or REJECTED.

        Uses adaptive thresholds (C2) when enabled — effect size and p-value
        thresholds scale with dataset size. Uses the pattern's p_value which
        may have been FDR-corrected (C1) by apply_fdr_correction().

        Args:
            pattern: Detected pattern to validate

        Returns:
            ValidationResult with outcome, status tier, and metrics
        """
        logger.debug(f"Validating pattern: {pattern.pattern_name}")

        # C2: Compute adaptive thresholds based on dataset size
        eff_threshold = self._adaptive_effect_threshold(pattern.sample_size)
        p_threshold = self.significance_level  # p-value threshold stays fixed (FDR handles multiplicity)

        # 1. Sample size check
        if pattern.sample_size < self.min_samples:
            if pattern.is_preliminary():
                return ValidationResult(
                    valid=False,
                    status="PRELIMINARY",
                    reason=f"Need n≥{self.min_samples} (have {pattern.sample_size})",
                    p_value=pattern.p_value,
                    effect_size=pattern.effect_size,
                )
            return ValidationResult(
                valid=False,
                status="REJECTED",
                reason=f"Insufficient samples: {pattern.sample_size} < {self.min_samples}",
            )

        # 2. Statistical significance check (p-value, possibly FDR-corrected)
        if pattern.p_value > p_threshold:
            if pattern.is_preliminary():
                return ValidationResult(
                    valid=False,
                    status="PRELIMINARY",
                    reason=f"Need p<{p_threshold} (p={pattern.p_value:.4f})",
                    p_value=pattern.p_value,
                    effect_size=pattern.effect_size,
                )
            return ValidationResult(
                valid=False,
                status="REJECTED",
                reason=f"p={pattern.p_value:.4f} > {p_threshold}",
                p_value=pattern.p_value,
            )

        # 3. Effect size check (C2: adaptive threshold)
        if abs(pattern.effect_size) < eff_threshold:
            if pattern.is_preliminary():
                return ValidationResult(
                    valid=False,
                    status="PRELIMINARY",
                    reason=f"Need |d|>{eff_threshold:.2f} (d={pattern.effect_size:.3f})",
                    p_value=pattern.p_value,
                    effect_size=pattern.effect_size,
                )
            return ValidationResult(
                valid=False,
                status="REJECTED",
                reason=f"|d|={abs(pattern.effect_size):.3f} < {eff_threshold:.2f}",
                p_value=pattern.p_value,
                effect_size=pattern.effect_size,
            )

        # 4. Cross-validation across time periods
        cv_score = self.cross_validate(pattern)
        if cv_score < self.min_cv_score:
            return ValidationResult(
                valid=False,
                status="PRELIMINARY",
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
            status="VALIDATED",
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
        all_trades = self.db.query(Trade).filter(Trade.exit_date.isnot(None)).filter(get_learning_account_filter()).all()
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
        all_trades = self.db.query(Trade).filter(Trade.exit_date.isnot(None)).filter(get_learning_account_filter()).all()
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

    def cross_validate(self, pattern: DetectedPattern, n_splits: int = 5) -> float:
        """Walk-forward cross-validation (C3).

        Uses expanding-window walk-forward: always trains on data *before*
        the test period, never on future data. This matches how trading
        decisions are made in practice.

        The data is split into n_splits+1 chronological blocks. For each
        split i, trains on blocks 0..i and tests on block i+1.

        Args:
            pattern: Pattern to cross-validate
            n_splits: Number of walk-forward splits (default 5)

        Returns:
            Average validation score across splits
        """
        pattern_trades = self._get_pattern_trades(pattern)

        min_trades_needed = n_splits + 1
        if len(pattern_trades) < min_trades_needed:
            logger.warning(f"Not enough trades for {n_splits}-split walk-forward CV")
            return 0.0

        # Sort trades chronologically
        pattern_trades = sorted(pattern_trades, key=lambda t: t.entry_date)

        # Split into n_splits + 1 blocks
        n_blocks = n_splits + 1
        block_size = len(pattern_trades) // n_blocks
        blocks = []
        for i in range(n_blocks):
            start = i * block_size
            end = start + block_size if i < n_blocks - 1 else len(pattern_trades)
            blocks.append(pattern_trades[start:end])

        split_scores = []

        for i in range(n_splits):
            # Train on blocks 0..i, test on block i+1
            train_trades = []
            for j in range(i + 1):
                train_trades.extend(blocks[j])
            test_trades = blocks[i + 1]

            if not test_trades or not train_trades:
                continue

            train_rois = [t.roi for t in train_trades if t.roi is not None]
            test_rois = [t.roi for t in test_trades if t.roi is not None]

            if not train_rois or not test_rois:
                continue

            train_roi = float(np.mean(train_rois))
            test_roi = float(np.mean(test_rois))

            # Score measures consistency: how close is out-of-sample to in-sample?
            # 1.0 = perfect match, decays as test diverges from train
            if train_roi == 0:
                score = 1.0 if test_roi >= 0 else 0.0
            else:
                ratio = test_roi / train_roi
                score = max(0.0, min(1.0, 1.0 - abs(1.0 - ratio)))

            split_scores.append(score)

        if not split_scores:
            return 0.0

        avg_score = float(np.mean(split_scores))

        logger.debug(
            f"Walk-forward CV: {n_splits} splits, avg score={avg_score:.2f}"
        )

        return avg_score

    def _get_pattern_trades(self, pattern: DetectedPattern) -> list[Trade]:
        """Get trades matching the pattern criteria.

        Args:
            pattern: Pattern to match trades against

        Returns:
            List of matching trades
        """
        all_trades = self.db.query(Trade).filter(Trade.exit_date.isnot(None)).filter(get_learning_account_filter()).all()

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

    # ======================================================================
    # C1: Benjamini-Hochberg FDR Correction
    # ======================================================================

    def apply_fdr_correction(self, patterns: list[DetectedPattern]) -> list[DetectedPattern]:
        """Apply Benjamini-Hochberg FDR correction across all patterns (C1).

        When testing many hypotheses simultaneously, some will appear
        significant by chance. BH-FDR controls the expected *proportion*
        of false discoveries among all discoveries.

        Modifies pattern.p_value in-place to the adjusted p-value.
        Returns the same list (mutated) for convenience.

        Args:
            patterns: All detected patterns from pattern_detector

        Returns:
            Same list with p_values replaced by FDR-adjusted values
        """
        if not self.fdr_correction or len(patterns) < 2:
            return patterns

        m = len(patterns)
        p_values = np.array([p.p_value for p in patterns])

        # Benjamini-Hochberg procedure:
        # 1. Sort p-values ascending
        sorted_indices = np.argsort(p_values)
        sorted_p = p_values[sorted_indices]

        # 2. Compute adjusted p-values: p_adj[i] = p[i] * m / (i+1)
        adjusted = np.zeros(m)
        for i in range(m):
            adjusted[i] = sorted_p[i] * m / (i + 1)

        # 3. Enforce monotonicity (from right): p_adj[i] = min(p_adj[i], p_adj[i+1])
        for i in range(m - 2, -1, -1):
            adjusted[i] = min(adjusted[i], adjusted[i + 1])

        # 4. Cap at 1.0
        adjusted = np.minimum(adjusted, 1.0)

        # 5. Map back to original order and update patterns
        for idx, orig_idx in enumerate(sorted_indices):
            patterns[orig_idx].p_value = float(adjusted[idx])

        n_significant = sum(1 for p in patterns if p.p_value < self.significance_level)
        logger.info(
            f"FDR correction applied: {m} tests, "
            f"{n_significant} significant at α={self.significance_level} "
            f"(was {sum(1 for p in p_values if p < self.significance_level)} before correction)"
        )

        return patterns

    # ======================================================================
    # C2: Adaptive Validation Thresholds
    # ======================================================================

    def _adaptive_effect_threshold(self, sample_size: int) -> float:
        """Compute adaptive effect size threshold based on sample size (C2).

        With few trades, we need large effects to be confident.
        With many trades, we can reliably detect smaller effects.

        Scaling: uses Cohen's detectable effect size formula, with a
        floor at 0.10 (trivial effects are never actionable) and
        ceiling at self.min_effect_size (the configured default).

        Args:
            sample_size: Number of trades in the pattern bucket

        Returns:
            Minimum |Cohen's d| required for this sample size
        """
        if not self.adaptive_thresholds:
            return self.min_effect_size

        # Minimum detectable effect at 80% power:
        # d ≈ 2.8 / sqrt(n) for a two-sample test
        # We use a slightly less aggressive formula with a floor
        if sample_size <= 0:
            return self.min_effect_size

        detectable = 2.8 / math.sqrt(sample_size)

        # Clamp: never below 0.10 (trivial), never above configured max
        threshold = max(0.10, min(self.min_effect_size, detectable))

        return round(threshold, 3)
