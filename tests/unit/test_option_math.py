"""Tests for src/utils/option_math — direction-aware OTM/ITM calculations."""

import pytest

from src.utils.option_math import (
    calc_otm_pct,
    calc_otm_dollars,
    is_itm,
    is_otm_strike,
    max_otm_strike,
)


class TestCalcOtmPct:
    """OTM percentage calculations for puts and calls."""

    def test_put_otm(self):
        """PUT with strike below stock is OTM (positive)."""
        result = calc_otm_pct(stock_price=100.0, strike=85.0, option_type="PUT")
        assert result == pytest.approx(0.15)

    def test_put_itm(self):
        """PUT with strike above stock is ITM (negative)."""
        result = calc_otm_pct(stock_price=100.0, strike=110.0, option_type="PUT")
        assert result == pytest.approx(-0.10)

    def test_put_atm(self):
        """PUT with strike equal to stock is ATM (zero)."""
        result = calc_otm_pct(stock_price=100.0, strike=100.0, option_type="PUT")
        assert result == pytest.approx(0.0)

    def test_call_otm(self):
        """CALL with strike above stock is OTM (positive)."""
        result = calc_otm_pct(stock_price=100.0, strike=115.0, option_type="CALL")
        assert result == pytest.approx(0.15)

    def test_call_itm(self):
        """CALL with strike below stock is ITM (negative)."""
        result = calc_otm_pct(stock_price=100.0, strike=90.0, option_type="CALL")
        assert result == pytest.approx(-0.10)

    def test_call_atm(self):
        """CALL with strike equal to stock is ATM (zero)."""
        result = calc_otm_pct(stock_price=100.0, strike=100.0, option_type="CALL")
        assert result == pytest.approx(0.0)

    def test_accepts_short_form(self):
        """Accepts 'P' and 'C' as well as 'PUT' and 'CALL'."""
        assert calc_otm_pct(100.0, 85.0, "P") == pytest.approx(0.15)
        assert calc_otm_pct(100.0, 115.0, "C") == pytest.approx(0.15)

    def test_zero_stock_price(self):
        """Returns 0.0 when stock price is zero."""
        assert calc_otm_pct(0.0, 50.0, "PUT") == 0.0

    def test_invalid_option_type(self):
        """Raises ValueError for unknown option type."""
        with pytest.raises(ValueError, match="Unknown option_type"):
            calc_otm_pct(100.0, 85.0, "STRADDLE")

    def test_alab_call_scenario(self):
        """Real scenario: ALAB $140 CALL with stock at $120.51.

        The old put-only formula gave -16.2% (ITM), but this call
        is actually 16.2% OTM (stock below strike).
        """
        result = calc_otm_pct(stock_price=120.51, strike=140.0, option_type="CALL")
        assert result > 0.15  # Should be ~16.2% OTM, not ITM


class TestCalcOtmDollars:
    """Dollar-based OTM calculations."""

    def test_put_otm_dollars(self):
        result = calc_otm_dollars(stock_price=100.0, strike=85.0, option_type="PUT")
        assert result == pytest.approx(15.0)

    def test_call_otm_dollars(self):
        result = calc_otm_dollars(stock_price=100.0, strike=115.0, option_type="CALL")
        assert result == pytest.approx(15.0)


class TestIsItm:
    """ITM detection for puts and calls."""

    def test_put_itm_when_stock_below_strike(self):
        assert is_itm(stock_price=90.0, strike=100.0, option_type="PUT") is True

    def test_put_not_itm_when_stock_above_strike(self):
        assert is_itm(stock_price=110.0, strike=100.0, option_type="PUT") is False

    def test_call_itm_when_stock_above_strike(self):
        assert is_itm(stock_price=110.0, strike=100.0, option_type="CALL") is True

    def test_call_not_itm_when_stock_below_strike(self):
        assert is_itm(stock_price=90.0, strike=100.0, option_type="CALL") is False


class TestMaxOtmStrike:
    """Strike boundary calculation."""

    def test_put_max_strike(self):
        """For puts, boundary is below stock price."""
        result = max_otm_strike(stock_price=100.0, min_otm_pct=0.15, option_type="PUT")
        assert result == pytest.approx(85.0)

    def test_call_min_strike(self):
        """For calls, boundary is above stock price."""
        result = max_otm_strike(stock_price=100.0, min_otm_pct=0.15, option_type="CALL")
        assert result == pytest.approx(115.0)


class TestIsOtmStrike:
    """Strike OTM validation."""

    def test_put_strike_sufficiently_otm(self):
        assert is_otm_strike(100.0, 80.0, 0.15, "PUT") is True

    def test_put_strike_not_otm_enough(self):
        assert is_otm_strike(100.0, 90.0, 0.15, "PUT") is False

    def test_call_strike_sufficiently_otm(self):
        assert is_otm_strike(100.0, 120.0, 0.15, "CALL") is True

    def test_call_strike_not_otm_enough(self):
        assert is_otm_strike(100.0, 110.0, 0.15, "CALL") is False
