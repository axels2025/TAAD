"""Unit tests for AdaptiveOrderExecutor.

Tests the Adaptive Algo + LIMIT fallback execution strategy including:
- Adaptive order creation with correct algoStrategy and algoParams
- Fallback triggers when Adaptive status is 'Inactive'
- Floor price calculated from live bid/ask, not staged price
- Rejection when live premium < PREMIUM_MIN
- Event-driven quote fetching with timeout
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.services.adaptive_order_executor import (
    AdaptiveOrderExecutor,
    LiveQuote,
    OrderResult,
    OrderStatus,
)
from src.services.limit_price_calculator import LimitPriceCalculator
from src.services.market_calendar import MarketSession
from src.services.premarket_validator import StagedOpportunity
from src.tools.ibkr_client import Quote


@pytest.fixture(autouse=True)
def mock_market_open():
    """Default all tests to run during regular market hours."""
    mock_cal = MagicMock()
    mock_cal.get_current_session.return_value = MarketSession.REGULAR
    with patch(
        "src.services.adaptive_order_executor.MarketCalendar", return_value=mock_cal
    ):
        yield mock_cal


@pytest.fixture
def limit_calc():
    """Fixture for LimitPriceCalculator."""
    return LimitPriceCalculator()


@pytest.fixture
def mock_ibkr_client():
    """Fixture for mocked IBKRClient."""
    client = Mock()
    client.get_quote = AsyncMock()
    client.place_order = AsyncMock()
    client.cancel_order = AsyncMock()
    return client


@pytest.fixture
def executor(mock_ibkr_client, limit_calc):
    """Fixture for AdaptiveOrderExecutor."""
    with patch.dict("os.environ", {"PREMIUM_MIN": "0.30", "USE_ADAPTIVE_ALGO": "true"}):
        return AdaptiveOrderExecutor(
            ibkr_client=mock_ibkr_client,
            limit_calc=limit_calc,
        )


@pytest.fixture
def staged_opportunity():
    """Fixture for a staged opportunity."""
    return StagedOpportunity(
        id=1,
        symbol="AAPL",
        strike=150.0,
        expiration="2026-02-14",
        staged_stock_price=155.0,
        staged_limit_price=0.45,
        staged_contracts=5,
        staged_margin=3750.0,
        otm_pct=0.15,
    )


@pytest.fixture
def mock_contract():
    """Fixture for a mock contract."""
    contract = Mock()
    contract.symbol = "AAPL"
    contract.strike = 150.0
    contract.lastTradeDateOrContractMonth = "20260214"
    contract.right = "P"
    return contract


class TestAdaptiveOrderCreation:
    """Tests for creating Adaptive Algo orders."""

    def test_create_adaptive_order_has_correct_algo_strategy(self, executor):
        """Test that Adaptive order has algoStrategy = 'Adaptive'."""
        order = executor.create_adaptive_order(contracts=5, floor_price=0.45)

        assert order.algoStrategy == "Adaptive"
        assert order.action == "SELL"
        assert order.totalQuantity == 5
        assert order.lmtPrice == 0.45
        assert order.tif == "DAY"

    def test_create_adaptive_order_has_urgent_priority(self, executor):
        """Test that Adaptive order has adaptivePriority = 'Urgent'."""
        order = executor.create_adaptive_order(contracts=5, floor_price=0.45)

        assert order.algoParams is not None
        assert len(order.algoParams) == 1
        assert order.algoParams[0].tag == "adaptivePriority"
        assert order.algoParams[0].value == "Urgent"

    def test_create_adaptive_order_uses_floor_price(self, executor):
        """Test that limit price becomes the floor for Adaptive."""
        floor_price = 0.42
        order = executor.create_adaptive_order(contracts=3, floor_price=floor_price)

        assert order.lmtPrice == floor_price


class TestLimitOrderCreation:
    """Tests for creating standard LIMIT orders (fallback)."""

    def test_create_limit_order_has_correct_type(self, executor):
        """Test that LIMIT order is created correctly."""
        order = executor.create_limit_order(contracts=5, limit_price=0.45)

        assert order.action == "SELL"
        assert order.totalQuantity == 5
        assert order.lmtPrice == 0.45
        assert order.tif == "DAY"
        assert not order.algoStrategy


class TestLiveQuoteGeneration:
    """Tests for live quote fetching and assessment."""

    @pytest.mark.asyncio
    async def test_get_live_quote_returns_valid_quote(
        self, executor, mock_ibkr_client, mock_contract
    ):
        """Test that live quote is fetched and assessed correctly."""
        # Mock the quote from IBKR
        mock_ibkr_client.get_quote.return_value = Quote(
            bid=0.44,
            ask=0.48,
            is_valid=True,
        )

        quote = await executor.get_live_quote(mock_contract)

        assert quote.is_tradeable
        assert quote.bid == 0.44
        assert quote.ask == 0.48
        # With default 0.3 ratio: 0.44 + (0.46 - 0.44) * 0.3 = 0.44 + 0.006 = 0.446 ≈ 0.45
        assert quote.limit >= 0.44
        assert quote.reason == ""

    @pytest.mark.asyncio
    async def test_get_live_quote_rejects_below_minimum(
        self, executor, mock_ibkr_client, mock_contract
    ):
        """Test that quote below minimum premium is rejected."""
        # Mock quote with premium below $0.30
        mock_ibkr_client.get_quote.return_value = Quote(
            bid=0.25,
            ask=0.28,
            is_valid=True,
        )

        quote = await executor.get_live_quote(mock_contract)

        assert not quote.is_tradeable
        assert "min $0.30" in quote.reason

    @pytest.mark.asyncio
    async def test_get_live_quote_handles_invalid_quote(
        self, executor, mock_ibkr_client, mock_contract
    ):
        """Test that invalid quote from IBKR is handled."""
        # Mock invalid quote (timeout or no data)
        mock_ibkr_client.get_quote.return_value = Quote(
            bid=0,
            ask=0,
            is_valid=False,
            reason="Timeout after 0.5s",
        )

        quote = await executor.get_live_quote(mock_contract)

        assert not quote.is_tradeable
        assert quote.bid == 0
        assert quote.ask == 0
        assert "Timeout" in quote.reason


class TestLiveQuoteFromQuote:
    """Tests for LiveQuote.from_quote() factory method."""

    def test_from_quote_valid_quote_above_minimum(self, limit_calc):
        """Test creating LiveQuote from valid Quote above minimum."""
        quote = Quote(bid=0.44, ask=0.48, is_valid=True)

        live_quote = LiveQuote.from_quote(
            quote=quote,
            limit_calc=limit_calc,
            min_premium=0.30,
        )

        assert live_quote.is_tradeable
        assert live_quote.bid == 0.44
        assert live_quote.ask == 0.48
        assert live_quote.limit >= 0.44

    def test_from_quote_valid_quote_below_minimum(self, limit_calc):
        """Test creating LiveQuote from valid Quote below minimum."""
        quote = Quote(bid=0.25, ask=0.28, is_valid=True)

        live_quote = LiveQuote.from_quote(
            quote=quote,
            limit_calc=limit_calc,
            min_premium=0.30,
        )

        assert not live_quote.is_tradeable
        assert "min $0.30" in live_quote.reason

    def test_from_quote_invalid_quote(self, limit_calc):
        """Test creating LiveQuote from invalid Quote."""
        quote = Quote(bid=0, ask=0, is_valid=False, reason="Timeout")

        live_quote = LiveQuote.from_quote(
            quote=quote,
            limit_calc=limit_calc,
            min_premium=0.30,
        )

        assert not live_quote.is_tradeable
        assert live_quote.reason == "Timeout"


class TestOrderPlacement:
    """Tests for order placement with Adaptive and fallback."""

    @pytest.mark.asyncio
    async def test_place_order_rejects_untradeable_quote(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Test that order is rejected if quote is not tradeable."""
        # Mock untradeable quote
        quote = LiveQuote(
            bid=0.25,
            ask=0.28,
            limit=0.26,
            is_tradeable=False,
            reason="Premium $0.26 < min $0.30",
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "Not tradeable" in result.error_message
        assert result.live_bid == 0.25
        assert result.live_ask == 0.28

    @pytest.mark.asyncio
    async def test_place_order_uses_adaptive_when_enabled(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Test that Adaptive Algo is used when USE_ADAPTIVE_ALGO=true."""
        # Mock tradeable quote
        quote = LiveQuote(
            bid=0.44,
            ask=0.48,
            limit=0.45,
            is_tradeable=True,
        )

        # Mock successful trade
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success
        assert result.order_id == 12345
        assert result.order_type == "Adaptive"
        assert result.calculated_limit == 0.45

        # Verify Adaptive order was created
        call_args = mock_ibkr_client.place_order.call_args
        order = call_args[0][1]
        assert order.algoStrategy == "Adaptive"

    @pytest.mark.asyncio
    async def test_place_order_falls_back_to_limit_on_inactive(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Test that fallback to LIMIT occurs when Adaptive is rejected."""
        # Mock tradeable quote
        quote = LiveQuote(
            bid=0.44,
            ask=0.48,
            limit=0.45,
            is_tradeable=True,
        )

        # First call: Adaptive rejected (Inactive status)
        mock_trade_inactive = Mock()
        mock_trade_inactive.order.orderId = 12345
        mock_trade_inactive.orderStatus.status = "Inactive"

        # Second call: LIMIT fallback accepted
        mock_trade_active = Mock()
        mock_trade_active.order.orderId = 12346
        mock_trade_active.orderStatus.status = "Submitted"

        mock_ibkr_client.place_order.side_effect = [
            mock_trade_inactive,
            mock_trade_active,
        ]

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success
        assert result.order_id == 12346
        assert result.order_type == "LIMIT (fallback)"

        # Verify cancel was called for rejected Adaptive order
        mock_ibkr_client.cancel_order.assert_called_once_with(
            12345, reason="Adaptive rejected, trying LIMIT"
        )

    @pytest.mark.asyncio
    async def test_place_order_uses_limit_when_adaptive_disabled(
        self, mock_ibkr_client, limit_calc, staged_opportunity, mock_contract
    ):
        """Test that LIMIT is used when USE_ADAPTIVE_ALGO=false."""
        with patch.dict("os.environ", {"USE_ADAPTIVE_ALGO": "false"}):
            executor = AdaptiveOrderExecutor(
                ibkr_client=mock_ibkr_client,
                limit_calc=limit_calc,
            )

            quote = LiveQuote(
                bid=0.44,
                ask=0.48,
                limit=0.45,
                is_tradeable=True,
            )

            mock_trade = Mock()
            mock_trade.order.orderId = 12345
            mock_trade.orderStatus.status = "Submitted"
            mock_ibkr_client.place_order.return_value = mock_trade

            result = await executor.place_order(
                staged_opportunity, mock_contract, quote
            )

            assert result.success
            assert result.order_type == "LIMIT"

            # Verify standard LIMIT order was created
            call_args = mock_ibkr_client.place_order.call_args
            order = call_args[0][1]
            assert not order.algoStrategy

    @pytest.mark.asyncio
    async def test_place_order_handles_exception(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Test that exceptions during order placement are handled."""
        quote = LiveQuote(
            bid=0.44,
            ask=0.48,
            limit=0.45,
            is_tradeable=True,
        )

        # Mock exception during placement
        mock_ibkr_client.place_order.side_effect = Exception("Connection lost")

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "Connection lost" in result.error_message


class TestOrderResultTracking:
    """Tests for OrderResult data tracking."""

    @pytest.mark.asyncio
    async def test_order_result_tracks_limit_deviation(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Test that limit deviation is calculated correctly."""
        # Staged limit was $0.45, live quote is $0.48
        quote = LiveQuote(
            bid=0.47,
            ask=0.51,
            limit=0.48,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success
        assert result.staged_limit == 0.45
        assert result.calculated_limit == 0.48
        assert result.limit_deviation == pytest.approx(0.03, rel=1e-2)

    @pytest.mark.asyncio
    async def test_order_result_tracks_live_quotes(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Test that live bid/ask are tracked in result."""
        quote = LiveQuote(
            bid=0.44,
            ask=0.48,
            limit=0.45,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.live_bid == 0.44
        assert result.live_ask == 0.48


class TestSpreadCheck:
    """Tests for order-time bid-ask spread validation."""

    @pytest.mark.asyncio
    async def test_wide_spread_rejects_order(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Spread > 30% should reject the order."""
        # bid=0.30, ask=0.50 → spread = (0.50-0.30)/0.30 = 66%
        quote = LiveQuote(
            bid=0.30,
            ask=0.50,
            limit=0.36,
            is_tradeable=True,
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "Spread" in result.error_message
        assert "exceeds max" in result.error_message
        # Order should never have been placed
        mock_ibkr_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_narrow_spread_allows_order(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Spread <= 30% should allow the order through."""
        # bid=0.44, ask=0.48 → spread = (0.48-0.44)/0.44 = 9%
        quote = LiveQuote(
            bid=0.44,
            ask=0.48,
            limit=0.45,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success
        mock_ibkr_client.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_spread_just_under_limit_allows_order(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Spread just under 30% should be allowed."""
        # bid=0.40, ask=0.51 → spread = (0.51-0.40)/0.40 = 27.5%
        # limit ≈ 0.43, staged=0.45 → deviation < 20%, stability OK
        quote = LiveQuote(
            bid=0.40,
            ask=0.51,
            limit=0.43,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success

    @pytest.mark.asyncio
    async def test_zero_bid_rejects_as_wide_spread(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Zero bid should produce infinite spread and reject."""
        quote = LiveQuote(
            bid=0.0,
            ask=0.05,
            limit=0.01,
            is_tradeable=True,
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "Spread" in result.error_message

    @pytest.mark.asyncio
    async def test_custom_spread_limit(
        self, mock_ibkr_client, limit_calc, staged_opportunity, mock_contract
    ):
        """Custom MAX_EXECUTION_SPREAD_PCT is respected."""
        with patch.dict("os.environ", {"MAX_EXECUTION_SPREAD_PCT": "0.50"}):
            executor = AdaptiveOrderExecutor(
                ibkr_client=mock_ibkr_client,
                limit_calc=limit_calc,
            )

        # bid=0.30, ask=0.44 → spread = (0.44-0.30)/0.30 = 46.7% < 50%
        quote = LiveQuote(
            bid=0.30,
            ask=0.44,
            limit=0.34,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success


class TestPriceStabilityCheck:
    """Tests for price stability validation (staged vs live limit deviation)."""

    @pytest.mark.asyncio
    async def test_large_deviation_rejects_order(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """>50% deviation between staged and live limit rejects the order.

        staged_limit=0.45, live limit=0.70 → deviation=0.25/0.45=55.6% → "56%"
        """
        quote = LiveQuote(
            bid=0.68,
            ask=0.74,
            limit=0.70,
            is_tradeable=True,
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "Price unstable" in result.error_message
        assert "deviation" in result.error_message
        assert result.limit_deviation == pytest.approx(0.25, rel=1e-2)
        mock_ibkr_client.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_moderate_deviation_warns_but_proceeds(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """20-50% deviation logs warning but order still goes through.

        staged_limit=0.45, live limit=0.58 → deviation=29%
        """
        quote = LiveQuote(
            bid=0.56,
            ask=0.62,
            limit=0.58,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success
        mock_ibkr_client.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_small_deviation_no_issue(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """<20% deviation passes cleanly.

        staged_limit=0.45, live limit=0.48 → deviation=6.7%
        """
        quote = LiveQuote(
            bid=0.46,
            ask=0.52,
            limit=0.48,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success

    @pytest.mark.asyncio
    async def test_zero_staged_limit_skips_check(
        self, executor, mock_contract, mock_ibkr_client
    ):
        """Zero staged limit price should skip the stability check."""
        staged = StagedOpportunity(
            id=1,
            symbol="AAPL",
            strike=150.0,
            expiration="2026-02-14",
            staged_stock_price=155.0,
            staged_limit_price=0.0,
            staged_contracts=5,
            staged_margin=3750.0,
            otm_pct=0.15,
        )

        quote = LiveQuote(
            bid=0.44,
            ask=0.48,
            limit=0.45,
            is_tradeable=True,
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged, mock_contract, quote)

        assert result.success

    @pytest.mark.asyncio
    async def test_deviation_stored_in_result_on_rejection(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client
    ):
        """Rejected order should have limit_deviation populated."""
        # staged=0.45, live=0.90 → deviation=100%
        quote = LiveQuote(
            bid=0.88,
            ask=0.94,
            limit=0.90,
            is_tradeable=True,
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert result.limit_deviation == pytest.approx(0.45, rel=1e-2)
        assert result.staged_limit == 0.45
        assert result.calculated_limit == 0.90


class TestMarketHoursCheck:
    """Tests for market hours enforcement in AdaptiveOrderExecutor."""

    @pytest.mark.asyncio
    async def test_rejects_order_when_market_closed(
        self, executor, staged_opportunity, mock_contract, mock_market_open
    ):
        """Orders when market is closed are rejected."""
        mock_market_open.get_current_session.return_value = MarketSession.CLOSED

        quote = LiveQuote(
            bid=0.44, ask=0.48, limit=0.45, is_tradeable=True
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "closed" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_rejects_order_on_weekend(
        self, executor, staged_opportunity, mock_contract, mock_market_open
    ):
        """Orders on weekends are rejected."""
        mock_market_open.get_current_session.return_value = MarketSession.WEEKEND

        quote = LiveQuote(
            bid=0.44, ask=0.48, limit=0.45, is_tradeable=True
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "weekend" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_rejects_order_on_holiday(
        self, executor, staged_opportunity, mock_contract, mock_market_open
    ):
        """Orders on holidays are rejected."""
        mock_market_open.get_current_session.return_value = MarketSession.HOLIDAY

        quote = LiveQuote(
            bid=0.44, ask=0.48, limit=0.45, is_tradeable=True
        )

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert not result.success
        assert "holiday" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_allows_order_during_regular_hours(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client, mock_market_open
    ):
        """Orders during regular hours proceed normally."""
        mock_market_open.get_current_session.return_value = MarketSession.REGULAR

        quote = LiveQuote(
            bid=0.44, ask=0.48, limit=0.45, is_tradeable=True
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success

    @pytest.mark.asyncio
    async def test_allows_order_during_pre_market(
        self, executor, staged_opportunity, mock_contract, mock_ibkr_client, mock_market_open
    ):
        """Pre-market orders are allowed for 9:30 AM execution."""
        mock_market_open.get_current_session.return_value = MarketSession.PRE_MARKET

        quote = LiveQuote(
            bid=0.44, ask=0.48, limit=0.45, is_tradeable=True
        )

        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Submitted"
        mock_ibkr_client.place_order.return_value = mock_trade

        result = await executor.place_order(staged_opportunity, mock_contract, quote)

        assert result.success
