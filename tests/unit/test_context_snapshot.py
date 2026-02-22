"""Unit tests for context snapshot dataclasses."""

from datetime import datetime

import pytest

from src.data.context_snapshot import (
    DecisionContext,
    MarketContext,
    UnderlyingContext,
)


@pytest.fixture
def sample_market_context():
    """Create sample MarketContext for testing."""
    return MarketContext(
        timestamp=datetime(2026, 1, 28, 10, 30, 0),
        spy_price=450.25,
        spy_change_pct=0.015,
        qqq_price=380.75,
        qqq_change_pct=0.021,
        vix=15.5,
        vix_change_pct=-0.05,
        advance_decline_ratio=1.25,
        new_highs=150,
        new_lows=45,
        sector_leaders=[("Technology", 0.025), ("Healthcare", 0.018)],
        sector_laggards=[("Energy", -0.015), ("Utilities", -0.008)],
    )


@pytest.fixture
def sample_underlying_context():
    """Create sample UnderlyingContext for testing."""
    return UnderlyingContext(
        symbol="AAPL",
        timestamp=datetime(2026, 1, 28, 10, 30, 0),
        current_price=175.50,
        open_price=174.25,
        high_price=176.00,
        low_price=174.00,
        previous_close=173.75,
        sma_20=172.50,
        sma_50=170.00,
        trend_direction="uptrend",
        trend_strength=0.75,
        iv_rank=65.0,
        iv_percentile=70.0,
        historical_vol_20d=0.25,
        volume=50000000,
        avg_volume_20d=45000000,
        relative_volume=1.11,
        support_levels=[172.00, 170.00],
        resistance_levels=[178.00, 180.00],
    )


@pytest.fixture
def sample_decision_context(sample_market_context, sample_underlying_context):
    """Create sample DecisionContext for testing."""
    return DecisionContext(
        decision_id="decision_20260128_103000_abc123",
        timestamp=datetime(2026, 1, 28, 10, 30, 0),
        market=sample_market_context,
        underlying=sample_underlying_context,
        strategy_params={"otm_pct": 0.10, "dte_min": 30, "dte_max": 45},
        source="barchart",
        rank_position=1,
        rank_score=0.85,
        rank_factors={"premium": 0.3, "otm": 0.25, "iv_rank": 0.2},
        ai_confidence_score=0.90,
        ai_reasoning="High IV rank with strong uptrend",
    )


class TestMarketContext:
    """Test MarketContext dataclass."""

    def test_market_context_initialization(self, sample_market_context):
        """Test MarketContext initializes with correct values."""
        assert sample_market_context.spy_price == 450.25
        assert sample_market_context.vix == 15.5
        assert len(sample_market_context.sector_leaders) == 2

    def test_market_context_to_dict(self, sample_market_context):
        """Test MarketContext.to_dict() serialization."""
        result = sample_market_context.to_dict()

        assert result["spy_price"] == 450.25
        assert result["vix"] == 15.5
        assert result["timestamp"] == "2026-01-28T10:30:00"

    def test_market_context_from_dict(self, sample_market_context):
        """Test MarketContext.from_dict() deserialization."""
        data = sample_market_context.to_dict()
        result = MarketContext.from_dict(data)

        assert result.spy_price == sample_market_context.spy_price
        assert result.timestamp == sample_market_context.timestamp

    def test_market_context_minimal_fields(self):
        """Test MarketContext with minimal required fields."""
        context = MarketContext(
            timestamp=datetime(2026, 1, 28, 10, 30, 0),
            spy_price=450.0,
            spy_change_pct=0.01,
            qqq_price=380.0,
            qqq_change_pct=0.02,
            vix=15.0,
            vix_change_pct=-0.05,
        )

        assert context.advance_decline_ratio is None
        assert context.sector_leaders == []


class TestUnderlyingContext:
    """Test UnderlyingContext dataclass."""

    def test_underlying_context_initialization(self, sample_underlying_context):
        """Test UnderlyingContext initializes with correct values."""
        assert sample_underlying_context.symbol == "AAPL"
        assert sample_underlying_context.current_price == 175.50
        assert sample_underlying_context.trend_direction == "uptrend"

    def test_underlying_context_to_dict(self, sample_underlying_context):
        """Test UnderlyingContext.to_dict() serialization."""
        result = sample_underlying_context.to_dict()

        assert result["symbol"] == "AAPL"
        assert result["current_price"] == 175.50
        assert result["timestamp"] == "2026-01-28T10:30:00"

    def test_underlying_context_from_dict(self, sample_underlying_context):
        """Test UnderlyingContext.from_dict() deserialization."""
        data = sample_underlying_context.to_dict()
        result = UnderlyingContext.from_dict(data)

        assert result.symbol == sample_underlying_context.symbol
        assert result.current_price == sample_underlying_context.current_price

    def test_underlying_context_minimal_fields(self):
        """Test UnderlyingContext with minimal required fields."""
        context = UnderlyingContext(
            symbol="MSFT",
            timestamp=datetime(2026, 1, 28, 10, 30, 0),
            current_price=350.0,
            open_price=348.0,
            high_price=352.0,
            low_price=347.0,
            previous_close=349.0,
        )

        assert context.sma_20 is None
        assert context.trend_direction == "unknown"


class TestDecisionContext:
    """Test DecisionContext dataclass."""

    def test_decision_context_initialization(self, sample_decision_context):
        """Test DecisionContext initializes with correct values."""
        assert sample_decision_context.decision_id == "decision_20260128_103000_abc123"
        assert sample_decision_context.source == "barchart"
        assert sample_decision_context.rank_position == 1

    def test_decision_context_to_dict(self, sample_decision_context):
        """Test DecisionContext.to_dict() serialization."""
        result = sample_decision_context.to_dict()

        assert result["decision_id"] == "decision_20260128_103000_abc123"
        assert isinstance(result["market"], dict)
        assert result["market"]["spy_price"] == 450.25

    def test_decision_context_from_dict(self, sample_decision_context):
        """Test DecisionContext.from_dict() deserialization."""
        data = sample_decision_context.to_dict()
        result = DecisionContext.from_dict(data)

        assert result.decision_id == sample_decision_context.decision_id
        assert result.market.spy_price == sample_decision_context.market.spy_price

    def test_decision_context_without_ai_fields(
        self, sample_market_context, sample_underlying_context
    ):
        """Test DecisionContext without AI confidence/reasoning."""
        context = DecisionContext(
            decision_id="decision_test_123",
            timestamp=datetime(2026, 1, 28, 10, 30, 0),
            market=sample_market_context,
            underlying=sample_underlying_context,
            strategy_params={"otm_pct": 0.10},
            source="manual",
            rank_position=1,
            rank_score=0.75,
        )

        assert context.ai_confidence_score is None
        assert context.ai_reasoning is None
