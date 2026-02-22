"""Unit tests for OrderReconciliation.

Tests the order reconciliation system including:
- Status mismatch detection (DB says submitted, TWS says filled)
- Fill price updates and mismatch detection
- Commission extraction from fills
- Orphan order detection (in TWS but not in database)
- Missing in TWS detection (in database but not in TWS)
- Position reconciliation (quantity mismatches, divergence detection)
- Database update operations
"""

import pytest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, Mock

from src.services.order_reconciliation import (
    OrderReconciliation,
    ReconciliationReport,
    PositionReconciliationReport,
    Discrepancy,
    ReconciledTrade,
    PositionMismatch,
)


@pytest.fixture
def mock_ibkr_client():
    """Fixture for mocked IBKRClient."""
    client = Mock()
    client.get_orders = Mock(return_value=[])
    client.get_trades = Mock(return_value=[])
    client.get_executions = Mock(return_value=[])
    client.get_fills = Mock(return_value=[])
    client.get_positions = Mock(return_value=[])
    return client


@pytest.fixture
def mock_trade_repo():
    """Fixture for mocked trade repository."""
    repo = Mock()
    repo.get_trades_by_date = Mock(return_value=[])
    repo.get_open_positions = Mock(return_value=[])
    repo.update_trade = Mock(return_value=True)
    return repo


@pytest.fixture
def reconciler(mock_ibkr_client, mock_trade_repo):
    """Fixture for OrderReconciliation."""
    return OrderReconciliation(
        ibkr_client=mock_ibkr_client,
        trade_repository=mock_trade_repo,
    )


def create_mock_ib_trade(order_id, symbol, status, avg_fill_price=0.0, filled_qty=0):
    """Helper to create mock IBKR trade object."""
    trade = Mock()
    trade.order = Mock()
    trade.order.orderId = order_id

    trade.contract = Mock()
    trade.contract.symbol = symbol
    trade.contract.strike = 150.0
    trade.contract.lastTradeDateOrContractMonth = "20260214"
    trade.contract.right = "P"

    trade.orderStatus = Mock()
    trade.orderStatus.status = status
    trade.orderStatus.avgFillPrice = avg_fill_price
    trade.orderStatus.filled = filled_qty

    return trade


def create_mock_db_trade(id, order_id, symbol, status, fill_price=None, commission=None, expiration=None):
    """Helper to create mock database trade object."""
    trade = Mock()
    trade.id = id
    trade.order_id = order_id
    trade.symbol = symbol
    trade.status = status
    trade.fill_price = fill_price
    trade.commission = commission
    trade.contracts = 5
    trade.strike = 150.0
    trade.expiration = expiration or date(2026, 12, 31)  # Future date by default
    trade.option_type = "P"
    trade.entry_date = datetime(2026, 1, 15, 10, 0, 0)
    trade.entry_premium = 1.50
    return trade


def create_mock_execution(order_id, exec_id, time):
    """Helper to create mock execution object.

    Note: _group_executions_by_order accesses exec.orderId directly,
    and _get_fill_time accesses e.time directly on the execution object.
    """
    exec_obj = Mock()
    exec_obj.orderId = order_id
    exec_obj.execId = exec_id
    exec_obj.time = time
    # Also keep nested structure for other code paths
    exec_obj.execution = Mock()
    exec_obj.execution.orderId = order_id
    exec_obj.execution.execId = exec_id
    exec_obj.execution.time = time
    return exec_obj


def create_mock_fill(order_id, commission_amount):
    """Helper to create mock fill object."""
    fill = Mock()
    fill.execution = Mock()
    fill.execution.orderId = order_id
    fill.commissionReport = Mock()
    fill.commissionReport.commission = commission_amount
    return fill


def create_mock_position(symbol, strike, expiration, right, quantity):
    """Helper to create mock IBKR position object."""
    position = Mock()
    position.contract = Mock()
    position.contract.symbol = symbol
    position.contract.strike = strike
    position.contract.lastTradeDateOrContractMonth = expiration
    position.contract.right = right
    position.position = quantity
    return position


class TestStatusMismatchDetection:
    """Tests for status mismatch detection and resolution."""

    @pytest.mark.asyncio
    async def test_detect_filled_status_mismatch(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection when DB says 'submitted' but TWS says 'Filled'."""
        # DB has order as 'submitted'
        db_trade = create_mock_db_trade(
            id=1,
            order_id=12345,
            symbol="AAPL",
            status="submitted",
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS shows order as 'Filled'
        ib_trade = create_mock_ib_trade(
            order_id=12345,
            symbol="AAPL",
            status="Filled",
            avg_fill_price=0.45,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Create fill with commission
        fill = create_mock_fill(order_id=12345, commission_amount=1.25)
        mock_ibkr_client.get_fills.return_value = [fill]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
            include_filled=True,
        )

        # Verify discrepancy was detected
        assert report.total_reconciled == 1
        assert report.total_discrepancies == 1
        assert report.total_resolved == 1

        # Verify database was updated
        mock_trade_repo.update_trade.assert_called_once()
        call_args = mock_trade_repo.update_trade.call_args
        assert call_args[0][0] == 1  # trade ID
        updates = call_args[1]
        assert updates["status"] == "filled"
        assert updates["fill_price"] == 0.45
        assert updates["filled_quantity"] == 5
        assert updates["commission"] == 1.25
        assert updates["tws_status"] == "Filled"
        assert "reconciled_at" in updates

    @pytest.mark.asyncio
    async def test_detect_cancelled_status_mismatch(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection when DB says 'submitted' but TWS says 'Cancelled'."""
        # DB has order as 'submitted'
        db_trade = create_mock_db_trade(
            id=2,
            order_id=12346,
            symbol="MSFT",
            status="submitted",
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS shows order as 'Cancelled'
        ib_trade = create_mock_ib_trade(
            order_id=12346,
            symbol="MSFT",
            status="Cancelled",
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify discrepancy was detected
        assert report.total_discrepancies == 1
        assert report.total_resolved == 1

        # Verify database was updated
        mock_trade_repo.update_trade.assert_called_once()
        call_args = mock_trade_repo.update_trade.call_args
        updates = call_args[1]
        assert updates["status"] == "cancelled"
        assert updates["tws_status"] == "Cancelled"

    @pytest.mark.asyncio
    async def test_no_discrepancy_when_status_matches(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test no discrepancy when statuses match."""
        # DB has order as 'filled'
        db_trade = create_mock_db_trade(
            id=3,
            order_id=12347,
            symbol="GOOGL",
            status="filled",
            fill_price=0.40,
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS also shows order as 'Filled' with same price
        ib_trade = create_mock_ib_trade(
            order_id=12347,
            symbol="GOOGL",
            status="Filled",
            avg_fill_price=0.40,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify no discrepancy
        assert report.total_discrepancies == 0


class TestFillPriceUpdates:
    """Tests for fill price updates and mismatch detection."""

    @pytest.mark.asyncio
    async def test_fill_price_mismatch_detection(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection when fill price differs by >$0.01."""
        # DB has fill price of $0.40
        db_trade = create_mock_db_trade(
            id=4,
            order_id=12348,
            symbol="TSLA",
            status="filled",
            fill_price=0.40,
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS shows fill price of $0.45 (difference = $0.05)
        ib_trade = create_mock_ib_trade(
            order_id=12348,
            symbol="TSLA",
            status="Filled",
            avg_fill_price=0.45,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify discrepancy was detected
        assert report.total_discrepancies == 1

        # Verify database was updated with correct price
        mock_trade_repo.update_trade.assert_called_once()
        call_args = mock_trade_repo.update_trade.call_args
        updates = call_args[1]
        assert updates["fill_price"] == 0.45
        assert updates["fill_price_discrepancy"] == pytest.approx(0.05, abs=1e-9)

    @pytest.mark.asyncio
    async def test_no_mismatch_for_small_price_difference(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test no mismatch when price difference is <$0.01."""
        # DB has fill price of $0.400
        db_trade = create_mock_db_trade(
            id=5,
            order_id=12349,
            symbol="NVDA",
            status="filled",
            fill_price=0.400,
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS shows fill price of $0.405 (difference = $0.005 < threshold)
        ib_trade = create_mock_ib_trade(
            order_id=12349,
            symbol="NVDA",
            status="Filled",
            avg_fill_price=0.405,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify no discrepancy for small difference
        assert report.total_discrepancies == 0


class TestCommissionExtraction:
    """Tests for commission extraction from fills."""

    @pytest.mark.asyncio
    async def test_commission_extracted_from_fills(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test commission is extracted from fills and added to database."""
        # DB has no commission
        db_trade = create_mock_db_trade(
            id=6,
            order_id=12350,
            symbol="AAPL",
            status="filled",
            fill_price=0.45,
            commission=None,
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS has fill
        ib_trade = create_mock_ib_trade(
            order_id=12350,
            symbol="AAPL",
            status="Filled",
            avg_fill_price=0.45,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Create fill with commission
        fill = create_mock_fill(order_id=12350, commission_amount=2.50)
        mock_ibkr_client.get_fills.return_value = [fill]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify commission was added
        mock_trade_repo.update_trade.assert_called_once()
        call_args = mock_trade_repo.update_trade.call_args
        updates = call_args[1]
        assert updates["commission"] == 2.50

    @pytest.mark.asyncio
    async def test_commission_sum_from_multiple_fills(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test commission is summed when order has multiple fills."""
        # DB has no commission
        db_trade = create_mock_db_trade(
            id=7,
            order_id=12351,
            symbol="MSFT",
            status="filled",
            fill_price=0.50,
            commission=0,
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS has fill
        ib_trade = create_mock_ib_trade(
            order_id=12351,
            symbol="MSFT",
            status="Filled",
            avg_fill_price=0.50,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Multiple fills (partial fills)
        fills = [
            create_mock_fill(order_id=12351, commission_amount=1.25),
            create_mock_fill(order_id=12351, commission_amount=1.25),
            create_mock_fill(order_id=12351, commission_amount=1.00),
        ]
        mock_ibkr_client.get_fills.return_value = fills

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify total commission (1.25 + 1.25 + 1.00 = 3.50)
        mock_trade_repo.update_trade.assert_called_once()
        call_args = mock_trade_repo.update_trade.call_args
        updates = call_args[1]
        assert updates["commission"] == 3.50


class TestOrphanOrderDetection:
    """Tests for orphan order detection (in TWS but not in database)."""

    @pytest.mark.asyncio
    async def test_detect_orphan_order(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection of order in TWS but not in database."""
        # DB has no trades
        mock_trade_repo.get_trades_by_date.return_value = []

        # TWS has an order
        ib_trade = create_mock_ib_trade(
            order_id=99999,
            symbol="ORPHAN",
            status="Filled",
            avg_fill_price=0.50,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify orphan was detected
        assert len(report.orphans) == 1
        assert report.orphans[0].order.orderId == 99999

    @pytest.mark.asyncio
    async def test_multiple_orphan_orders(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection of multiple orphan orders."""
        # DB has one trade
        db_trade = create_mock_db_trade(
            id=8,
            order_id=12352,
            symbol="AAPL",
            status="filled",
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS has 3 orders (1 matching, 2 orphans)
        ib_trades = [
            create_mock_ib_trade(12352, "AAPL", "Filled"),  # Matches DB
            create_mock_ib_trade(99997, "ORPHAN1", "Filled"),  # Orphan
            create_mock_ib_trade(99998, "ORPHAN2", "Submitted"),  # Orphan
        ]
        mock_ibkr_client.get_trades.return_value = ib_trades

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify orphans were detected
        assert len(report.orphans) == 2
        assert report.total_reconciled == 1


class TestMissingInTWS:
    """Tests for detection of orders in database but missing in TWS."""

    @pytest.mark.asyncio
    async def test_detect_missing_in_tws(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection of order in database but not in TWS."""
        # DB has an order
        db_trade = create_mock_db_trade(
            id=9,
            order_id=12353,
            symbol="MISSING",
            status="submitted",
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS has no orders
        mock_ibkr_client.get_trades.return_value = []

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Verify missing order was detected
        assert len(report.missing_in_tws) == 1
        assert report.missing_in_tws[0].order_id == 12353


class TestPositionReconciliation:
    """Tests for position reconciliation."""

    @pytest.mark.asyncio
    async def test_quantity_mismatch_detection(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection of position quantity mismatch."""
        # DB says 5 contracts
        db_trade = create_mock_db_trade(
            id=10,
            order_id=12354,
            symbol="AAPL",
            status="filled",
        )
        db_trade.contracts = 5
        mock_trade_repo.get_open_positions.return_value = [db_trade]

        # IBKR says 3 contracts (2 were closed somehow)
        ib_position = create_mock_position(
            symbol="AAPL",
            strike=150.0,
            expiration="20260214",
            right="P",
            quantity=3,
        )
        mock_ibkr_client.get_positions.return_value = [ib_position]

        # Execute position reconciliation
        report = await reconciler.reconcile_positions()

        # Verify mismatch was detected
        assert report.has_discrepancies
        assert len(report.quantity_mismatches) == 1
        mismatch = report.quantity_mismatches[0]
        assert mismatch.db_quantity == 5
        assert mismatch.ibkr_quantity == 3
        assert mismatch.difference == -2

    @pytest.mark.asyncio
    async def test_position_in_ibkr_not_db(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection of position in IBKR but not in database."""
        # DB has no positions
        mock_trade_repo.get_open_positions.return_value = []

        # IBKR has a position
        ib_position = create_mock_position(
            symbol="MYSTERY",
            strike=200.0,
            expiration="20260228",
            right="P",
            quantity=5,
        )
        mock_ibkr_client.get_positions.return_value = [ib_position]

        # Execute position reconciliation
        report = await reconciler.reconcile_positions()

        # Verify discrepancy
        assert report.has_discrepancies
        assert len(report.in_ibkr_not_db) == 1

    @pytest.mark.asyncio
    async def test_position_in_db_not_ibkr(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test detection of position in database but not in IBKR."""
        # DB has a position
        db_trade = create_mock_db_trade(
            id=11,
            order_id=12355,
            symbol="GHOST",
            status="filled",
        )
        mock_trade_repo.get_open_positions.return_value = [db_trade]

        # IBKR has no positions
        mock_ibkr_client.get_positions.return_value = []

        # Execute position reconciliation
        report = await reconciler.reconcile_positions()

        # Verify discrepancy
        assert report.has_discrepancies
        assert len(report.in_db_not_ibkr) == 1

    @pytest.mark.asyncio
    async def test_expired_option_auto_closed(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test expired option in DB but not IBKR is auto-closed."""
        # DB has a position with past expiration
        db_trade = create_mock_db_trade(
            id=13,
            order_id=12357,
            symbol="XSP",
            status="filled",
            expiration=date(2026, 2, 14),  # Past date
        )
        db_trade.entry_premium = 0.85
        db_trade.contracts = 1
        mock_trade_repo.get_open_positions.return_value = [db_trade]
        mock_trade_repo.session = MagicMock()

        # IBKR has no positions (option expired and was removed)
        mock_ibkr_client.get_positions.return_value = []

        # Execute position reconciliation
        report = await reconciler.reconcile_positions()

        # Expired option should be auto-closed, NOT reported as discrepancy
        assert len(report.in_db_not_ibkr) == 0
        assert db_trade.exit_premium == 0.0
        assert db_trade.exit_reason == "expired"
        assert db_trade.profit_loss == 0.85 * 1 * 100  # Full premium kept
        assert db_trade.roi == 1.0
        mock_trade_repo.session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_perfect_position_match(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test no discrepancies when positions match perfectly."""
        # DB has position
        db_trade = create_mock_db_trade(
            id=12,
            order_id=12356,
            symbol="PERFECT",
            status="filled",
        )
        db_trade.contracts = 5
        db_trade.strike = 100.0
        db_trade.expiration = date(2026, 3, 15)
        db_trade.option_type = "P"
        mock_trade_repo.get_open_positions.return_value = [db_trade]

        # IBKR has matching position
        ib_position = create_mock_position(
            symbol="PERFECT",
            strike=100.0,
            expiration="20260315",
            right="P",
            quantity=5,
        )
        mock_ibkr_client.get_positions.return_value = [ib_position]

        # Execute position reconciliation
        report = await reconciler.reconcile_positions()

        # Verify no discrepancies
        assert not report.has_discrepancies


class TestGroupingMethods:
    """Tests for execution and fill grouping methods."""

    def test_group_executions_by_order(self, reconciler):
        """Test executions are correctly grouped by order ID."""
        # Create executions for different orders
        exec1 = create_mock_execution(12345, "E1", datetime.now())
        exec2 = create_mock_execution(12345, "E2", datetime.now())
        exec3 = create_mock_execution(12346, "E3", datetime.now())

        executions = [exec1, exec2, exec3]
        grouped = reconciler._group_executions_by_order(executions)

        # Verify grouping
        assert len(grouped) == 2
        assert len(grouped[12345]) == 2
        assert len(grouped[12346]) == 1

    def test_group_fills_by_order(self, reconciler):
        """Test fills are correctly grouped by order ID."""
        # Create fills for different orders
        fill1 = create_mock_fill(12345, 1.25)
        fill2 = create_mock_fill(12345, 1.25)
        fill3 = create_mock_fill(12346, 2.00)

        fills = [fill1, fill2, fill3]
        grouped = reconciler._group_fills_by_order(fills)

        # Verify grouping
        assert len(grouped) == 2
        assert len(grouped[12345]) == 2
        assert len(grouped[12346]) == 1

    def test_get_fill_time_from_executions(self, reconciler):
        """Test fill time is extracted from executions (latest time)."""
        # Create executions with different times
        time1 = datetime(2026, 2, 3, 9, 30, 0)
        time2 = datetime(2026, 2, 3, 9, 30, 15)  # Latest
        time3 = datetime(2026, 2, 3, 9, 30, 5)

        executions = [
            create_mock_execution(12345, "E1", time1),
            create_mock_execution(12345, "E2", time2),
            create_mock_execution(12345, "E3", time3),
        ]

        fill_time = reconciler._get_fill_time(executions)

        # Verify latest time was selected
        assert fill_time == time2

    def test_get_fill_time_with_no_executions(self, reconciler):
        """Test fill time is None when no executions."""
        fill_time = reconciler._get_fill_time([])
        assert fill_time is None


class TestPositionKeyGeneration:
    """Tests for position key generation methods."""

    def test_position_key_from_contract(self, reconciler):
        """Test position key is generated correctly from IBKR contract."""
        contract = Mock()
        contract.symbol = "AAPL"
        contract.strike = 150.0
        contract.lastTradeDateOrContractMonth = "20260214"
        contract.right = "P"

        key = reconciler._position_key(contract)

        assert key == "AAPL_150.0_20260214_P"

    def test_trade_key_from_database_trade(self, reconciler):
        """Test trade key is generated correctly from database trade."""
        trade = Mock()
        trade.symbol = "MSFT"
        trade.strike = 400.0
        trade.expiration = date(2026, 2, 14)
        trade.option_type = "P"

        key = reconciler._trade_key(trade)

        assert key == "MSFT_400.0_20260214_P"

    def test_trade_key_with_string_expiration(self, reconciler):
        """Test trade key handles string expiration dates."""
        trade = Mock()
        trade.symbol = "GOOGL"
        trade.strike = 140.0
        trade.expiration = "2026-02-14"  # String format
        trade.option_type = "P"

        key = reconciler._trade_key(trade)

        assert key == "GOOGL_140.0_20260214_P"


class TestEdgeCases:
    """Tests for edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_reconciliation_without_trade_repo(
        self,
        mock_ibkr_client,
    ):
        """Test reconciliation works without trade repository (reporting only)."""
        reconciler = OrderReconciliation(
            ibkr_client=mock_ibkr_client,
            trade_repository=None,  # No repo
        )

        # TWS has orders
        ib_trade = create_mock_ib_trade(12345, "AAPL", "Filled")
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Execute reconciliation
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Should complete without errors (no database updates)
        assert report.total_reconciled == 0
        assert len(report.orphans) == 0

    @pytest.mark.asyncio
    async def test_position_reconciliation_without_trade_repo(
        self,
        mock_ibkr_client,
    ):
        """Test position reconciliation works without trade repository."""
        reconciler = OrderReconciliation(
            ibkr_client=mock_ibkr_client,
            trade_repository=None,
        )

        # IBKR has positions
        ib_position = create_mock_position("AAPL", 150.0, "20260214", "P", 5)
        mock_ibkr_client.get_positions.return_value = [ib_position]

        # Execute position reconciliation
        report = await reconciler.reconcile_positions()

        # Should complete without errors
        assert not report.has_discrepancies

    @pytest.mark.asyncio
    async def test_database_update_failure_handling(
        self,
        reconciler,
        mock_ibkr_client,
        mock_trade_repo,
    ):
        """Test handling of database update failures."""
        # DB has order
        db_trade = create_mock_db_trade(
            id=13,
            order_id=12357,
            symbol="AAPL",
            status="submitted",
        )
        mock_trade_repo.get_trades_by_date.return_value = [db_trade]

        # TWS shows filled
        ib_trade = create_mock_ib_trade(
            order_id=12357,
            symbol="AAPL",
            status="Filled",
            avg_fill_price=0.45,
            filled_qty=5,
        )
        mock_ibkr_client.get_trades.return_value = [ib_trade]

        # Mock database update failure
        mock_trade_repo.update_trade.side_effect = Exception("Database error")

        # Execute reconciliation (should not crash)
        report = await reconciler.sync_all_orders(
            sync_date=date(2026, 2, 3),
        )

        # Should still complete and report discrepancy
        assert report.total_discrepancies == 1


class TestReconciliationReport:
    """Tests for ReconciliationReport dataclass."""

    def test_total_reconciled_count(self):
        """Test total_reconciled property."""
        report = ReconciliationReport(date=date.today())

        # Add reconciled trades
        mock_db_trade = Mock(symbol="AAPL", order_id=12345, status="filled")
        mock_ib_trade = create_mock_ib_trade(12345, "AAPL", "Filled")

        report.add_reconciled(mock_db_trade, mock_ib_trade)
        report.add_reconciled(mock_db_trade, mock_ib_trade)

        assert report.total_reconciled == 2

    def test_total_discrepancies_count(self):
        """Test total_discrepancies property."""
        report = ReconciliationReport(date=date.today())

        mock_db_trade = Mock(symbol="AAPL", order_id=12345, status="filled")
        mock_ib_trade = create_mock_ib_trade(12345, "AAPL", "Filled")

        # Add with discrepancy
        discrepancy = Discrepancy(
            type="STATUS_MISMATCH",
            field="status",
            db_value="submitted",
            tws_value="Filled",
            resolved=True,
        )
        report.add_reconciled(mock_db_trade, mock_ib_trade, discrepancy)

        # Add without discrepancy
        report.add_reconciled(mock_db_trade, mock_ib_trade, None)

        assert report.total_discrepancies == 1

    def test_total_resolved_count(self):
        """Test total_resolved property."""
        report = ReconciliationReport(date=date.today())

        mock_db_trade = Mock(symbol="AAPL", order_id=12345, status="filled")
        mock_ib_trade = create_mock_ib_trade(12345, "AAPL", "Filled")

        # Add resolved discrepancy
        resolved = Discrepancy(
            type="STATUS_MISMATCH",
            field="status",
            db_value="submitted",
            tws_value="Filled",
            resolved=True,
        )
        report.add_reconciled(mock_db_trade, mock_ib_trade, resolved)

        # Add unresolved discrepancy
        unresolved = Discrepancy(
            type="FILL_PRICE_MISMATCH",
            field="fill_price",
            db_value=0.40,
            tws_value=0.45,
            resolved=False,
        )
        report.add_reconciled(mock_db_trade, mock_ib_trade, unresolved)

        assert report.total_resolved == 1


class TestPositionMismatchDataclass:
    """Tests for PositionMismatch dataclass."""

    def test_difference_calculation(self):
        """Test difference property calculates correctly."""
        mismatch = PositionMismatch(
            contract_key="AAPL_150.0_20260214_P",
            db_quantity=5,
            ibkr_quantity=3,
        )

        # Difference should be IBKR - DB = 3 - 5 = -2
        assert mismatch.difference == -2

    def test_positive_difference(self):
        """Test positive difference (IBKR has more)."""
        mismatch = PositionMismatch(
            contract_key="MSFT_400.0_20260214_P",
            db_quantity=3,
            ibkr_quantity=5,
        )

        assert mismatch.difference == 2
