"""Unit tests for Sunday session combined CLI command.

Tests the full Sunday workflow including:
- Session configuration
- Display utilities
- Full workflow execution
- Stage progression
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.cli.commands.sunday_session import (
    SundaySessionConfig,
    SundaySessionDisplay,
    SundaySessionResult,
    format_session_id,
    run_sunday_session,
)
from src.data.candidates import BarchartCandidate


def _make_mock_barchart_candidates() -> list[BarchartCandidate]:
    """Create realistic BarchartCandidate objects for testing.

    These candidates have values that will pass through the scorer,
    strike finder filters, and portfolio builder without issues.

    Returns:
        List of BarchartCandidate objects with realistic field values.
    """
    # Use a future Friday for expiration
    expiration = date(2026, 2, 20)

    base_candidates = [
        {
            "symbol": "AAPL",
            "underlying_price": 180.0,
            "strike": 145.0,
            "bid": 0.40,
            "moneyness_pct": -0.1944,
        },
        {
            "symbol": "MSFT",
            "underlying_price": 380.0,
            "strike": 305.0,
            "bid": 0.45,
            "moneyness_pct": -0.1974,
        },
        {
            "symbol": "JPM",
            "underlying_price": 195.0,
            "strike": 157.0,
            "bid": 0.38,
            "moneyness_pct": -0.1949,
        },
        {
            "symbol": "UNH",
            "underlying_price": 520.0,
            "strike": 420.0,
            "bid": 0.50,
            "moneyness_pct": -0.1923,
        },
        {
            "symbol": "PG",
            "underlying_price": 165.0,
            "strike": 133.0,
            "bid": 0.35,
            "moneyness_pct": -0.1939,
        },
        {
            "symbol": "V",
            "underlying_price": 280.0,
            "strike": 225.0,
            "bid": 0.42,
            "moneyness_pct": -0.1964,
        },
        {
            "symbol": "GOOGL",
            "underlying_price": 170.0,
            "strike": 137.0,
            "bid": 0.37,
            "moneyness_pct": -0.1941,
        },
        {
            "symbol": "JNJ",
            "underlying_price": 160.0,
            "strike": 129.0,
            "bid": 0.33,
            "moneyness_pct": -0.1938,
        },
    ]

    candidates = []
    for c in base_candidates:
        otm_pct = abs(c["moneyness_pct"])
        breakeven = c["strike"] - c["bid"]
        breakeven_pct = -(c["underlying_price"] - breakeven) / c["underlying_price"]
        candidates.append(
            BarchartCandidate(
                symbol=c["symbol"],
                expiration=expiration,
                strike=c["strike"],
                option_type="PUT",
                underlying_price=c["underlying_price"],
                bid=c["bid"],
                dte=11,
                moneyness_pct=c["moneyness_pct"],
                breakeven=breakeven,
                breakeven_pct=breakeven_pct,
                volume=500,
                open_interest=2000,
                iv_rank=0.45,
                delta=-0.10,
                premium_return_pct=0.01,
                annualized_return_pct=0.35,
                profit_probability=0.88,
                source="barchart_csv",
            )
        )

    return candidates


class TestSundaySessionConfig:
    """Tests for SundaySessionConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = SundaySessionConfig()

        assert config.margin_budget == 50000.0
        assert config.max_positions == 10
        assert config.max_sector_concentration == 0.40
        assert config.min_otm_pct == 0.12
        assert config.min_premium == 0.30
        assert config.use_live_margin is True
        assert config.auto_stage is False

    def test_from_env(self, monkeypatch):
        """Test loading config from environment.

        Shared values now come from the central Config singleton which
        reads the same env vars as base.py (MAX_POSITIONS, not
        SUNDAY_MAX_POSITIONS).
        """
        monkeypatch.setenv("MARGIN_BUDGET_DEFAULT", "75000")
        monkeypatch.setenv("MAX_POSITIONS", "15")
        monkeypatch.setenv("MAX_SECTOR_COUNT", "4")
        monkeypatch.setenv("OTM_MIN_PCT", "0.15")
        monkeypatch.setenv("SUNDAY_AUTO_STAGE", "true")

        config = SundaySessionConfig.from_env()

        assert config.margin_budget == 75000.0
        assert config.max_positions == 15
        assert config.max_sector_concentration == 4.0
        assert config.min_otm_pct == 0.15
        assert config.auto_stage is True

    def test_custom_values(self):
        """Test with custom values."""
        config = SundaySessionConfig(
            margin_budget=100000.0,
            max_positions=20,
            min_premium=0.50,
        )

        assert config.margin_budget == 100000.0
        assert config.max_positions == 20
        assert config.min_premium == 0.50


class TestSundaySessionResult:
    """Tests for SundaySessionResult."""

    def test_default_values(self):
        """Test default result values."""
        result = SundaySessionResult(
            session_id="week_of_2026-02-02",
            started_at=datetime.now(),
        )

        assert result.session_id == "week_of_2026-02-02"
        assert result.completed_at is None
        assert result.candidates_screened == 0
        assert result.trades_staged == 0
        assert result.warnings == []

    def test_with_values(self):
        """Test result with values set."""
        result = SundaySessionResult(
            session_id="week_of_2026-02-02",
            started_at=datetime.now(),
            completed_at=datetime.now(),
            candidates_screened=50,
            opportunities_scored=30,
            opportunities_selected=10,
            trades_staged=8,
            total_margin_required=40000.0,
            total_expected_premium=500.0,
        )

        assert result.candidates_screened == 50
        assert result.trades_staged == 8
        assert result.total_margin_required == 40000.0

    def test_warnings_list(self):
        """Test warnings list initialization."""
        result = SundaySessionResult(
            session_id="test",
            started_at=datetime.now(),
        )

        # Should be able to append
        result.warnings.append("Test warning")
        assert len(result.warnings) == 1


class TestSundaySessionDisplay:
    """Tests for SundaySessionDisplay."""

    @pytest.fixture
    def display(self):
        """Create display with mock console."""
        console = MagicMock()
        return SundaySessionDisplay(console=console)

    def test_display_session_header(self, display):
        """Test session header display."""
        display.display_session_header("week_of_2026-02-02")
        assert display.console.print.called

    def test_display_stage_header(self, display):
        """Test stage header display."""
        display.display_stage_header(1, "SCREEN CANDIDATES")
        assert display.console.print.called

    def test_display_candidates_summary_with_results(self, display):
        """Test candidates summary with results."""
        symbols = ["AAPL", "MSFT", "GOOGL"]
        display.display_candidates_summary(3, symbols)
        assert display.console.print.called

    def test_display_candidates_summary_empty(self, display):
        """Test candidates summary with no results."""
        display.display_candidates_summary(0, [])

        # Should show "no candidates" message
        calls_str = str(display.console.print.call_args_list)
        assert "No candidates" in calls_str or display.console.print.called

    def test_display_candidates_summary_truncated(self, display):
        """Test candidates summary truncates long lists."""
        symbols = [f"SYM{i}" for i in range(20)]
        display.display_candidates_summary(20, symbols)

        # Should show "and X more"
        assert display.console.print.called

    def test_display_scoring_summary(self, display):
        """Test scoring summary display."""
        top_scores = [("AAPL", 92.5), ("MSFT", 88.3), ("GOOGL", 85.0)]
        display.display_scoring_summary(30, top_scores)
        assert display.console.print.called

    def test_display_selection_summary(self, display):
        """Test selection summary display."""
        display.display_selection_summary(5, 20, ["AAPL", "MSFT", "GOOGL"])
        assert display.console.print.called

    def test_display_staging_summary(self, display):
        """Test staging summary display."""
        display.display_staging_summary(5, "week_of_2026-02-02")
        assert display.console.print.called

    def test_display_session_complete(self, display):
        """Test session complete display."""
        result = SundaySessionResult(
            session_id="week_of_2026-02-02",
            started_at=datetime(2026, 2, 1, 18, 0, 0),
            completed_at=datetime(2026, 2, 1, 18, 15, 0),
            candidates_screened=50,
            trades_staged=8,
            total_margin_required=40000.0,
            total_expected_premium=500.0,
        )

        display.display_session_complete(result)
        assert display.console.print.called

    def test_display_session_complete_with_warnings(self, display):
        """Test session complete with warnings."""
        result = SundaySessionResult(
            session_id="test",
            started_at=datetime.now(),
            completed_at=datetime.now(),
            warnings=["Warning 1", "Warning 2"],
        )

        display.display_session_complete(result)
        assert display.console.print.called

    def test_prompt_continue_yes(self, display):
        """Test continue prompt with 'y'."""
        display.console.input.return_value = "y"

        result = display.prompt_continue("Continue?")

        assert result is True

    def test_prompt_continue_no(self, display):
        """Test continue prompt with 'n'."""
        display.console.input.return_value = "n"

        result = display.prompt_continue("Continue?")

        assert result is False

    def test_prompt_continue_keyboard_interrupt(self, display):
        """Test continue prompt with keyboard interrupt."""
        display.console.input.side_effect = KeyboardInterrupt()

        result = display.prompt_continue("Continue?")

        assert result is False


class TestFormatSessionId:
    """Tests for format_session_id function."""

    def test_returns_string(self):
        """Test that function returns a string."""
        result = format_session_id()
        assert isinstance(result, str)

    def test_starts_with_week_of(self):
        """Test that ID starts with week_of_."""
        result = format_session_id()
        assert result.startswith("week_of_")

    def test_contains_date(self):
        """Test that ID contains a date."""
        result = format_session_id()

        # Extract date part
        date_part = result.replace("week_of_", "")
        parts = date_part.split("-")

        assert len(parts) == 3
        assert len(parts[0]) == 4  # Year
        assert len(parts[1]) == 2  # Month
        assert len(parts[2]) == 2  # Day


class TestRunSundaySession:
    """Tests for run_sunday_session function."""

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_full_workflow_skip_confirmations(self, mock_parse_csv):
        """Test full workflow with confirmations skipped."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()
        config = SundaySessionConfig(
            margin_budget=50000.0,
            max_positions=5,
        )

        result = run_sunday_session(
            config=config,
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        assert result.session_id.startswith("week_of_")
        assert result.candidates_screened > 0
        assert result.opportunities_scored > 0
        assert result.trades_staged > 0
        assert result.completed_at is not None

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_workflow_cancelled_at_stage2(self, mock_parse_csv):
        """Test workflow cancelled at Stage 2 (first user confirmation prompt)."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()
        # First input returns 'n' to cancel at "Proceed to selection?"
        console.input.return_value = "n"

        result = run_sunday_session(
            console=console,
            skip_confirmations=False,
            csv_file="mock.csv",
        )

        assert any("cancelled by user" in w.lower() for w in result.warnings)
        assert result.trades_staged == 0

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_workflow_calculates_premium(self, mock_parse_csv):
        """Test that workflow calculates expected premium."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()

        result = run_sunday_session(
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        assert result.total_expected_premium > 0

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_workflow_respects_max_positions(self, mock_parse_csv):
        """Test that workflow respects max_positions config."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()
        config = SundaySessionConfig(max_positions=3)

        result = run_sunday_session(
            config=config,
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        assert result.opportunities_selected <= 3
        assert result.trades_staged <= 3

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_workflow_with_ibkr_client(self, mock_parse_csv):
        """Test workflow with mock IBKR client."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()
        mock_ibkr = MagicMock()

        # Mock IBKR methods used in the workflow
        mock_ibkr.get_stock_contract.return_value = MagicMock()
        mock_ibkr.ib.reqHistoricalData.return_value = []
        mock_ibkr.get_contract_details.return_value = {"industry": "Technology"}
        mock_ibkr.get_account_summary.return_value = {
            "NetLiquidation": 500000.0,
            "InitMarginReq": 10000.0,
        }
        mock_ibkr.get_option_contract.return_value = MagicMock()
        mock_ibkr.qualify_contract.return_value = [MagicMock()]
        mock_ibkr.get_actual_margin.return_value = 5000.0

        result = run_sunday_session(
            ibkr_client=mock_ibkr,
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        assert result.trades_staged > 0

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_workflow_creates_portfolio_plan(self, mock_parse_csv):
        """Test that workflow creates a portfolio plan."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()

        result = run_sunday_session(
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        assert result.portfolio_plan is not None
        assert len(result.portfolio_plan.trades) > 0

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_workflow_timing(self, mock_parse_csv):
        """Test that workflow tracks timing."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()

        result = run_sunday_session(
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        assert result.started_at is not None
        assert result.completed_at is not None
        assert result.completed_at >= result.started_at


class TestSundaySessionIntegration:
    """Integration tests for Sunday session workflow."""

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_full_happy_path(self, mock_parse_csv):
        """Test complete happy path through all stages."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()
        config = SundaySessionConfig(
            margin_budget=100000.0,
            max_positions=10,
            max_sector_concentration=0.40,
        )

        result = run_sunday_session(
            config=config,
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        # All stages completed
        assert result.candidates_screened > 0
        assert result.opportunities_scored > 0
        assert result.opportunities_selected > 0
        assert result.trades_staged > 0

        # Portfolio built
        assert result.portfolio_plan is not None
        assert result.total_margin_required > 0
        assert result.total_expected_premium > 0

        # No errors
        assert len([w for w in result.warnings if "error" in w.lower()]) == 0

    @patch("src.tools.barchart_csv_parser.parse_barchart_csv")
    def test_session_id_consistency(self, mock_parse_csv):
        """Test that session ID is consistent within a session."""
        mock_parse_csv.return_value = _make_mock_barchart_candidates()
        console = MagicMock()

        result = run_sunday_session(
            console=console,
            skip_confirmations=True,
            csv_file="mock.csv",
        )

        # Session ID should match the week format
        assert result.session_id == format_session_id()
