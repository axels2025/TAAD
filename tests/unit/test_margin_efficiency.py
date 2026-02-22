"""Unit tests for margin efficiency calculation in TradeOpportunity."""

from datetime import datetime, timedelta

import pytest

from src.strategies.base import TradeOpportunity


class TestMarginEfficiencyCalculation:
    """Test calculate_margin_efficiency method."""

    def test_calculate_margin_efficiency_normal_case(self):
        """Test margin efficiency calculation with normal values."""
        # Arrange
        opp = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=4.00,  # $4 per share
            contracts=1,
            otm_pct=0.10,
            dte=30,
            stock_price=165.0,
            trend="uptrend",
            margin_required=4000.0,  # $4000 margin
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $4 * 100 = $400
        # Efficiency % = ($400 / $4000) * 100 = 10%
        # Ratio = $4000 / $400 = 10 → "1:10"
        assert opp.margin_efficiency_pct == pytest.approx(10.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:10"

    def test_calculate_margin_efficiency_example_from_spec(self):
        """Test with example from spec: $400 premium, $4000 margin = 10% = 1:10."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=4.00,  # $4/share * 100 = $400
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=4000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        assert opp.margin_efficiency_pct == pytest.approx(10.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:10"

    def test_calculate_margin_efficiency_high_efficiency(self):
        """Test high margin efficiency (15%)."""
        # Arrange
        opp = TradeOpportunity(
            symbol="MSFT",
            strike=300.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=3.00,  # $300 per contract
            contracts=1,
            otm_pct=0.08,
            dte=30,
            stock_price=320.0,
            trend="uptrend",
            margin_required=2000.0,  # $2000 margin
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $3 * 100 = $300
        # Efficiency % = ($300 / $2000) * 100 = 15%
        # Ratio = $2000 / $300 = 6.67 → "1:7"
        assert opp.margin_efficiency_pct == pytest.approx(15.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:7"

    def test_calculate_margin_efficiency_low_efficiency(self):
        """Test low margin efficiency (5%)."""
        # Arrange
        opp = TradeOpportunity(
            symbol="GOOGL",
            strike=120.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=2.00,  # $200 per contract
            contracts=1,
            otm_pct=0.12,
            dte=30,
            stock_price=135.0,
            trend="uptrend",
            margin_required=4000.0,  # $4000 margin
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $2 * 100 = $200
        # Efficiency % = ($200 / $4000) * 100 = 5%
        # Ratio = $4000 / $200 = 20 → "1:20"
        assert opp.margin_efficiency_pct == pytest.approx(5.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:20"

    def test_calculate_margin_efficiency_zero_margin(self):
        """Test margin efficiency with zero margin returns zero/N/A."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=2.00,
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=0.0,  # Zero margin
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        assert opp.margin_efficiency_pct == 0.0
        assert opp.margin_efficiency_ratio == "N/A"

    def test_calculate_margin_efficiency_zero_premium(self):
        """Test margin efficiency with zero premium returns zero/N/A."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=0.0,  # Zero premium
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=4000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        assert opp.margin_efficiency_pct == 0.0
        assert opp.margin_efficiency_ratio == "N/A"

    def test_calculate_margin_efficiency_very_small_premium(self):
        """Test margin efficiency with very small premium."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=0.10,  # $10 per contract
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=4000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $0.10 * 100 = $10
        # Efficiency % = ($10 / $4000) * 100 = 0.25%
        # Ratio = $4000 / $10 = 400 → "1:400"
        assert opp.margin_efficiency_pct == pytest.approx(0.25, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:400"

    def test_calculate_margin_efficiency_large_premium(self):
        """Test margin efficiency with large premium."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=500.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=20.00,  # $2000 per contract
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=550.0,
            trend="uptrend",
            margin_required=10000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $20 * 100 = $2000
        # Efficiency % = ($2000 / $10000) * 100 = 20%
        # Ratio = $10000 / $2000 = 5 → "1:5"
        assert opp.margin_efficiency_pct == pytest.approx(20.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:5"

    def test_calculate_margin_efficiency_fractional_ratio(self):
        """Test margin efficiency with non-integer ratio."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=2.50,  # $250 per contract
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=3000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $2.50 * 100 = $250
        # Efficiency % = ($250 / $3000) * 100 = 8.33%
        # Ratio = $3000 / $250 = 12 → "1:12"
        assert opp.margin_efficiency_pct == pytest.approx(8.33, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:12"

    def test_calculate_margin_efficiency_multiple_contracts(self):
        """Test margin efficiency calculation respects contracts."""
        # Arrange
        # Note: margin_required should already account for contracts
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=2.00,  # $2/share
            contracts=5,  # 5 contracts
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=20000.0,  # $20000 for 5 contracts
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $2 * 100 = $200 per contract
        # Efficiency % = ($200 / $20000) * 100 = 1%
        # Ratio = $20000 / $200 = 100 → "1:100"
        # Note: The calculation uses premium for 1 contract, not total
        assert opp.margin_efficiency_pct == pytest.approx(1.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:100"

    def test_to_dict_includes_margin_efficiency(self):
        """Test to_dict() includes margin efficiency fields."""
        # Arrange
        opp = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=4.00,
            contracts=1,
            otm_pct=0.10,
            dte=30,
            stock_price=165.0,
            trend="uptrend",
            margin_required=4000.0,
        )
        opp.calculate_margin_efficiency()

        # Act
        result = opp.to_dict()

        # Assert
        assert "margin_efficiency_pct" in result
        assert "margin_efficiency_ratio" in result
        assert result["margin_efficiency_pct"] == pytest.approx(10.0, abs=0.01)
        assert result["margin_efficiency_ratio"] == "1:10"

    def test_initial_values_before_calculation(self):
        """Test initial margin efficiency values before calculation."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=2.00,
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=4000.0,
        )

        # Assert - before calculation
        assert opp.margin_efficiency_pct == 0.0
        assert opp.margin_efficiency_ratio == ""

    def test_calculate_can_be_called_multiple_times(self):
        """Test calculate_margin_efficiency can be called multiple times."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=2.00,
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=4000.0,
        )

        # Act
        opp.calculate_margin_efficiency()
        first_pct = opp.margin_efficiency_pct
        first_ratio = opp.margin_efficiency_ratio

        # Change margin and recalculate
        opp.margin_required = 2000.0
        opp.calculate_margin_efficiency()

        # Assert
        assert first_pct == pytest.approx(5.0, abs=0.01)
        assert first_ratio == "1:20"
        assert opp.margin_efficiency_pct == pytest.approx(10.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:10"

    def test_margin_efficiency_ratio_rounds_to_integer(self):
        """Test margin efficiency ratio rounds to nearest integer."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=1.75,  # $175 per contract
            contracts=1,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            margin_required=2000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Ratio = $2000 / $175 = 11.43 → "1:11" (rounded)
        assert opp.margin_efficiency_ratio == "1:11"

    def test_margin_efficiency_for_call_option(self):
        """Test margin efficiency works for CALL options too."""
        # Arrange
        opp = TradeOpportunity(
            symbol="AAPL",
            strike=200.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="CALL",  # CALL instead of PUT
            premium=5.00,
            contracts=1,
            otm_pct=0.15,
            dte=30,
            stock_price=175.0,
            trend="uptrend",
            margin_required=5000.0,
        )

        # Act
        opp.calculate_margin_efficiency()

        # Assert
        # Premium value = $5 * 100 = $500
        # Efficiency % = ($500 / $5000) * 100 = 10%
        assert opp.margin_efficiency_pct == pytest.approx(10.0, abs=0.01)
        assert opp.margin_efficiency_ratio == "1:10"
