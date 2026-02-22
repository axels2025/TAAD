"""Unit tests for WhatIfAnalyzer class."""

from datetime import datetime, timedelta

import pytest

from src.analysis.what_if import WhatIfAnalyzer, WhatIfResult
from src.strategies.base import TradeOpportunity


@pytest.fixture
def analyzer():
    """Create WhatIfAnalyzer instance with default settings."""
    return WhatIfAnalyzer(
        max_positions=10,
        max_sector_concentration=3,
        max_margin_pct=0.80,
        total_available_margin=50000.0,
    )


@pytest.fixture
def sample_opportunities():
    """Create sample trade opportunities for testing."""
    base_date = datetime.now() + timedelta(days=30)
    opportunities = []

    # Create 10 opportunities across 3 sectors
    sectors = ["Technology", "Healthcare", "Finance"]
    for i in range(10):
        opp = TradeOpportunity(
            symbol=f"SYMB{i+1}",
            strike=100.0 + i * 5,
            expiration=base_date,
            option_type="PUT",
            premium=2.00 + i * 0.10,
            contracts=1,
            otm_pct=0.05 + i * 0.01,
            dte=30,
            stock_price=110.0 + i * 5,
            trend="uptrend",
            sector=sectors[i % 3],
            confidence=0.85,
            margin_required=4000.0 + i * 500,
        )
        opp.calculate_margin_efficiency()
        opportunities.append(opp)

    return opportunities


class TestWhatIfAnalyzerInitialization:
    """Test WhatIfAnalyzer initialization."""

    def test_initialization_with_defaults(self):
        """Test analyzer initializes with correct defaults."""
        # Act
        analyzer = WhatIfAnalyzer()

        # Assert
        assert analyzer.max_positions == 10
        assert analyzer.max_sector_concentration == 3
        assert analyzer.max_margin_pct == 0.80
        assert analyzer.total_available_margin == 50000.0

    def test_initialization_with_custom_values(self):
        """Test analyzer initializes with custom values."""
        # Act
        analyzer = WhatIfAnalyzer(
            max_positions=20,
            max_sector_concentration=5,
            max_margin_pct=0.60,
            total_available_margin=100000.0,
        )

        # Assert
        assert analyzer.max_positions == 20
        assert analyzer.max_sector_concentration == 5
        assert analyzer.max_margin_pct == 0.60
        assert analyzer.total_available_margin == 100000.0


class TestAnalyzeSelections:
    """Test analyze_selections method."""

    def test_analyze_empty_selection(self, analyzer, sample_opportunities):
        """Test analyzing empty selection returns zero result."""
        # Arrange
        selected_indices = []
        current_positions = 5
        current_margin = 10000.0

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, current_positions, current_margin
        )

        # Assert
        assert result.approved_count == 0
        assert result.total_premium == 0.0
        assert result.total_margin == 0.0
        assert result.current_positions == 5
        assert result.new_total_positions == 5
        assert not result.exceeds_position_limit
        assert not result.exceeds_sector_limit
        assert not result.exceeds_margin_limit
        assert len(result.warnings) == 0

    def test_analyze_single_selection(self, analyzer, sample_opportunities):
        """Test analyzing single opportunity selection."""
        # Arrange
        selected_indices = [0]  # First opportunity
        opp = sample_opportunities[0]
        expected_premium = opp.premium * 100 * opp.contracts
        expected_margin = opp.margin_required * opp.contracts

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.approved_count == 1
        assert result.total_premium == expected_premium
        assert result.total_margin == expected_margin
        assert result.new_total_positions == 1
        assert not result.exceeds_position_limit

    def test_analyze_multiple_selections(self, analyzer, sample_opportunities):
        """Test analyzing multiple opportunity selections."""
        # Arrange
        selected_indices = [0, 2, 4]  # Three opportunities
        expected_premium = sum(
            sample_opportunities[i].premium * 100 * sample_opportunities[i].contracts
            for i in selected_indices
        )
        expected_margin = sum(
            sample_opportunities[i].margin_required * sample_opportunities[i].contracts
            for i in selected_indices
        )

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.approved_count == 3
        assert result.total_premium == pytest.approx(expected_premium, abs=0.01)
        assert result.total_margin == pytest.approx(expected_margin, abs=0.01)
        assert result.new_total_positions == 3

    def test_exceeds_position_limit(self, analyzer, sample_opportunities):
        """Test detection of position limit exceeded."""
        # Arrange
        selected_indices = list(range(10))  # All 10 opportunities
        current_positions = 5  # 5 + 10 = 15 > max (10)

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, current_positions, 0.0
        )

        # Assert
        assert result.exceeds_position_limit is True
        assert result.new_total_positions == 15
        assert any("exceed position limit" in w.lower() for w in result.warnings)

    def test_exceeds_sector_concentration(self, analyzer, sample_opportunities):
        """Test detection of sector concentration limit exceeded."""
        # Arrange
        # Select 4 opportunities from same sector (Technology: indices 0, 3, 6, 9)
        selected_indices = [0, 3, 6, 9]

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.exceeds_sector_limit is True
        assert result.sector_concentration["Technology"] == 4
        assert any("exceed sector limit" in w.lower() for w in result.warnings)

    def test_exceeds_margin_limit(self, analyzer, sample_opportunities):
        """Test detection of margin limit exceeded."""
        # Arrange
        # Select all 10 opportunities (total margin > 80% of 50000)
        selected_indices = list(range(10))
        total_margin = sum(
            opp.margin_required * opp.contracts for opp in sample_opportunities
        )
        # Ensure this exceeds 80% of 50000 = 40000
        assert total_margin > 40000

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.exceeds_margin_limit is True
        assert result.margin_utilization_pct > 80.0
        assert any("exceed margin limit" in w.lower() for w in result.warnings)

    def test_approaching_position_limit_warning(self, analyzer, sample_opportunities):
        """Test warning when approaching position limit (>80%)."""
        # Arrange
        # 9 total positions (current 5 + new 4) = 90% of max (10)
        selected_indices = [0, 1, 2, 3]
        current_positions = 5

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, current_positions, 0.0
        )

        # Assert
        assert not result.exceeds_position_limit  # Not exceeding
        assert any("approaching position limit" in w.lower() for w in result.warnings)

    def test_high_margin_utilization_warning(self, analyzer, sample_opportunities):
        """Test warning for high margin utilization (>50% but <80%)."""
        # Arrange
        # Select opportunities totaling ~30000 margin (60% of 50000)
        selected_indices = [0, 1, 2, 3, 4, 5]
        total_margin = sum(
            sample_opportunities[i].margin_required for i in selected_indices
        )
        # Ensure between 50% and 80%
        assert 25000 < total_margin < 40000

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert not result.exceeds_margin_limit
        assert any("high margin utilization" in w.lower() for w in result.warnings)

    def test_sector_concentration_details(self, analyzer, sample_opportunities):
        """Test sector concentration is correctly calculated."""
        # Arrange
        # Technology: 0, 3, 6, 9
        # Healthcare: 1, 4, 7
        # Finance: 2, 5, 8
        selected_indices = [0, 1, 2, 3, 4]

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.sector_concentration["Technology"] == 2  # indices 0, 3
        assert result.sector_concentration["Healthcare"] == 2  # indices 1, 4
        assert result.sector_concentration["Finance"] == 1  # index 2

    def test_current_margin_tracking(self, analyzer, sample_opportunities):
        """Test current margin is tracked correctly."""
        # Arrange
        selected_indices = [0]
        current_margin_used = 15000.0
        new_margin = sample_opportunities[0].margin_required

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, current_margin_used
        )

        # Assert
        assert result.details["current_margin_used"] == 15000.0
        assert result.details["new_margin_used"] == 15000.0 + new_margin
        assert (
            result.details["available_margin_remaining"]
            == 50000.0 - 15000.0 - new_margin
        )

    def test_selected_symbols_in_details(self, analyzer, sample_opportunities):
        """Test selected symbols are recorded in details."""
        # Arrange
        selected_indices = [0, 2, 4]

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.details["selected_symbols"] == ["SYMB1", "SYMB3", "SYMB5"]

    def test_average_premium_and_margin(self, analyzer, sample_opportunities):
        """Test average premium and margin calculations."""
        # Arrange
        selected_indices = [0, 1]
        expected_avg_premium = (
            sample_opportunities[0].premium * 100 + sample_opportunities[1].premium * 100
        ) / 2
        expected_avg_margin = (
            sample_opportunities[0].margin_required
            + sample_opportunities[1].margin_required
        ) / 2

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.details["avg_premium"] == pytest.approx(
            expected_avg_premium, abs=0.01
        )
        assert result.details["avg_margin"] == pytest.approx(
            expected_avg_margin, abs=0.01
        )

    def test_invalid_index_ignored(self, analyzer, sample_opportunities):
        """Test indices out of range are safely ignored."""
        # Arrange
        selected_indices = [0, 50, 100]  # Only index 0 is valid

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert result.approved_count == 1  # Only valid index counted
        assert result.details["selected_symbols"] == ["SYMB1"]

    def test_no_warnings_for_safe_selections(self, analyzer, sample_opportunities):
        """Test no warnings when all limits are safe."""
        # Arrange
        selected_indices = [0, 1]  # Just 2 opportunities

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Assert
        assert len(result.warnings) == 0

    def test_margin_utilization_calculation(self, analyzer, sample_opportunities):
        """Test margin utilization percentage is calculated correctly."""
        # Arrange
        selected_indices = [0]
        current_margin = 20000.0  # 40% already used
        new_margin = sample_opportunities[0].margin_required
        expected_utilization = ((current_margin + new_margin) / 50000.0) * 100

        # Act
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, current_margin
        )

        # Assert
        assert result.margin_utilization_pct == pytest.approx(
            expected_utilization, abs=0.01
        )


class TestEmptyResult:
    """Test _empty_result method."""

    def test_empty_result_structure(self, analyzer):
        """Test empty result has correct structure."""
        # Arrange
        current_positions = 5
        current_margin = 10000.0

        # Act
        result = analyzer._empty_result(current_positions, current_margin)

        # Assert
        assert result.approved_count == 0
        assert result.total_premium == 0.0
        assert result.total_margin == 0.0
        assert result.current_positions == 5
        assert result.new_total_positions == 5
        assert result.margin_utilization_pct == 20.0  # 10000/50000 * 100
        assert len(result.warnings) == 0


class TestFormatResult:
    """Test format_result method."""

    def test_format_result_with_selections(self, analyzer, sample_opportunities):
        """Test formatting result with selections."""
        # Arrange
        selected_indices = [0, 1, 2]
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Act
        formatted = analyzer.format_result(result)

        # Assert
        assert "What-If Analysis" in formatted
        assert "Opportunities: 3" in formatted
        assert "SYMB1" in formatted
        assert "Total premium:" in formatted
        assert "Total margin required:" in formatted
        assert "Positions:" in formatted
        assert "Margin utilization:" in formatted

    def test_format_result_empty_selection(self, analyzer, sample_opportunities):
        """Test formatting empty result."""
        # Arrange
        selected_indices = []
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Act
        formatted = analyzer.format_result(result)

        # Assert
        assert "What-If Analysis" in formatted
        assert "No opportunities selected" in formatted

    def test_format_result_with_warnings(self, analyzer, sample_opportunities):
        """Test formatting result with warnings."""
        # Arrange
        # Select enough to trigger warnings
        selected_indices = list(range(10))
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 5, 0.0
        )

        # Act
        formatted = analyzer.format_result(result)

        # Assert
        assert "Warnings:" in formatted
        assert len(result.warnings) > 0  # Should have warnings

    def test_format_result_with_sector_concentration(
        self, analyzer, sample_opportunities
    ):
        """Test formatting includes sector concentration."""
        # Arrange
        selected_indices = [0, 1, 2, 3]
        result = analyzer.analyze_selections(
            sample_opportunities, selected_indices, 0, 0.0
        )

        # Act
        formatted = analyzer.format_result(result)

        # Assert
        assert "Sector concentration:" in formatted
        for sector, count in result.sector_concentration.items():
            assert f"{sector}={count}" in formatted
