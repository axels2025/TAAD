"""Unit tests for execution CLI commands.

Tests the display and interaction logic for execution commands.
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.cli.commands.execution_commands import (
    ExecutionDisplay,
    format_execution_session,
    run_execute_staged,
    run_show_staged,
)
from src.services.execution_scheduler import (
    ExecutionReport,
    ExecutionStatus,
    TradeExecutionResult,
)
from src.services.premarket_validator import StagedOpportunity


def create_staged_opportunity(
    symbol: str = "AAPL",
    strike: float = 150.0,
    staged_stock_price: float = 180.0,
    staged_limit_price: float = 0.50,
    staged_contracts: int = 5,
    staged_margin: float = 3000.0,
) -> StagedOpportunity:
    """Create a test staged opportunity."""
    return StagedOpportunity(
        id=1,
        symbol=symbol,
        strike=strike,
        expiration="2026-02-07",
        staged_stock_price=staged_stock_price,
        staged_limit_price=staged_limit_price,
        staged_contracts=staged_contracts,
        staged_margin=staged_margin,
        otm_pct=0.167,
    )


class TestExecutionDisplay:
    """Tests for ExecutionDisplay class."""

    @pytest.fixture
    def display(self):
        """Create display with mock console."""
        console = MagicMock()
        return ExecutionDisplay(console=console)

    def test_display_staged_trades_empty(self, display):
        """Test display with no staged trades."""
        display.display_staged_trades([])

        # Should print "no staged trades" message
        assert display.console.print.called

    def test_display_staged_trades_with_data(self, display):
        """Test display with staged trades."""
        opps = [
            create_staged_opportunity("AAPL"),
            create_staged_opportunity("MSFT", strike=300.0, staged_margin=5000.0),
        ]

        display.display_staged_trades(opps, session="week_of_2026-02-02")

        # Should have printed table
        assert display.console.print.called

    def test_display_execution_progress_filled(self, display):
        """Test progress display for filled trade."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.FILLED,
            fill_price=0.50,
            contracts_filled=5,
        )

        display.display_execution_progress(result, 1, 3)

        assert display.console.print.called

    def test_display_execution_progress_working(self, display):
        """Test progress display for working trade."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.WORKING,
            final_limit=0.48,
            adjustments_made=2,
        )

        display.display_execution_progress(result, 2, 3)

        assert display.console.print.called

    def test_display_execution_progress_error(self, display):
        """Test progress display for failed trade."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.ERROR,
            error_message="Connection lost",
        )

        display.display_execution_progress(result, 3, 3)

        assert display.console.print.called

    def test_display_execution_report(self, display):
        """Test execution report display."""
        opp = create_staged_opportunity()
        exec_result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.FILLED,
            limit_price=0.50,
            fill_price=0.50,
            contracts_filled=5,
            contracts_requested=5,
        )

        report = ExecutionReport(
            execution_date=datetime(2026, 2, 2).date(),
            started_at=datetime(2026, 2, 2, 9, 15, 0),
            completed_at=datetime(2026, 2, 2, 9, 45, 0),
            dry_run=True,
            staged_count=1,
            validated_count=1,
            confirmed_count=1,
            executed_count=1,
            filled_count=1,
            total_premium=250.0,
            execution_results=[exec_result],
        )

        display.display_execution_report(report)

        assert display.console.print.called

    def test_display_execution_report_with_warnings(self, display):
        """Test execution report display with warnings."""
        report = ExecutionReport(
            execution_date=datetime(2026, 2, 2).date(),
            started_at=datetime(2026, 2, 2, 9, 15, 0),
            completed_at=datetime(2026, 2, 2, 9, 45, 0),
            warnings=["Some warning", "Another warning"],
        )

        display.display_execution_report(report)

        assert display.console.print.called

    def test_format_execution_status_filled(self, display):
        """Test status formatting for FILLED."""
        result = display._format_execution_status(ExecutionStatus.FILLED)
        assert "green" in result
        assert "FILLED" in result

    def test_format_execution_status_working(self, display):
        """Test status formatting for WORKING."""
        result = display._format_execution_status(ExecutionStatus.WORKING)
        assert "yellow" in result
        assert "WORKING" in result

    def test_format_execution_status_error(self, display):
        """Test status formatting for ERROR."""
        result = display._format_execution_status(ExecutionStatus.ERROR)
        assert "red" in result
        assert "ERROR" in result

    def test_prompt_confirmation_yes(self, display):
        """Test confirmation prompt with 'y'."""
        display.console.input.return_value = "y"
        opps = [create_staged_opportunity()]

        result = display.prompt_execution_confirmation(opps, dry_run=True)

        assert result is True

    def test_prompt_confirmation_no(self, display):
        """Test confirmation prompt with 'n'."""
        display.console.input.return_value = "n"
        opps = [create_staged_opportunity()]

        result = display.prompt_execution_confirmation(opps, dry_run=True)

        assert result is False

    def test_prompt_confirmation_keyboard_interrupt(self, display):
        """Test confirmation prompt with keyboard interrupt."""
        display.console.input.side_effect = KeyboardInterrupt()
        opps = [create_staged_opportunity()]

        result = display.prompt_execution_confirmation(opps, dry_run=True)

        assert result is False


class TestRunExecuteStaged:
    """Tests for run_execute_staged function."""

    def test_empty_opportunities(self):
        """Test with no opportunities."""
        console = MagicMock()
        result = run_execute_staged([], console=console)

        assert result is None

    def test_with_opportunities_dry_run(self):
        """Test dry run execution."""
        console = MagicMock()
        opps = [create_staged_opportunity()]

        # Skip confirmation
        report = run_execute_staged(
            opps,
            dry_run=True,
            skip_confirmation=True,
            console=console,
        )

        assert report is not None
        assert report.dry_run is True
        assert report.filled_count == 1

    def test_with_opportunities_cancelled(self):
        """Test execution cancelled by user."""
        console = MagicMock()
        console.input.return_value = "n"
        opps = [create_staged_opportunity()]

        report = run_execute_staged(opps, dry_run=True, console=console)

        assert report is None


class TestRunShowStaged:
    """Tests for run_show_staged function."""

    def test_empty(self):
        """Test with no opportunities."""
        console = MagicMock()
        run_show_staged([], console=console)

        assert console.print.called

    def test_with_opportunities(self):
        """Test with opportunities."""
        console = MagicMock()
        opps = [
            create_staged_opportunity("AAPL"),
            create_staged_opportunity("MSFT"),
        ]

        run_show_staged(opps, session="week_of_2026-02-02", console=console)

        assert console.print.called


class TestFormatExecutionSession:
    """Tests for format_execution_session function."""

    def test_format_returns_string(self):
        """Test that format returns a string."""
        result = format_execution_session()

        assert isinstance(result, str)
        assert result.startswith("week_of_")

    def test_format_contains_date(self):
        """Test that format contains a date-like pattern."""
        result = format_execution_session()

        # Should contain YYYY-MM-DD pattern
        parts = result.replace("week_of_", "").split("-")
        assert len(parts) == 3
        assert len(parts[0]) == 4  # Year
        assert len(parts[1]) == 2  # Month
        assert len(parts[2]) == 2  # Day
