"""Unit tests for price deviation validation.

Tests the PriceDeviationValidator class including deviation calculations,
staleness checks, and combined validation.
"""

from datetime import datetime, timedelta

import pytest

from src.validation.price_deviation import (
    PriceDeviationCheck,
    PriceDeviationValidator,
    StalenessCheck,
)


class TestPriceDeviationCheck:
    """Tests for PriceDeviationCheck dataclass."""

    def test_create_deviation_check(self):
        """Test creating a PriceDeviationCheck."""
        check = PriceDeviationCheck(
            passed=True,
            current_price=100.0,
            original_price=98.0,
            deviation_pct=0.0204,
            deviation_amount=2.0,
            limit_pct=0.03,
            message="Price acceptable",
            warning=None,
        )

        assert check.passed is True
        assert check.current_price == 100.0
        assert check.original_price == 98.0
        assert check.deviation_pct == 0.0204
        assert check.deviation_amount == 2.0
        assert check.limit_pct == 0.03
        assert check.message == "Price acceptable"
        assert check.warning is None

    def test_deviation_check_with_warning(self):
        """Test deviation check with warning."""
        check = PriceDeviationCheck(
            passed=True,
            current_price=102.5,
            original_price=100.0,
            deviation_pct=0.025,
            deviation_amount=2.5,
            limit_pct=0.03,
            message="Price near limit",
            warning="Approaching max deviation",
        )

        assert check.passed is True
        assert check.warning is not None
        assert "Approaching" in check.warning


class TestStalenessCheck:
    """Tests for StalenessCheck dataclass."""

    def test_create_staleness_check(self):
        """Test creating a StalenessCheck."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 27, 12, 0, 0)

        check = StalenessCheck(
            passed=True,
            age_hours=2.0,
            limit_hours=24.0,
            created_at=created,
            checked_at=checked,
            message="Recent: 2.0 hours old",
        )

        assert check.passed is True
        assert check.age_hours == 2.0
        assert check.limit_hours == 24.0
        assert check.created_at == created
        assert check.checked_at == checked
        assert "Recent" in check.message


class TestPriceDeviationValidator:
    """Tests for PriceDeviationValidator class."""

    @pytest.fixture
    def validator(self):
        """Create a validator with default settings."""
        return PriceDeviationValidator(
            max_deviation_pct=0.03,  # 3%
            manual_staleness_hours=24.0,
        )

    @pytest.fixture
    def strict_validator(self):
        """Create a validator with strict settings."""
        return PriceDeviationValidator(
            max_deviation_pct=0.01,  # 1%
            manual_staleness_hours=4.0,
        )

    def test_validator_initialization(self, validator):
        """Test validator initializes with correct settings."""
        assert validator.max_deviation_pct == 0.03
        assert validator.manual_staleness_hours == 24.0

    def test_validator_custom_settings(self):
        """Test validator with custom settings."""
        validator = PriceDeviationValidator(
            max_deviation_pct=0.05,
            manual_staleness_hours=48.0,
        )

        assert validator.max_deviation_pct == 0.05
        assert validator.manual_staleness_hours == 48.0


class TestDeviationCalculation:
    """Tests for deviation calculation logic."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance."""
        return PriceDeviationValidator(max_deviation_pct=0.03)

    def test_positive_deviation_within_limit(self, validator):
        """Test positive deviation within acceptable limit."""
        # Price moved up 2%
        check = validator.check_deviation(
            current_price=102.0,
            original_price=100.0,
        )

        assert check.passed is True
        assert check.current_price == 102.0
        assert check.original_price == 100.0
        assert check.deviation_pct == 0.02
        assert check.deviation_amount == 2.0
        assert check.limit_pct == 0.03
        assert "up" in check.message

    def test_negative_deviation_within_limit(self, validator):
        """Test negative deviation within acceptable limit."""
        # Price moved down 2%
        check = validator.check_deviation(
            current_price=98.0,
            original_price=100.0,
        )

        assert check.passed is True
        assert check.current_price == 98.0
        assert check.original_price == 100.0
        assert check.deviation_pct == -0.02
        assert check.deviation_amount == -2.0
        assert check.limit_pct == 0.03
        assert "down" in check.message

    def test_positive_deviation_exceeds_limit(self, validator):
        """Test positive deviation exceeds limit."""
        # Price moved up 4% (exceeds 3% limit)
        check = validator.check_deviation(
            current_price=104.0,
            original_price=100.0,
        )

        assert check.passed is False
        assert check.deviation_pct == 0.04
        assert "too much" in check.message.lower()
        assert "exceeds" in check.message.lower()

    def test_negative_deviation_exceeds_limit(self, validator):
        """Test negative deviation exceeds limit."""
        # Price moved down 4% (exceeds 3% limit)
        check = validator.check_deviation(
            current_price=96.0,
            original_price=100.0,
        )

        assert check.passed is False
        assert check.deviation_pct == -0.04
        assert "too much" in check.message.lower()

    def test_no_deviation(self, validator):
        """Test when price hasn't moved."""
        check = validator.check_deviation(
            current_price=100.0,
            original_price=100.0,
        )

        assert check.passed is True
        assert check.deviation_pct == 0.0
        assert check.deviation_amount == 0.0
        assert "stable" in check.message.lower()

    def test_small_deviation(self, validator):
        """Test very small deviation (less than 1%)."""
        check = validator.check_deviation(
            current_price=100.5,
            original_price=100.0,
        )

        assert check.passed is True
        assert check.deviation_pct == 0.005
        assert "stable" in check.message.lower()
        assert check.warning is None

    def test_deviation_near_limit(self, validator):
        """Test deviation approaching limit (>80% of max)."""
        # 2.5% is >80% of 3% limit
        check = validator.check_deviation(
            current_price=102.5,
            original_price=100.0,
        )

        assert check.passed is True
        assert check.deviation_pct == 0.025
        assert "near limit" in check.message.lower()
        assert check.warning is not None
        assert "approaching" in check.warning.lower()

    def test_deviation_override_limit(self, validator):
        """Test overriding default deviation limit."""
        # 4% deviation with 5% override limit
        check = validator.check_deviation(
            current_price=104.0,
            original_price=100.0,
            max_deviation_pct=0.05,
        )

        assert check.passed is True
        assert check.limit_pct == 0.05

    def test_deviation_with_zero_original_price(self, validator):
        """Test handling of zero original price."""
        check = validator.check_deviation(
            current_price=100.0,
            original_price=0.0,
        )

        assert check.deviation_pct == 0.0


class TestStalenessChecking:
    """Tests for staleness checking logic."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance."""
        return PriceDeviationValidator(manual_staleness_hours=24.0)

    def test_fresh_opportunity(self, validator):
        """Test opportunity less than 1 hour old."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 27, 10, 30, 0)

        check = validator.check_staleness(created, checked)

        assert check.passed is True
        assert check.age_hours == 0.5
        assert "fresh" in check.message.lower()
        assert "minutes" in check.message.lower()

    def test_recent_opportunity(self, validator):
        """Test opportunity a few hours old."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 27, 15, 0, 0)

        check = validator.check_staleness(created, checked)

        assert check.passed is True
        assert check.age_hours == 5.0
        assert "recent" in check.message.lower()

    def test_aging_opportunity(self, validator):
        """Test opportunity approaching age limit."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 28, 6, 0, 0)  # 20 hours (>80% of 24)

        check = validator.check_staleness(created, checked)

        assert check.passed is True
        assert check.age_hours == 20.0
        assert "aging" in check.message.lower()
        assert "approaching" in check.message.lower()

    def test_too_old_opportunity(self, validator):
        """Test opportunity exceeding age limit."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 28, 12, 0, 0)  # 26 hours

        check = validator.check_staleness(created, checked)

        assert check.passed is False
        assert check.age_hours == 26.0
        assert "too old" in check.message.lower()
        assert "exceeds" in check.message.lower()

    def test_staleness_at_limit(self, validator):
        """Test opportunity exactly at age limit."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 28, 10, 0, 0)  # Exactly 24 hours

        check = validator.check_staleness(created, checked)

        assert check.passed is True
        assert check.age_hours == 24.0

    def test_staleness_default_checked_at(self, validator):
        """Test staleness with default checked_at (now)."""
        # Create opportunity 5 hours ago
        created = datetime.now() - timedelta(hours=5)

        check = validator.check_staleness(created)

        assert check.passed is True
        assert 4.9 <= check.age_hours <= 5.1  # Allow small variance

    def test_staleness_override_limit(self, validator):
        """Test overriding default staleness limit."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 27, 14, 0, 0)  # 4 hours

        # 4 hours would fail with 2-hour limit
        check = validator.check_staleness(created, checked, max_age_hours=2.0)

        assert check.passed is False
        assert check.limit_hours == 2.0

    def test_staleness_with_fractional_hours(self, validator):
        """Test staleness with fractional hours."""
        created = datetime(2026, 1, 27, 10, 0, 0)
        checked = datetime(2026, 1, 27, 12, 30, 0)  # 2.5 hours

        check = validator.check_staleness(created, checked)

        assert check.passed is True
        assert check.age_hours == 2.5


class TestValidateOpportunity:
    """Tests for combined validation."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance."""
        return PriceDeviationValidator(
            max_deviation_pct=0.03,
            manual_staleness_hours=24.0,
        )

    def test_validate_manual_trade_all_pass(self, validator):
        """Test manual trade validation when all checks pass."""
        created = datetime.now() - timedelta(hours=1)

        passed, messages = validator.validate_opportunity(
            current_price=101.0,
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        assert passed is True
        assert len(messages) == 2  # Deviation + staleness
        assert any("✓" in msg for msg in messages)

    def test_validate_manual_trade_deviation_fails(self, validator):
        """Test manual trade validation when deviation fails."""
        created = datetime(2026, 1, 27, 10, 0, 0)

        passed, messages = validator.validate_opportunity(
            current_price=105.0,
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        assert passed is False
        assert any("❌" in msg for msg in messages)
        assert any("too much" in msg.lower() for msg in messages)

    def test_validate_manual_trade_staleness_fails(self, validator):
        """Test manual trade validation when staleness fails."""
        created = datetime(2026, 1, 26, 10, 0, 0)  # 26 hours ago

        passed, messages = validator.validate_opportunity(
            current_price=101.0,
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        assert passed is False
        assert any("❌" in msg for msg in messages)
        assert any("too old" in msg.lower() for msg in messages)

    def test_validate_manual_trade_both_fail(self, validator):
        """Test manual trade validation when both checks fail."""
        created = datetime(2026, 1, 26, 10, 0, 0)  # Too old

        passed, messages = validator.validate_opportunity(
            current_price=105.0,  # Too much deviation
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        assert passed is False
        # Should have 2 failure messages
        failure_count = sum(1 for msg in messages if "❌" in msg)
        assert failure_count == 2

    def test_validate_barchart_source(self, validator):
        """Test validation for barchart source (no staleness check)."""
        created = datetime(2026, 1, 26, 10, 0, 0)  # Old but shouldn't matter

        passed, messages = validator.validate_opportunity(
            current_price=101.0,
            original_price=100.0,
            created_at=created,
            source="barchart",
        )

        assert passed is True
        assert len(messages) == 1  # Only deviation check
        assert not any("aging" in msg.lower() for msg in messages)
        assert not any("old" in msg.lower() for msg in messages)

    def test_validate_with_warning(self, validator):
        """Test validation that passes but includes warning."""
        created = datetime.now() - timedelta(hours=1)

        # 2.5% deviation is >80% of 3% limit
        passed, messages = validator.validate_opportunity(
            current_price=102.5,
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        assert passed is True
        assert any("⚠️" in msg for msg in messages)
        assert any("approaching" in msg.lower() for msg in messages)

    def test_validate_stable_price(self, validator):
        """Test validation with minimal price movement."""
        created = datetime.now() - timedelta(hours=1)

        passed, messages = validator.validate_opportunity(
            current_price=100.2,
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        assert passed is True
        assert any("stable" in msg.lower() for msg in messages)


class TestDifferentSources:
    """Tests for different opportunity sources."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance."""
        return PriceDeviationValidator(
            max_deviation_pct=0.03,
            manual_staleness_hours=24.0,
        )

    def test_manual_source_checks_staleness(self, validator):
        """Test that manual source checks staleness."""
        created = datetime.now() - timedelta(hours=30)

        passed, messages = validator.validate_opportunity(
            current_price=100.0,
            original_price=100.0,
            created_at=created,
            source="manual",
        )

        # Should fail due to staleness
        assert passed is False
        assert any("old" in msg.lower() for msg in messages)

    def test_barchart_source_skips_staleness(self, validator):
        """Test that barchart source skips staleness check."""
        created = datetime.now() - timedelta(hours=30)

        passed, messages = validator.validate_opportunity(
            current_price=100.0,
            original_price=100.0,
            created_at=created,
            source="barchart",
        )

        # Should pass (no staleness check)
        assert passed is True
        assert len(messages) == 1  # Only deviation message

    def test_scanner_source_skips_staleness(self, validator):
        """Test that scanner source skips staleness check."""
        created = datetime.now() - timedelta(days=7)  # Very old

        passed, messages = validator.validate_opportunity(
            current_price=100.0,
            original_price=100.0,
            created_at=created,
            source="scanner",
        )

        # Should pass (no staleness check)
        assert passed is True

    def test_custom_source_skips_staleness(self, validator):
        """Test that custom/unknown source skips staleness check."""
        created = datetime.now() - timedelta(days=7)

        passed, messages = validator.validate_opportunity(
            current_price=100.0,
            original_price=100.0,
            created_at=created,
            source="custom_api",
        )

        # Should pass (no staleness check)
        assert passed is True


class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    @pytest.fixture
    def validator(self):
        """Create a validator instance."""
        return PriceDeviationValidator(max_deviation_pct=0.03)

    def test_exactly_at_deviation_limit(self, validator):
        """Test deviation exactly at limit."""
        check = validator.check_deviation(
            current_price=103.0,
            original_price=100.0,
        )

        assert check.passed is True
        assert check.deviation_pct == 0.03

    def test_slightly_over_deviation_limit(self, validator):
        """Test deviation just over limit."""
        check = validator.check_deviation(
            current_price=103.01,
            original_price=100.0,
        )

        assert check.passed is False

    def test_very_large_deviation(self, validator):
        """Test very large price deviation."""
        check = validator.check_deviation(
            current_price=150.0,
            original_price=100.0,
        )

        assert check.passed is False
        assert check.deviation_pct == 0.5

    def test_very_small_prices(self, validator):
        """Test deviation calculation with very small prices."""
        check = validator.check_deviation(
            current_price=1.03,
            original_price=1.00,
        )

        assert check.passed is True
        assert abs(check.deviation_pct - 0.03) < 0.0001

    def test_large_prices(self, validator):
        """Test deviation calculation with large prices."""
        check = validator.check_deviation(
            current_price=10300.0,
            original_price=10000.0,
        )

        assert check.passed is True
        assert abs(check.deviation_pct - 0.03) < 0.0001

    def test_negative_prices_not_realistic(self, validator):
        """Test that negative prices are handled (though not realistic)."""
        # This shouldn't happen in real trading, but test robustness
        check = validator.check_deviation(
            current_price=-100.0,
            original_price=100.0,
        )

        assert check.passed is False
        assert check.deviation_pct == -2.0
