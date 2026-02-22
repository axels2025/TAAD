"""Unit tests for scoring rules."""

import pytest

from src.scoring.score_rules import (
    score_capital_efficiency,
    score_iv_rank,
    score_liquidity,
    score_probability,
    score_risk_adjusted_return,
    score_safety_buffer,
)


class TestScoreRiskAdjustedReturn:
    """Test risk-adjusted return scoring."""

    def test_optimal_range_30_50(self):
        """Test optimal range 30-50% returns perfect score."""
        assert score_risk_adjusted_return(0.30) == 100.0
        assert score_risk_adjusted_return(0.40) == 100.0
        assert score_risk_adjusted_return(0.50) == 100.0

    def test_good_range_50_75(self):
        """Test 50-75% range scores 90."""
        assert score_risk_adjusted_return(0.60) == 90.0
        assert score_risk_adjusted_return(0.75) == 90.0

    def test_conservative_range_25_30(self):
        """Test conservative 25-30% range scores 85."""
        assert score_risk_adjusted_return(0.25) == 85.0
        assert score_risk_adjusted_return(0.29) == 85.0

    def test_high_risk_100_150(self):
        """Test 100-150% range scores 60 (risky)."""
        assert score_risk_adjusted_return(1.00) == 60.0
        assert score_risk_adjusted_return(1.50) == 60.0

    def test_extreme_over_200(self):
        """Test >200% scores 20 (hidden risk)."""
        assert score_risk_adjusted_return(2.01) == 20.0
        assert score_risk_adjusted_return(3.00) == 20.0

    def test_low_under_10(self):
        """Test <10% scores 10 (poor)."""
        assert score_risk_adjusted_return(0.05) == 10.0
        assert score_risk_adjusted_return(0.09) == 10.0


class TestScoreProbability:
    """Test probability scoring."""

    def test_excellent_90_plus(self):
        """Test >=90% probability scores 100."""
        assert score_probability(0.90) == 100.0
        assert score_probability(0.95) == 100.0

    def test_very_good_85_90(self):
        """Test 85-90% range scores 90."""
        assert score_probability(0.85) == 90.0
        assert score_probability(0.89) == 90.0

    def test_good_80_85(self):
        """Test 80-85% range scores 80."""
        assert score_probability(0.80) == 80.0
        assert score_probability(0.84) == 80.0

    def test_acceptable_75_80(self):
        """Test 75-80% range scores 65."""
        assert score_probability(0.75) == 65.0
        assert score_probability(0.79) == 65.0

    def test_poor_under_60(self):
        """Test <60% scores 10 (near coin-flip)."""
        assert score_probability(0.55) == 10.0
        assert score_probability(0.50) == 10.0


class TestScoreIvRank:
    """Test IV rank scoring."""

    def test_optimal_60_80(self):
        """Test optimal 60-80% IV rank scores 100."""
        assert score_iv_rank(0.60) == 100.0
        assert score_iv_rank(0.70) == 100.0
        assert score_iv_rank(0.80) == 100.0

    def test_very_high_over_80(self):
        """Test >80% IV rank scores 90."""
        assert score_iv_rank(0.85) == 90.0
        assert score_iv_rank(0.95) == 90.0

    def test_good_50_60(self):
        """Test 50-60% range scores 85."""
        assert score_iv_rank(0.50) == 85.0
        assert score_iv_rank(0.59) == 85.0

    def test_moderate_40_50(self):
        """Test 40-50% range scores 70."""
        assert score_iv_rank(0.40) == 70.0
        assert score_iv_rank(0.49) == 70.0

    def test_poor_under_20(self):
        """Test <20% scores 15 (poor selling environment)."""
        assert score_iv_rank(0.15) == 15.0
        assert score_iv_rank(0.10) == 15.0


class TestScoreLiquidity:
    """Test liquidity scoring."""

    def test_excellent_both_high(self):
        """Test excellent liquidity (OI 5000+, Vol 500+) scores 100."""
        assert score_liquidity(5000, 500) == 100.0
        assert score_liquidity(10000, 1000) == 100.0

    def test_good_both_adequate(self):
        """Test good liquidity (OI 2000+, Vol 200+) scores 80."""
        assert score_liquidity(2000, 200) == 80.0
        assert score_liquidity(2500, 250) == 80.0

    def test_minimum_of_two_scores(self):
        """Test that lower score dominates (safety rule)."""
        # High OI, low volume -> constrained by volume
        assert score_liquidity(5000, 50) == 45.0  # Volume score

        # Low OI, high volume -> constrained by OI
        assert score_liquidity(300, 500) == 25.0  # OI score

    def test_poor_both_low(self):
        """Test poor liquidity (OI <250, Vol <25) scores 10."""
        assert score_liquidity(200, 20) == 10.0
        assert score_liquidity(100, 10) == 10.0


class TestScoreCapitalEfficiency:
    """Test capital efficiency scoring."""

    def test_excellent_3_percent_plus(self):
        """Test >=3% efficiency scores 100."""
        # 3% of strike
        assert score_capital_efficiency(3.0, 100.0) == 100.0
        assert score_capital_efficiency(5.0, 100.0) == 100.0

    def test_good_2_to_2_5_percent(self):
        """Test 2-2.5% range scores 80."""
        assert score_capital_efficiency(2.0, 100.0) == 80.0
        assert score_capital_efficiency(2.4, 100.0) == 80.0

    def test_acceptable_1_to_1_5_percent(self):
        """Test 1-1.5% range scores 50."""
        assert score_capital_efficiency(1.0, 100.0) == 50.0
        assert score_capital_efficiency(1.4, 100.0) == 50.0

    def test_poor_under_0_5_percent(self):
        """Test <0.5% scores 15 (very poor)."""
        assert score_capital_efficiency(0.3, 100.0) == 15.0
        assert score_capital_efficiency(0.1, 100.0) == 15.0

    def test_zero_strike_handling(self):
        """Test zero strike returns zero score."""
        assert score_capital_efficiency(1.0, 0.0) == 15.0


class TestScoreSafetyBuffer:
    """Test safety buffer (OTM distance) scoring."""

    def test_optimal_12_18_otm(self):
        """Test optimal 12-18% OTM scores 100."""
        # Barchart moneyness is negative for OTM puts
        assert score_safety_buffer(-0.12) == 100.0
        assert score_safety_buffer(-0.15) == 100.0
        assert score_safety_buffer(-0.18) == 100.0

    def test_conservative_18_22_otm(self):
        """Test 18-22% OTM scores 90."""
        assert score_safety_buffer(-0.20) == 90.0
        assert score_safety_buffer(-0.22) == 90.0

    def test_good_10_12_otm(self):
        """Test 10-12% OTM scores 85."""
        assert score_safety_buffer(-0.10) == 85.0
        assert score_safety_buffer(-0.11) == 85.0

    def test_thin_buffer_5_8_otm(self):
        """Test 5-8% OTM scores 40 (thin buffer)."""
        assert score_safety_buffer(-0.05) == 40.0
        assert score_safety_buffer(-0.07) == 40.0

    def test_danger_under_5_otm(self):
        """Test <5% OTM scores 15 (near ATM danger)."""
        assert score_safety_buffer(-0.03) == 15.0
        assert score_safety_buffer(-0.01) == 15.0

    def test_too_far_over_28_otm(self):
        """Test >28% OTM scores 30 (too far, minimal premium)."""
        assert score_safety_buffer(-0.30) == 30.0
        assert score_safety_buffer(-0.35) == 30.0


class TestScoringEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_exact_boundary_values(self):
        """Test exact boundary values are handled correctly."""
        # Test exact boundary for return scoring
        assert score_risk_adjusted_return(0.30) == 100.0  # Exact lower bound
        assert score_risk_adjusted_return(0.50) == 100.0  # Exact upper bound

        # Test exact boundary for probability
        assert score_probability(0.85) == 90.0
        assert score_probability(0.90) == 100.0

    def test_negative_values_handled(self):
        """Test negative values are handled gracefully."""
        # Negative return should score low
        assert score_risk_adjusted_return(-0.10) == 10.0

        # Negative probability should score low
        assert score_probability(-0.05) == 10.0

    def test_extreme_high_values(self):
        """Test extreme high values are handled."""
        # Very high return (500% annualized)
        assert score_risk_adjusted_return(5.0) == 20.0

        # Very high IV rank (150%)
        assert score_iv_rank(1.5) == 90.0
