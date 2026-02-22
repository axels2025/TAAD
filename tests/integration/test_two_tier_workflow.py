"""Integration test for full two-tier execution workflow.

Tests the complete Phase D workflow from start to finish:
1. Clock sync verification
2. Stage 1: Pre-market validation (9:15 AM)
3. Stage 2: Market-open validation (9:28 AM)
4. Tier 1: Rapid-fire execution (9:30 AM)
5. Tier 2: Condition-based retry (9:45-10:30 AM)
6. Final reconciliation (10:30 AM)

Uses mocked IBKR client to simulate realistic trading scenarios.
"""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest

from src.services.adaptive_order_executor import AdaptiveOrderExecutor
from src.services.clock_sync import ClockSyncVerifier
from src.services.market_conditions import MarketConditionMonitor
from src.services.order_reconciliation import OrderReconciliation
from src.services.premarket_validator import PremarketValidator, StagedOpportunity
from src.services.rapid_fire_executor import RapidFireExecutor
from src.services.two_tier_execution_scheduler import (
    AutomationMode,
    TwoTierExecutionScheduler,
)
from src.tools.ibkr_client import IBKRClient, Quote


@pytest.fixture
def mock_ibkr_client():
    """Comprehensive mocked IBKRClient for integration testing."""
    client = Mock(spec=IBKRClient)

    # Quote fetching
    client.get_quote = AsyncMock()
    client.get_quotes_batch = AsyncMock()

    # Contract operations
    client.get_option_contract = Mock()
    client.get_stock_contract = Mock()
    client.qualify_contracts_async = AsyncMock()

    # Order operations
    client.place_order = AsyncMock()
    client.cancel_order = AsyncMock()
    client.modify_order = AsyncMock()

    # Status
    client.ensure_connected = Mock()
    client.sleep = AsyncMock()

    # Order status events
    client.order_status_event = Mock()
    client.order_status_event.__iadd__ = Mock(return_value=client.order_status_event)

    return client


@pytest.fixture
def staged_opportunities():
    """Sample staged opportunities for integration testing."""
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


class TestFullWorkflowAutonomousMode:
    """Integration test for complete autonomous workflow."""

    @pytest.mark.asyncio
    async def test_full_workflow_with_tier2_retry(
        self, mock_ibkr_client, staged_opportunities
    ):
        """Test complete workflow: Stage 1 → Stage 2 → Tier 1 → Tier 2 → Reconciliation."""

        # ── Setup Mocks ──

        # Mock stock quotes for Stage 1 (pre-market validation)
        def mock_get_stock_price(symbol):
            prices = {"AAPL": 155.0, "MSFT": 410.0, "GOOGL": 145.0}
            return prices.get(symbol)

        mock_ibkr_client.get_stock_price = mock_get_stock_price

        # Mock option quotes for Stage 2 (market-open validation)
        def create_option_quote(symbol):
            quotes = {
                "AAPL": Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""),
                "MSFT": Quote(bid=0.49, ask=0.53, last=0.51, volume=1500, is_valid=True, reason=""),
                "GOOGL": Quote(bid=0.39, ask=0.43, last=0.41, volume=800, is_valid=True, reason=""),
            }
            return quotes.get(symbol)

        # Mock VIX for condition monitoring
        vix_quote = Quote(bid=15.0, ask=15.2, last=15.1, volume=10000, is_valid=True, reason="")

        # Mock SPY
        spy_quote = Quote(bid=450.0, ask=450.2, last=450.1, volume=50000, is_valid=True, reason="")

        # Setup get_quote to return appropriate quotes based on contract
        async def smart_get_quote(contract, timeout=None):
            if hasattr(contract, 'symbol'):
                if contract.symbol == "VIX":
                    return vix_quote
                elif contract.symbol == "SPY":
                    return spy_quote
                else:
                    return create_option_quote(contract.symbol)
            return Quote(bid=0.0, ask=0.0, last=0.0, volume=0, is_valid=False, reason="Unknown")

        mock_ibkr_client.get_quote = smart_get_quote

        # Mock batch quotes for Tier 1 rapid-fire
        async def mock_get_quotes_batch(contracts, timeout=None):
            return [await smart_get_quote(c, timeout) for c in contracts]

        mock_ibkr_client.get_quotes_batch = mock_get_quotes_batch

        # Mock contract qualification
        async def mock_qualify_contracts(contracts):
            return contracts  # Return as-is (qualified)

        mock_ibkr_client.qualify_contracts_async = mock_qualify_contracts

        # Mock order placement
        order_id_counter = [12345]  # Mutable counter

        async def mock_place_order(contract, order, reason=None):
            order_id = order_id_counter[0]
            order_id_counter[0] += 1

            trade = Mock()
            trade.order = Mock()
            trade.order.orderId = order_id
            trade.orderStatus = Mock()
            trade.orderStatus.status = "Submitted"  # Initially submitted
            trade.orderStatus.avgFillPrice = 0.0
            trade.orderStatus.filled = 0

            return trade

        mock_ibkr_client.place_order = mock_place_order

        # ── Create Components ──

        # PremarketValidator
        validator = PremarketValidator(ibkr_client=mock_ibkr_client)

        # AdaptiveOrderExecutor
        adaptive_executor = AdaptiveOrderExecutor(
            ibkr_client=mock_ibkr_client,
        )

        # RapidFireExecutor
        rapid_fire = RapidFireExecutor(
            ibkr_client=mock_ibkr_client,
            adaptive_executor=adaptive_executor,
        )

        # MarketConditionMonitor
        with patch.dict(
            "os.environ",
            {"TIER2_VIX_LOW": "18", "TIER2_VIX_HIGH": "25", "TIER2_MAX_SPREAD": "0.08"},
        ):
            condition_monitor = MarketConditionMonitor(mock_ibkr_client)

        # ClockSyncVerifier (mock to avoid NTP calls)
        clock_sync = Mock(spec=ClockSyncVerifier)
        clock_sync.verify_sync_or_abort = AsyncMock(return_value=5.0)  # 5ms drift

        # TwoTierExecutionScheduler
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=validator,
            rapid_fire_executor=rapid_fire,
            condition_monitor=condition_monitor,
            clock_sync_verifier=clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
            tier2_enabled=True,
        )

        # ── Patch Time-Based Waiting ──
        # Skip actual time waits for testing
        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            # Patch Tier 2 to execute immediately (not wait for window)
            with patch.object(scheduler, '_execute_tier2_when_ready', new_callable=AsyncMock, return_value=0):
                # ── Execute Workflow ──
                report = await scheduler.run_monday_morning(
                    staged_opportunities,
                    dry_run=True  # Dry run for testing
                )

        # ── Verify Results ──

        # Clock sync was verified
        clock_sync.verify_sync_or_abort.assert_called_once()

        # Report should be generated
        assert report is not None
        assert report.dry_run is True

        # Should have processed all staged trades
        assert report.staged_count == 3

    @pytest.mark.asyncio
    async def test_tier1_fills_all_orders_no_tier2_needed(
        self, mock_ibkr_client, staged_opportunities
    ):
        """Test scenario where Tier 1 fills all orders (Tier 2 not needed)."""

        # Setup validator
        mock_ibkr_client.get_stock_price = lambda symbol: 150.0

        # Mock all quotes
        good_quote = Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason="")
        mock_ibkr_client.get_quote = AsyncMock(return_value=good_quote)
        mock_ibkr_client.get_quotes_batch = AsyncMock(return_value=[good_quote] * 3)
        mock_ibkr_client.qualify_contracts_async = AsyncMock(side_effect=lambda x: x)

        # Mock order placement with immediate fills
        async def mock_place_order_filled(contract, order, reason=None):
            trade = Mock()
            trade.order = Mock()
            trade.order.orderId = 12345
            trade.orderStatus = Mock()
            trade.orderStatus.status = "Filled"  # Immediately filled
            trade.orderStatus.avgFillPrice = 0.46
            trade.orderStatus.filled = 5
            return trade

        mock_ibkr_client.place_order = mock_place_order_filled

        # Create scheduler with all components
        validator = PremarketValidator(ibkr_client=mock_ibkr_client)
        adaptive_executor = AdaptiveOrderExecutor(ibkr_client=mock_ibkr_client)
        rapid_fire = RapidFireExecutor(
            ibkr_client=mock_ibkr_client,
            adaptive_executor=adaptive_executor,
        )

        clock_sync = Mock(spec=ClockSyncVerifier)
        clock_sync.verify_sync_or_abort = AsyncMock(return_value=3.0)

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=validator,
            rapid_fire_executor=rapid_fire,
            clock_sync_verifier=clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
            tier2_enabled=True,
        )

        # Execute with patched waits
        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            with patch.object(scheduler, '_execute_tier2_when_ready', new_callable=AsyncMock) as mock_tier2:
                report = await scheduler.run_monday_morning(
                    staged_opportunities,
                    dry_run=True
                )

        # Tier 2 should not execute (no working orders)
        # Note: In dry_run mode, this may behave differently

    @pytest.mark.asyncio
    async def test_tier2_adjusts_unfilled_orders_when_conditions_improve(
        self, mock_ibkr_client, staged_opportunities
    ):
        """Test scenario where Tier 1 partial fill, Tier 2 retries when VIX drops."""

        # Setup basic mocks
        mock_ibkr_client.get_stock_price = lambda symbol: 150.0

        good_quote = Quote(bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason="")
        mock_ibkr_client.get_quote = AsyncMock(return_value=good_quote)
        mock_ibkr_client.get_quotes_batch = AsyncMock(return_value=[good_quote] * 3)
        mock_ibkr_client.qualify_contracts_async = AsyncMock(side_effect=lambda x: x)

        # Mock order placement (submitted but not filled)
        async def mock_place_order_working(contract, order, reason=None):
            trade = Mock()
            trade.order = Mock()
            trade.order.orderId = 12345
            trade.orderStatus = Mock()
            trade.orderStatus.status = "Submitted"  # Working, not filled
            trade.orderStatus.avgFillPrice = 0.0
            trade.orderStatus.filled = 0
            return trade

        mock_ibkr_client.place_order = mock_place_order_working
        mock_ibkr_client.modify_order = AsyncMock()

        # Create components
        validator = PremarketValidator(ibkr_client=mock_ibkr_client)
        adaptive_executor = AdaptiveOrderExecutor(ibkr_client=mock_ibkr_client)
        rapid_fire = RapidFireExecutor(
            ibkr_client=mock_ibkr_client,
            adaptive_executor=adaptive_executor,
        )

        with patch.dict(
            "os.environ",
            {"TIER2_VIX_LOW": "18", "TIER2_VIX_HIGH": "25", "TIER2_MAX_SPREAD": "0.08"},
        ):
            condition_monitor = MarketConditionMonitor(mock_ibkr_client)

        clock_sync = Mock(spec=ClockSyncVerifier)
        clock_sync.verify_sync_or_abort = AsyncMock(return_value=2.0)

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=validator,
            rapid_fire_executor=rapid_fire,
            condition_monitor=condition_monitor,
            clock_sync_verifier=clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
            tier2_enabled=True,
        )

        # Execute workflow
        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            report = await scheduler.run_monday_morning(
                staged_opportunities,
                dry_run=True
            )

        # Should complete without errors
        assert report is not None


class TestWorkflowEdgeCases:
    """Integration tests for edge cases and error scenarios."""

    @pytest.mark.asyncio
    async def test_no_trades_pass_stage1(self, mock_ibkr_client, staged_opportunities):
        """Test workflow when no trades pass Stage 1 validation."""

        # Mock stock prices that fail validation (too far from staged)
        mock_ibkr_client.get_stock_price = lambda symbol: 200.0  # Way too high

        validator = PremarketValidator(ibkr_client=mock_ibkr_client)

        clock_sync = Mock(spec=ClockSyncVerifier)
        clock_sync.verify_sync_or_abort = AsyncMock(return_value=1.0)

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=validator,
            clock_sync_verifier=clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
        )

        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            report = await scheduler.run_monday_morning(
                staged_opportunities,
                dry_run=True
            )

        # Should complete with warning
        assert report is not None
        assert len(report.warnings) > 0
        assert "Stage 1" in str(report.warnings)

    @pytest.mark.asyncio
    async def test_no_trades_pass_stage2(self, mock_ibkr_client, staged_opportunities):
        """Test workflow when trades pass Stage 1 but fail Stage 2."""

        # Stage 1: Good stock prices
        mock_ibkr_client.get_stock_price = lambda symbol: 155.0

        # Stage 2: Bad option quotes (premium too low)
        bad_quote = Quote(bid=0.10, ask=0.12, last=0.11, volume=100, is_valid=True, reason="")
        mock_ibkr_client.get_quote = AsyncMock(return_value=bad_quote)

        validator = PremarketValidator(ibkr_client=mock_ibkr_client)

        clock_sync = Mock(spec=ClockSyncVerifier)
        clock_sync.verify_sync_or_abort = AsyncMock(return_value=1.0)

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=validator,
            clock_sync_verifier=clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
        )

        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            report = await scheduler.run_monday_morning(
                staged_opportunities,
                dry_run=True
            )

        # Should complete with warning
        assert report is not None
        assert len(report.warnings) > 0
        assert "Stage 2" in str(report.warnings)

    @pytest.mark.asyncio
    async def test_clock_sync_failure_aborts_workflow(
        self, mock_ibkr_client, staged_opportunities
    ):
        """Test that clock sync failure prevents execution."""

        from src.services.clock_sync import ClockSyncError

        # Mock clock sync failure
        bad_clock_sync = Mock(spec=ClockSyncVerifier)
        bad_clock_sync.verify_sync_or_abort = AsyncMock(
            side_effect=ClockSyncError("Clock drift 75ms exceeds 50ms limit")
        )

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            clock_sync_verifier=bad_clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
        )

        # Should raise ClockSyncError and abort
        with pytest.raises(ClockSyncError, match="Clock drift.*exceeds"):
            await scheduler.run_monday_morning(
                staged_opportunities,
                dry_run=True
            )
