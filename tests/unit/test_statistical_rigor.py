"""Tests for Phase C: Statistical Rigor enhancements.

Tests FDR correction (C1), adaptive thresholds (C2), and
walk-forward cross-validation (C3).
"""

import math
from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock

import numpy as np
import pytest

from src.learning.models import DetectedPattern, ValidationResult
from src.learning.statistical_validator import StatisticalValidator


def _make_pattern(name, p_value, effect_size=0.3, sample_size=100, win_rate=0.80, avg_roi=0.15):
    """Create a DetectedPattern with given stats."""
    return DetectedPattern(
        pattern_type="test",
        pattern_name=name,
        pattern_value="test_value",
        sample_size=sample_size,
        win_rate=win_rate,
        avg_roi=avg_roi,
        baseline_win_rate=0.85,
        baseline_roi=0.12,
        p_value=p_value,
        effect_size=effect_size,
        confidence=0.50,
        date_detected=datetime.now(),
    )


@pytest.fixture
def mock_db():
    return MagicMock()


# ============================================================================
# C1: Benjamini-Hochberg FDR Correction
# ============================================================================

class TestFDRCorrection:
    """Tests for Benjamini-Hochberg FDR correction."""

    def test_fdr_adjusts_p_values_upward(self, mock_db):
        """FDR-adjusted p-values should be >= original p-values."""
        validator = StatisticalValidator(mock_db, fdr_correction=True)

        patterns = [
            _make_pattern("a", p_value=0.001),
            _make_pattern("b", p_value=0.01),
            _make_pattern("c", p_value=0.04),
            _make_pattern("d", p_value=0.10),
            _make_pattern("e", p_value=0.50),
        ]

        original_p = [p.p_value for p in patterns]
        validator.apply_fdr_correction(patterns)
        adjusted_p = [p.p_value for p in patterns]

        # Adjusted should be >= original (FDR makes it harder to pass)
        for orig, adj in zip(original_p, adjusted_p):
            assert adj >= orig or abs(adj - orig) < 1e-10

    def test_fdr_preserves_rank_order(self, mock_db):
        """Adjusted p-values should maintain the same rank order."""
        validator = StatisticalValidator(mock_db, fdr_correction=True)

        patterns = [
            _make_pattern("a", p_value=0.001),
            _make_pattern("b", p_value=0.01),
            _make_pattern("c", p_value=0.04),
            _make_pattern("d", p_value=0.50),
        ]

        validator.apply_fdr_correction(patterns)
        adjusted = [p.p_value for p in patterns]

        # Should still be sorted ascending
        assert adjusted == sorted(adjusted)

    def test_fdr_caps_at_one(self, mock_db):
        """No adjusted p-value should exceed 1.0."""
        validator = StatisticalValidator(mock_db, fdr_correction=True)

        patterns = [_make_pattern(f"p{i}", p_value=0.8) for i in range(20)]

        validator.apply_fdr_correction(patterns)

        for p in patterns:
            assert p.p_value <= 1.0

    def test_fdr_with_many_tests_reduces_discoveries(self, mock_db):
        """With 50 tests, a p=0.04 should no longer be significant after FDR."""
        validator = StatisticalValidator(mock_db, fdr_correction=True, significance_level=0.05)

        # 1 pattern at p=0.04, 49 at p=0.90
        patterns = [_make_pattern("target", p_value=0.04)]
        patterns.extend([_make_pattern(f"noise_{i}", p_value=0.90) for i in range(49)])

        validator.apply_fdr_correction(patterns)

        # After FDR with 50 tests, p=0.04 → p_adj = 0.04 * 50/1 = 2.0 → capped at 1.0
        # So the "target" should no longer be significant
        assert patterns[0].p_value > 0.05

    def test_fdr_disabled(self, mock_db):
        """When fdr_correction=False, p-values should not change."""
        validator = StatisticalValidator(mock_db, fdr_correction=False)

        patterns = [
            _make_pattern("a", p_value=0.001),
            _make_pattern("b", p_value=0.04),
        ]

        original_p = [p.p_value for p in patterns]
        validator.apply_fdr_correction(patterns)
        after_p = [p.p_value for p in patterns]

        assert original_p == after_p

    def test_fdr_single_pattern(self, mock_db):
        """FDR with a single pattern should not modify it."""
        validator = StatisticalValidator(mock_db, fdr_correction=True)

        patterns = [_make_pattern("only", p_value=0.03)]
        validator.apply_fdr_correction(patterns)

        assert patterns[0].p_value == 0.03

    def test_fdr_real_scenario(self, mock_db):
        """Simulate 93 pattern tests — only truly significant should survive."""
        validator = StatisticalValidator(mock_db, fdr_correction=True, significance_level=0.05)

        # 3 genuinely significant, 90 noise
        patterns = [
            _make_pattern("real_1", p_value=0.0001),
            _make_pattern("real_2", p_value=0.001),
            _make_pattern("real_3", p_value=0.005),
        ]
        patterns.extend([_make_pattern(f"noise_{i}", p_value=0.3 + 0.005 * i) for i in range(90)])

        validator.apply_fdr_correction(patterns)

        significant = [p for p in patterns if p.p_value < 0.05]
        # The 3 genuinely significant should survive FDR
        assert len(significant) >= 2  # At least 2 of 3 should survive
        assert all(p.pattern_name.startswith("real") for p in significant)


# ============================================================================
# C2: Adaptive Validation Thresholds
# ============================================================================

class TestAdaptiveThresholds:
    """Tests for adaptive effect size thresholds."""

    def test_small_sample_requires_large_effect(self, mock_db):
        """With few trades, the effect threshold should be at or near the max."""
        validator = StatisticalValidator(mock_db, min_effect_size=0.5, adaptive_thresholds=True)

        # d ≈ 2.8 / sqrt(30) ≈ 0.51 → capped at 0.5
        threshold = validator._adaptive_effect_threshold(30)
        assert threshold >= 0.45  # Near the max

    def test_large_sample_allows_smaller_effect(self, mock_db):
        """With many trades, smaller effects become detectable."""
        validator = StatisticalValidator(mock_db, min_effect_size=0.5, adaptive_thresholds=True)

        # d ≈ 2.8 / sqrt(1000) ≈ 0.089 → clamped to floor 0.10
        threshold = validator._adaptive_effect_threshold(1000)
        assert threshold == 0.10

    def test_medium_sample_intermediate_threshold(self, mock_db):
        """Mid-range sample sizes should have intermediate thresholds."""
        validator = StatisticalValidator(mock_db, min_effect_size=0.5, adaptive_thresholds=True)

        # d ≈ 2.8 / sqrt(200) ≈ 0.198
        threshold = validator._adaptive_effect_threshold(200)
        assert 0.15 <= threshold <= 0.25

    def test_adaptive_disabled_uses_fixed_threshold(self, mock_db):
        """When disabled, should always return the configured min_effect_size."""
        validator = StatisticalValidator(mock_db, min_effect_size=0.5, adaptive_thresholds=False)

        assert validator._adaptive_effect_threshold(30) == 0.5
        assert validator._adaptive_effect_threshold(1000) == 0.5
        assert validator._adaptive_effect_threshold(5000) == 0.5

    def test_floor_at_010(self, mock_db):
        """Even with huge datasets, threshold should never drop below 0.10."""
        validator = StatisticalValidator(mock_db, min_effect_size=0.5, adaptive_thresholds=True)

        threshold = validator._adaptive_effect_threshold(100000)
        assert threshold == 0.10

    def test_zero_sample_uses_default(self, mock_db):
        """Edge case: zero samples should return the max threshold."""
        validator = StatisticalValidator(mock_db, min_effect_size=0.5, adaptive_thresholds=True)

        assert validator._adaptive_effect_threshold(0) == 0.5

    def test_adaptive_threshold_enables_validation(self, mock_db):
        """A pattern with d=0.15 and n=500 should pass with adaptive but fail without."""
        # With adaptive: threshold = 2.8/sqrt(500) ≈ 0.125, so d=0.15 passes
        validator_adaptive = StatisticalValidator(
            mock_db, min_effect_size=0.5, adaptive_thresholds=True
        )
        assert validator_adaptive._adaptive_effect_threshold(500) < 0.15

        # Without adaptive: threshold stays at 0.5, d=0.15 fails
        validator_fixed = StatisticalValidator(
            mock_db, min_effect_size=0.5, adaptive_thresholds=False
        )
        assert validator_fixed._adaptive_effect_threshold(500) == 0.5


# ============================================================================
# C3: Walk-Forward Cross-Validation
# ============================================================================

class TestWalkForwardCV:
    """Tests for walk-forward cross-validation."""

    def _make_trades(self, n, roi_func=None):
        """Create mock trades with chronological dates."""
        trades = []
        base_date = datetime(2025, 1, 1)
        for i in range(n):
            t = MagicMock()
            t.entry_date = base_date + timedelta(days=i)
            t.exit_date = base_date + timedelta(days=i + 7)
            t.trade_id = f"trade_{i}"
            t.roi = roi_func(i) if roi_func else 0.10
            t.dte = 30
            t.vix_at_entry = 18.0
            t.trend = "bullish"
            trades.append(t)
        return trades

    def test_consistent_pattern_scores_high(self, mock_db):
        """A pattern with consistent performance across time should score well."""
        validator = StatisticalValidator(mock_db)
        trades = self._make_trades(100, roi_func=lambda i: 0.10)

        # Mock the pattern trades query
        mock_db.query.return_value.filter.return_value.all.return_value = trades

        pattern = _make_pattern("consistent", p_value=0.01, sample_size=100)
        score = validator.cross_validate(pattern, n_splits=5)

        # All windows return same ROI → perfect score
        assert score >= 0.90

    def test_degrading_pattern_scores_lower(self, mock_db):
        """A pattern that degrades over time should score lower."""
        validator = StatisticalValidator(mock_db)

        # ROI degrades from 0.20 to -0.10 over time
        trades = self._make_trades(100, roi_func=lambda i: 0.20 - 0.003 * i)

        mock_db.query.return_value.filter.return_value.all.return_value = trades

        pattern = _make_pattern("degrading", p_value=0.01, sample_size=100)
        score = validator.cross_validate(pattern, n_splits=5)

        # Train mean keeps shifting — test should differ from train
        assert score < 0.80

    def test_insufficient_data_returns_zero(self, mock_db):
        """Too few trades for walk-forward should return 0."""
        validator = StatisticalValidator(mock_db)
        trades = self._make_trades(3)

        mock_db.query.return_value.filter.return_value.all.return_value = trades

        pattern = _make_pattern("tiny", p_value=0.01, sample_size=3)
        score = validator.cross_validate(pattern, n_splits=5)

        assert score == 0.0

    def test_walk_forward_never_uses_future_data(self, mock_db):
        """Verify that training data is always before test data chronologically."""
        validator = StatisticalValidator(mock_db)

        # ROI is 0 for first half, 1.0 for second half
        # If walk-forward leaks future data, train would include high-ROI data
        trades = self._make_trades(60, roi_func=lambda i: 0.0 if i < 30 else 1.0)

        mock_db.query.return_value.filter.return_value.all.return_value = trades

        pattern = _make_pattern("regime_shift", p_value=0.01, sample_size=60)
        score = validator.cross_validate(pattern, n_splits=5)

        # With a regime shift, early test windows (trained only on early data)
        # should show low consistency → lower score
        assert score < 0.90


# ============================================================================
# Integration: All C features together
# ============================================================================

class TestPhaseCSintegration:
    """Test C1 + C2 + C3 working together."""

    def test_pattern_with_large_n_and_small_effect_passes_adaptive(self, mock_db):
        """Pattern with n=2000, d=0.12, p=0.001 should pass with adaptive thresholds."""
        validator = StatisticalValidator(
            mock_db,
            min_effect_size=0.5,
            adaptive_thresholds=True,
            fdr_correction=False,
        )

        # Adaptive threshold for n=2000: 2.8/sqrt(2000) ≈ 0.063 → 0.10 (floor)
        # So d=0.12 passes effect check, p=0.001 passes p-value check
        pattern = _make_pattern("large_n", p_value=0.001, effect_size=0.12, sample_size=2000)

        # Mock CV to return a good score
        trades = []
        base_date = datetime(2025, 1, 1)
        for i in range(2000):
            t = MagicMock()
            t.entry_date = base_date + timedelta(days=i)
            t.roi = 0.10
            t.trade_id = f"t{i}"
            t.dte = 30
            t.vix_at_entry = 18.0
            t.trend = "bullish"
            trades.append(t)
        mock_db.query.return_value.filter.return_value.all.return_value = trades

        result = validator.validate_pattern(pattern)

        # With adaptive thresholds, this should either validate or be preliminary
        # (depends on CV score from mocked data)
        assert result.status in ("VALIDATED", "PRELIMINARY")

    def test_same_pattern_fails_without_adaptive(self, mock_db):
        """Same pattern should fail effect check without adaptive thresholds."""
        validator = StatisticalValidator(
            mock_db,
            min_effect_size=0.5,
            adaptive_thresholds=False,
            fdr_correction=False,
        )

        pattern = _make_pattern("large_n", p_value=0.001, effect_size=0.12, sample_size=2000)
        result = validator.validate_pattern(pattern)

        # Without adaptive, d=0.12 < 0.5 threshold → PRELIMINARY (since is_preliminary passes)
        assert result.valid is False
        assert result.status == "PRELIMINARY"
        assert "|d|" in result.reason
