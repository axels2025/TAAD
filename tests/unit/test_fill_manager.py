"""Unit tests for FillManager.

Tests time-boxed fill monitoring, partial fill handling, and progressive
limit adjustment with mocked IBKR client.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.fill_manager import (
    FillManager,
    FillManagerConfig,
    FillReport,
    FillStatus,
)
from src.services.premarket_validator import StagedOpportunity
from src.services.rapid_fire_executor import PendingOrder


def make_staged(symbol: str = "AAPL", contracts: int = 5) -> StagedOpportunity:
    """Create a test StagedOpportunity."""
    return StagedOpportunity(
        id=1,
        symbol=symbol,
        strike=200.0,
        expiration="2026-02-20",
        staged_stock_price=230.0,
        staged_limit_price=0.50,
        staged_contracts=contracts,
        staged_margin=4000.0,
        otm_pct=0.13,
    )


def make_pending(
    symbol: str = "AAPL",
    order_id: int = 100,
    limit: float = 0.50,
    status: str = "Submitted",
    filled_qty: int = 0,
    contracts: int = 5,
) -> PendingOrder:
    """Create a test PendingOrder."""
    return PendingOrder(
        staged=make_staged(symbol=symbol, contracts=contracts),
        contract=MagicMock(),
        order_id=order_id,
        initial_limit=limit,
        current_limit=limit,
        last_bid=0.45,
        last_ask=0.55,
        submitted_at=datetime.now(),
        order_type="Adaptive",
        last_status=status,
        filled_qty=filled_qty,
        remaining_qty=contracts - filled_qty,
    )


def make_config(**overrides) -> FillManagerConfig:
    """Create a test config with optional overrides."""
    defaults = dict(
        monitoring_window_seconds=5,  # Short window for tests
        check_interval_seconds=0.1,
        max_adjustments=5,
        adjustment_increment=0.01,
        adjustment_interval_seconds=1,
        partial_fill_threshold=0.5,
        leave_working_on_timeout=True,
        min_premium_floor=0.20,
    )
    defaults.update(overrides)
    return FillManagerConfig(**defaults)


class TestFillManagerConfig:
    """Tests for FillManagerConfig."""

    def test_default_values(self):
        config = FillManagerConfig()
        assert config.monitoring_window_seconds == 600
        assert config.max_adjustments == 5
        assert config.adjustment_increment == 0.01
        assert config.min_premium_floor == 0.20

    def test_from_env(self):
        with patch.dict("os.environ", {
            "FILL_MONITOR_WINDOW_SECONDS": "300",
            "FILL_MAX_ADJUSTMENTS": "3",
        }):
            config = FillManagerConfig.from_env()
            assert config.monitoring_window_seconds == 300
            assert config.max_adjustments == 3


class TestFillReport:
    """Tests for FillReport dataclass."""

    def test_duration_seconds(self):
        report = FillReport(
            started_at=datetime(2026, 2, 16, 9, 30, 0),
            completed_at=datetime(2026, 2, 16, 9, 35, 0),
        )
        assert report.duration_seconds == 300.0

    def test_duration_no_completed(self):
        report = FillReport()
        assert report.duration_seconds == 0.0


class TestMonitorFills:
    """Tests for the main monitor_fills loop."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.sleep = AsyncMock()
        self.client.cancel_order = AsyncMock(return_value=True)

        # Mock get_quote for progressive adjustments
        quote_mock = MagicMock()
        quote_mock.is_valid = True
        quote_mock.bid = 0.45
        quote_mock.ask = 0.55
        self.client.get_quote = AsyncMock(return_value=quote_mock)

        self.manager = FillManager(
            ibkr_client=self.client,
            config=make_config(),
        )

    @pytest.mark.asyncio
    async def test_empty_pending_orders(self):
        """Empty pending orders returns immediately."""
        report = await self.manager.monitor_fills({})
        assert report.orders_monitored == 0
        assert report.completed_at is not None

    @pytest.mark.asyncio
    async def test_already_filled_orders(self):
        """Orders already filled are counted correctly."""
        pending = {
            100: make_pending(order_id=100, status="Filled", filled_qty=5),
        }

        report = await self.manager.monitor_fills(pending)

        assert report.fully_filled == 1
        assert report.left_working == 0

    @pytest.mark.asyncio
    async def test_timeout_leaves_working(self):
        """Orders still submitted after window are left working."""
        pending = {
            100: make_pending(order_id=100, status="Submitted"),
        }

        report = await self.manager.monitor_fills(pending)

        assert report.left_working == 1
        assert report.fully_filled == 0

    @pytest.mark.asyncio
    async def test_cancelled_orders_counted(self):
        """Cancelled orders are counted correctly."""
        pending = {
            100: make_pending(order_id=100, status="Cancelled"),
        }

        report = await self.manager.monitor_fills(pending)

        assert report.cancelled == 1

    @pytest.mark.asyncio
    async def test_timeout_cancels_when_not_leave_working(self):
        """If leave_working=False, cancel unfilled orders on timeout."""
        self.manager.config.leave_working_on_timeout = False

        pending = {
            100: make_pending(order_id=100, status="Submitted"),
        }

        report = await self.manager.monitor_fills(pending)

        assert report.cancelled == 1
        self.client.cancel_order.assert_called()


class TestCheckPartialFills:
    """Tests for _check_partial_fills."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.sleep = AsyncMock()
        self.manager = FillManager(
            ibkr_client=self.client,
            config=make_config(),
        )

    @pytest.mark.asyncio
    async def test_no_fills(self):
        pending = make_pending(filled_qty=0, contracts=5)
        filled, remaining = await self.manager._check_partial_fills(pending)
        assert filled == 0
        assert remaining == 5

    @pytest.mark.asyncio
    async def test_partial_fill(self):
        pending = make_pending(filled_qty=3, contracts=5)
        filled, remaining = await self.manager._check_partial_fills(pending)
        assert filled == 3
        assert remaining == 2

    @pytest.mark.asyncio
    async def test_full_fill(self):
        pending = make_pending(filled_qty=5, contracts=5)
        filled, remaining = await self.manager._check_partial_fills(pending)
        assert filled == 5
        assert remaining == 0


class TestProgressiveAdjust:
    """Tests for _progressive_adjust."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.sleep = AsyncMock()
        self.client.cancel_order = AsyncMock(return_value=True)

        # Mock placeOrder
        mock_trade = MagicMock()
        mock_trade.order.orderId = 200
        self.client.ib = MagicMock()
        self.client.ib.placeOrder.return_value = mock_trade

        self.manager = FillManager(
            ibkr_client=self.client,
            config=make_config(),
        )

    @pytest.mark.asyncio
    async def test_adjusts_limit_down(self):
        """Limit is lowered by adjustment_increment."""
        pending = make_pending(order_id=100, limit=0.50)
        pending_orders = {100: pending}

        success = await self.manager._progressive_adjust(pending, 1, pending_orders)

        assert success is True
        assert pending.current_limit == 0.49

    @pytest.mark.asyncio
    async def test_respects_floor(self):
        """Adjustment is rejected if it would go below min_premium_floor."""
        pending = make_pending(order_id=100, limit=0.20)
        pending_orders = {100: pending}

        success = await self.manager._progressive_adjust(pending, 1, pending_orders)

        assert success is False  # 0.20 - 0.01 = 0.19 < 0.20 floor

    @pytest.mark.asyncio
    async def test_exceeds_max_adjustments(self):
        """Adjustment is rejected if max_adjustments exceeded."""
        pending = make_pending(order_id=100, limit=0.50)
        pending_orders = {100: pending}

        success = await self.manager._progressive_adjust(pending, 6, pending_orders)

        assert success is False

    @pytest.mark.asyncio
    async def test_updates_pending_orders_dict(self):
        """After cancel+replace, old key removed and new key added."""
        pending = make_pending(order_id=100, limit=0.50)
        pending_orders = {100: pending}

        success = await self.manager._progressive_adjust(pending, 1, pending_orders)

        assert success is True
        assert 100 not in pending_orders  # Old ID removed
        assert 200 in pending_orders  # New ID added
        assert pending_orders[200].current_limit == 0.49

    @pytest.mark.asyncio
    async def test_cancel_failure_returns_false(self):
        """If cancel fails, adjustment is aborted."""
        self.client.cancel_order = AsyncMock(return_value=False)
        pending = make_pending(order_id=100, limit=0.50)
        pending_orders = {100: pending}

        success = await self.manager._progressive_adjust(pending, 1, pending_orders)

        assert success is False
