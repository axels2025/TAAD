"""Tests for PositionSizer — fixed fractional position sizing."""

import pytest

from src.services.position_sizer import PositionSizer


class TestPositionSizer:
    """Test risk-based position sizing."""

    def test_expensive_stock_limits_to_one_contract(self):
        """$500 stock, $100K account, 2% risk → 1 contract (not 3).

        max_risk = $100,000 * 0.02 = $2,000
        practical_max_loss = $500 * 0.25 * 100 = $12,500/contract
        risk_based = floor(2000 / 12500) = 0, minimum 1
        final = min(3, 1) = 1
        """
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=500.0, price_based_max=3)
        assert result == 1

    def test_cheap_stock_allows_two_contracts(self):
        """$30 stock, $100K account, 2% risk → 2 contracts (not 5).

        max_risk = $100,000 * 0.02 = $2,000
        practical_max_loss = $30 * 0.25 * 100 = $750/contract
        risk_based = floor(2000 / 750) = 2
        final = min(5, 2) = 2
        """
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=30.0, price_based_max=5)
        assert result == 2

    def test_moderate_stock_risk_based_lower(self):
        """$200 stock, $100K account, 2% risk → 1 contract (not 3).

        max_risk = $2,000
        practical_max_loss = $200 * 0.25 * 100 = $5,000/contract
        risk_based = floor(2000 / 5000) = 0, minimum 1
        final = min(3, 1) = 1
        """
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=200.0, price_based_max=3)
        assert result == 1

    def test_price_based_max_is_respected(self):
        """Even with large account, price-based max caps the result.

        $50 stock, $1M account, 2% risk:
        risk_based = floor(20000 / 1250) = 16
        final = min(5, 16) = 5 (price-based wins)
        """
        sizer = PositionSizer(account_equity=1_000_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=50.0, price_based_max=5)
        assert result == 5

    def test_minimum_one_contract(self):
        """Even if risk says 0, always trade at least 1.

        $500 stock, $10K account, 2% risk:
        max_risk = $200
        practical_max_loss = $12,500
        risk_based = floor(200 / 12500) = 0
        final = max(1, min(3, 0)) → max(1, 0) → 1
        """
        sizer = PositionSizer(account_equity=10_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=500.0, price_based_max=3)
        assert result == 1

    def test_larger_account_allows_more(self):
        """$100 stock, $500K account, 2% risk → 4 contracts.

        max_risk = $10,000
        practical_max_loss = $100 * 0.25 * 100 = $2,500/contract
        risk_based = floor(10000 / 2500) = 4
        final = min(5, 4) = 4
        """
        sizer = PositionSizer(account_equity=500_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=100.0, price_based_max=5)
        assert result == 4

    def test_custom_risk_pct(self):
        """Custom 5% risk allows more contracts.

        $100 stock, $100K account, 5% risk:
        max_risk = $5,000
        practical_max_loss = $2,500/contract
        risk_based = floor(5000 / 2500) = 2
        final = min(5, 2) = 2
        """
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.05)
        result = sizer.calculate_contracts(strike=100.0, price_based_max=5)
        assert result == 2

    def test_zero_strike_uses_price_based(self):
        """Zero strike falls back to price-based max."""
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=0.0, price_based_max=5)
        assert result == 5

    def test_negative_strike_uses_price_based(self):
        """Negative strike falls back to price-based max."""
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=-10.0, price_based_max=3)
        assert result == 3


class TestVIXScaling:
    """Test VIX-aware position sizing."""

    def test_vix_scaling_factor_low_vol(self):
        """VIX < 15 returns 1.0 (full sizing)."""
        assert PositionSizer.get_vix_scaling_factor(12.0) == 1.0

    def test_vix_scaling_factor_normal(self):
        """VIX 15-25 returns 0.80 (20% reduction)."""
        assert PositionSizer.get_vix_scaling_factor(20.0) == 0.80

    def test_vix_scaling_factor_elevated(self):
        """VIX 25-35 returns 0.50 (50% reduction)."""
        assert PositionSizer.get_vix_scaling_factor(30.0) == 0.50

    def test_vix_scaling_factor_extreme(self):
        """VIX >= 35 returns 0.25 (75% reduction)."""
        assert PositionSizer.get_vix_scaling_factor(38.0) == 0.25

    def test_vix_scaling_factor_boundary_15(self):
        """VIX exactly 15 → normal tier (0.80)."""
        assert PositionSizer.get_vix_scaling_factor(15.0) == 0.80

    def test_vix_scaling_factor_boundary_25(self):
        """VIX exactly 25 → elevated tier (0.50)."""
        assert PositionSizer.get_vix_scaling_factor(25.0) == 0.50

    def test_vix_scaling_factor_boundary_35(self):
        """VIX exactly 35 → extreme tier (0.25)."""
        assert PositionSizer.get_vix_scaling_factor(35.0) == 0.25

    def test_vix_halt_returns_zero(self):
        """VIX >= 40 halts all new positions (returns 0 contracts)."""
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=50.0, price_based_max=5, vix=42.0)
        assert result == 0

    def test_vix_halt_at_exact_threshold(self):
        """VIX exactly at halt threshold (40.0) returns 0."""
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=50.0, price_based_max=5, vix=40.0)
        assert result == 0

    def test_vix_none_no_adjustment(self):
        """vix=None means no VIX adjustment applied."""
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        # Without VIX: $30 strike → 2 contracts (same as existing test)
        result = sizer.calculate_contracts(strike=30.0, price_based_max=5, vix=None)
        assert result == 2

    def test_vix_reduces_contracts(self):
        """Elevated VIX reduces contract count.

        $50 stock, $1M account, 5% risk:
        risk_based = floor(50000 / 1250) = 40
        final = min(5, 40) = 5
        VIX=30 → factor=0.50 → floor(5 * 0.50) = 2
        """
        sizer = PositionSizer(account_equity=1_000_000, max_risk_pct=0.05)
        result = sizer.calculate_contracts(strike=50.0, price_based_max=5, vix=30.0)
        assert result == 2

    def test_vix_scaling_minimum_one(self):
        """Even with VIX scaling, minimum is 1 contract (not 0).

        $500 stock, $100K account, 2% risk:
        risk_based = floor(2000 / 12500) = 0, minimum 1
        VIX=30 → factor=0.50 → floor(1 * 0.50) = 0 → max(1, 0) = 1
        """
        sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        result = sizer.calculate_contracts(strike=500.0, price_based_max=3, vix=30.0)
        assert result == 1

    def test_low_vix_no_reduction(self):
        """VIX < 15 applies factor 1.0 — no change to contracts."""
        sizer = PositionSizer(account_equity=1_000_000, max_risk_pct=0.02)
        without_vix = sizer.calculate_contracts(strike=50.0, price_based_max=5)
        with_low_vix = sizer.calculate_contracts(strike=50.0, price_based_max=5, vix=12.0)
        assert without_vix == with_low_vix
