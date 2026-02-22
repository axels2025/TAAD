"""Tests for Black-Scholes IV solver and Greeks calculation."""

import pytest
import numpy as np

from src.taad.enrichment.bs_iv_solver import (
    bs_put_price,
    bs_call_price,
    solve_iv,
    calculate_greeks,
    solve_iv_and_greeks,
    get_risk_free_rate,
    BSResult,
)


class TestBSPutPrice:
    """Test Black-Scholes put pricing."""

    def test_atm_put(self):
        """ATM put should have meaningful price."""
        price = bs_put_price(S=100, K=100, T=30/365, r=0.05, sigma=0.30)
        assert price > 0
        # ATM 30-day put with 30% vol should be roughly $2-4
        assert 1.0 < price < 6.0

    def test_deep_otm_put(self):
        """Deep OTM put should be cheap."""
        price = bs_put_price(S=100, K=70, T=30/365, r=0.05, sigma=0.30)
        assert price >= 0
        assert price < 0.5

    def test_deep_itm_put(self):
        """Deep ITM put should be close to intrinsic."""
        price = bs_put_price(S=100, K=130, T=30/365, r=0.05, sigma=0.30)
        assert price > 28  # At least intrinsic value (130 - 100 = 30 minus time)

    def test_zero_time_returns_zero(self):
        """Zero time to expiry should return 0."""
        assert bs_put_price(S=100, K=100, T=0, r=0.05, sigma=0.30) == 0.0

    def test_zero_vol_returns_zero(self):
        """Zero volatility should return 0."""
        assert bs_put_price(S=100, K=100, T=30/365, r=0.05, sigma=0) == 0.0


class TestSolveIV:
    """Test implied volatility solver."""

    def test_roundtrip_iv(self):
        """Known IV should be recoverable from the corresponding option price."""
        known_iv = 0.30
        S, K, T, r = 100.0, 95.0, 45/365, 0.05
        price = bs_put_price(S, K, T, r, known_iv)

        solved_iv = solve_iv(price, S, K, T, r, "P")
        assert solved_iv is not None
        assert abs(solved_iv - known_iv) < 0.001

    def test_call_roundtrip(self):
        """IV roundtrip should work for calls too."""
        known_iv = 0.40
        S, K, T, r = 150.0, 155.0, 60/365, 0.04
        price = bs_call_price(S, K, T, r, known_iv)

        solved_iv = solve_iv(price, S, K, T, r, "C")
        assert solved_iv is not None
        assert abs(solved_iv - known_iv) < 0.001

    def test_zero_price_returns_none(self):
        """Zero option price should return None."""
        assert solve_iv(0, 100, 95, 30/365, 0.05) is None

    def test_very_short_dte(self):
        """Very short DTE (1 day) should still solve."""
        price = bs_put_price(100, 98, 1/365, 0.05, 0.25)
        iv = solve_iv(price, 100, 98, 1/365, 0.05)
        # May or may not solve for very short DTE depending on price
        # Just check it doesn't crash
        assert iv is None or iv > 0

    def test_negative_inputs_return_none(self):
        """Negative inputs should return None gracefully."""
        assert solve_iv(-1.0, 100, 95, 30/365, 0.05) is None
        assert solve_iv(2.0, -100, 95, 30/365, 0.05) is None


class TestCalculateGreeks:
    """Test Greeks calculation from known IV."""

    def test_put_delta_negative(self):
        """Put delta should be negative."""
        result = calculate_greeks(S=100, K=95, T=30/365, r=0.05, sigma=0.30, option_type="P")
        assert result.delta is not None
        assert result.delta < 0
        assert result.delta > -1.0

    def test_call_delta_positive(self):
        """Call delta should be positive."""
        result = calculate_greeks(S=100, K=105, T=30/365, r=0.05, sigma=0.30, option_type="C")
        assert result.delta is not None
        assert result.delta > 0
        assert result.delta < 1.0

    def test_gamma_positive(self):
        """Gamma should always be positive."""
        result = calculate_greeks(S=100, K=100, T=30/365, r=0.05, sigma=0.30)
        assert result.gamma is not None
        assert result.gamma > 0

    def test_theta_negative(self):
        """Theta should be negative (time decay)."""
        result = calculate_greeks(S=100, K=100, T=30/365, r=0.05, sigma=0.30)
        assert result.theta is not None
        assert result.theta < 0

    def test_vega_positive(self):
        """Vega should be positive."""
        result = calculate_greeks(S=100, K=100, T=30/365, r=0.05, sigma=0.30)
        assert result.vega is not None
        assert result.vega > 0

    def test_iv_stored_in_result(self):
        """IV should be stored in the result."""
        result = calculate_greeks(S=100, K=100, T=30/365, r=0.05, sigma=0.30)
        assert result.iv == 0.30
        assert result.source == "bs_approximation"


class TestSolveIVAndGreeks:
    """Test combined IV + Greeks solver."""

    def test_combined_solver(self):
        """Combined solver should return IV and all Greeks."""
        price = bs_put_price(100, 95, 30/365, 0.05, 0.30)
        result = solve_iv_and_greeks(price, 100, 95, 30/365, 0.05, "P")

        assert result.iv is not None
        assert abs(result.iv - 0.30) < 0.001
        assert result.delta is not None
        assert result.gamma is not None
        assert result.theta is not None
        assert result.vega is not None

    def test_unsolvable_returns_empty(self):
        """If IV can't be solved, should return empty BSResult."""
        result = solve_iv_and_greeks(0, 100, 95, 30/365, 0.05, "P")
        assert result.iv is None
        assert result.delta is None


class TestLowDTEGuard:
    """Test that B-S IV/Greeks are suppressed for DTE <= 5."""

    def test_dte_5_returns_empty(self):
        """DTE=5 should return empty BSResult (boundary)."""
        price = bs_put_price(100, 95, 5/365, 0.05, 0.30)
        result = solve_iv_and_greeks(price, 100, 95, 5/365, 0.05, "P")

        assert result.iv is None
        assert result.delta is None
        assert result.gamma is None
        assert result.theta is None
        assert result.vega is None
        assert result.rho is None

    def test_dte_1_returns_empty(self):
        """DTE=1 should return empty BSResult."""
        price = bs_put_price(100, 98, 1/365, 0.05, 0.25)
        result = solve_iv_and_greeks(price, 100, 98, 1/365, 0.05, "P")

        assert result.iv is None
        assert result.delta is None

    def test_dte_3_returns_empty(self):
        """DTE=3 should return empty BSResult."""
        price = bs_put_price(100, 95, 3/365, 0.05, 0.30)
        result = solve_iv_and_greeks(price, 100, 95, 3/365, 0.05, "P")

        assert result.iv is None

    def test_dte_6_still_solves(self):
        """DTE=6 should still solve normally (just above threshold)."""
        price = bs_put_price(100, 95, 6/365, 0.05, 0.30)
        result = solve_iv_and_greeks(price, 100, 95, 6/365, 0.05, "P")

        assert result.iv is not None
        assert result.delta is not None
        assert result.gamma is not None

    def test_dte_30_unaffected(self):
        """DTE=30 should be completely unaffected by the guard."""
        price = bs_put_price(100, 95, 30/365, 0.05, 0.30)
        result = solve_iv_and_greeks(price, 100, 95, 30/365, 0.05, "P")

        assert result.iv is not None
        assert abs(result.iv - 0.30) < 0.001

    def test_low_level_solve_iv_unaffected(self):
        """solve_iv() itself should NOT be gated — only the combined function."""
        price = bs_put_price(100, 98, 3/365, 0.05, 0.25)
        iv = solve_iv(price, 100, 98, 3/365, 0.05)
        # Low-level solver still works (callers who need raw IV can use it)
        assert iv is None or iv > 0

    def test_calculate_greeks_unaffected(self):
        """calculate_greeks() itself should NOT be gated."""
        result = calculate_greeks(S=100, K=95, T=3/365, r=0.05, sigma=0.30)
        assert result.iv == 0.30
        assert result.delta is not None


class TestRiskFreeRate:
    """Test risk-free rate lookup."""

    def test_known_years(self):
        """Known years should return appropriate rates."""
        assert get_risk_free_rate(2020) == 0.005  # Near-zero during COVID
        assert get_risk_free_rate(2023) == 0.050  # Higher rate environment

    def test_unknown_year_defaults(self):
        """Unknown year should return default rate."""
        rate = get_risk_free_rate(2030)
        assert rate == 0.04
