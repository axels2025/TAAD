"""Unit tests for interactive selection module.

Tests the interactive symbol selection and chart review workflow
for the Sunday-to-Monday trading workflow.
"""

import pytest
from datetime import date
from unittest.mock import MagicMock, patch

from rich.console import Console

from src.cli.commands.interactive_selection import (
    InteractiveSelector,
    SelectionResult,
    SymbolSummary,
    run_interactive_selection,
)
from src.data.candidates import BarchartCandidate
from src.scoring.scorer import ScoredCandidate


def create_test_candidate(
    symbol: str,
    strike: float = 100.0,
    underlying_price: float = 120.0,
    bid: float = 0.50,
    dte: int = 7,
    iv_rank: float = 0.45,
    moneyness_pct: float = -0.17,
) -> BarchartCandidate:
    """Create a test BarchartCandidate."""
    return BarchartCandidate(
        symbol=symbol,
        expiration=date(2026, 2, 7),
        strike=strike,
        option_type="PUT",
        underlying_price=underlying_price,
        bid=bid,
        dte=dte,
        moneyness_pct=moneyness_pct,
        breakeven=strike - bid,
        breakeven_pct=moneyness_pct - (bid / underlying_price),
        volume=500,
        open_interest=1000,
        iv_rank=iv_rank,
        delta=-0.15,
        premium_return_pct=0.01,
        annualized_return_pct=0.50,
        profit_probability=0.85,
    )


def create_scored_candidate(
    symbol: str,
    composite_score: float = 75.0,
    **kwargs,
) -> ScoredCandidate:
    """Create a test ScoredCandidate."""
    candidate = create_test_candidate(symbol, **kwargs)
    scored = ScoredCandidate(candidate=candidate)
    scored.composite_score = composite_score
    scored.grade = "A" if composite_score >= 75 else "B" if composite_score >= 65 else "C"
    return scored


class TestSymbolSummary:
    """Tests for SymbolSummary dataclass."""

    def test_is_high_iv_true(self):
        """Test high IV detection when IV rank > 60%."""
        summary = SymbolSummary(
            symbol="TEST",
            stock_price=100.0,
            option_count=5,
            best_otm_pct=0.20,
            best_score=75.0,
            best_grade="A",
            iv_rank=0.65,  # 65% IV rank
        )
        assert summary.is_high_iv is True

    def test_is_high_iv_false(self):
        """Test high IV detection when IV rank <= 60%."""
        summary = SymbolSummary(
            symbol="TEST",
            stock_price=100.0,
            option_count=5,
            best_otm_pct=0.20,
            best_score=75.0,
            best_grade="A",
            iv_rank=0.55,  # 55% IV rank
        )
        assert summary.is_high_iv is False

    def test_is_high_iv_boundary(self):
        """Test high IV detection at exactly 60%."""
        summary = SymbolSummary(
            symbol="TEST",
            stock_price=100.0,
            option_count=5,
            best_otm_pct=0.20,
            best_score=75.0,
            best_grade="A",
            iv_rank=0.60,  # Exactly 60%
        )
        assert summary.is_high_iv is False  # Not > 60%


class TestInteractiveSelector:
    """Tests for InteractiveSelector class."""

    @pytest.fixture
    def selector(self):
        """Create a selector with a mock console."""
        console = MagicMock(spec=Console)
        return InteractiveSelector(console=console)

    @pytest.fixture
    def sample_candidates(self):
        """Create sample scored candidates for testing."""
        return [
            create_scored_candidate("AAPL", composite_score=85.0, iv_rank=0.45),
            create_scored_candidate("AAPL", composite_score=80.0, strike=95.0, iv_rank=0.45),
            create_scored_candidate("MSFT", composite_score=78.0, iv_rank=0.55),
            create_scored_candidate("GOOGL", composite_score=75.0, iv_rank=0.65),  # High IV
            create_scored_candidate("TSLA", composite_score=70.0, iv_rank=0.70),  # High IV
            create_scored_candidate("AMZN", composite_score=65.0, iv_rank=0.40),
        ]

    def test_aggregate_by_symbol(self, selector, sample_candidates):
        """Test aggregation of candidates by symbol."""
        summaries = selector.aggregate_by_symbol(sample_candidates)

        # Check we have the right symbols
        assert set(summaries.keys()) == {"AAPL", "MSFT", "GOOGL", "TSLA", "AMZN"}

        # Check AAPL aggregation (has 2 candidates)
        aapl = summaries["AAPL"]
        assert aapl.option_count == 2
        assert aapl.best_score == 85.0
        assert len(aapl.candidates) == 2

        # Check MSFT aggregation (has 1 candidate)
        msft = summaries["MSFT"]
        assert msft.option_count == 1
        assert msft.best_score == 78.0

    def test_aggregate_preserves_best_values(self, selector):
        """Test that aggregation preserves best values correctly."""
        candidates = [
            create_scored_candidate(
                "TEST", composite_score=60.0, moneyness_pct=-0.15, iv_rank=0.50
            ),
            create_scored_candidate(
                "TEST", composite_score=80.0, moneyness_pct=-0.25, iv_rank=0.50
            ),
            create_scored_candidate(
                "TEST", composite_score=70.0, moneyness_pct=-0.20, iv_rank=0.50
            ),
        ]

        summaries = selector.aggregate_by_symbol(candidates)
        test_summary = summaries["TEST"]

        # Best score should be 80.0
        assert test_summary.best_score == 80.0
        # Best OTM should be 25% (largest absolute value)
        assert test_summary.best_otm_pct == 0.25
        # Should have 3 candidates
        assert test_summary.option_count == 3

    def test_count_opportunities(self, selector):
        """Test counting opportunities for selected symbols."""
        summaries = {
            "AAPL": SymbolSummary(
                symbol="AAPL",
                stock_price=150.0,
                option_count=5,
                best_otm_pct=0.20,
                best_score=80.0,
                best_grade="A",
                iv_rank=0.45,
            ),
            "MSFT": SymbolSummary(
                symbol="MSFT",
                stock_price=300.0,
                option_count=3,
                best_otm_pct=0.18,
                best_score=75.0,
                best_grade="A",
                iv_rank=0.50,
            ),
            "GOOGL": SymbolSummary(
                symbol="GOOGL",
                stock_price=140.0,
                option_count=4,
                best_otm_pct=0.22,
                best_score=70.0,
                best_grade="B",
                iv_rank=0.55,
            ),
        }

        # Count for all symbols
        assert selector._count_opportunities(["AAPL", "MSFT", "GOOGL"], summaries) == 12

        # Count for subset
        assert selector._count_opportunities(["AAPL", "MSFT"], summaries) == 8

        # Count for single symbol
        assert selector._count_opportunities(["GOOGL"], summaries) == 4

        # Count for non-existent symbol
        assert selector._count_opportunities(["UNKNOWN"], summaries) == 0

    def test_get_candidates_by_symbol(self, selector, sample_candidates):
        """Test grouping candidates by symbol."""
        by_symbol = selector._get_candidates_by_symbol(sample_candidates)

        assert len(by_symbol["AAPL"]) == 2
        assert len(by_symbol["MSFT"]) == 1
        assert len(by_symbol["GOOGL"]) == 1

    def test_display_header(self, selector):
        """Test that header is displayed correctly."""
        selector._display_header(5, 20)
        selector.console.print.assert_called()


class TestSelectionResult:
    """Tests for SelectionResult dataclass."""

    def test_total_opportunities(self):
        """Test calculating total opportunities."""
        candidates_by_symbol = {
            "AAPL": [MagicMock(), MagicMock()],
            "MSFT": [MagicMock()],
            "GOOGL": [MagicMock(), MagicMock(), MagicMock()],
        }

        result = SelectionResult(
            selected_symbols=["AAPL", "GOOGL"],
            removed_symbols=["MSFT"],
            symbol_summaries={},
            candidates_by_symbol=candidates_by_symbol,
        )

        # Should only count AAPL (2) + GOOGL (3) = 5
        assert result.total_opportunities == 5

    def test_total_opportunities_empty(self):
        """Test total opportunities when no symbols selected."""
        result = SelectionResult(
            selected_symbols=[],
            removed_symbols=["AAPL", "MSFT"],
            symbol_summaries={},
            candidates_by_symbol={"AAPL": [MagicMock()], "MSFT": [MagicMock()]},
        )

        assert result.total_opportunities == 0


class TestRunInteractiveSelection:
    """Tests for the convenience function."""

    def test_run_interactive_selection_creates_selector(self):
        """Test that the convenience function creates a selector and runs it."""
        candidates = [
            create_scored_candidate("TEST", composite_score=75.0),
        ]

        with patch.object(InteractiveSelector, "run_selection") as mock_run:
            mock_run.return_value = SelectionResult(
                selected_symbols=["TEST"],
                removed_symbols=[],
                symbol_summaries={},
                candidates_by_symbol={"TEST": candidates},
            )

            result = run_interactive_selection(candidates)

            mock_run.assert_called_once()
            assert result.selected_symbols == ["TEST"]


class TestSymbolRemovalParsing:
    """Tests for symbol removal input parsing."""

    @pytest.fixture
    def selector(self):
        """Create selector with mock console."""
        console = MagicMock(spec=Console)
        return InteractiveSelector(console=console)

    def test_empty_input_removes_nothing(self, selector):
        """Test that empty input removes no symbols."""
        selector.console.input.return_value = ""

        result = selector._prompt_symbol_removal(
            ["AAPL", "MSFT", "GOOGL"],
            "Remove symbols:",
        )

        assert result == []

    def test_single_symbol_removal(self, selector):
        """Test removing a single symbol."""
        selector.console.input.return_value = "AAPL"

        result = selector._prompt_symbol_removal(
            ["AAPL", "MSFT", "GOOGL"],
            "Remove symbols:",
        )

        assert result == ["AAPL"]

    def test_multiple_symbol_removal(self, selector):
        """Test removing multiple symbols."""
        selector.console.input.return_value = "AAPL, MSFT"

        result = selector._prompt_symbol_removal(
            ["AAPL", "MSFT", "GOOGL"],
            "Remove symbols:",
        )

        assert set(result) == {"AAPL", "MSFT"}

    def test_case_insensitive_removal(self, selector):
        """Test that removal is case insensitive."""
        selector.console.input.return_value = "aapl, msft"

        result = selector._prompt_symbol_removal(
            ["AAPL", "MSFT", "GOOGL"],
            "Remove symbols:",
        )

        assert set(result) == {"AAPL", "MSFT"}

    def test_invalid_symbol_ignored(self, selector):
        """Test that invalid symbols are ignored."""
        selector.console.input.return_value = "AAPL, INVALID, MSFT"

        result = selector._prompt_symbol_removal(
            ["AAPL", "MSFT", "GOOGL"],
            "Remove symbols:",
        )

        # Should only include valid symbols
        assert set(result) == {"AAPL", "MSFT"}
        # Should warn about invalid
        selector.console.print.assert_any_call(
            "[yellow]Warning: Symbols not found (ignored): INVALID[/yellow]"
        )

    def test_keyboard_interrupt_removes_all(self, selector):
        """Test that KeyboardInterrupt removes all symbols."""
        selector.console.input.side_effect = KeyboardInterrupt()

        result = selector._prompt_symbol_removal(
            ["AAPL", "MSFT", "GOOGL"],
            "Remove symbols:",
        )

        assert result == ["AAPL", "MSFT", "GOOGL"]


class TestConfirmProceed:
    """Tests for proceed confirmation."""

    @pytest.fixture
    def selector(self):
        """Create selector with mock console."""
        console = MagicMock(spec=Console)
        return InteractiveSelector(console=console)

    def test_confirm_yes(self, selector):
        """Test confirmation with 'y'."""
        selector.console.input.return_value = "y"
        assert selector._confirm_proceed() is True

    def test_confirm_yes_uppercase(self, selector):
        """Test confirmation with 'Y'."""
        selector.console.input.return_value = "Y"
        assert selector._confirm_proceed() is True

    def test_confirm_empty_defaults_yes(self, selector):
        """Test confirmation with empty input defaults to yes."""
        selector.console.input.return_value = ""
        assert selector._confirm_proceed() is True

    def test_confirm_no(self, selector):
        """Test decline with 'n'."""
        selector.console.input.return_value = "n"
        assert selector._confirm_proceed() is False

    def test_confirm_keyboard_interrupt(self, selector):
        """Test KeyboardInterrupt cancels."""
        selector.console.input.side_effect = KeyboardInterrupt()
        assert selector._confirm_proceed() is False
