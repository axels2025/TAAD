"""Tests for Phase 6.6: Post-Decision Monitoring guardrails.

Tests confidence calibration and reasoning entropy monitoring.
"""

import pytest

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.monitoring import (
    ConfidenceCalibrator,
    ReasoningEntropyMonitor,
)


# ---------- Confidence Calibration ----------


class TestConfidenceCalibrator:

    @pytest.fixture
    def calibrator(self):
        return ConfidenceCalibrator()

    @pytest.fixture
    def config(self):
        return GuardrailConfig()

    def test_confidence_bucketing(self, calibrator):
        """Outcomes should be grouped into correct confidence buckets."""
        # Add some outcomes in different buckets
        calibrator.record_outcome(0.90, True)   # [0.85, 1.0]
        calibrator.record_outcome(0.75, True)   # [0.7, 0.85)
        calibrator.record_outcome(0.55, False)  # [0.5, 0.7)
        calibrator.record_outcome(0.20, False)  # [0.0, 0.3)

        cal = calibrator.compute_calibration()
        assert cal["sample_size"] == 4
        assert len(cal["buckets"]) == 4

    def test_calibration_error_computed(self, calibrator):
        """Calibration error should be computed as weighted mean absolute error."""
        # Perfect calibration: 80% confident, 80% actually correct
        for _ in range(8):
            calibrator.record_outcome(0.80, True)
        for _ in range(2):
            calibrator.record_outcome(0.80, False)

        cal = calibrator.compute_calibration()
        # Predicted accuracy ~0.80, actual accuracy = 0.80
        assert cal["calibration_error"] < 0.05

    def test_overconfidence_triggers_warning(self, calibrator, config):
        """Overconfident predictions with poor accuracy should warn."""
        # High confidence but only 40% accuracy
        for _ in range(10):
            calibrator.record_outcome(0.90, True)
        for _ in range(15):
            calibrator.record_outcome(0.90, False)

        result = calibrator.check_calibration(config)
        assert not result.passed
        assert result.severity == "warning"
        assert "Calibration error" in result.reason

    def test_good_calibration_passes(self, calibrator, config):
        """Well-calibrated predictions should pass."""
        # 70% confidence, ~70% accuracy
        for _ in range(7):
            calibrator.record_outcome(0.70, True)
        for _ in range(3):
            calibrator.record_outcome(0.70, False)

        result = calibrator.check_calibration(config)
        assert result.passed

    def test_insufficient_samples_passes(self, calibrator, config):
        """Fewer than 10 samples should pass with info message."""
        calibrator.record_outcome(0.90, True)
        calibrator.record_outcome(0.50, False)

        result = calibrator.check_calibration(config)
        assert result.passed
        assert "Insufficient samples" in result.reason

    def test_empty_outcomes_passes(self, calibrator, config):
        """No recorded outcomes should pass."""
        result = calibrator.check_calibration(config)
        assert result.passed


# ---------- Reasoning Entropy ----------


class TestReasoningEntropyMonitor:

    @pytest.fixture
    def monitor(self):
        return ReasoningEntropyMonitor(max_history=20)

    @pytest.fixture
    def config(self):
        return GuardrailConfig()

    def test_reasoning_stagnation_detected(self, monitor, config):
        """Repeated similar reasoning should trigger stagnation warning."""
        # Add 10 nearly identical reasoning strings
        for i in range(10):
            monitor.record_reasoning(
                "Market conditions are stable. VIX is low. No action needed.",
                ["VIX low", "stable market"],
            )

        results = monitor.check_stagnation(config)
        assert any(not r.passed and "stagnation" in r.reason.lower() for r in results)

    def test_diverse_reasoning_passes(self, monitor, config):
        """Diverse reasoning strings should not trigger stagnation."""
        reasonings = [
            "VIX is elevated, monitoring closely for opportunities.",
            "AAPL position approaching profit target, considering close.",
            "Market just opened, waiting for initial volatility to settle.",
            "SPY gap down detected, reviewing staged candidates.",
            "End of day approaching, positions look healthy.",
            "New earnings data for MSFT changes the outlook.",
        ]
        for i, reasoning in enumerate(reasonings):
            monitor.record_reasoning(reasoning, [f"factor_{i}"])

        results = monitor.check_stagnation(config)
        assert all(r.passed for r in results)

    def test_low_unique_factors_flagged(self, monitor, config):
        """Low unique key_factors ratio should flag."""
        # 10 decisions all with the same 2 factors
        for _ in range(10):
            monitor.record_reasoning(
                f"Some varying reasoning text about things {_}.",
                ["VIX low", "stable"],
            )

        results = monitor.check_stagnation(config)
        factor_warnings = [r for r in results if "factor diversity" in r.reason]
        assert len(factor_warnings) >= 1

    def test_high_unique_factors_passes(self, monitor, config):
        """High unique key_factors ratio should pass."""
        factors = [
            ["VIX low", "trend up"],
            ["earnings upcoming", "IV elevated"],
            ["SPY bullish", "low DTE"],
            ["margin comfortable", "good premium"],
            ["delta safe", "theta positive"],
        ]
        for i, f in enumerate(factors):
            monitor.record_reasoning(f"Unique reasoning for decision {i}.", f)

        ratio = monitor.compute_unique_factors_ratio()
        assert ratio > 0.50

    def test_jaccard_similarity_identical(self, monitor):
        """Identical texts should have similarity 1.0."""
        sim = monitor._jaccard_similarity("hello world", "hello world")
        assert sim == 1.0

    def test_jaccard_similarity_disjoint(self, monitor):
        """Completely different texts should have similarity 0.0."""
        sim = monitor._jaccard_similarity("hello world", "foo bar baz")
        assert sim == 0.0

    def test_jaccard_similarity_partial(self, monitor):
        """Partially overlapping texts should have intermediate similarity."""
        sim = monitor._jaccard_similarity("hello world foo", "hello bar foo")
        assert 0.0 < sim < 1.0

    def test_empty_history_passes(self, monitor, config):
        """Empty history should pass."""
        results = monitor.check_stagnation(config)
        assert all(r.passed for r in results)

    def test_single_entry_passes(self, monitor, config):
        """Single entry should pass (need at least 2 for comparison)."""
        monitor.record_reasoning("Single reasoning entry.", ["factor_a"])
        results = monitor.check_stagnation(config)
        assert all(r.passed for r in results)
