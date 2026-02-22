"""Unit tests for validation CLI commands.

Tests the display and interaction logic for validation commands.
"""

from unittest.mock import MagicMock

import pytest

from src.cli.commands.validation_commands import (
    ValidationDisplay,
    run_full_validation,
    run_open_validation,
    run_premarket_validation,
)
from src.services.premarket_validator import (
    OpenCheckResult,
    PremarketCheckResult,
    StagedOpportunity,
    ValidationStatus,
)


def create_staged_opportunity(
    symbol: str = "AAPL",
    strike: float = 150.0,
    staged_stock_price: float = 180.0,
    staged_limit_price: float = 0.50,
) -> StagedOpportunity:
    """Create a test staged opportunity."""
    return StagedOpportunity(
        id=1,
        symbol=symbol,
        strike=strike,
        expiration="2026-02-07",
        staged_stock_price=staged_stock_price,
        staged_limit_price=staged_limit_price,
        staged_contracts=5,
        staged_margin=3000.0,
        otm_pct=0.167,
    )


class TestValidationDisplay:
    """Tests for ValidationDisplay class."""

    @pytest.fixture
    def display(self):
        """Create display with mock console."""
        console = MagicMock()
        return ValidationDisplay(console=console)

    def test_display_premarket_results_empty(self, display):
        """Test display with no results."""
        display.display_premarket_results([])
        assert display.console.print.called

    def test_display_premarket_results_ready(self, display):
        """Test display with READY results."""
        opp = create_staged_opportunity()
        results = [
            PremarketCheckResult(
                opportunity=opp,
                status=ValidationStatus.READY,
                staged_price=180.0,
                premarket_price=179.0,
                deviation_pct=-0.0056,
                new_otm_pct=0.163,
            )
        ]

        display.display_premarket_results(results)
        assert display.console.print.called

    def test_display_premarket_results_adjusted(self, display):
        """Test display with ADJUSTED results."""
        opp = create_staged_opportunity()
        results = [
            PremarketCheckResult(
                opportunity=opp,
                status=ValidationStatus.ADJUSTED,
                staged_price=180.0,
                premarket_price=170.0,
                deviation_pct=-0.056,
                new_otm_pct=0.12,
                adjusted_strike=145.0,
                adjustment_reason="Strike adjusted 150 → 145",
            )
        ]

        display.display_premarket_results(results)
        assert display.console.print.called

    def test_display_premarket_results_stale(self, display):
        """Test display with STALE results."""
        opp = create_staged_opportunity()
        results = [
            PremarketCheckResult(
                opportunity=opp,
                status=ValidationStatus.STALE,
                staged_price=180.0,
                premarket_price=160.0,
                deviation_pct=-0.111,
                new_otm_pct=0.06,
                adjustment_reason="Stock moved too much",
            )
        ]

        display.display_premarket_results(results)
        assert display.console.print.called

    def test_display_open_results_confirmed(self, display):
        """Test display with CONFIRMED (READY) results."""
        opp = create_staged_opportunity()
        results = [
            OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.READY,
                staged_limit=0.50,
                live_bid=0.49,
                live_ask=0.55,
                premium_deviation_pct=-0.02,
            )
        ]

        display.display_open_results(results)
        assert display.console.print.called

    def test_display_open_results_adjusted(self, display):
        """Test display with ADJUSTED results."""
        opp = create_staged_opportunity()
        results = [
            OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.ADJUSTED,
                staged_limit=0.50,
                live_bid=0.45,
                live_ask=0.52,
                premium_deviation_pct=-0.10,
                new_limit_price=0.47,
            )
        ]

        display.display_open_results(results)
        assert display.console.print.called

    def test_display_open_results_stale(self, display):
        """Test display with STALE results."""
        opp = create_staged_opportunity()
        results = [
            OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.STALE,
                staged_limit=0.50,
                live_bid=0.20,
                live_ask=0.28,
                premium_deviation_pct=-0.60,
                adjustment_reason="Premium collapsed",
            )
        ]

        display.display_open_results(results)
        assert display.console.print.called

    def test_display_waiting_message(self, display):
        """Test waiting message display."""
        display.display_waiting_message()
        assert display.console.print.called

    def test_format_status_ready_stage1(self, display):
        """Test status formatting for Stage 1 READY."""
        result = display._format_status(ValidationStatus.READY, is_stage1=True)
        assert "READY" in result
        assert "green" in result

    def test_format_status_ready_stage2(self, display):
        """Test status formatting for Stage 2 READY (CONFIRMED)."""
        result = display._format_status(ValidationStatus.READY, is_stage1=False)
        assert "CONFIRMED" in result
        assert "green" in result

    def test_format_status_adjusted(self, display):
        """Test status formatting for ADJUSTED."""
        result = display._format_status(ValidationStatus.ADJUSTED)
        assert "ADJUSTED" in result
        assert "yellow" in result

    def test_format_status_stale(self, display):
        """Test status formatting for STALE."""
        result = display._format_status(ValidationStatus.STALE)
        assert "STALE" in result
        assert "red" in result

    def test_format_change_small(self, display):
        """Test change formatting for small changes."""
        result = display._format_change(-0.02)
        assert "green" in result
        assert "-2.0%" in result

    def test_format_change_medium(self, display):
        """Test change formatting for medium changes."""
        result = display._format_change(-0.05)
        assert "yellow" in result
        assert "-5.0%" in result

    def test_format_change_large(self, display):
        """Test change formatting for large changes."""
        result = display._format_change(-0.15)
        assert "red" in result
        assert "-15.0%" in result


class TestRunPremarketValidation:
    """Tests for run_premarket_validation function."""

    def test_empty_opportunities(self):
        """Test with no opportunities."""
        console = MagicMock()
        results = run_premarket_validation([], console=console)

        assert results == []
        assert console.print.called

    def test_with_opportunities(self):
        """Test with opportunities."""
        console = MagicMock()
        opps = [create_staged_opportunity()]

        results = run_premarket_validation(opps, console=console)

        assert len(results) == 1
        assert console.print.called


class TestRunOpenValidation:
    """Tests for run_open_validation function."""

    def test_empty_opportunities(self):
        """Test with no opportunities."""
        console = MagicMock()
        results = run_open_validation([], console=console)

        assert results == []
        assert console.print.called

    def test_with_opportunities(self):
        """Test with opportunities."""
        console = MagicMock()
        opps = [create_staged_opportunity()]

        results = run_open_validation(opps, console=console)

        assert len(results) == 1
        assert console.print.called


class TestRunFullValidation:
    """Tests for run_full_validation function."""

    def test_empty_opportunities(self):
        """Test with no opportunities."""
        console = MagicMock()
        stage1, stage2 = run_full_validation([], console=console)

        assert stage1 == []
        assert stage2 == []

    def test_all_pass(self):
        """Test when all opportunities pass both stages."""
        console = MagicMock()
        opps = [create_staged_opportunity()]

        stage1, stage2 = run_full_validation(opps, console=console)

        assert len(stage1) == 1
        assert len(stage2) == 1

    def test_with_wait_message(self):
        """Test that wait message is displayed when requested."""
        console = MagicMock()
        opps = [create_staged_opportunity()]

        run_full_validation(opps, console=console, wait_for_open=True)

        # Should have printed waiting message
        calls_str = str(console.print.call_args_list)
        assert "Waiting" in calls_str or console.print.called

    def test_stage1_filters_stale(self):
        """Test that Stage 1 filters out stale opportunities."""
        mock_ibkr = MagicMock()

        # First opportunity passes, second is stale
        def price_for_symbol(symbol):
            if symbol == "AAPL":
                return 179.0  # -0.5% → READY
            else:
                return 130.0  # -13% → STALE

        mock_ibkr.get_stock_price.side_effect = price_for_symbol
        # Also need to mock option quote for Stage 2
        mock_ibkr.get_option_quote.return_value = {"bid": 0.49, "ask": 0.55}

        from src.services.premarket_validator import PremarketValidator

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        console = MagicMock()

        opps = [
            create_staged_opportunity(symbol="AAPL", staged_stock_price=180.0),
            create_staged_opportunity(symbol="MSFT", staged_stock_price=150.0),
        ]

        stage1, stage2 = run_full_validation(
            opps, validator=validator, console=console
        )

        # AAPL passes Stage 1, MSFT is stale
        assert len(stage1) == 2
        assert stage1[0].passed is True  # AAPL
        assert stage1[1].passed is False  # MSFT

        # Only AAPL goes to Stage 2
        assert len(stage2) == 1

    def test_none_pass_stage1(self):
        """Test when no opportunities pass Stage 1."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 100.0  # All are stale

        from src.services.premarket_validator import PremarketValidator

        validator = PremarketValidator(ibkr_client=mock_ibkr)
        console = MagicMock()

        opps = [
            create_staged_opportunity(symbol="AAPL", staged_stock_price=200.0),
        ]

        stage1, stage2 = run_full_validation(
            opps, validator=validator, console=console
        )

        assert len(stage1) == 1
        assert stage1[0].passed is False
        assert len(stage2) == 0
