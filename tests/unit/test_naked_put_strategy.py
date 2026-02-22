"""Unit tests for NakedPutStrategy."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.config.baseline_strategy import BaselineStrategy
from src.strategies.base import TradeOpportunity
from src.strategies.naked_put import NakedPutStrategy


@pytest.fixture
def mock_ibkr_client():
    """Create a mock IBKR client."""
    client = MagicMock()
    client.is_connected.return_value = True
    return client


@pytest.fixture
def baseline_config():
    """Create baseline strategy configuration with explicit values for legacy NakedPutStrategy."""
    return BaselineStrategy(
        otm_range=(0.15, 0.20),
        premium_range=(0.30, 0.50),
        dte_range=(7, 14),
    )


@pytest.fixture
def strategy(mock_ibkr_client, baseline_config):
    """Create NakedPutStrategy instance."""
    return NakedPutStrategy(mock_ibkr_client, baseline_config)


class TestNakedPutStrategyInitialization:
    """Test strategy initialization."""

    def test_initialization_with_client_and_config(
        self, mock_ibkr_client, baseline_config
    ):
        """Test strategy initializes correctly with client and config."""
        strategy = NakedPutStrategy(mock_ibkr_client, baseline_config)

        assert strategy.ibkr_client == mock_ibkr_client
        assert strategy.config == baseline_config
        assert strategy.screener is not None
        assert strategy.options_finder is not None

    def test_initialization_with_default_config(self, mock_ibkr_client):
        """Test strategy initializes with default config."""
        strategy = NakedPutStrategy(mock_ibkr_client)

        assert strategy.config is not None
        assert strategy.config.otm_range == (0.10, 0.30)
        assert strategy.config.premium_range == (0.20, 2.00)


class TestShouldEnterTrade:
    """Test entry criteria validation."""

    def test_should_enter_valid_opportunity(self, strategy):
        """Test entry validation for valid opportunity."""
        opportunity = TradeOpportunity(
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
        )

        assert strategy.should_enter_trade(opportunity) is True

    def test_should_reject_low_premium(self, strategy):
        """Test entry rejection for low premium."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=10),
            option_type="PUT",
            premium=0.20,  # Below 0.30 minimum
            contracts=5,
            otm_pct=0.18,
            dte=10,
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_high_premium(self, strategy):
        """Test entry rejection for high premium."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=10),
            option_type="PUT",
            premium=0.60,  # Above 0.50 maximum
            contracts=5,
            otm_pct=0.18,
            dte=10,
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_low_otm(self, strategy):
        """Test entry rejection for low OTM percentage."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=10),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.10,  # Below 0.15 minimum
            dte=10,
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_high_otm(self, strategy):
        """Test entry rejection for high OTM percentage."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=10),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.25,  # Above 0.20 maximum
            dte=10,
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_low_dte(self, strategy):
        """Test entry rejection for low DTE."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=5),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.18,
            dte=5,  # Below 7 day minimum
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_high_dte(self, strategy):
        """Test entry rejection for high DTE."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=20),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.18,
            dte=20,  # Above 14 day maximum
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_wrong_trend(self, strategy):
        """Test entry rejection for wrong trend."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=10),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.18,
            dte=10,
            stock_price=180.0,
            trend="downtrend",  # Not uptrend
            confidence=0.8,
        )

        assert strategy.should_enter_trade(opportunity) is False

    def test_should_reject_low_confidence(self, strategy):
        """Test entry rejection for low confidence."""
        opportunity = TradeOpportunity(
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
            confidence=0.3,  # Below 0.5 threshold
        )

        assert strategy.should_enter_trade(opportunity) is False


class TestShouldExitTrade:
    """Test exit criteria."""

    def test_profit_target_reached(self, strategy):
        """Test exit signal for profit target."""
        entry_premium = 0.50
        current_premium = 0.25  # 50% profit
        current_dte = 10
        entry_date = datetime.now()

        signal = strategy.should_exit_trade(
            entry_premium, current_premium, current_dte, entry_date
        )

        assert signal.should_exit is True
        assert signal.reason == "profit_target"
        assert signal.profit_pct == 0.50

    def test_stop_loss_triggered(self, strategy):
        """Test exit signal for stop loss."""
        entry_premium = 0.30
        current_premium = 0.90  # -200% loss
        current_dte = 10
        entry_date = datetime.now()

        signal = strategy.should_exit_trade(
            entry_premium, current_premium, current_dte, entry_date
        )

        assert signal.should_exit is True
        assert signal.reason == "stop_loss"

    def test_time_exit_triggered(self, strategy):
        """Test exit signal for time exit."""
        entry_premium = 0.40
        current_premium = 0.35
        current_dte = 2  # 2 days < 3 day threshold
        entry_date = datetime.now()

        signal = strategy.should_exit_trade(
            entry_premium, current_premium, current_dte, entry_date
        )

        assert signal.should_exit is True
        assert signal.reason == "time_exit"

    def test_no_exit_holding(self, strategy):
        """Test no exit signal when holding."""
        entry_premium = 0.40
        current_premium = 0.30  # 25% profit (below 50% target)
        current_dte = 8
        entry_date = datetime.now()

        signal = strategy.should_exit_trade(
            entry_premium, current_premium, current_dte, entry_date
        )

        assert signal.should_exit is False
        assert signal.reason == "holding"

    def test_profit_target_priority_over_time(self, strategy):
        """Test profit target takes priority over time exit."""
        entry_premium = 0.50
        current_premium = 0.25  # 50% profit
        current_dte = 2  # Also meets time exit
        entry_date = datetime.now()

        signal = strategy.should_exit_trade(
            entry_premium, current_premium, current_dte, entry_date
        )

        assert signal.should_exit is True
        assert signal.reason == "profit_target"  # Not time_exit


class TestGetPositionSize:
    """Test position sizing."""

    def test_returns_config_position_size(self, strategy):
        """Test position size matches configuration."""
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now(),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.18,
            dte=10,
            stock_price=180.0,
            trend="uptrend",
        )

        size = strategy.get_position_size(opportunity)

        assert size == strategy.config.position_size
        assert size == 5


class TestValidateConfiguration:
    """Test configuration validation."""

    def test_valid_configuration(self, strategy):
        """Test validation passes for valid config."""
        assert strategy.validate_configuration() is True

    def test_invalid_otm_range(self, mock_ibkr_client):
        """Test validation fails for invalid OTM range."""
        config = BaselineStrategy()
        config.otm_range = (0.25, 0.15)  # Invalid: max < min

        strategy = NakedPutStrategy(mock_ibkr_client, config)

        with pytest.raises(ValueError, match="Invalid OTM range"):
            strategy.validate_configuration()

    def test_invalid_premium_range(self, mock_ibkr_client):
        """Test validation fails for invalid premium range."""
        config = BaselineStrategy()
        config.premium_range = (0.50, 0.30)  # Invalid: max < min

        strategy = NakedPutStrategy(mock_ibkr_client, config)

        with pytest.raises(ValueError, match="Invalid premium range"):
            strategy.validate_configuration()

    def test_invalid_dte_range(self, mock_ibkr_client):
        """Test validation fails for invalid DTE range."""
        config = BaselineStrategy()
        config.dte_range = (14, 7)  # Invalid: max < min

        strategy = NakedPutStrategy(mock_ibkr_client, config)

        with pytest.raises(ValueError, match="Invalid DTE range"):
            strategy.validate_configuration()

    def test_invalid_position_size(self, mock_ibkr_client):
        """Test validation fails for invalid position size."""
        config = BaselineStrategy(
            otm_range=(0.15, 0.20), premium_range=(0.30, 0.50), dte_range=(7, 14)
        )
        config.position_size = -5  # Invalid: negative

        strategy = NakedPutStrategy(mock_ibkr_client, config)

        with pytest.raises(ValueError, match="Invalid position size"):
            strategy.validate_configuration()


class TestCalculateConfidence:
    """Test confidence calculation."""

    def test_high_confidence_for_optimal_opportunity(self, strategy):
        """Test high confidence for optimal parameters."""
        stock = {
            "symbol": "AAPL",
            "price": 180.0,
            "volume": 2_000_000,
            "trend": "uptrend",
            "trend_score": 0.8,
        }
        option = {
            "premium": 0.45,  # High in range
            "otm_pct": 0.175,  # Middle of range
            "dte": 10,
            "strike": 150.0,
        }

        confidence = strategy._calculate_confidence(stock, option)

        assert 0.6 <= confidence <= 1.0

    def test_lower_confidence_for_suboptimal_opportunity(self, strategy):
        """Test lower confidence for suboptimal parameters."""
        stock = {
            "symbol": "AAPL",
            "price": 180.0,
            "volume": 500_000,  # Low volume
            "trend": "uptrend",
            "trend_score": 0.3,  # Weak trend
        }
        option = {
            "premium": 0.30,  # Low in range
            "otm_pct": 0.15,  # Edge of range
            "dte": 7,
            "strike": 153.0,
        }

        confidence = strategy._calculate_confidence(stock, option)

        assert 0.0 <= confidence < 0.6


class TestGenerateReasoning:
    """Test reasoning generation."""

    def test_generates_readable_reasoning(self, strategy):
        """Test reasoning string is generated correctly."""
        stock = {
            "symbol": "AAPL",
            "price": 180.0,
            "trend": "uptrend",
        }
        option = {
            "premium": 0.40,
            "otm_pct": 0.18,
            "dte": 10,
            "strike": 147.6,
        }

        reasoning = strategy._generate_reasoning(stock, option)

        assert "AAPL" in reasoning
        assert "uptrend" in reasoning
        assert "18.0%" in reasoning
        assert "$0.40" in reasoning
        assert "10 DTE" in reasoning
