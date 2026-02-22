"""Unit tests for TwoTierExecutionScheduler.

Tests two-tier execution with condition-based retry:
- Tier 1 execution at 9:30 AM
- Tier 2 condition monitoring and retry
- Progressive automation modes (hybrid, supervised, autonomous)
- Clock sync verification
- VIX/spread-based execution timing
"""

import asyncio
from datetime import datetime, time
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import pytest

from src.services.clock_sync import ClockSyncError, ClockSyncResult
from src.services.execution_scheduler import ExecutionReport
from src.services.market_conditions import MarketConditions
from src.services.two_tier_execution_scheduler import (
    AutomationMode,
    TwoTierExecutionScheduler,
)
from src.services.premarket_validator import StagedOpportunity, ValidationStatus


@pytest.fixture
def mock_ibkr_client():
    """Fixture for mocked IBKRClient."""
    client = Mock()
    client.get_quote = AsyncMock()
    client.get_option_contract = Mock()
    client.check_market_data_health = Mock(return_value=(True, None))
    return client


@pytest.fixture
def mock_validator():
    """Fixture for mocked PremarketValidator."""
    validator = Mock()
    validator.validate_premarket = Mock(return_value=[])
    validator.validate_at_open = Mock(return_value=[])
    return validator


@pytest.fixture
def mock_rapid_fire():
    """Fixture for mocked RapidFireExecutor."""
    executor = Mock()
    executor.execute_all = AsyncMock()
    executor.pending_orders = {}
    executor.adaptive_executor = Mock()
    executor.adaptive_executor.limit_calc = Mock()
    executor.adaptive_executor.limit_calc.calculate_sell_limit = Mock(return_value=0.45)
    executor._modify_order_price = AsyncMock(return_value=True)
    executor.min_premium = 0.30
    return executor


@pytest.fixture
def mock_condition_monitor():
    """Fixture for mocked MarketConditionMonitor."""
    monitor = Mock()
    monitor.check_conditions = AsyncMock()
    return monitor


@pytest.fixture
def mock_clock_sync():
    """Fixture for mocked ClockSyncVerifier."""
    verifier = Mock()
    verifier.verify_sync_or_abort = AsyncMock(return_value=5.0)  # 5ms drift
    return verifier


@pytest.fixture
def staged_trades():
    """Fixture for staged opportunities."""
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
    ]


class TestClockSyncVerification:
    """Tests for clock synchronization verification."""

    @pytest.mark.asyncio
    async def test_clock_sync_verified_before_execution(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        mock_clock_sync,
        staged_trades,
    ):
        """Test that clock sync is verified before execution starts."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            clock_sync_verifier=mock_clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
        )

        # Mock validator to return no trades (abort early)
        mock_validator.validate_premarket.return_value = []

        # Patch _wait_until_time to avoid actual waiting
        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Verify clock sync was called
        mock_clock_sync.verify_sync_or_abort.assert_called_once()

    @pytest.mark.asyncio
    async def test_execution_aborted_if_clock_not_synced(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        staged_trades,
    ):
        """Test that execution aborts if clock sync fails."""
        bad_clock_sync = Mock()
        bad_clock_sync.verify_sync_or_abort = AsyncMock(
            side_effect=ClockSyncError("Clock drift 75ms exceeds 50ms limit")
        )

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            clock_sync_verifier=bad_clock_sync,
        )

        # Should raise ClockSyncError
        with pytest.raises(ClockSyncError, match="Clock drift.*exceeds"):
            await scheduler.run_monday_morning(staged_trades, dry_run=True)


class TestAutomationModes:
    """Tests for progressive automation modes."""

    @pytest.mark.asyncio
    async def test_hybrid_mode_waits_for_user_input(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        mock_clock_sync,
        staged_trades,
    ):
        """Test hybrid mode waits for user to type 'execute'."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            clock_sync_verifier=mock_clock_sync,
            automation_mode=AutomationMode.HYBRID,
        )

        # Mock validators to pass trades through
        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]
        mock_validator.validate_at_open.return_value = [
            Mock(status=ValidationStatus.READY, opportunity=staged_trades[0])
        ]

        # Mock market conditions
        mock_condition_monitor.check_conditions.return_value = MarketConditions(
            timestamp=datetime.now(ZoneInfo("America/New_York")),
            vix=15.0,
            spy_price=450.0,
            avg_spread=0.03,
            conditions_favorable=True,
            reason="VIX low, spreads tight"
        )

        # Mock user input to abort (to exit quickly)
        with patch.object(scheduler, '_wait_for_user_input', new_callable=AsyncMock, return_value='abort'):
            with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
                report = await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Should have aborted
        assert len(report.warnings) > 0
        assert "aborted" in report.warnings[0].lower()

    @pytest.mark.asyncio
    async def test_supervised_mode_executes_automatically(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        mock_clock_sync,
        staged_trades,
    ):
        """Test supervised mode executes automatically without user input."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            clock_sync_verifier=mock_clock_sync,
            automation_mode=AutomationMode.SUPERVISED,
        )

        # Mock validators
        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]
        mock_validator.validate_at_open.return_value = [
            Mock(status=ValidationStatus.READY, opportunity=staged_trades[0])
        ]

        # Create a valid return value for the mocked execution
        mock_report = ExecutionReport(
            execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
            started_at=datetime.now(ZoneInfo("America/New_York")),
            completed_at=datetime.now(ZoneInfo("America/New_York")),
            dry_run=True,
        )

        # Patch execution methods
        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            with patch.object(scheduler, '_execute_tier1_and_tier2', new_callable=AsyncMock, return_value=mock_report) as mock_exec:
                await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Should have called execution (no user input required)
        mock_exec.assert_called_once()

    @pytest.mark.asyncio
    async def test_autonomous_mode_executes_automatically(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        mock_clock_sync,
        staged_trades,
    ):
        """Test autonomous mode executes automatically."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            clock_sync_verifier=mock_clock_sync,
            automation_mode=AutomationMode.AUTONOMOUS,
        )

        # Mock validators
        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]
        mock_validator.validate_at_open.return_value = [
            Mock(status=ValidationStatus.READY, opportunity=staged_trades[0])
        ]

        # Create a valid return value for the mocked execution
        mock_report = ExecutionReport(
            execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
            started_at=datetime.now(ZoneInfo("America/New_York")),
            completed_at=datetime.now(ZoneInfo("America/New_York")),
            dry_run=True,
        )

        # Patch execution methods
        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            with patch.object(scheduler, '_execute_tier1_and_tier2', new_callable=AsyncMock, return_value=mock_report) as mock_exec:
                await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Should have called execution
        mock_exec.assert_called_once()


class TestTier2ConditionMonitoring:
    """Tests for Tier 2 condition-based execution."""

    @pytest.mark.asyncio
    @patch("src.services.two_tier_execution_scheduler.datetime")
    async def test_tier2_executes_when_conditions_favorable(
        self,
        mock_datetime,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test Tier 2 executes when VIX low and spreads tight."""
        # Mock datetime.now to return a time within the Tier 2 window (9:50 AM ET)
        fake_now = datetime(2026, 2, 10, 9, 50, 0, tzinfo=ZoneInfo("America/New_York"))
        mock_datetime.now.return_value = fake_now
        mock_datetime.combine = datetime.combine
        mock_datetime.side_effect = lambda *args, **kw: datetime(*args, **kw)

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            tier2_enabled=True,
        )

        # Mock pending order
        mock_pending = Mock()
        mock_pending.last_status = 'Submitted'
        mock_pending.contract = Mock()
        mock_pending.staged = Mock(symbol='AAPL')
        mock_pending.current_limit = 0.45
        mock_rapid_fire.pending_orders = {12345: mock_pending}

        # Mock favorable conditions (first check)
        mock_condition_monitor.check_conditions.return_value = MarketConditions(
            timestamp=fake_now,
            vix=15.0,  # Low VIX
            spy_price=450.0,
            avg_spread=0.03,  # Tight spreads
            conditions_favorable=True,
            reason="VIX low, spreads tight"
        )

        # Mock quote for adjustment
        from src.tools.ibkr_client import Quote
        mock_ibkr_client.get_quote.return_value = Quote(
            bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""
        )

        # Execute Tier 2
        adjustments = await scheduler._execute_tier2_when_ready()

        # Should have adjusted the order
        assert adjustments == 1
        mock_rapid_fire._modify_order_price.assert_called_once()

    @pytest.mark.asyncio
    @patch("src.services.two_tier_execution_scheduler.datetime")
    async def test_tier2_skips_when_vix_too_high(
        self,
        mock_datetime,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test Tier 2 skips execution when VIX > threshold."""
        et = ZoneInfo("America/New_York")
        # First now() → in window, second now() (while loop) → past window end
        fake_now = datetime(2026, 2, 10, 10, 25, 0, tzinfo=et)
        fake_expired = datetime(2026, 2, 10, 10, 31, 0, tzinfo=et)
        mock_datetime.now.side_effect = [fake_now, fake_expired]
        mock_datetime.combine.side_effect = datetime.combine

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            tier2_enabled=True,
        )

        # Mock pending order
        mock_pending = Mock()
        mock_pending.last_status = 'Submitted'
        mock_rapid_fire.pending_orders = {12345: mock_pending}

        # Mock unfavorable conditions (VIX too high)
        mock_condition_monitor.check_conditions.return_value = MarketConditions(
            timestamp=fake_now,
            vix=30.0,  # High VIX
            spy_price=450.0,
            avg_spread=0.03,
            conditions_favorable=False,
            reason="VIX too high: 30.0"
        )

        # Execute Tier 2
        adjustments = await scheduler._execute_tier2_when_ready()

        # Should not have adjusted (window timeout)
        assert adjustments == 0

    @pytest.mark.asyncio
    @patch("src.services.two_tier_execution_scheduler.datetime")
    async def test_tier2_skips_when_spreads_too_wide(
        self,
        mock_datetime,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test Tier 2 skips when spreads > threshold."""
        et = ZoneInfo("America/New_York")
        fake_now = datetime(2026, 2, 10, 10, 25, 0, tzinfo=et)
        fake_expired = datetime(2026, 2, 10, 10, 31, 0, tzinfo=et)
        mock_datetime.now.side_effect = [fake_now, fake_expired]
        mock_datetime.combine.side_effect = datetime.combine

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            tier2_enabled=True,
        )

        # Mock pending order
        mock_pending = Mock()
        mock_pending.last_status = 'Submitted'
        mock_rapid_fire.pending_orders = {12345: mock_pending}

        # Mock unfavorable conditions (spreads too wide)
        mock_condition_monitor.check_conditions.return_value = MarketConditions(
            timestamp=fake_now,
            vix=15.0,
            spy_price=450.0,
            avg_spread=0.15,  # Wide spreads
            conditions_favorable=False,
            reason="Spreads too wide: $0.15"
        )

        adjustments = await scheduler._execute_tier2_when_ready()

        assert adjustments == 0


class TestTier2LimitAdjustment:
    """Tests for Tier 2 limit price adjustment logic."""

    @pytest.mark.asyncio
    async def test_tier2_uses_more_aggressive_limits(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test that Tier 2 applies adjustment factor to limits."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            tier2_enabled=True,
        )

        # Set adjustment factor to 1.2 (20% more aggressive)
        scheduler.tier2_limit_adjustment = 1.2

        # Mock pending order
        mock_pending = Mock()
        mock_pending.last_status = 'Submitted'
        mock_pending.contract = Mock()
        mock_pending.staged = Mock(symbol='AAPL')
        mock_pending.current_limit = 0.40  # Current limit
        mock_rapid_fire.pending_orders = {12345: mock_pending}

        # Mock quote
        from src.tools.ibkr_client import Quote
        mock_ibkr_client.get_quote.return_value = Quote(
            bid=0.44, ask=0.50, last=0.47, volume=1000, is_valid=True, reason=""
        )

        # Mock base limit calculation returns 0.45
        mock_rapid_fire.adaptive_executor.limit_calc.calculate_sell_limit.return_value = 0.45

        # Mock favorable conditions
        conditions = MarketConditions(
            timestamp=datetime.now(ZoneInfo("America/New_York")),
            vix=15.0,
            spy_price=450.0,
            avg_spread=0.03,
            conditions_favorable=True,
            reason="Favorable"
        )

        # Execute adjustment
        adjustments = await scheduler._adjust_unfilled_orders(conditions)

        # Tier 2 limit should be: 0.45 * 1.2 = 0.54, but capped at ask-0.01 = 0.49
        # Should call modify_order_price with adjusted limit
        assert adjustments == 1
        call_args = mock_rapid_fire._modify_order_price.call_args[0]
        new_limit = call_args[1]
        assert new_limit == pytest.approx(0.49, abs=0.01)  # Capped at ask-0.01

    @pytest.mark.asyncio
    async def test_tier2_skips_if_limit_unchanged(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test Tier 2 skips adjustment if new limit same as current."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
        )

        # Mock pending order with current_limit = 0.45
        mock_pending = Mock()
        mock_pending.last_status = 'Submitted'
        mock_pending.contract = Mock()
        mock_pending.staged = Mock(symbol='AAPL')
        mock_pending.current_limit = 0.45
        mock_rapid_fire.pending_orders = {12345: mock_pending}

        # Mock quote
        from src.tools.ibkr_client import Quote
        mock_ibkr_client.get_quote.return_value = Quote(
            bid=0.44, ask=0.48, last=0.46, volume=1000, is_valid=True, reason=""
        )

        # Mock base limit = 0.45, with adjustment factor 1.0 → still 0.45
        scheduler.tier2_limit_adjustment = 1.0
        mock_rapid_fire.adaptive_executor.limit_calc.calculate_sell_limit.return_value = 0.45

        conditions = MarketConditions(
            timestamp=datetime.now(ZoneInfo("America/New_York")),
            vix=15.0,
            spy_price=450.0,
            avg_spread=0.03,
            conditions_favorable=True,
            reason="Favorable"
        )

        adjustments = await scheduler._adjust_unfilled_orders(conditions)

        # Should skip (new limit same as current)
        assert adjustments == 0
        mock_rapid_fire._modify_order_price.assert_not_called()


class TestConfigurableTimings:
    """Tests for configurable execution timings."""

    def test_parse_time_from_string(self, mock_ibkr_client):
        """Test time parsing from HH:MM format."""
        scheduler = TwoTierExecutionScheduler(ibkr_client=mock_ibkr_client)

        parsed = scheduler._parse_time("09:30")
        assert parsed == time(9, 30)

        parsed = scheduler._parse_time("14:45")
        assert parsed == time(14, 45)

    def test_custom_tier2_window_from_env(self, mock_ibkr_client):
        """Test custom Tier 2 window loaded from environment."""
        with patch.dict(
            "os.environ",
            {
                "TIER2_WINDOW_START": "10:00",
                "TIER2_WINDOW_END": "11:00",
            },
        ):
            scheduler = TwoTierExecutionScheduler(ibkr_client=mock_ibkr_client)

            assert scheduler.tier2_window_start == time(10, 0)
            assert scheduler.tier2_window_end == time(11, 0)


class TestSavePendingTradesToDb:
    """Tests for _save_pending_trades_to_db() crash-safety method."""

    @pytest.mark.asyncio
    async def test_saves_pending_records_for_submitted_orders(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test that PENDING Trade records are created for submitted orders."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
        )

        staged = [
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
        ]

        from src.services.rapid_fire_executor import ExecutionSummary, OrderStatus as RFOrderStatus

        mock_report = Mock()
        mock_report.submitted = [
            ExecutionSummary(
                symbol="AAPL",
                strike=150.0,
                order_id=12345,
                status=RFOrderStatus.SUBMITTED,
                order_type="Adaptive",
                submitted_limit=0.45,
                submission_time=datetime.now(),
            )
        ]

        mock_session = Mock()
        mock_session.__enter__ = Mock(return_value=mock_session)
        mock_session.__exit__ = Mock(return_value=False)

        with patch("src.data.database.get_db_session", return_value=mock_session):
            with patch("src.data.models.Trade") as MockTrade:
                mock_trade_instance = Mock()
                MockTrade.return_value = mock_trade_instance
                await scheduler._save_pending_trades_to_db(mock_report, staged)

        # Should have added a record and tracked the order_id
        mock_session.add.assert_called_once()
        assert 12345 in scheduler._saved_order_ids

    @pytest.mark.asyncio
    async def test_skips_already_saved_orders(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test that already-saved orders are skipped."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
        )

        # Pre-populate saved IDs
        scheduler._saved_order_ids.add(12345)

        from src.services.rapid_fire_executor import ExecutionSummary, OrderStatus as RFOrderStatus

        mock_report = Mock()
        mock_report.submitted = [
            ExecutionSummary(
                symbol="AAPL",
                strike=150.0,
                order_id=12345,
                status=RFOrderStatus.SUBMITTED,
                order_type="Adaptive",
                submitted_limit=0.45,
                submission_time=datetime.now(),
            )
        ]

        mock_session = Mock()
        mock_session.__enter__ = Mock(return_value=mock_session)
        mock_session.__exit__ = Mock(return_value=False)

        with patch("src.data.database.get_db_session", return_value=mock_session):
            with patch("src.data.models.Trade"):
                await scheduler._save_pending_trades_to_db(mock_report, [])

        # session.add should NOT have been called (order was already saved)
        mock_session.add.assert_not_called()


class TestSaveFilledUpdatesExisting:
    """Tests for _save_filled_trades_to_db() upsert logic."""

    @pytest.mark.asyncio
    async def test_updates_existing_pending_record(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test that fills update existing PENDING records instead of inserting."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
        )

        staged = [
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
        ]

        from src.services.rapid_fire_executor import ExecutionSummary, OrderStatus as RFOrderStatus

        filled = [
            ExecutionSummary(
                symbol="AAPL",
                strike=150.0,
                order_id=12345,
                status=RFOrderStatus.FILLED,
                order_type="Adaptive",
                submitted_limit=0.45,
                fill_price=0.46,
                fill_time=datetime.now(),
                submission_time=datetime.now(),
            )
        ]

        # Mock DB session with an existing PENDING record
        existing_trade = Mock()
        existing_trade.id = 1
        existing_trade.order_id = 12345
        existing_trade.ai_reasoning = "PENDING - awaiting fill"
        existing_trade.expiration = "2026-02-14"
        existing_trade.contracts = 5
        existing_trade.entry_premium = 0.45

        mock_session = Mock()
        mock_session.__enter__ = Mock(return_value=mock_session)
        mock_session.__exit__ = Mock(return_value=False)

        # query().filter().first() returns existing record
        mock_query = Mock()
        mock_filter = Mock()
        mock_filter.first.return_value = existing_trade
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query

        mock_lifecycle = Mock()

        with patch("src.data.database.get_db_session", return_value=mock_session):
            with patch("src.data.models.Trade") as MockTrade:
                with patch(
                    "src.services.entry_snapshot.EntrySnapshotService"
                ) as MockSnapshot:
                    MockSnapshot.return_value.capture_entry_snapshot = AsyncMock()
                    with patch(
                        "src.execution.opportunity_lifecycle.OpportunityLifecycleManager",
                        return_value=mock_lifecycle,
                    ):
                        await scheduler._save_filled_trades_to_db(filled, staged)

        # Should have UPDATED existing record, not added new one
        assert existing_trade.entry_premium == 0.46
        assert existing_trade.ai_reasoning == "Executed via two-tier scheduler"
        assert existing_trade.ai_confidence == 0.8
        mock_session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_inserts_new_record_when_no_pending_exists(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test that a new record is inserted when no PENDING exists."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
        )

        staged = [
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
        ]

        from src.services.rapid_fire_executor import ExecutionSummary, OrderStatus as RFOrderStatus

        filled = [
            ExecutionSummary(
                symbol="AAPL",
                strike=150.0,
                order_id=12345,
                status=RFOrderStatus.FILLED,
                order_type="Adaptive",
                submitted_limit=0.45,
                fill_price=0.46,
                fill_time=datetime.now(),
                submission_time=datetime.now(),
            )
        ]

        # Mock DB session with NO existing record
        mock_session = Mock()
        mock_session.__enter__ = Mock(return_value=mock_session)
        mock_session.__exit__ = Mock(return_value=False)

        mock_query = Mock()
        mock_filter = Mock()
        mock_filter.first.return_value = None  # No existing record
        mock_query.filter.return_value = mock_filter
        mock_session.query.return_value = mock_query

        mock_lifecycle = Mock()

        mock_trade_instance = Mock()
        mock_trade_instance.id = 1
        mock_trade_instance.expiration = "2026-02-14"
        mock_trade_instance.contracts = 5
        mock_trade_instance.entry_premium = 0.46
        mock_trade_instance.trade_id = "AAPL_150.0_20260214_P"

        with patch("src.data.database.get_db_session", return_value=mock_session):
            with patch(
                "src.data.models.Trade",
                return_value=mock_trade_instance,
            ) as MockTrade:
                with patch(
                    "src.services.entry_snapshot.EntrySnapshotService"
                ) as MockSnapshot:
                    MockSnapshot.return_value.capture_entry_snapshot = AsyncMock()
                    with patch(
                        "src.execution.opportunity_lifecycle.OpportunityLifecycleManager",
                        return_value=mock_lifecycle,
                    ):
                        await scheduler._save_filled_trades_to_db(filled, staged)

        # Should have ADDED new record
        mock_session.add.assert_called_once()


class TestCleanupCalledAtSessionEnd:
    """Test that executor.cleanup() is called at session end."""

    @pytest.mark.asyncio
    async def test_cleanup_called_after_final_save(
        self,
        mock_ibkr_client,
        mock_rapid_fire,
        mock_condition_monitor,
    ):
        """Test that cleanup() is called in _execute_tier1_and_tier2."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            tier2_enabled=False,
        )

        from src.services.rapid_fire_executor import ExecutionReport as RFReport

        mock_rf_report = RFReport()
        mock_rf_report.started_at = datetime.now()
        mock_rapid_fire.execute_all.return_value = mock_rf_report
        mock_rapid_fire.cleanup = Mock()

        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            with patch.object(scheduler, '_save_pending_trades_to_db', new_callable=AsyncMock):
                with patch.object(scheduler, '_save_filled_trades_to_db', new_callable=AsyncMock):
                    with patch.object(scheduler, '_get_newly_filled_trades', return_value=[]):
                        with patch("asyncio.sleep", new_callable=AsyncMock):
                            report = await scheduler._execute_tier1_and_tier2(
                                [], dry_run=False
                            )

        mock_rapid_fire.cleanup.assert_called_once()


class TestAdaptiveStrikeSelectionPipeline:
    """Tests for the adaptive strike selection pipeline ordering.

    When LiveStrikeSelector is enabled, Stage 2 should be skipped and
    strike selection should run after waiting for market open (9:30).
    When disabled, Stage 2 should run at 9:30 (not 9:28).
    """

    @pytest.mark.asyncio
    async def test_strike_selector_skips_stage2(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        staged_trades,
    ):
        """Test that Stage 2 is skipped when strike selector is enabled."""
        mock_strike_selector = AsyncMock()
        mock_strike_result = Mock(
            status="SELECTED",
            opportunity=staged_trades[0],
            selected_strike=148.0,
            selected_delta=-0.19,
            reason="Delta target",
        )
        mock_strike_selector.select_all.return_value = [mock_strike_result]

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            strike_selector=mock_strike_selector,
            automation_mode=AutomationMode.SUPERVISED,
        )

        # Stage 1 passes one trade
        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]

        mock_report = ExecutionReport(
            execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
            started_at=datetime.now(ZoneInfo("America/New_York")),
            completed_at=datetime.now(ZoneInfo("America/New_York")),
            dry_run=True,
        )

        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            with patch.object(
                scheduler, '_execute_tier1_and_tier2',
                new_callable=AsyncMock, return_value=mock_report,
            ):
                await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Strike selector should have been called
        mock_strike_selector.select_all.assert_called_once()
        # Stage 2 (validate_at_open) should NOT have been called
        mock_validator.validate_at_open.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_strike_selector_uses_stage2(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        staged_trades,
    ):
        """Test that Stage 2 runs when strike selector is disabled."""
        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            strike_selector=None,  # No strike selector
            automation_mode=AutomationMode.SUPERVISED,
        )

        # Stage 1 passes one trade
        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]
        # Stage 2 confirms
        mock_validator.validate_at_open.return_value = [
            Mock(status=ValidationStatus.READY, opportunity=staged_trades[0])
        ]

        mock_report = ExecutionReport(
            execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
            started_at=datetime.now(ZoneInfo("America/New_York")),
            completed_at=datetime.now(ZoneInfo("America/New_York")),
            dry_run=True,
        )

        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            with patch.object(
                scheduler, '_execute_tier1_and_tier2',
                new_callable=AsyncMock, return_value=mock_report,
            ):
                await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Stage 2 should have been called
        mock_validator.validate_at_open.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_abandoned_returns_empty_report(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        staged_trades,
    ):
        """Test that all ABANDONED results return empty report."""
        mock_strike_selector = AsyncMock()
        mock_strike_selector.select_all.return_value = [
            Mock(
                status="ABANDONED",
                opportunity=staged_trades[0],
                reason="No viable strikes",
            ),
        ]

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            strike_selector=mock_strike_selector,
            automation_mode=AutomationMode.SUPERVISED,
        )

        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]

        with patch.object(scheduler, '_wait_until_time', new_callable=AsyncMock):
            report = await scheduler.run_monday_morning(staged_trades, dry_run=True)

        assert "abandoned" in report.warnings[0].lower()

    @pytest.mark.asyncio
    async def test_strike_selector_waits_for_market_open(
        self,
        mock_ibkr_client,
        mock_validator,
        mock_rapid_fire,
        mock_condition_monitor,
        staged_trades,
    ):
        """Test that strike selection waits for tier1_time (9:30) before running."""
        mock_strike_selector = AsyncMock()
        mock_strike_selector.select_all.return_value = [
            Mock(
                status="SELECTED",
                opportunity=staged_trades[0],
                selected_strike=148.0,
                selected_delta=-0.19,
                reason="Delta target",
            ),
        ]

        scheduler = TwoTierExecutionScheduler(
            ibkr_client=mock_ibkr_client,
            premarket_validator=mock_validator,
            rapid_fire_executor=mock_rapid_fire,
            condition_monitor=mock_condition_monitor,
            strike_selector=mock_strike_selector,
            automation_mode=AutomationMode.SUPERVISED,
        )

        mock_validator.validate_premarket.return_value = [
            Mock(passed=True, opportunity=staged_trades[0])
        ]

        mock_report = ExecutionReport(
            execution_date=datetime.now(ZoneInfo("America/New_York")).date(),
            started_at=datetime.now(ZoneInfo("America/New_York")),
            completed_at=datetime.now(ZoneInfo("America/New_York")),
            dry_run=True,
        )

        wait_calls = []

        async def track_wait(target, reason):
            wait_calls.append((target, reason))

        with patch.object(scheduler, '_wait_until_time', side_effect=track_wait):
            with patch.object(
                scheduler, '_execute_tier1_and_tier2',
                new_callable=AsyncMock, return_value=mock_report,
            ):
                await scheduler.run_monday_morning(staged_trades, dry_run=True)

        # Should have waited for stage1_time and tier1_time
        wait_times = [t for t, _ in wait_calls]
        assert scheduler.stage1_time in wait_times
        assert scheduler.tier1_time in wait_times
        # The tier1_time wait should be for "market open for strike selection"
        tier1_wait = next(
            (t, r) for t, r in wait_calls if t == scheduler.tier1_time
        )
        assert "strike selection" in tier1_wait[1]
