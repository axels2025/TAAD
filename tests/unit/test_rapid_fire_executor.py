"""Unit tests for RapidFireExecutor.

Tests the rapid-fire parallel execution system including:
- Parallel submission timing (<3 seconds for all orders)
- Event-driven fill monitoring via callbacks
- Condition-based adjustment triggers (>$0.02 outside spread)
- Max wait timeout handling
- Execution report generation
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest

from src.services.adaptive_order_executor import (
    AdaptiveOrderExecutor,
    LiveQuote,
    OrderResult,
    OrderStatus as AdaptiveOrderStatus,
)
from src.services.premarket_validator import StagedOpportunity
from src.services.rapid_fire_executor import (
    ExecutionReport,
    OrderStatus,
    PendingOrder,
    RapidFireExecutor,
)


@pytest.fixture
def mock_ibkr_client():
    """Fixture for mocked IBKRClient."""
    client = Mock()
    client.get_option_contract = Mock()
    client.qualify_contracts_async = AsyncMock()
    client.get_quote = AsyncMock()
    client.place_order = AsyncMock()
    client.cancel_order = AsyncMock()
    client.sleep = AsyncMock()
    # RapidFireExecutor.__init__ does: self.client.ib.orderStatusEvent += handler
    # Mock needs __iadd__ support on the orderStatusEvent attribute
    order_status_event = Mock()
    order_status_event.__iadd__ = Mock(return_value=order_status_event)
    client.ib = Mock()
    client.ib.orderStatusEvent = order_status_event
    return client


@pytest.fixture
def mock_adaptive_executor():
    """Fixture for mocked AdaptiveOrderExecutor."""
    executor = Mock(spec=AdaptiveOrderExecutor)
    executor.get_live_quote = AsyncMock()
    executor.place_order = AsyncMock()
    executor.limit_calc = Mock()
    executor.limit_calc.calculate_sell_limit = Mock(return_value=0.45)
    return executor


@pytest.fixture
def rapid_fire_executor(mock_ibkr_client, mock_adaptive_executor):
    """Fixture for RapidFireExecutor."""
    with patch.dict(
        "os.environ",
        {
            "RAPID_FIRE_MAX_WAIT_SECONDS": "120",
            "ADJUSTMENT_THRESHOLD": "0.02",
            "PREMIUM_MIN": "0.30",
        },
    ):
        return RapidFireExecutor(
            ibkr_client=mock_ibkr_client,
            adaptive_executor=mock_adaptive_executor,
        )


@pytest.fixture
def staged_trades():
    """Fixture for staged trades."""
    return [
        StagedOpportunity(
            id=1,
            symbol="AAPL",
            strike=150.0,
            expiration="2026-02-14",
            staged_stock_price=155.0,
            staged_limit_price=0.45,
            staged_contracts=5,
            staged_margin=3750.0,
            otm_pct=0.15,
        ),
        StagedOpportunity(
            id=2,
            symbol="MSFT",
            strike=400.0,
            expiration="2026-02-14",
            staged_stock_price=410.0,
            staged_limit_price=0.50,
            staged_contracts=3,
            staged_margin=6000.0,
            otm_pct=0.20,
        ),
        StagedOpportunity(
            id=3,
            symbol="GOOGL",
            strike=140.0,
            expiration="2026-02-14",
            staged_stock_price=145.0,
            staged_limit_price=0.40,
            staged_contracts=4,
            staged_margin=2800.0,
            otm_pct=0.18,
        ),
    ]


class TestParallelSubmission:
    """Tests for parallel order submission."""

    @pytest.mark.asyncio
    async def test_execute_all_submits_in_parallel(
        self,
        rapid_fire_executor,
        staged_trades,
        mock_ibkr_client,
        mock_adaptive_executor,
    ):
        """Test that all orders are submitted in parallel (<3 seconds)."""
        # Mock contract qualification
        mock_contracts = [Mock() for _ in staged_trades]
        mock_ibkr_client.qualify_contracts_async.return_value = mock_contracts

        # Mock batch quotes (new method - returns Quote objects)
        from src.tools.ibkr_client import Quote
        mock_quotes = [
            Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason="")
            for _ in staged_trades
        ]
        mock_ibkr_client.get_quotes_batch = AsyncMock(return_value=mock_quotes)

        # Mock successful order placements
        def create_mock_result(order_id):
            return OrderResult(
                success=True,
                order_id=order_id,
                status=AdaptiveOrderStatus.SUBMITTED,
                order_type="Adaptive",
                live_bid=0.44,
                live_ask=0.48,
                calculated_limit=0.45,
                staged_limit=0.45,
                limit_deviation=0.0,
            )

        mock_adaptive_executor.place_order.side_effect = [
            create_mock_result(12345),
            create_mock_result(12346),
            create_mock_result(12347),
        ]

        # Mock sleep to avoid long waits
        mock_ibkr_client.sleep.return_value = None

        # Patch the monitoring to complete immediately
        with patch.object(rapid_fire_executor, "_monitor_and_adjust", new_callable=AsyncMock):
            import time
            start = time.time()
            report = await rapid_fire_executor.execute_all(staged_trades)
            elapsed = time.time() - start

            # Should complete very quickly (parallel, not sequential)
            assert elapsed < 1.0  # Much faster than 3s
            assert report.total_submitted == 3
            assert report.submission_time < 1.0

    @pytest.mark.asyncio
    async def test_batch_contract_qualification(
        self,
        rapid_fire_executor,
        staged_trades,
        mock_ibkr_client,
        mock_adaptive_executor,
    ):
        """Test that contracts are qualified in batch (single API call)."""
        mock_contracts = [Mock() for _ in staged_trades]
        mock_ibkr_client.qualify_contracts_async.return_value = mock_contracts

        # Mock batch quotes (new method)
        from src.tools.ibkr_client import Quote
        mock_quotes = [
            Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason="")
            for _ in staged_trades
        ]
        mock_ibkr_client.get_quotes_batch = AsyncMock(return_value=mock_quotes)

        mock_adaptive_executor.place_order.return_value = OrderResult(
            success=True,
            order_id=12345,
            status=AdaptiveOrderStatus.SUBMITTED,
            order_type="Adaptive",
            live_bid=0.44,
            live_ask=0.48,
            calculated_limit=0.45,
        )

        with patch.object(rapid_fire_executor, "_monitor_and_adjust", new_callable=AsyncMock):
            await rapid_fire_executor.execute_all(staged_trades)

            # Should call qualify_contracts_async ONCE with all contracts
            assert mock_ibkr_client.qualify_contracts_async.call_count == 1
            call_args = mock_ibkr_client.qualify_contracts_async.call_args[0]
            assert len(call_args) == 3  # All 3 contracts in one call


class TestEventDrivenMonitoring:
    """Tests for event-driven fill monitoring."""

    @pytest.mark.asyncio
    async def test_event_callback_registers(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
    ):
        """Test that orderStatusEvent callback is registered."""
        # Verify callback was registered on ib.orderStatusEvent
        assert mock_ibkr_client.ib.orderStatusEvent.__iadd__.called

    @pytest.mark.asyncio
    async def test_on_order_status_updates_pending(
        self,
        rapid_fire_executor,
    ):
        """Test that _on_order_status callback updates pending orders."""
        # Add a pending order
        staged = StagedOpportunity(
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

        pending = PendingOrder(
            staged=staged,
            contract=Mock(),
            order_id=12345,
            initial_limit=0.45,
            current_limit=0.45,
            last_bid=0.44,
            last_ask=0.48,
            submitted_at=datetime.now(),
            order_type="Adaptive",
        )

        rapid_fire_executor.pending_orders[12345] = pending

        # Mock trade status update (filled)
        mock_trade = Mock()
        mock_trade.order.orderId = 12345
        mock_trade.orderStatus.status = "Filled"
        mock_trade.orderStatus.avgFillPrice = 0.46
        mock_trade.orderStatus.filled = 5
        mock_trade.orderStatus.remaining = 0

        # Call the callback
        rapid_fire_executor._on_order_status(mock_trade)

        # Verify pending order was updated
        assert pending.last_status == "Filled"
        assert pending.fill_price == 0.46
        assert pending.filled_qty == 5
        assert pending.remaining_qty == 0


class TestConditionBasedAdjustment:
    """Tests for condition-based price adjustment."""

    @pytest.mark.asyncio
    async def test_no_adjustment_when_within_threshold(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
    ):
        """Test that no adjustment occurs when limit is within threshold."""
        # Add pending order with limit slightly above ask (within threshold)
        staged = StagedOpportunity(
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

        pending = PendingOrder(
            staged=staged,
            contract=Mock(),
            order_id=12345,
            initial_limit=0.45,
            current_limit=0.46,  # $0.46 limit
            last_bid=0.44,
            last_ask=0.47,  # Ask is $0.47, so distance is $0.01 (below $0.02 threshold)
            submitted_at=datetime.now(),
            order_type="Adaptive",
        )

        rapid_fire_executor.pending_orders[12345] = pending

        # Mock quote showing we're still close to spread
        mock_ibkr_client.get_quote.return_value = Mock(
            is_valid=True,
            bid=0.44,
            ask=0.47,
        )

        report = ExecutionReport()
        await rapid_fire_executor._adjust_if_outside_spread(report)

        # Should NOT have adjusted (within threshold)
        assert pending.current_limit == 0.46  # Unchanged
        assert pending.adjustment_count == 0

    @pytest.mark.asyncio
    async def test_adjustment_when_outside_threshold(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
        mock_adaptive_executor,
    ):
        """Test that adjustment occurs when limit is > $0.02 outside spread."""
        # Add pending order with limit too far above ask
        staged = StagedOpportunity(
            id=1,
            symbol="AAPL",
            strike=150.0,
            expiration="2026-02-14",
            staged_stock_price=155.0,
            staged_limit_price=0.50,
            staged_contracts=5,
            staged_margin=3750.0,
            otm_pct=0.15,
        )

        pending = PendingOrder(
            staged=staged,
            contract=Mock(),
            order_id=12345,
            initial_limit=0.50,
            current_limit=0.50,  # $0.50 limit
            last_bid=0.42,
            last_ask=0.45,  # Ask is $0.45, distance is $0.05 (above threshold)
            submitted_at=datetime.now(),
            order_type="Adaptive",
        )

        rapid_fire_executor.pending_orders[12345] = pending

        # Mock quote showing we're too far outside spread
        mock_ibkr_client.get_quote.return_value = Mock(
            is_valid=True,
            bid=0.42,
            ask=0.45,
        )

        # Mock limit calculator
        mock_adaptive_executor.limit_calc.calculate_sell_limit.return_value = 0.43

        # Mock successful order modification
        with patch.object(
            rapid_fire_executor,
            "_modify_order_price",
            new_callable=AsyncMock,
            return_value=True,
        ):
            report = ExecutionReport()
            await rapid_fire_executor._adjust_if_outside_spread(report)

            # Should have adjusted
            assert pending.current_limit == 0.43  # Lowered
            assert pending.adjustment_count == 1


class TestExecutionReport:
    """Tests for ExecutionReport tracking."""

    def test_execution_report_calculates_fill_rate(self):
        """Test that fill rate is calculated correctly."""
        report = ExecutionReport()

        # Add some submitted and filled orders
        staged = StagedOpportunity(
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

        report.add_submitted(staged, 12345, 0.45, "Adaptive")
        report.add_submitted(staged, 12346, 0.50, "Adaptive")
        report.add_submitted(staged, 12347, 0.40, "Adaptive")

        report.add_filled(staged, 12345, 0.45, "Adaptive")
        report.add_filled(staged, 12346, 0.50, "Adaptive")

        assert report.total_submitted == 3
        assert report.total_filled == 2
        assert report.fill_rate == pytest.approx(2 / 3, rel=1e-2)

    def test_execution_report_tracks_premium(self):
        """Test that total premium is tracked correctly."""
        report = ExecutionReport()

        staged = StagedOpportunity(
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

        # 5 contracts @ $0.45 = $0.45 * 5 * 100 = $225
        report.add_filled(staged, 12345, 0.45, "Adaptive")

        assert report.total_premium == 225.0


class TestMaxWaitTimeout:
    """Tests for maximum wait timeout."""

    @pytest.mark.asyncio
    async def test_monitoring_respects_max_wait(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
    ):
        """Test that monitoring stops after max_wait seconds."""
        # Add a pending order that never fills
        staged = StagedOpportunity(
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

        pending = PendingOrder(
            staged=staged,
            contract=Mock(),
            order_id=12345,
            initial_limit=0.45,
            current_limit=0.45,
            last_bid=0.44,
            last_ask=0.48,
            submitted_at=datetime.now(),
            order_type="Adaptive",
            last_status="Submitted",  # Never fills
        )

        rapid_fire_executor.pending_orders[12345] = pending
        rapid_fire_executor.max_wait = 0.5  # Short timeout for test

        # Mock quote (within spread, no adjustment needed)
        mock_ibkr_client.get_quote.return_value = Mock(
            is_valid=True,
            bid=0.44,
            ask=0.48,
        )

        report = ExecutionReport()

        import time
        start = time.time()
        await rapid_fire_executor._monitor_and_adjust(report)
        elapsed = time.time() - start

        # Should have stopped after max_wait
        assert elapsed < 1.0  # Stopped around 0.5s, not forever
        assert len(report.working) == 1  # Order left working

        # Working orders should remain in pending_orders (not cleared)
        assert 12345 in rapid_fire_executor.pending_orders
        assert rapid_fire_executor.pending_orders[12345].last_status == "Submitted"


class TestWorkingOrdersRetained:
    """Tests that working orders remain in pending_orders after monitoring."""

    @pytest.mark.asyncio
    async def test_filled_orders_removed_working_retained(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
    ):
        """Test that filled orders are removed but working ones stay."""
        staged_a = StagedOpportunity(
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
        staged_b = StagedOpportunity(
            id=2,
            symbol="MSFT",
            strike=400.0,
            expiration="2026-02-14",
            staged_stock_price=410.0,
            staged_limit_price=0.50,
            staged_contracts=3,
            staged_margin=6000.0,
            otm_pct=0.20,
        )

        # One filled, one still working
        rapid_fire_executor.pending_orders[111] = PendingOrder(
            staged=staged_a,
            contract=Mock(),
            order_id=111,
            initial_limit=0.45,
            current_limit=0.45,
            last_bid=0.44,
            last_ask=0.48,
            submitted_at=datetime.now(),
            order_type="Adaptive",
            last_status="Filled",
            fill_price=0.46,
            filled_qty=5,
        )
        rapid_fire_executor.pending_orders[222] = PendingOrder(
            staged=staged_b,
            contract=Mock(),
            order_id=222,
            initial_limit=0.50,
            current_limit=0.50,
            last_bid=0.48,
            last_ask=0.52,
            submitted_at=datetime.now(),
            order_type="Adaptive",
            last_status="Submitted",
        )

        rapid_fire_executor.max_wait = 0.1  # Short timeout

        mock_ibkr_client.get_quote.return_value = Mock(
            is_valid=True, bid=0.48, ask=0.52
        )

        report = ExecutionReport()
        await rapid_fire_executor._monitor_and_adjust(report)

        # Filled order removed, working order retained
        assert 111 not in rapid_fire_executor.pending_orders
        assert 222 in rapid_fire_executor.pending_orders
        assert len(report.filled) == 1
        assert len(report.working) == 1


class TestCleanup:
    """Tests for cleanup() method."""

    def test_cleanup_clears_pending_orders(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
    ):
        """Test that cleanup() empties pending_orders."""
        staged = StagedOpportunity(
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

        rapid_fire_executor.pending_orders[111] = PendingOrder(
            staged=staged,
            contract=Mock(),
            order_id=111,
            initial_limit=0.45,
            current_limit=0.45,
            last_bid=0.44,
            last_ask=0.48,
            submitted_at=datetime.now(),
            order_type="Adaptive",
            last_status="Submitted",
        )

        # Enable -= operator on mock event
        order_status_event = mock_ibkr_client.ib.orderStatusEvent
        order_status_event.__isub__ = Mock(return_value=order_status_event)

        rapid_fire_executor.cleanup()

        assert len(rapid_fire_executor.pending_orders) == 0

    def test_cleanup_unregisters_event_callback(
        self,
        rapid_fire_executor,
        mock_ibkr_client,
    ):
        """Test that cleanup() removes the orderStatusEvent handler."""
        order_status_event = mock_ibkr_client.ib.orderStatusEvent
        order_status_event.__isub__ = Mock(return_value=order_status_event)

        rapid_fire_executor.cleanup()

        order_status_event.__isub__.assert_called_once_with(
            rapid_fire_executor._on_order_status
        )
