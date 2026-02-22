"""Unit tests for ContextCaptureService."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.data.context_snapshot import DecisionContext, MarketContext, UnderlyingContext
from src.services.context_capture import ContextCaptureService
from src.strategies.base import TradeOpportunity


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    client = MagicMock()
    return client


@pytest.fixture
def context_service(mock_ibkr_client):
    """Create ContextCaptureService with mock client."""
    return ContextCaptureService(mock_ibkr_client, timeout=10)


@pytest.fixture
def mock_ticker():
    """Create mock ticker with market data."""
    ticker = Mock()
    ticker.marketPrice = Mock(return_value=450.25)
    ticker.close = 448.50
    ticker.open = 449.00
    ticker.high = 451.00
    ticker.low = 448.00
    ticker.volume = 50000000
    ticker.avgVolume = 45000000
    return ticker


@pytest.fixture
def sample_opportunity():
    """Create sample trade opportunity."""
    return TradeOpportunity(
        symbol="AAPL",
        strike=175.0,
        expiration=datetime.now() + timedelta(days=30),
        option_type="PUT",
        premium=2.50,
        contracts=1,
        otm_pct=0.10,
        dte=30,
        stock_price=195.0,
        trend="uptrend",
        confidence=0.85,
        margin_required=3500.0,
    )


class TestContextCaptureServiceInitialization:
    """Test ContextCaptureService initialization."""

    def test_initialization_with_client(self, mock_ibkr_client):
        """Test service initializes with IBKR client."""
        service = ContextCaptureService(mock_ibkr_client, timeout=15)

        assert service.ibkr is mock_ibkr_client
        assert service.timeout == 15

    def test_initialization_default_timeout(self, mock_ibkr_client):
        """Test service uses default timeout."""
        service = ContextCaptureService(mock_ibkr_client)

        assert service.timeout == 10


class TestCaptureMarketContext:
    """Test capture_market_context method."""

    def test_capture_market_context_success(self, context_service, mock_ticker):
        """Test successful market context capture."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)

        # Act
        result = context_service.capture_market_context()

        # Assert
        assert isinstance(result, MarketContext)
        assert result.spy_price == 450.25
        assert result.spy_change_pct == pytest.approx(0.0039, abs=0.001)
        assert context_service.ibkr.ticker.call_count == 3  # SPY, QQQ, VIX

    def test_capture_market_context_calls_correct_symbols(
        self, context_service, mock_ticker
    ):
        """Test market context fetches SPY, QQQ, VIX."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)

        # Act
        context_service.capture_market_context()

        # Assert
        calls = [call[0][0] for call in context_service.ibkr.ticker.call_args_list]
        assert "SPY" in calls
        assert "QQQ" in calls
        assert "VIX" in calls

    def test_capture_market_context_handles_missing_close(self, context_service):
        """Test market context handles missing close price."""
        # Arrange
        ticker = Mock()
        ticker.marketPrice = Mock(return_value=450.0)
        ticker.close = None  # Missing close
        context_service.ibkr.ticker = Mock(return_value=ticker)

        # Act
        result = context_service.capture_market_context()

        # Assert
        assert result.spy_change_pct == 0.0  # Should default to 0

    def test_capture_market_context_handles_error(self, context_service):
        """Test market context returns minimal data on error."""
        # Arrange
        context_service.ibkr.ticker = Mock(side_effect=Exception("IBKR error"))

        # Act
        result = context_service.capture_market_context()

        # Assert
        assert isinstance(result, MarketContext)
        assert result.spy_price == 0.0
        assert result.vix == 0.0

    def test_capture_market_context_timestamp(self, context_service, mock_ticker):
        """Test market context includes timestamp."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)
        before = datetime.now()

        # Act
        result = context_service.capture_market_context()
        after = datetime.now()

        # Assert
        assert before <= result.timestamp <= after


class TestCaptureUnderlyingContext:
    """Test capture_underlying_context method."""

    def test_capture_underlying_context_success(self, context_service, mock_ticker):
        """Test successful underlying context capture."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)

        # Act
        result = context_service.capture_underlying_context("AAPL")

        # Assert
        assert isinstance(result, UnderlyingContext)
        assert result.symbol == "AAPL"
        assert result.current_price == 450.25
        assert result.volume == 50000000

    def test_capture_underlying_context_trend_uptrend(self, context_service):
        """Test trend detection identifies uptrend."""
        # Arrange
        ticker = Mock()
        ticker.marketPrice = Mock(return_value=103.0)  # +3% from close
        ticker.close = 100.0
        ticker.open = 101.0
        ticker.high = 104.0
        ticker.low = 100.0
        ticker.volume = 1000000
        ticker.avgVolume = 900000
        context_service.ibkr.ticker = Mock(return_value=ticker)

        # Act
        result = context_service.capture_underlying_context("TEST")

        # Assert
        assert result.trend_direction == "uptrend"
        assert result.trend_strength > 0

    def test_capture_underlying_context_trend_downtrend(self, context_service):
        """Test trend detection identifies downtrend."""
        # Arrange
        ticker = Mock()
        ticker.marketPrice = Mock(return_value=97.0)  # -3% from close
        ticker.close = 100.0
        ticker.open = 99.0
        ticker.high = 100.0
        ticker.low = 96.0
        ticker.volume = 1000000
        ticker.avgVolume = 900000
        context_service.ibkr.ticker = Mock(return_value=ticker)

        # Act
        result = context_service.capture_underlying_context("TEST")

        # Assert
        assert result.trend_direction == "downtrend"

    def test_capture_underlying_context_trend_sideways(self, context_service):
        """Test trend detection identifies sideways."""
        # Arrange
        ticker = Mock()
        ticker.marketPrice = Mock(return_value=100.5)  # +0.5% from close
        ticker.close = 100.0
        ticker.open = 100.2
        ticker.high = 101.0
        ticker.low = 99.5
        ticker.volume = 1000000
        ticker.avgVolume = 900000
        context_service.ibkr.ticker = Mock(return_value=ticker)

        # Act
        result = context_service.capture_underlying_context("TEST")

        # Assert
        assert result.trend_direction == "sideways"

    def test_capture_underlying_context_relative_volume(self, context_service):
        """Test relative volume calculation."""
        # Arrange
        ticker = Mock()
        ticker.marketPrice = Mock(return_value=100.0)
        ticker.close = 100.0
        ticker.open = 100.0
        ticker.high = 101.0
        ticker.low = 99.0
        ticker.volume = 5500000  # 1.1x average
        ticker.avgVolume = 5000000
        context_service.ibkr.ticker = Mock(return_value=ticker)

        # Act
        result = context_service.capture_underlying_context("TEST")

        # Assert
        assert result.relative_volume == pytest.approx(1.1, abs=0.01)

    def test_capture_underlying_context_handles_error(self, context_service):
        """Test underlying context returns minimal data on error."""
        # Arrange
        context_service.ibkr.ticker = Mock(side_effect=Exception("IBKR error"))

        # Act
        result = context_service.capture_underlying_context("FAIL")

        # Assert
        assert isinstance(result, UnderlyingContext)
        assert result.symbol == "FAIL"
        assert result.current_price == 0.0


class TestCaptureFullContext:
    """Test capture_full_context method."""

    def test_capture_full_context_success(
        self, context_service, mock_ticker, sample_opportunity
    ):
        """Test successful full context capture."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)
        strategy_params = {"otm_pct": 0.10, "dte_min": 30}
        rank_info = {"source": "barchart", "position": 1, "score": 0.85}

        # Act
        result = context_service.capture_full_context(
            sample_opportunity, strategy_params, rank_info
        )

        # Assert
        assert isinstance(result, DecisionContext)
        assert result.decision_id.startswith("decision_")
        assert isinstance(result.market, MarketContext)
        assert isinstance(result.underlying, UnderlyingContext)
        assert result.underlying.symbol == "AAPL"
        assert result.source == "barchart"
        assert result.rank_position == 1

    def test_capture_full_context_generates_unique_id(
        self, context_service, mock_ticker, sample_opportunity
    ):
        """Test each call generates unique decision ID."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)
        strategy_params = {}
        rank_info = {"source": "manual", "position": 1, "score": 0.75}

        # Act
        result1 = context_service.capture_full_context(
            sample_opportunity, strategy_params, rank_info
        )
        result2 = context_service.capture_full_context(
            sample_opportunity, strategy_params, rank_info
        )

        # Assert
        assert result1.decision_id != result2.decision_id

    def test_capture_full_context_preserves_strategy_params(
        self, context_service, mock_ticker, sample_opportunity
    ):
        """Test strategy parameters are preserved in context."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)
        strategy_params = {
            "otm_pct": 0.12,
            "dte_min": 25,
            "dte_max": 40,
            "min_premium": 0.50,
        }
        rank_info = {"source": "barchart", "position": 2, "score": 0.80}

        # Act
        result = context_service.capture_full_context(
            sample_opportunity, strategy_params, rank_info
        )

        # Assert
        assert result.strategy_params == strategy_params

    def test_capture_full_context_handles_missing_rank_fields(
        self, context_service, mock_ticker, sample_opportunity
    ):
        """Test missing rank info fields default appropriately."""
        # Arrange
        context_service.ibkr.ticker = Mock(return_value=mock_ticker)
        strategy_params = {}
        rank_info = {}  # Empty rank info

        # Act
        result = context_service.capture_full_context(
            sample_opportunity, strategy_params, rank_info
        )

        # Assert
        assert result.source == "unknown"
        assert result.rank_position == 0
        assert result.rank_score == 0.0


class TestGenerateDecisionId:
    """Test _generate_decision_id method."""

    def test_generate_decision_id_format(self, context_service):
        """Test decision ID has correct format."""
        # Act
        result = context_service._generate_decision_id()

        # Assert
        assert result.startswith("decision_")
        parts = result.split("_")
        assert len(parts) == 4  # decision, date, time, uuid
        assert len(parts[1]) == 8  # YYYYMMDD
        assert len(parts[2]) == 6  # HHMMSS
        assert len(parts[3]) == 8  # UUID (first 8 chars)

    def test_generate_decision_id_unique(self, context_service):
        """Test generates unique IDs."""
        # Act
        id1 = context_service._generate_decision_id()
        id2 = context_service._generate_decision_id()

        # Assert
        assert id1 != id2
