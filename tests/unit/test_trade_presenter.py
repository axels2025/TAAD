"""Unit tests for TradePresenter class.

Tests batch approval parsing, table formatting, and edge cases.
"""

from datetime import datetime, timedelta
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

from src.cli.trade_presenter import TradePresenter
from src.strategies.base import TradeOpportunity


@pytest.fixture
def presenter():
    """Create TradePresenter instance with mock console."""
    console = Console(file=StringIO(), width=120)
    return TradePresenter(console=console)


@pytest.fixture
def sample_opportunities():
    """Create sample trade opportunities for testing."""
    base_date = datetime.now()
    opportunities = []

    for i in range(10):
        opp = TradeOpportunity(
            symbol=f"SYMB{i+1}",
            strike=100.0 + i * 5,
            expiration=base_date + timedelta(days=30 + i),
            option_type="PUT",
            premium=0.50 + i * 0.10,
            contracts=5,
            otm_pct=0.05 + i * 0.01,
            dte=30 + i,
            stock_price=110.0 + i * 5,
            trend="uptrend",
            sector=f"Sector{i % 3}",  # 3 sectors
            confidence=0.85 + i * 0.01,
            reasoning=f"Test opportunity {i+1}",
            margin_required=4000.0 + i * 500,
        )
        opp.calculate_margin_efficiency()
        opportunities.append(opp)

    return opportunities


@pytest.fixture
def risk_blocked_opportunities(sample_opportunities):
    """Create sample risk-blocked opportunities."""
    blocked = [
        (sample_opportunities[0], "Exceeds position limit"),
        (sample_opportunities[1], "Exceeds sector concentration"),
        (sample_opportunities[2], "Insufficient margin available"),
    ]
    return blocked


class TestTradePresenterInitialization:
    """Test TradePresenter initialization."""

    def test_initialization_with_console(self):
        """Test presenter initializes with provided console."""
        console = Console(file=StringIO())
        presenter = TradePresenter(console=console)

        assert presenter.console is console

    def test_initialization_without_console(self):
        """Test presenter creates console if not provided."""
        presenter = TradePresenter()

        assert presenter.console is not None
        assert isinstance(presenter.console, Console)


class TestParseSelection:
    """Test _parse_selection method for batch approval parsing."""

    def test_parse_single_number(self, presenter):
        """Test parsing single number returns correct 0-based index."""
        # Arrange
        choice = "3"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [2]  # 3 -> index 2 (0-based)

    def test_parse_multiple_numbers_comma_separated(self, presenter):
        """Test parsing comma-separated numbers."""
        # Arrange
        choice = "1,3,5"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 2, 4]  # 1,3,5 -> 0,2,4 (0-based)

    def test_parse_range(self, presenter):
        """Test parsing range notation (1-5)."""
        # Arrange
        choice = "1-5"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 1, 2, 3, 4]  # 1-5 -> 0,1,2,3,4 (0-based)

    def test_parse_mixed_numbers_and_ranges(self, presenter):
        """Test parsing mixed notation (1,3-5,7)."""
        # Arrange
        choice = "1,3-5,7"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 2, 3, 4, 6]  # 1,3-5,7 -> 0,2,3,4,6 (0-based)

    def test_parse_invalid_non_numeric(self, presenter):
        """Test invalid input (non-numeric) returns None."""
        # Arrange
        choice = "abc"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result is None

    def test_parse_number_exceeds_max(self, presenter):
        """Test number exceeding max_count returns None."""
        # Arrange
        choice = "15"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result is None

    def test_parse_number_below_minimum(self, presenter):
        """Test number below 1 returns None."""
        # Arrange
        choice = "0"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result is None

    def test_parse_range_exceeds_max(self, presenter):
        """Test range exceeding max_count returns None."""
        # Arrange
        choice = "5-15"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result is None

    def test_parse_invalid_range_start_greater_than_end(self, presenter):
        """Test invalid range (start > end) returns None."""
        # Arrange
        choice = "5-3"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result is None

    def test_parse_empty_string(self, presenter):
        """Test empty string returns empty list."""
        # Arrange
        choice = ""
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == []

    def test_parse_whitespace_only(self, presenter):
        """Test whitespace-only string returns empty list."""
        # Arrange
        choice = "   "
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == []

    def test_parse_with_spaces(self, presenter):
        """Test parsing handles spaces correctly."""
        # Arrange
        choice = " 1 , 3 - 5 , 7 "
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 2, 3, 4, 6]

    def test_parse_duplicate_numbers(self, presenter):
        """Test duplicate numbers are deduplicated."""
        # Arrange
        choice = "1,1,3,3"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 2]  # Duplicates removed, sorted

    def test_parse_overlapping_ranges(self, presenter):
        """Test overlapping ranges are merged correctly."""
        # Arrange
        choice = "1-5,3-7"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 1, 2, 3, 4, 5, 6]  # 1-7 merged

    def test_parse_max_count_edge_case(self, presenter):
        """Test parsing with max_count as edge value."""
        # Arrange
        choice = "10"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [9]  # Valid: 10 -> index 9

    def test_parse_single_item_range(self, presenter):
        """Test range with same start and end."""
        # Arrange
        choice = "5-5"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [4]  # 5-5 -> index 4

    def test_parse_multiple_commas(self, presenter):
        """Test handling multiple consecutive commas."""
        # Arrange
        choice = "1,,3,,5"
        max_count = 10

        # Act
        result = presenter._parse_selection(choice, max_count)

        # Assert
        assert result == [0, 2, 4]  # Empty parts ignored


class TestShowQualifiedTable:
    """Test _show_qualified_table method."""

    def test_show_qualified_table_with_opportunities(self, presenter, sample_opportunities):
        """Test displaying qualified opportunities table."""
        # Arrange
        opportunities = sample_opportunities[:5]

        # Act
        presenter._show_qualified_table(opportunities)

        # Assert - check console output contains expected data
        output = presenter.console.file.getvalue()
        assert "Qualified Opportunities (5)" in output
        assert "SYMB1" in output
        assert "SYMB5" in output

    def test_show_qualified_table_with_margin_efficiency(self, presenter, sample_opportunities):
        """Test margin efficiency is displayed correctly."""
        # Arrange
        opportunities = sample_opportunities[:1]
        opp = opportunities[0]

        # Act
        presenter._show_qualified_table(opportunities)

        # Assert
        output = presenter.console.file.getvalue()
        assert "Margin Eff." in output
        # Check margin efficiency values are present
        assert f"{opp.margin_efficiency_pct:.1f}%" in output

    def test_show_qualified_table_empty_list(self, presenter):
        """Test displaying empty list doesn't crash."""
        # Arrange
        opportunities = []

        # Act
        presenter._show_qualified_table(opportunities)

        # Assert - should complete without error
        output = presenter.console.file.getvalue()
        assert "Qualified Opportunities (0)" in output

    def test_show_qualified_table_single_item(self, presenter, sample_opportunities):
        """Test displaying single opportunity."""
        # Arrange
        opportunities = sample_opportunities[:1]

        # Act
        presenter._show_qualified_table(opportunities)

        # Assert
        output = presenter.console.file.getvalue()
        assert "Qualified Opportunities (1)" in output
        assert "SYMB1" in output

    def test_show_qualified_table_with_no_margin_efficiency(self, presenter):
        """Test displaying opportunity without margin efficiency."""
        # Arrange
        opp = TradeOpportunity(
            symbol="TEST",
            strike=100.0,
            expiration=datetime.now() + timedelta(days=30),
            option_type="PUT",
            premium=0.50,
            contracts=5,
            otm_pct=0.05,
            dte=30,
            stock_price=110.0,
            trend="uptrend",
            confidence=0.85,
            reasoning="Test",
            margin_required=0.0,  # No margin
        )
        # Don't call calculate_margin_efficiency

        # Act
        presenter._show_qualified_table([opp])

        # Assert
        output = presenter.console.file.getvalue()
        assert "N/A" in output  # Should show N/A for missing margin efficiency


class TestShowRiskBlockedTable:
    """Test _show_risk_blocked_table method."""

    def test_show_risk_blocked_table_with_blocked(self, presenter, risk_blocked_opportunities):
        """Test displaying risk-blocked opportunities."""
        # Arrange
        blocked = risk_blocked_opportunities

        # Act
        presenter._show_risk_blocked_table(blocked)

        # Assert
        output = presenter.console.file.getvalue()
        assert "Risk-Blocked Opportunities (3)" in output
        assert "SYMB1" in output
        assert "Exceeds position limit" in output
        assert "Exceeds sector concentration" in output

    def test_show_risk_blocked_table_empty_list(self, presenter):
        """Test displaying empty blocked list doesn't crash."""
        # Arrange
        blocked = []

        # Act
        presenter._show_risk_blocked_table(blocked)

        # Assert
        output = presenter.console.file.getvalue()
        assert "Risk-Blocked Opportunities (0)" in output

    def test_show_risk_blocked_table_single_item(self, presenter, sample_opportunities):
        """Test displaying single blocked opportunity."""
        # Arrange
        blocked = [(sample_opportunities[0], "Test reason")]

        # Act
        presenter._show_risk_blocked_table(blocked)

        # Assert
        output = presenter.console.file.getvalue()
        assert "Risk-Blocked Opportunities (1)" in output
        assert "Test reason" in output


class TestGetBatchApproval:
    """Test _get_batch_approval method."""

    @patch("rich.console.Console.input")
    def test_batch_approval_all(self, mock_input, presenter):
        """Test 'all' approves all opportunities."""
        # Arrange
        mock_input.return_value = "all"
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == [0, 1, 2, 3, 4]

    @patch("rich.console.Console.input")
    def test_batch_approval_a_shorthand(self, mock_input, presenter):
        """Test 'a' (shorthand for all) approves all."""
        # Arrange
        mock_input.return_value = "a"
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == [0, 1, 2, 3, 4]

    @patch("rich.console.Console.input")
    def test_batch_approval_none(self, mock_input, presenter):
        """Test 'none' rejects all opportunities."""
        # Arrange
        mock_input.return_value = "none"
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == []

    @patch("rich.console.Console.input")
    def test_batch_approval_n_shorthand(self, mock_input, presenter):
        """Test 'n' (shorthand for none) rejects all."""
        # Arrange
        mock_input.return_value = "n"
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == []

    @patch("rich.console.Console.input")
    def test_batch_approval_quit(self, mock_input, presenter):
        """Test 'q' quits and returns empty list."""
        # Arrange
        mock_input.return_value = "q"
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == []

    @patch("rich.console.Console.input")
    def test_batch_approval_specific_numbers(self, mock_input, presenter):
        """Test specific number selection."""
        # Arrange
        mock_input.return_value = "1,3,5"
        count = 10

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == [0, 2, 4]

    @patch("rich.console.Console.input")
    def test_batch_approval_range(self, mock_input, presenter):
        """Test range selection."""
        # Arrange
        mock_input.return_value = "1-5"
        count = 10

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == [0, 1, 2, 3, 4]

    @patch("rich.console.Console.input")
    def test_batch_approval_invalid_then_valid(self, mock_input, presenter):
        """Test retry on invalid input."""
        # Arrange
        mock_input.side_effect = ["invalid", "1,3,5"]
        count = 10

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == [0, 2, 4]
        assert mock_input.call_count == 2

    @patch("rich.console.Console.input")
    def test_batch_approval_keyboard_interrupt(self, mock_input, presenter):
        """Test KeyboardInterrupt returns empty list."""
        # Arrange
        mock_input.side_effect = KeyboardInterrupt()
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == []

    @patch("rich.console.Console.input")
    def test_batch_approval_empty_selection(self, mock_input, presenter):
        """Test empty selection returns empty list."""
        # Arrange
        mock_input.return_value = ""
        count = 5

        # Act
        result = presenter._get_batch_approval(count)

        # Assert
        assert result == []


class TestPresentOpportunities:
    """Test present_opportunities method (integration)."""

    @patch("rich.console.Console.input")
    def test_present_opportunities_with_qualified_and_approval(
        self, mock_input, presenter, sample_opportunities
    ):
        """Test presenting opportunities and getting approval."""
        # Arrange
        mock_input.return_value = "1,3,5"
        qualified = sample_opportunities[:5]

        # Act
        result = presenter.present_opportunities(qualified)

        # Assert
        assert result == [0, 2, 4]
        output = presenter.console.file.getvalue()
        assert "Qualified Opportunities (5)" in output

    @patch("rich.console.Console.input")
    def test_present_opportunities_with_risk_blocked(
        self, mock_input, presenter, sample_opportunities, risk_blocked_opportunities
    ):
        """Test presenting with risk-blocked opportunities."""
        # Arrange
        mock_input.return_value = "all"
        qualified = sample_opportunities[:5]
        risk_blocked = risk_blocked_opportunities

        # Act
        result = presenter.present_opportunities(qualified, risk_blocked)

        # Assert
        assert result == [0, 1, 2, 3, 4]
        output = presenter.console.file.getvalue()
        assert "Qualified Opportunities (5)" in output
        assert "Risk-Blocked Opportunities (3)" in output

    def test_present_opportunities_empty_qualified(self, presenter):
        """Test presenting with no qualified opportunities."""
        # Arrange
        qualified = []

        # Act
        result = presenter.present_opportunities(qualified)

        # Assert
        assert result == []
        output = presenter.console.file.getvalue()
        assert "No qualified opportunities to display" in output

    @patch("rich.console.Console.input")
    def test_present_opportunities_only_risk_blocked(
        self, mock_input, presenter, risk_blocked_opportunities
    ):
        """Test presenting with only risk-blocked opportunities."""
        # Arrange
        qualified = []
        risk_blocked = risk_blocked_opportunities

        # Act
        result = presenter.present_opportunities(qualified, risk_blocked)

        # Assert
        assert result == []
        output = presenter.console.file.getvalue()
        assert "No qualified opportunities to display" in output
        assert "Risk-Blocked Opportunities (3)" in output


class TestShowApprovalSummary:
    """Test show_approval_summary method."""

    def test_show_approval_summary_with_approved(self, presenter, sample_opportunities):
        """Test showing summary of approved opportunities."""
        # Arrange
        approved = sample_opportunities[:3]
        total = 10

        # Act
        presenter.show_approval_summary(approved, total)

        # Assert
        output = presenter.console.file.getvalue()
        assert "Approved 3 of 10 opportunities" in output
        assert "SYMB1" in output
        assert "SYMB2" in output
        assert "SYMB3" in output

    def test_show_approval_summary_empty_approved(self, presenter):
        """Test showing summary with no approved opportunities."""
        # Arrange
        approved = []
        total = 10

        # Act
        presenter.show_approval_summary(approved, total)

        # Assert
        output = presenter.console.file.getvalue()
        assert "No opportunities approved for execution" in output

    def test_show_approval_summary_all_approved(self, presenter, sample_opportunities):
        """Test showing summary when all are approved."""
        # Arrange
        approved = sample_opportunities
        total = len(sample_opportunities)

        # Act
        presenter.show_approval_summary(approved, total)

        # Assert
        output = presenter.console.file.getvalue()
        assert f"Approved {total} of {total} opportunities" in output
