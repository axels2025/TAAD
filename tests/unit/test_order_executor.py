"""Unit tests for OrderExecutor."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config.base import Config
from src.execution.order_executor import OrderExecutor, OrderResult, OrderStatus
from src.strategies.base import TradeOpportunity


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    client = MagicMock()
    client.is_connected.return_value = True
    client.ib = MagicMock()
    return client


@pytest.fixture
def config():
    """Create config."""
    return Config()


@pytest.fixture
def trade_opportunity():
    """Create sample trade opportunity."""
    return TradeOpportunity(
        symbol="AAPL",
        strike=150.0,
        expiration=datetime.now() + timedelta(days=10),
        option_type="PUT",
        premium=0.40,
        contracts=5,
        otm_pct=0.18,
        dte=10,
        stock_price=180.0,
        trend="uptrend",
        confidence=0.8,
        reasoning="Test trade",
        margin_required=1500.0,
    )


@pytest.fixture
@patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
def executor_dry_run(mock_ibkr_client, config):
    """Create OrderExecutor in dry-run mode."""
    return OrderExecutor(mock_ibkr_client, config, dry_run=True)


@pytest.fixture
@patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
def executor_live(mock_ibkr_client, config):
    """Create OrderExecutor in live mode (paper trading)."""
    return OrderExecutor(mock_ibkr_client, config, dry_run=False)


class TestOrderExecutorInitialization:
    """Test OrderExecutor initialization."""

    @patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
    def test_initialization_in_paper_trading_mode(self, mock_ibkr_client, config):
        """Test successful initialization in paper trading mode."""
        executor = OrderExecutor(mock_ibkr_client, config, dry_run=True)

        assert executor.ibkr_client == mock_ibkr_client
        assert executor.config == config
        assert executor.dry_run is True

    @patch.dict("os.environ", {"PAPER_TRADING": "false", "IBKR_PORT": "7497"})
    def test_initialization_fails_without_paper_trading(self, mock_ibkr_client, config):
        """Test initialization fails when PAPER_TRADING is not true."""
        with pytest.raises(ValueError, match="PAPER_TRADING is not set to 'true'"):
            OrderExecutor(mock_ibkr_client, config)

    @patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7496"})
    def test_initialization_fails_with_wrong_port(self, mock_ibkr_client, config):
        """Test initialization fails when port is not 7497."""
        with pytest.raises(ValueError, match="not the paper trading port"):
            OrderExecutor(mock_ibkr_client, config)


class TestDryRunMode:
    """Test dry-run mode functionality."""

    def test_dry_run_executes_successfully(self, executor_dry_run, trade_opportunity):
        """Test dry-run mode simulates order successfully."""
        result = executor_dry_run.execute_trade(trade_opportunity)

        assert result.success is True
        assert result.dry_run is True
        assert result.status == OrderStatus.PENDING
        assert result.order_id is None
        assert "DRY-RUN" in result.reasoning

    def test_dry_run_with_limit_order(self, executor_dry_run, trade_opportunity):
        """Test dry-run with limit order."""
        result = executor_dry_run.execute_trade(
            trade_opportunity,
            order_type="LIMIT",
            limit_price=0.42,
        )

        assert result.success is True
        assert result.dry_run is True
        assert (
            "@ limit $0.42" in result.reasoning or "limit" in result.reasoning.lower()
        )

    def test_dry_run_with_market_order(self, executor_dry_run, trade_opportunity):
        """Test dry-run with market order."""
        result = executor_dry_run.execute_trade(
            trade_opportunity,
            order_type="MARKET",
        )

        assert result.success is True
        assert result.dry_run is True
        assert "market" in result.reasoning.lower()

    def test_dry_run_includes_trade_details(self, executor_dry_run, trade_opportunity):
        """Test dry-run includes all trade details."""
        result = executor_dry_run.execute_trade(trade_opportunity)

        assert trade_opportunity.symbol in result.reasoning
        assert (
            str(trade_opportunity.strike) in result.reasoning
            or "150" in result.reasoning
        )


class TestTradeValidation:
    """Test pre-flight validation."""

    def test_validation_passes_for_valid_trade(
        self, executor_dry_run, trade_opportunity
    ):
        """Test validation passes for valid trade."""
        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is True
        assert validation["reason"] == ""

    def test_validation_fails_when_disconnected(
        self, executor_dry_run, trade_opportunity
    ):
        """Test validation fails when IBKR disconnected."""
        executor_dry_run.ibkr_client.is_connected.return_value = False

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is False
        assert "not connected" in validation["reason"].lower()

    def test_validation_fails_for_invalid_contracts(
        self, executor_dry_run, trade_opportunity
    ):
        """Test validation fails for invalid contract quantity."""
        trade_opportunity.contracts = 0

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is False
        assert "contract" in validation["reason"].lower()

    def test_validation_fails_for_invalid_premium(
        self, executor_dry_run, trade_opportunity
    ):
        """Test validation fails for invalid premium."""
        trade_opportunity.premium = -0.10

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is False
        assert "premium" in validation["reason"].lower()

    def test_validation_fails_for_missing_symbol(
        self, executor_dry_run, trade_opportunity
    ):
        """Test validation fails for missing symbol."""
        trade_opportunity.symbol = ""

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is False
        assert "symbol" in validation["reason"].lower()

    def test_validation_fails_for_expired_option(
        self, executor_dry_run, trade_opportunity
    ):
        """Test validation fails for expired option."""
        trade_opportunity.expiration = datetime.now() - timedelta(days=2)

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is False
        assert "expired" in validation["reason"].lower()

    def test_validation_fails_for_suspiciously_high_premium(
        self, executor_dry_run, trade_opportunity
    ):
        """Premium > 20% of strike should be rejected as suspicious.

        strike=150, premium=35 → 35/150 = 23.3% > 20%
        """
        trade_opportunity.premium = 35.0

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is False
        assert "suspiciously high" in validation["reason"].lower()

    def test_validation_passes_for_normal_premium(
        self, executor_dry_run, trade_opportunity
    ):
        """Normal premium (1-5% of strike) passes without issue.

        strike=150, premium=0.40 → 0.40/150 = 0.27%
        """
        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is True

    def test_validation_passes_for_high_but_acceptable_premium(
        self, executor_dry_run, trade_opportunity
    ):
        """Premium at 19% of strike should still pass.

        strike=150, premium=28.5 → 28.5/150 = 19%
        """
        trade_opportunity.premium = 28.5

        validation = executor_dry_run._validate_trade(trade_opportunity)

        assert validation["valid"] is True


class TestMarketHoursCheck:
    """Test market hours enforcement at order execution level."""

    @patch("src.execution.order_executor.MarketCalendar")
    @patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
    def test_rejects_order_when_market_closed(
        self, mock_cal_cls, mock_ibkr_client, config, trade_opportunity
    ):
        """Orders outside market hours are rejected (live mode)."""
        from src.services.market_calendar import MarketSession

        mock_cal = MagicMock()
        mock_cal.get_current_session.return_value = MarketSession.CLOSED
        mock_cal_cls.return_value = mock_cal

        executor = OrderExecutor(mock_ibkr_client, config, dry_run=False)
        result = executor.execute_trade(trade_opportunity, "LIMIT", 0.40)

        assert not result.success
        assert result.status == OrderStatus.REJECTED
        assert "closed" in result.error_message.lower()

    @patch("src.execution.order_executor.MarketCalendar")
    @patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
    def test_rejects_order_on_weekend(
        self, mock_cal_cls, mock_ibkr_client, config, trade_opportunity
    ):
        """Orders on weekends are rejected."""
        from src.services.market_calendar import MarketSession

        mock_cal = MagicMock()
        mock_cal.get_current_session.return_value = MarketSession.WEEKEND
        mock_cal_cls.return_value = mock_cal

        executor = OrderExecutor(mock_ibkr_client, config, dry_run=False)
        result = executor.execute_trade(trade_opportunity, "LIMIT", 0.40)

        assert not result.success
        assert "weekend" in result.error_message.lower()

    @patch("src.execution.order_executor.MarketCalendar")
    @patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
    def test_allows_order_during_regular_hours(
        self, mock_cal_cls, mock_ibkr_client, config, trade_opportunity
    ):
        """Orders during regular hours proceed to validation."""
        from src.services.market_calendar import MarketSession

        mock_cal = MagicMock()
        mock_cal.get_current_session.return_value = MarketSession.REGULAR
        mock_cal_cls.return_value = mock_cal

        executor = OrderExecutor(mock_ibkr_client, config, dry_run=False)
        # Will proceed past market check to validation (which passes),
        # then fail at order placement since mock isn't set up for that
        result = executor.execute_trade(trade_opportunity, "LIMIT", 0.40)

        # The market hours check did NOT reject — it moved on
        assert result.error_message is None or "closed" not in (
            result.error_message or ""
        ).lower()

    @patch("src.execution.order_executor.MarketCalendar")
    @patch.dict("os.environ", {"PAPER_TRADING": "true", "IBKR_PORT": "7497"})
    def test_allows_order_during_pre_market(
        self, mock_cal_cls, mock_ibkr_client, config, trade_opportunity
    ):
        """Pre-market orders are allowed (needed for 9:30 AM execution)."""
        from src.services.market_calendar import MarketSession

        mock_cal = MagicMock()
        mock_cal.get_current_session.return_value = MarketSession.PRE_MARKET
        mock_cal_cls.return_value = mock_cal

        executor = OrderExecutor(mock_ibkr_client, config, dry_run=False)
        result = executor.execute_trade(trade_opportunity, "LIMIT", 0.40)

        # Market check passed — didn't reject for market hours
        assert result.error_message is None or "closed" not in (
            result.error_message or ""
        ).lower()

    def test_dry_run_bypasses_market_hours_check(
        self, executor_dry_run, trade_opportunity
    ):
        """Dry-run mode skips market hours check entirely."""
        # executor_dry_run is dry_run=True, should not check market hours
        result = executor_dry_run.execute_trade(trade_opportunity, "LIMIT", 0.40)

        # Should succeed as a dry-run regardless of current time
        assert result.success
        assert result.dry_run is True


class TestOrderCreation:
    """Test order object creation."""

    def test_creates_limit_order_correctly(self, executor_dry_run, trade_opportunity):
        """Test limit order creation."""
        order = executor_dry_run._create_order(
            trade_opportunity,
            "LIMIT",
            0.42,
        )

        assert order.action == "SELL"
        assert order.totalQuantity == trade_opportunity.contracts
        assert order.lmtPrice == 0.42

    def test_creates_market_order_correctly(self, executor_dry_run, trade_opportunity):
        """Test market order creation."""
        order = executor_dry_run._create_order(
            trade_opportunity,
            "MARKET",
            None,
        )

        assert order.action == "SELL"
        assert order.totalQuantity == trade_opportunity.contracts

    def test_uses_opportunity_premium_as_default_limit(
        self, executor_dry_run, trade_opportunity
    ):
        """Test uses opportunity premium as default limit price."""
        order = executor_dry_run._create_order(
            trade_opportunity,
            "LIMIT",
            None,
        )

        assert order.lmtPrice == trade_opportunity.premium

    def test_raises_error_for_invalid_order_type(
        self, executor_dry_run, trade_opportunity
    ):
        """Test raises error for invalid order type."""
        with pytest.raises(ValueError, match="Unsupported order type"):
            executor_dry_run._create_order(
                trade_opportunity,
                "INVALID",
                None,
            )


class TestOrderResult:
    """Test OrderResult dataclass."""

    def test_order_result_to_dict(self):
        """Test OrderResult converts to dict correctly."""
        result = OrderResult(
            success=True,
            order_id=123,
            status=OrderStatus.FILLED,
            fill_price=0.40,
            fill_time=datetime(2026, 1, 21, 10, 30),
            slippage=0.02,
            dry_run=False,
            reasoning="Test trade",
        )

        result_dict = result.to_dict()

        assert result_dict["success"] is True
        assert result_dict["order_id"] == 123
        assert result_dict["status"] == "filled"
        assert result_dict["fill_price"] == 0.40
        assert result_dict["slippage"] == 0.02


class TestFailureScenarios:
    """Test various failure scenarios."""

    def test_execute_trade_fails_validation(self, executor_dry_run, trade_opportunity):
        """Test execute_trade fails when validation fails."""
        trade_opportunity.contracts = -5  # Invalid

        result = executor_dry_run.execute_trade(trade_opportunity)

        assert result.success is False
        assert result.status == OrderStatus.REJECTED
        assert result.error_message is not None
