"""Unit tests for exit snapshot service.

Phase 2.6E - Exit Snapshots & Learning Data
Tests exit data capture, path analysis, and quality scoring.
"""

import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime, date, timedelta

from src.services.exit_snapshot import ExitSnapshotService
from src.data.models import Trade, TradeEntrySnapshot, TradeExitSnapshot, PositionSnapshot


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    mock = Mock()
    mock.ib = Mock()
    return mock


@pytest.fixture
def mock_db_session():
    """Create mock database session."""
    mock = Mock()
    return mock


@pytest.fixture
def exit_service(mock_ibkr_client, mock_db_session):
    """Create exit snapshot service instance."""
    return ExitSnapshotService(mock_ibkr_client, mock_db_session)


@pytest.fixture
def sample_trade():
    """Create sample trade object."""
    trade = Trade(
        id=1,
        trade_id="TEST001",
        symbol="AAPL",
        strike=150.0,
        expiration=date.today() + timedelta(days=30),
        option_type="PUT",
        entry_date=datetime.now() - timedelta(days=10),
        entry_premium=2.50,
        contracts=5,
        exit_date=datetime.now(),
    )
    return trade


@pytest.fixture
def sample_entry_snapshot():
    """Create sample entry snapshot."""
    snapshot = TradeEntrySnapshot(
        trade_id=1,
        symbol="AAPL",
        strike=150.0,
        expiration=date.today() + timedelta(days=30),
        option_type="PUT",
        entry_premium=2.50,
        stock_price=160.0,
        dte=30,
        contracts=5,
        captured_at=datetime.now() - timedelta(days=10),
        iv=0.25,
        vix=18.0,
        margin_requirement=3000.0,
    )
    return snapshot


# ============================================================
# Outcome Metrics Tests
# ============================================================


def test_calculate_outcome_metrics_winning_trade(exit_service, sample_trade, mock_db_session):
    """Test outcome metrics for a winning trade."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=sample_trade.exit_date,
        exit_premium=1.00,  # Bought back for less
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )

    # Mock entry snapshot query
    mock_entry = Mock()
    mock_entry.margin_requirement = 3000.0
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_entry

    exit_service._calculate_outcome_metrics(snapshot, sample_trade, 1.00)

    # Verify calculations
    assert snapshot.days_held == 10
    assert snapshot.gross_profit == 750.0  # (2.50 - 1.00) * 5 * 100
    assert snapshot.roi_pct == 0.6  # 750 / 1250
    assert snapshot.win is True
    assert snapshot.roi_on_margin == 0.25  # 750 / 3000


def test_calculate_outcome_metrics_losing_trade(exit_service, sample_trade, mock_db_session):
    """Test outcome metrics for a losing trade."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=sample_trade.exit_date,
        exit_premium=3.50,  # Bought back for more (loss)
        exit_reason="stop_loss",
        captured_at=datetime.now(),
    )

    mock_db_session.query.return_value.filter.return_value.first.return_value = None

    exit_service._calculate_outcome_metrics(snapshot, sample_trade, 3.50)

    assert snapshot.gross_profit == -500.0  # (2.50 - 3.50) * 5 * 100
    assert snapshot.roi_pct < 0
    assert snapshot.win is False


def test_calculate_outcome_metrics_expiration(exit_service, sample_trade, mock_db_session):
    """Test outcome metrics for trade held to expiration."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=sample_trade.exit_date,
        exit_premium=0.01,  # Expired worthless (best outcome)
        exit_reason="expiration",
        captured_at=datetime.now(),
    )

    mock_db_session.query.return_value.filter.return_value.first.return_value = None

    exit_service._calculate_outcome_metrics(snapshot, sample_trade, 0.01)

    assert snapshot.gross_profit == 1245.0  # (2.50 - 0.01) * 5 * 100
    assert snapshot.win is True


# ============================================================
# Path Analysis Tests
# ============================================================


def test_analyze_position_path_with_snapshots(exit_service, sample_trade, mock_db_session):
    """Test path analysis with position snapshots."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )
    snapshot.roi_pct = 0.6

    # Mock position snapshots
    mock_snapshots = [
        Mock(distance_to_strike_pct=0.10, current_pnl_pct=0.2),
        Mock(distance_to_strike_pct=0.08, current_pnl_pct=0.4),  # Closest to strike
        Mock(distance_to_strike_pct=0.12, current_pnl_pct=0.8),  # Max profit
        Mock(distance_to_strike_pct=0.09, current_pnl_pct=-0.1),  # Max drawdown
    ]

    mock_db_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        mock_snapshots
    )

    exit_service._analyze_position_path(snapshot, sample_trade)

    assert snapshot.closest_to_strike_pct == 0.08
    assert snapshot.max_profit_pct == 0.8
    assert snapshot.max_drawdown_pct == -0.1
    assert snapshot.max_profit_captured_pct == pytest.approx(0.75)  # 0.6 / 0.8


def test_analyze_position_path_no_snapshots(exit_service, sample_trade, mock_db_session):
    """Test path analysis when no position snapshots exist."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )

    # Mock empty snapshots
    mock_db_session.query.return_value.filter.return_value.order_by.return_value.all.return_value = (
        []
    )

    exit_service._analyze_position_path(snapshot, sample_trade)

    # Should not crash, fields should be None
    assert snapshot.closest_to_strike_pct is None
    assert snapshot.max_profit_pct is None
    assert snapshot.max_drawdown_pct is None


# ============================================================
# Quality Score Tests
# ============================================================


def test_quality_score_perfect_trade():
    """Test quality score for perfect trade execution."""
    snapshot = TradeExitSnapshot(
        trade_id=1,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )
    snapshot.win = True
    snapshot.max_profit_captured_pct = 1.0  # Captured 100% of max profit
    snapshot.max_drawdown_pct = 0.0  # No drawdown

    score = snapshot.calculate_quality_score()

    assert score == 1.0  # Perfect score


def test_quality_score_good_trade():
    """Test quality score for good (but not perfect) trade."""
    snapshot = TradeExitSnapshot(
        trade_id=1,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )
    snapshot.win = True
    snapshot.max_profit_captured_pct = 0.8  # Captured 80% of max profit
    snapshot.max_drawdown_pct = -0.1  # Small drawdown

    score = snapshot.calculate_quality_score()

    # Should be good but not perfect
    assert 0.7 < score < 1.0


def test_quality_score_losing_trade():
    """Test quality score for losing trade."""
    snapshot = TradeExitSnapshot(
        trade_id=1,
        exit_date=datetime.now(),
        exit_premium=3.00,
        exit_reason="stop_loss",
        captured_at=datetime.now(),
    )
    snapshot.win = False
    snapshot.max_profit_captured_pct = 0.0
    snapshot.max_drawdown_pct = -0.5  # Large drawdown

    score = snapshot.calculate_quality_score()

    # Should be low for losing trade
    assert score < 0.5


def test_quality_score_with_missing_data():
    """Test quality score calculation with missing data."""
    snapshot = TradeExitSnapshot(
        trade_id=1,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="manual",
        captured_at=datetime.now(),
    )
    # No other fields set

    score = snapshot.calculate_quality_score()

    # Should return default score
    assert 0.0 <= score <= 1.0


# ============================================================
# Context Changes Tests
# ============================================================


def test_calculate_context_changes(exit_service, sample_trade, sample_entry_snapshot, mock_db_session):
    """Test calculation of context changes during trade."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )
    snapshot.exit_iv = 0.20
    snapshot.stock_price_at_exit = 165.0
    snapshot.vix_at_exit = 15.0

    # Mock entry snapshot
    mock_db_session.query.return_value.filter.return_value.first.return_value = sample_entry_snapshot

    exit_service._calculate_context_changes(snapshot, sample_trade)

    assert snapshot.iv_change_during_trade == pytest.approx(-0.05)  # 0.20 - 0.25
    assert snapshot.stock_change_during_trade_pct == pytest.approx(0.03125)  # (165-160)/160
    assert snapshot.vix_change_during_trade == pytest.approx(-3.0)  # 15 - 18


def test_calculate_context_changes_no_entry_snapshot(exit_service, sample_trade, mock_db_session):
    """Test context changes when no entry snapshot exists."""
    snapshot = TradeExitSnapshot(
        trade_id=sample_trade.id,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )

    # Mock no entry snapshot
    mock_db_session.query.return_value.filter.return_value.first.return_value = None

    exit_service._calculate_context_changes(snapshot, sample_trade)

    # Should not crash, fields should remain None
    assert snapshot.iv_change_during_trade is None
    assert snapshot.stock_change_during_trade_pct is None


# ============================================================
# Integration Tests
# ============================================================


def test_capture_exit_snapshot_complete_flow(exit_service, sample_trade, mock_db_session, mock_ibkr_client):
    """Test complete exit snapshot capture flow."""
    # Mock all external calls
    exit_service._capture_exit_context = Mock()
    exit_service._calculate_context_changes = Mock()
    exit_service._analyze_position_path = Mock()

    # Mock entry snapshot
    mock_entry = Mock()
    mock_entry.margin_requirement = 3000.0
    mock_db_session.query.return_value.filter.return_value.first.return_value = mock_entry

    # Capture snapshot
    snapshot = exit_service.capture_exit_snapshot(sample_trade, 1.00, "profit_target")

    # Verify snapshot created
    assert snapshot is not None
    assert snapshot.trade_id == sample_trade.id
    assert snapshot.exit_premium == 1.00
    assert snapshot.exit_reason == "profit_target"
    assert snapshot.win is True
    assert snapshot.trade_quality_score is not None


def test_save_snapshot(exit_service, mock_db_session):
    """Test saving exit snapshot to database."""
    snapshot = TradeExitSnapshot(
        trade_id=1,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )
    snapshot.win = True
    snapshot.roi_pct = 0.5

    exit_service.save_snapshot(snapshot)

    # Verify database operations
    mock_db_session.add.assert_called_once_with(snapshot)
    mock_db_session.commit.assert_called_once()


def test_save_snapshot_handles_errors(exit_service, mock_db_session):
    """Test save snapshot handles database errors."""
    snapshot = TradeExitSnapshot(
        trade_id=1,
        exit_date=datetime.now(),
        exit_premium=1.00,
        exit_reason="profit_target",
        captured_at=datetime.now(),
    )

    # Mock commit error
    mock_db_session.commit.side_effect = Exception("Database error")

    with pytest.raises(Exception):
        exit_service.save_snapshot(snapshot)

    # Verify rollback was called
    mock_db_session.rollback.assert_called_once()
