"""Unit tests for execution scheduler module.

Tests the Monday morning execution workflow including:
- Two-stage validation
- Order execution with fill monitoring
- Execution report generation
"""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.services.execution_scheduler import (
    ExecutionConfig,
    ExecutionReport,
    ExecutionScheduler,
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
    otm_pct: float = 0.167,
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
        otm_pct=otm_pct,
    )


class TestExecutionConfig:
    """Tests for ExecutionConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = ExecutionConfig()

        assert config.fill_wait_seconds == 30
        assert config.price_adjustment_increment == 0.01
        assert config.max_price_adjustments == 2
        assert config.premarket_wakeup_minutes == 15
        assert config.dry_run_default is True

    def test_from_env(self, monkeypatch):
        """Test loading config from environment variables."""
        monkeypatch.setenv("EXECUTION_FILL_WAIT_SECONDS", "45")
        monkeypatch.setenv("PRICE_ADJUSTMENT_INCREMENT", "0.02")
        monkeypatch.setenv("MAX_PRICE_ADJUSTMENTS", "3")

        config = ExecutionConfig.from_env()

        assert config.fill_wait_seconds == 45
        assert config.price_adjustment_increment == 0.02
        assert config.max_price_adjustments == 3


class TestTradeExecutionResult:
    """Tests for TradeExecutionResult dataclass."""

    def test_is_success_filled(self):
        """Test is_success for FILLED status."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.FILLED,
            fill_price=0.50,
            contracts_filled=5,
        )

        assert result.is_success is True

    def test_is_success_working(self):
        """Test is_success for WORKING status."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.WORKING,
        )

        assert result.is_success is True

    def test_is_success_failed(self):
        """Test is_success for ERROR status."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.ERROR,
        )

        assert result.is_success is False

    def test_premium_received_filled(self):
        """Test premium calculation for filled trade."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.FILLED,
            fill_price=0.50,
            contracts_filled=5,
        )

        # 0.50 * 100 * 5 = $250
        assert result.premium_received == 250.0

    def test_premium_received_not_filled(self):
        """Test premium calculation for unfilled trade."""
        opp = create_staged_opportunity()
        result = TradeExecutionResult(
            opportunity=opp,
            status=ExecutionStatus.WORKING,
        )

        assert result.premium_received == 0.0


class TestExecutionReport:
    """Tests for ExecutionReport dataclass."""

    def test_duration_seconds(self):
        """Test duration calculation."""
        start = datetime(2026, 2, 2, 9, 15, 0)
        end = datetime(2026, 2, 2, 9, 45, 30)

        report = ExecutionReport(
            execution_date=start.date(),
            started_at=start,
            completed_at=end,
        )

        # 30 minutes + 30 seconds = 1830 seconds
        assert report.duration_seconds == 1830.0

    def test_duration_not_completed(self):
        """Test duration when not completed."""
        start = datetime(2026, 2, 2, 9, 15, 0)

        report = ExecutionReport(
            execution_date=start.date(),
            started_at=start,
            completed_at=None,
        )

        assert report.duration_seconds == 0.0

    def test_success_rate(self):
        """Test success rate calculation."""
        report = ExecutionReport(
            execution_date=datetime.now().date(),
            started_at=datetime.now(),
            executed_count=10,
            filled_count=8,
        )

        assert report.success_rate == 0.8

    def test_success_rate_no_executions(self):
        """Test success rate with no executions."""
        report = ExecutionReport(
            execution_date=datetime.now().date(),
            started_at=datetime.now(),
            executed_count=0,
            filled_count=0,
        )

        assert report.success_rate == 0.0


class TestExecutionScheduler:
    """Tests for ExecutionScheduler class."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with no IBKR."""
        return ExecutionScheduler(ibkr_client=None)

    @pytest.fixture
    def mock_ibkr(self):
        """Create a mock IBKR client."""
        mock = MagicMock()
        mock.get_stock_price.return_value = 179.0
        mock.get_option_quote.return_value = {"bid": 0.49, "ask": 0.55}
        return mock

    def test_run_monday_morning_empty(self, scheduler):
        """Test with no staged opportunities."""
        report = scheduler.run_monday_morning([], dry_run=True)

        assert report.staged_count == 0
        assert "No staged opportunities" in report.warnings[0]

    def test_run_monday_morning_dry_run(self, scheduler):
        """Test dry run execution."""
        opps = [
            create_staged_opportunity("AAPL"),
            create_staged_opportunity("MSFT", strike=300.0, staged_stock_price=350.0),
        ]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        assert report.dry_run is True
        assert report.staged_count == 2
        assert report.completed_at is not None

    def test_run_monday_morning_fills_all_dry_run(self, scheduler):
        """Test that dry run fills all trades."""
        opps = [create_staged_opportunity("AAPL")]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        assert report.filled_count == 1
        assert report.working_count == 0

    def test_run_monday_morning_with_ibkr(self, mock_ibkr):
        """Test with mock IBKR client."""
        scheduler = ExecutionScheduler(ibkr_client=mock_ibkr)
        opps = [create_staged_opportunity("AAPL")]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        # Even with IBKR, dry_run should simulate
        assert report.dry_run is True
        assert report.filled_count == 1

    def test_validate_only_premarket(self, scheduler):
        """Test pre-market only validation."""
        opps = [create_staged_opportunity("AAPL")]

        results = scheduler.validate_only_premarket(opps)

        assert len(results) == 1

    def test_validate_only_at_open(self, scheduler):
        """Test market-open only validation."""
        opps = [create_staged_opportunity("AAPL")]

        results = scheduler.validate_only_at_open(opps)

        assert len(results) == 1

    def test_get_execution_summary(self, scheduler):
        """Test execution summary generation."""
        report = ExecutionReport(
            execution_date=datetime(2026, 2, 2).date(),
            started_at=datetime(2026, 2, 2, 9, 15, 0),
            completed_at=datetime(2026, 2, 2, 9, 45, 0),
            dry_run=True,
            staged_count=5,
            validated_count=4,
            confirmed_count=3,
            executed_count=3,
            filled_count=2,
            working_count=1,
            failed_count=0,
            total_premium=500.0,
        )

        summary = scheduler.get_execution_summary(report)

        assert "EXECUTION REPORT" in summary
        assert "DRY-RUN" in summary
        assert "Staged:    5" in summary
        assert "Filled:    2" in summary


class TestExecutionSchedulerSingleTrade:
    """Tests for single trade execution."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler."""
        return ExecutionScheduler(ibkr_client=None)

    def test_execute_single_dry_run(self, scheduler):
        """Test single trade execution in dry run mode."""
        opp = create_staged_opportunity(
            symbol="AAPL",
            strike=150.0,
            staged_limit_price=0.50,
            staged_contracts=5,
        )

        result = scheduler._execute_single_trade(opp, dry_run=True)

        assert result.status == ExecutionStatus.FILLED
        assert result.fill_price == 0.50
        assert result.contracts_filled == 5
        assert result.dry_run is True

    def test_execute_single_with_adjusted_values(self, scheduler):
        """Test execution uses adjusted values."""
        opp = create_staged_opportunity(
            symbol="AAPL",
            strike=150.0,
            staged_limit_price=0.50,
            staged_contracts=5,
        )
        opp.adjusted_strike = 145.0
        opp.adjusted_limit_price = 0.45

        result = scheduler._execute_single_trade(opp, dry_run=True)

        # Should use adjusted values
        assert result.fill_price == 0.45  # adjusted_limit_price
        assert result.status == ExecutionStatus.FILLED

    def test_execute_single_no_ibkr_live(self, scheduler):
        """Test live execution without IBKR returns error."""
        opp = create_staged_opportunity("AAPL")

        result = scheduler._execute_single_trade(opp, dry_run=False)

        assert result.status == ExecutionStatus.ERROR
        assert "No IBKR client" in result.error_message


class TestExecutionSchedulerIntegration:
    """Integration tests for execution scheduler."""

    def test_full_workflow_all_pass(self):
        """Test full workflow where all opportunities pass validation."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 179.0  # -0.5% from 180
        mock_ibkr.get_option_quote.return_value = {"bid": 0.49, "ask": 0.55}

        scheduler = ExecutionScheduler(ibkr_client=mock_ibkr)

        opps = [
            create_staged_opportunity(
                symbol="AAPL",
                staged_stock_price=180.0,
                strike=150.0,
            ),
        ]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        assert report.staged_count == 1
        assert report.validated_count == 1  # Passes Stage 1
        assert report.confirmed_count == 1  # Passes Stage 2
        assert report.filled_count == 1

    def test_full_workflow_stage1_filters(self):
        """Test full workflow where Stage 1 filters some opportunities."""
        mock_ibkr = MagicMock()

        # AAPL stable, MSFT moved too much
        def price_for_symbol(symbol):
            if symbol == "AAPL":
                return 179.0  # -0.5%
            else:
                return 130.0  # -13%

        mock_ibkr.get_stock_price.side_effect = price_for_symbol
        mock_ibkr.get_option_quote.return_value = {"bid": 0.49, "ask": 0.55}

        scheduler = ExecutionScheduler(ibkr_client=mock_ibkr)

        opps = [
            create_staged_opportunity(
                symbol="AAPL", staged_stock_price=180.0, strike=150.0
            ),
            create_staged_opportunity(
                symbol="MSFT", staged_stock_price=150.0, strike=120.0
            ),
        ]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        assert report.staged_count == 2
        assert report.validated_count == 1  # Only AAPL passes Stage 1
        assert report.confirmed_count == 1
        assert report.filled_count == 1

    def test_full_workflow_all_fail_stage1(self):
        """Test full workflow where all fail Stage 1."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 100.0  # -44% from 180

        scheduler = ExecutionScheduler(ibkr_client=mock_ibkr)

        opps = [create_staged_opportunity(symbol="AAPL", staged_stock_price=180.0)]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        assert report.staged_count == 1
        assert report.validated_count == 0
        assert report.confirmed_count == 0
        assert report.filled_count == 0
        assert "No opportunities passed pre-market" in report.warnings[0]

    def test_full_workflow_multiple_trades(self):
        """Test workflow with multiple trades."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_stock_price.return_value = 179.0
        mock_ibkr.get_option_quote.return_value = {"bid": 0.49, "ask": 0.55}

        scheduler = ExecutionScheduler(ibkr_client=mock_ibkr)

        opps = [
            create_staged_opportunity(
                symbol="AAPL", staged_stock_price=180.0, strike=150.0
            ),
            create_staged_opportunity(
                symbol="GOOGL", staged_stock_price=180.0, strike=150.0
            ),
            create_staged_opportunity(
                symbol="META", staged_stock_price=180.0, strike=150.0
            ),
        ]

        report = scheduler.run_monday_morning(opps, dry_run=True)

        assert report.staged_count == 3
        assert report.validated_count == 3
        assert report.confirmed_count == 3
        assert report.executed_count == 3
        assert report.filled_count == 3
