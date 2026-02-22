"""Unit tests for portfolio CLI commands.

Tests the display and interaction logic for portfolio building
and trade staging commands.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from src.cli.commands.portfolio_commands import (
    PortfolioDisplay,
    build_portfolio_interactive,
    format_execution_session,
)
from src.services.portfolio_builder import (
    MarginComparison,
    PortfolioPlan,
    StagedTrade,
)
from src.services.strike_finder import StrikeCandidate


def create_test_candidate(
    symbol: str,
    strike: float = 100.0,
    stock_price: float = 120.0,
    bid: float = 0.50,
    contracts: int = 5,
    margin_estimate: float = 2000.0,
    margin_actual: float | None = None,
    iv_rank: float = 0.45,
    sector: str = "Technology",
    otm_pct: float = 0.17,
) -> StrikeCandidate:
    """Create a test StrikeCandidate."""
    mid = bid + 0.05
    suggested_limit = bid + (mid - bid) * 0.3
    premium_income = suggested_limit * 100 * contracts
    effective_margin = margin_actual if margin_actual else margin_estimate
    total_margin = effective_margin * contracts

    return StrikeCandidate(
        symbol=symbol,
        stock_price=stock_price,
        strike=strike,
        expiration=date(2026, 2, 7),
        dte=7,
        bid=bid,
        ask=bid + 0.10,
        mid=mid,
        suggested_limit=suggested_limit,
        otm_pct=otm_pct,
        delta=-0.15,
        iv=0.35,
        iv_rank=iv_rank,
        volume=1000,
        open_interest=5000,
        margin_estimate=margin_estimate,
        margin_actual=margin_actual,
        contracts=contracts,
        total_margin=total_margin,
        premium_income=premium_income,
        margin_efficiency=premium_income / total_margin if total_margin > 0 else 0,
        sector=sector,
        score=75.0,
        source="barchart",
    )


def create_test_trade(
    candidate: StrikeCandidate,
    portfolio_rank: int = 1,
    cumulative_margin: float = 10000.0,
) -> StagedTrade:
    """Create a test StagedTrade."""
    return StagedTrade(
        candidate=candidate,
        margin_per_contract=candidate.margin_estimate,
        margin_source="estimated",
        contracts=candidate.contracts,
        total_margin=candidate.margin_estimate * candidate.contracts,
        total_premium=candidate.suggested_limit * 100 * candidate.contracts,
        portfolio_rank=portfolio_rank,
        cumulative_margin=cumulative_margin,
        within_budget=True,
    )


class TestPortfolioDisplay:
    """Tests for PortfolioDisplay class."""

    @pytest.fixture
    def display(self):
        """Create display with mock console."""
        console = MagicMock()
        return PortfolioDisplay(console=console)

    def test_display_margin_reranking_empty(self, display):
        """Test display with no margin comparisons."""
        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=50000.0,
            margin_remaining=50000.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        # Should not raise
        display.display_margin_reranking(plan)

    def test_display_margin_reranking_with_data(self, display):
        """Test display with margin comparisons."""
        candidate = create_test_candidate("AAPL")

        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[
                MarginComparison(
                    candidate=candidate,
                    estimated_margin=10000.0,
                    estimated_efficiency=0.025,
                    estimated_rank=1,
                    actual_margin=12000.0,
                    actual_efficiency=0.021,
                    actual_rank=2,
                    rank_shift=-1,
                    margin_source="ibkr_whatif",
                )
            ],
            total_margin_used=0.0,
            margin_budget=50000.0,
            margin_remaining=50000.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        display.display_margin_reranking(plan)

        # Should have printed to console
        assert display.console.print.called

    def test_display_portfolio_plan(self, display):
        """Test display of portfolio plan."""
        candidate = create_test_candidate("AAPL")
        trade = create_test_trade(candidate)

        plan = PortfolioPlan(
            trades=[trade],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=10000.0,
            margin_budget=50000.0,
            margin_remaining=40000.0,
            total_premium_expected=250.0,
            sector_distribution={"Technology": 1},
            warnings=["Test warning"],
        )

        display.display_portfolio_plan(plan)

        # Should have printed to console
        assert display.console.print.called

    def test_display_portfolio_plan_with_skipped(self, display):
        """Test display with skipped trades."""
        candidate1 = create_test_candidate("AAPL")
        candidate2 = create_test_candidate("MSFT")

        trade = create_test_trade(candidate1, portfolio_rank=1)
        skipped = StagedTrade(
            candidate=candidate2,
            margin_per_contract=3000.0,
            margin_source="estimated",
            contracts=5,
            total_margin=15000.0,
            total_premium=250.0,
            portfolio_rank=2,
            cumulative_margin=25000.0,
            within_budget=False,
            skip_reason="Budget exceeded",
        )

        plan = PortfolioPlan(
            trades=[trade],
            skipped_trades=[skipped],
            margin_comparisons=[],
            total_margin_used=10000.0,
            margin_budget=15000.0,
            margin_remaining=5000.0,
            total_premium_expected=250.0,
            sector_distribution={"Technology": 1},
            warnings=[],
        )

        display.display_portfolio_plan(plan)

        # Verify skip reason displayed
        calls = [str(c) for c in display.console.print.call_args_list]
        assert any("skipped" in c.lower() or "budget" in c.lower() for c in calls)

    def test_display_staged_trades_empty(self, display):
        """Test display with no staged trades."""
        display.display_staged_trades([])

        # Should print "no staged trades" message
        assert display.console.print.called

    def test_display_staged_trades_with_data(self, display):
        """Test display with staged trades."""
        candidate = create_test_candidate("AAPL")
        trade = create_test_trade(candidate)

        display.display_staged_trades([trade], session="week_of_2026-02-02")

        # Should have printed table
        assert display.console.print.called

    def test_prompt_approval_yes(self, display):
        """Test approval prompt with 'y'."""
        display.console.input.return_value = "y"

        candidate = create_test_candidate("AAPL")
        trade = create_test_trade(candidate)
        plan = PortfolioPlan(
            trades=[trade],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=10000.0,
            margin_budget=50000.0,
            margin_remaining=40000.0,
            total_premium_expected=250.0,
            sector_distribution={},
            warnings=[],
        )

        result = display.prompt_approval(plan)

        assert result == "y"

    def test_prompt_approval_empty_is_yes(self, display):
        """Test approval prompt with empty input defaults to yes."""
        display.console.input.return_value = ""

        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=50000.0,
            margin_remaining=50000.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        result = display.prompt_approval(plan)

        assert result == "y"

    def test_prompt_approval_no(self, display):
        """Test approval prompt with 'n'."""
        display.console.input.return_value = "n"

        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=50000.0,
            margin_remaining=50000.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        result = display.prompt_approval(plan)

        assert result == "n"

    def test_prompt_approval_edit(self, display):
        """Test approval prompt with 'edit'."""
        display.console.input.return_value = "edit"

        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=50000.0,
            margin_remaining=50000.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        result = display.prompt_approval(plan)

        assert result == "edit"

    def test_prompt_approval_keyboard_interrupt(self, display):
        """Test approval prompt with keyboard interrupt."""
        display.console.input.side_effect = KeyboardInterrupt()

        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=50000.0,
            margin_remaining=50000.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        result = display.prompt_approval(plan)

        assert result is None


class TestBuildPortfolioInteractive:
    """Tests for build_portfolio_interactive function."""

    def test_no_candidates(self):
        """Test with no candidates."""
        console = MagicMock()

        plan, approved = build_portfolio_interactive(
            candidates=[],
            console=console,
        )

        assert plan is None
        assert approved is False

    def test_with_candidates_approved(self):
        """Test with candidates and approval."""
        console = MagicMock()
        console.input.return_value = "y"

        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0),
        ]

        plan, approved = build_portfolio_interactive(
            candidates=candidates,
            margin_budget=50000.0,
            console=console,
        )

        assert plan is not None
        assert plan.trade_count == 1
        assert approved is True

    def test_with_candidates_rejected(self):
        """Test with candidates and rejection."""
        console = MagicMock()
        console.input.return_value = "n"

        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0),
        ]

        plan, approved = build_portfolio_interactive(
            candidates=candidates,
            margin_budget=50000.0,
            console=console,
        )

        assert plan is not None
        assert approved is False

    def test_with_candidates_edit_mode(self):
        """Test with candidates and edit mode request."""
        console = MagicMock()
        console.input.return_value = "edit"

        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0),
        ]

        plan, approved = build_portfolio_interactive(
            candidates=candidates,
            margin_budget=50000.0,
            console=console,
        )

        assert plan is not None
        assert approved is False  # Edit mode not implemented yet


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
