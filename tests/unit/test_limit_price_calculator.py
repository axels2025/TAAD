"""Unit tests for LimitPriceCalculator.

Tests the centralized limit price calculation logic used throughout
the Sunday-to-Monday trading workflow.
"""

import pytest
from unittest.mock import patch

from src.services.limit_price_calculator import (
    LimitPriceCalculator,
    LimitPriceConfig,
    calculate_limit_price,
)


class TestLimitPriceConfig:
    """Tests for LimitPriceConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = LimitPriceConfig()
        assert config.bid_mid_ratio == 0.3
        assert config.adjustment_increment == 0.01
        assert config.max_adjustments == 2
        assert config.min_premium == 0.20

    def test_from_env_defaults(self):
        """Test loading from env with default values.

        LimitPriceConfig.from_env() now delegates to get_config() for
        shared values. We mock get_config to return a Config with
        known defaults, isolating this test from the .env file.
        """
        from unittest.mock import MagicMock

        mock_cfg = MagicMock()
        mock_cfg.price_adjustment_increment = 0.01
        mock_cfg.max_price_adjustments = 2
        mock_cfg.premium_floor = 0.20

        with patch(
            "src.config.base.get_config", return_value=mock_cfg
        ):
            config = LimitPriceConfig.from_env()
        assert config.bid_mid_ratio == 0.3
        assert config.adjustment_increment == 0.01

    def test_from_env_custom_values(self, monkeypatch):
        """Test loading custom values from env.

        adjustment_increment and max_adjustments now come from
        get_config(). min_premium maps to PREMIUM_FLOOR (the limit
        price floor), not PREMIUM_MIN.
        """
        monkeypatch.setenv("LIMIT_BID_MID_RATIO", "0.5")
        monkeypatch.setenv("PRICE_ADJUSTMENT_INCREMENT", "0.02")
        monkeypatch.setenv("MAX_PRICE_ADJUSTMENTS", "3")
        monkeypatch.setenv("PREMIUM_FLOOR", "0.25")

        config = LimitPriceConfig.from_env()
        assert config.bid_mid_ratio == 0.5
        assert config.adjustment_increment == 0.02
        assert config.max_adjustments == 3
        assert config.min_premium == 0.25


class TestLimitPriceCalculator:
    """Tests for LimitPriceCalculator class."""

    @pytest.fixture
    def calculator(self):
        """Create a calculator with default config."""
        return LimitPriceCalculator(config=LimitPriceConfig())

    @pytest.fixture
    def calculator_custom(self):
        """Create a calculator with custom config."""
        config = LimitPriceConfig(
            bid_mid_ratio=0.5,
            adjustment_increment=0.02,
            max_adjustments=3,
            min_premium=0.15,
        )
        return LimitPriceCalculator(config=config)

    # --- calculate_sell_limit tests ---

    def test_calculate_sell_limit_basic(self, calculator):
        """Test basic limit calculation."""
        # bid=0.45, ask=0.55, mid=0.50
        # limit = 0.45 + (0.50 - 0.45) * 0.3 = 0.45 + 0.015 = 0.465
        # Rounded to 0.47 (nearest penny, always rounds up for .5)
        result = calculator.calculate_sell_limit(0.45, 0.55)
        assert result == 0.47

    def test_calculate_sell_limit_tight_spread(self, calculator):
        """Test with tight bid-ask spread."""
        # bid=0.50, ask=0.52, mid=0.51
        # limit = 0.50 + (0.51 - 0.50) * 0.3 = 0.50 + 0.003 = 0.503
        result = calculator.calculate_sell_limit(0.50, 0.52)
        assert result == 0.50  # Rounds down to 0.50

    def test_calculate_sell_limit_wide_spread(self, calculator):
        """Test with wide bid-ask spread."""
        # bid=0.30, ask=0.50, mid=0.40
        # limit = 0.30 + (0.40 - 0.30) * 0.3 = 0.30 + 0.03 = 0.33
        result = calculator.calculate_sell_limit(0.30, 0.50)
        assert result == 0.33

    def test_calculate_sell_limit_never_below_bid(self, calculator):
        """Test that result is never below bid."""
        # Even with strange inputs, should not go below bid
        result = calculator.calculate_sell_limit(0.50, 0.51)
        assert result >= 0.50

    def test_calculate_sell_limit_custom_ratio(self, calculator_custom):
        """Test with custom ratio (0.5 = halfway to mid)."""
        # bid=0.40, ask=0.60, mid=0.50
        # limit = 0.40 + (0.50 - 0.40) * 0.5 = 0.40 + 0.05 = 0.45
        result = calculator_custom.calculate_sell_limit(0.40, 0.60)
        assert result == 0.45

    def test_calculate_sell_limit_invalid_spread(self, calculator):
        """Test that invalid spread raises error."""
        with pytest.raises(ValueError, match="Invalid spread"):
            calculator.calculate_sell_limit(0.55, 0.45)  # bid > ask

    def test_calculate_sell_limit_zero_bid(self, calculator):
        """Test handling of zero bid."""
        result = calculator.calculate_sell_limit(0.0, 0.10)
        assert result == 0.0

    # --- adjust_limit_for_fill tests ---

    def test_adjust_limit_first_adjustment(self, calculator):
        """Test first price adjustment."""
        # current_limit=0.47, adjustment=0.01
        # new_limit = 0.47 - 0.01 = 0.46
        result = calculator.adjust_limit_for_fill(0.47, 0.45, adjustment_number=1)
        assert result == 0.46

    def test_adjust_limit_second_adjustment(self, calculator):
        """Test second price adjustment."""
        result = calculator.adjust_limit_for_fill(0.46, 0.45, adjustment_number=2)
        assert result == 0.45

    def test_adjust_limit_max_exceeded(self, calculator):
        """Test that exceeding max adjustments returns None."""
        result = calculator.adjust_limit_for_fill(0.45, 0.44, adjustment_number=3)
        assert result is None

    def test_adjust_limit_never_below_bid(self, calculator):
        """Test that adjustment never goes below current bid."""
        # current_limit=0.46, bid=0.46
        # adjustment would give 0.45, but bid is 0.46
        result = calculator.adjust_limit_for_fill(0.46, 0.46, adjustment_number=1)
        assert result == 0.46

    def test_adjust_limit_below_min_premium(self, calculator):
        """Test that adjustment below min premium returns None."""
        # min_premium=0.20, trying to adjust to 0.19
        result = calculator.adjust_limit_for_fill(0.20, 0.15, adjustment_number=1)
        assert result is None

    def test_adjust_limit_custom_increment(self, calculator_custom):
        """Test adjustment with custom increment (0.02)."""
        result = calculator_custom.adjust_limit_for_fill(0.50, 0.45, adjustment_number=1)
        assert result == 0.48

    def test_adjust_limit_custom_max(self, calculator_custom):
        """Test adjustment with custom max (3)."""
        # Should allow 3rd adjustment
        result = calculator_custom.adjust_limit_for_fill(0.50, 0.40, adjustment_number=3)
        assert result == 0.48  # 0.50 - 0.02 = 0.48
        # But not 4th
        result = calculator_custom.adjust_limit_for_fill(0.48, 0.40, adjustment_number=4)
        assert result is None

    # --- calculate_premium_income tests ---

    def test_calculate_premium_income(self, calculator):
        """Test premium income calculation."""
        # limit=0.50, contracts=5
        # income = 0.50 * 100 * 5 = $250
        result = calculator.calculate_premium_income(0.50, 5)
        assert result == 250.0

    def test_calculate_premium_income_single_contract(self, calculator):
        """Test premium income for single contract."""
        result = calculator.calculate_premium_income(0.35, 1)
        assert result == 35.0

    # --- validate_limit_vs_bid tests ---

    def test_validate_limit_reasonable(self, calculator):
        """Test validation passes for reasonable limit."""
        # limit=0.48, bid=0.45 → 6.7% above bid (within 10%)
        assert calculator.validate_limit_vs_bid(0.48, 0.45) is True

    def test_validate_limit_too_high(self, calculator):
        """Test validation fails for limit too far above bid."""
        # limit=0.50, bid=0.45 → 11.1% above bid (exceeds 10%)
        assert calculator.validate_limit_vs_bid(0.50, 0.45) is False

    def test_validate_limit_custom_tolerance(self, calculator):
        """Test validation with custom tolerance."""
        # limit=0.50, bid=0.45 → 11.1% above bid
        # With 15% tolerance, should pass
        assert calculator.validate_limit_vs_bid(0.50, 0.45, tolerance=0.15) is True

    def test_validate_limit_zero_bid(self, calculator):
        """Test validation with zero bid."""
        assert calculator.validate_limit_vs_bid(0.50, 0.0) is False

    def test_validate_limit_at_bid(self, calculator):
        """Test validation when limit equals bid."""
        assert calculator.validate_limit_vs_bid(0.45, 0.45) is True

    # --- recalculate_from_fresh_quotes tests ---

    def test_recalculate_basic(self, calculator):
        """Test recalculation from fresh quotes."""
        new_limit, reason = calculator.recalculate_from_fresh_quotes(
            bid=0.42, ask=0.48
        )
        # mid = 0.45, limit = 0.42 + 0.03 * 0.3 = 0.429 → 0.43
        assert new_limit == 0.43
        assert "Calculated from fresh quotes" in reason

    def test_recalculate_with_original(self, calculator):
        """Test recalculation with original limit for comparison."""
        new_limit, reason = calculator.recalculate_from_fresh_quotes(
            bid=0.42, ask=0.48, original_limit=0.45
        )
        assert new_limit == 0.43
        assert "was $0.45" in reason
        assert "change:" in reason

    def test_recalculate_premium_increase(self, calculator):
        """Test recalculation when premium increased."""
        new_limit, reason = calculator.recalculate_from_fresh_quotes(
            bid=0.50, ask=0.56, original_limit=0.45
        )
        # mid = 0.53, limit = 0.50 + 0.03 * 0.3 = 0.509 → 0.51
        assert new_limit == 0.51
        assert "+" in reason  # Positive change


class TestCalculateLimitPriceFunction:
    """Tests for the module-level convenience function."""

    def test_convenience_function(self):
        """Test the calculate_limit_price convenience function."""
        # Uses default config
        result = calculate_limit_price(0.45, 0.55)
        # Should match calculator with defaults
        assert result == 0.47


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture
    def calculator(self):
        """Create a calculator with default config."""
        return LimitPriceCalculator()

    def test_very_small_spread(self, calculator):
        """Test with very small spread (1 cent)."""
        result = calculator.calculate_sell_limit(0.50, 0.51)
        assert result >= 0.50
        assert result <= 0.51

    def test_equal_bid_ask(self, calculator):
        """Test when bid equals ask (locked market)."""
        result = calculator.calculate_sell_limit(0.50, 0.50)
        assert result == 0.50

    def test_penny_stock_option(self, calculator):
        """Test with very low premium (penny stock option)."""
        result = calculator.calculate_sell_limit(0.05, 0.10)
        assert result >= 0.05

    def test_high_premium_option(self, calculator):
        """Test with high premium option."""
        result = calculator.calculate_sell_limit(5.00, 5.50)
        # mid = 5.25, limit = 5.00 + 0.25 * 0.3 = 5.075 → 5.08
        assert result == 5.08

    def test_rounding_consistency(self, calculator):
        """Test that rounding is consistent."""
        # Multiple calls with same input should give same result
        results = [
            calculator.calculate_sell_limit(0.45, 0.55)
            for _ in range(10)
        ]
        assert all(r == results[0] for r in results)
