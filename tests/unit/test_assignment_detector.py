"""Tests for AssignmentDetector — detects naked put assignments."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.services.assignment_detector import AssignmentDetector, AssignmentEvent


def make_ibkr_position(symbol, sec_type, position, avg_cost, strike=None, right=None, exp=None):
    """Helper to create a mock IBKR position."""
    contract = MagicMock()
    contract.symbol = symbol
    contract.secType = sec_type
    contract.strike = strike
    contract.right = right
    contract.lastTradeDateOrContractMonth = exp

    pos = MagicMock()
    pos.contract = contract
    pos.position = position
    pos.avgCost = avg_cost
    return pos


def make_trade(symbol, strike, expiration, option_type="PUT", exit_date=None, trade_id=None):
    """Helper to create a mock Trade object."""
    trade = MagicMock()
    trade.symbol = symbol
    trade.strike = strike
    trade.expiration = expiration
    trade.option_type = option_type
    trade.exit_date = exit_date
    trade.trade_id = trade_id or f"TRD_{symbol}_{strike}"
    return trade


class TestAssignmentEvent:
    """Test AssignmentEvent dataclass."""

    def test_contracts_assigned(self):
        """100 shares = 1 contract assigned."""
        event = AssignmentEvent(symbol="AAPL", shares=100, avg_cost=150.0)
        assert event.contracts_assigned == 1

    def test_contracts_assigned_multiple(self):
        """500 shares = 5 contracts assigned."""
        event = AssignmentEvent(symbol="AAPL", shares=500, avg_cost=150.0)
        assert event.contracts_assigned == 5

    def test_event_fields(self):
        """Verify all fields are set correctly."""
        event = AssignmentEvent(
            symbol="MSFT",
            shares=200,
            avg_cost=350.0,
            matched_trade_id="TRD_MSFT_340",
            matched_strike=340.0,
            matched_expiration="2026-02-20",
        )
        assert event.symbol == "MSFT"
        assert event.shares == 200
        assert event.avg_cost == 350.0
        assert event.matched_trade_id == "TRD_MSFT_340"
        assert event.matched_strike == 340.0
        assert event.contracts_assigned == 2


class TestAssignmentDetector:
    """Test AssignmentDetector.check_for_assignments()."""

    def test_no_stock_positions_no_assignments(self):
        """No stock positions → no assignments detected."""
        ibkr_client = MagicMock()
        # Only option positions, no stocks
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("AAPL", "OPT", -1, 250.0, strike=170.0, right="P", exp="20260220"),
        ]

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()
        assert events == []

    def test_stock_position_not_multiple_of_100_ignored(self):
        """Stock position not a multiple of 100 shares is not an assignment."""
        ibkr_client = MagicMock()
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("AAPL", "STK", 50, 180.0),
        ]

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()
        assert events == []

    def test_short_stock_position_ignored(self):
        """Short stock position (negative shares) is not a put assignment."""
        ibkr_client = MagicMock()
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("AAPL", "STK", -100, 180.0),
        ]

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()
        assert events == []

    @patch("src.data.database.get_db_session")
    def test_stock_position_with_matching_open_put(self, mock_db):
        """Stock position matching an open put trade → assignment detected."""
        ibkr_client = MagicMock()
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("AAPL", "STK", 100, 175.0),
            make_ibkr_position("AAPL", "OPT", -1, 250.0, strike=170.0, right="P", exp="20260220"),
        ]

        # Mock database with matching open put
        from datetime import date
        mock_session = MagicMock()
        mock_trade = make_trade(
            "AAPL", 170.0, date(2026, 2, 20),
            trade_id="TRD_AAPL_170",
        )
        mock_session.query.return_value.filter.return_value.all.return_value = [mock_trade]
        mock_db.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db.return_value.__exit__ = Mock(return_value=False)

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()

        assert len(events) == 1
        assert events[0].symbol == "AAPL"
        assert events[0].shares == 100
        assert events[0].avg_cost == 175.0
        assert events[0].matched_trade_id == "TRD_AAPL_170"
        assert events[0].matched_strike == 170.0

    @patch("src.data.database.get_db_session")
    def test_stock_position_no_matching_trade(self, mock_db):
        """Stock position with no matching put trade → not flagged.

        The user may have other stock positions unrelated to our put strategy.
        """
        ibkr_client = MagicMock()
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("TSLA", "STK", 200, 250.0),
        ]

        # Mock database with no matching trades
        mock_session = MagicMock()
        mock_session.query.return_value.filter.return_value.all.return_value = []
        mock_session.query.return_value.filter.return_value.order_by.return_value.first.return_value = None
        mock_db.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db.return_value.__exit__ = Mock(return_value=False)

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()
        assert events == []

    @patch("src.data.database.get_db_session")
    def test_deduplication(self, mock_db):
        """Same assignment should only be reported once."""
        ibkr_client = MagicMock()
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("AAPL", "STK", 100, 175.0),
        ]

        from datetime import date
        mock_session = MagicMock()
        mock_trade = make_trade("AAPL", 170.0, date(2026, 2, 20), trade_id="TRD_AAPL_170")
        mock_session.query.return_value.filter.return_value.all.return_value = [mock_trade]
        mock_db.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db.return_value.__exit__ = Mock(return_value=False)

        detector = AssignmentDetector(ibkr_client)

        # First check → detected
        events1 = detector.check_for_assignments()
        assert len(events1) == 1

        # Second check → already reported, deduplicated
        events2 = detector.check_for_assignments()
        assert len(events2) == 0

    def test_ibkr_connection_error_returns_empty(self):
        """IBKR connection error returns empty list, doesn't crash."""
        ibkr_client = MagicMock()
        ibkr_client.get_positions.side_effect = Exception("Connection lost")

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()
        assert events == []

    @patch("src.data.database.get_db_session")
    def test_multiple_assignments(self, mock_db):
        """Detect multiple assignments across different symbols."""
        ibkr_client = MagicMock()
        ibkr_client.get_positions.return_value = [
            make_ibkr_position("AAPL", "STK", 100, 175.0),
            make_ibkr_position("MSFT", "STK", 200, 350.0),
        ]

        from datetime import date

        # Mock: both have matching open puts
        # We need to mock the database calls for both symbols
        mock_session = MagicMock()
        apple_trade = make_trade("AAPL", 170.0, date(2026, 2, 20), trade_id="TRD_AAPL")
        msft_trade = make_trade("MSFT", 340.0, date(2026, 2, 20), trade_id="TRD_MSFT")

        # Each call to filter().all() returns the appropriate trade
        call_count = [0]
        def mock_filter(*args, **kwargs):
            result = MagicMock()
            call_count[0] += 1
            if call_count[0] <= 3:  # First 3 filter() calls are for AAPL
                result.all.return_value = [apple_trade]
            else:  # Next calls are for MSFT
                result.all.return_value = [msft_trade]
            return result

        mock_session.query.return_value.filter = mock_filter
        mock_db.return_value.__enter__ = Mock(return_value=mock_session)
        mock_db.return_value.__exit__ = Mock(return_value=False)

        detector = AssignmentDetector(ibkr_client)
        events = detector.check_for_assignments()

        assert len(events) == 2
        symbols = {e.symbol for e in events}
        assert symbols == {"AAPL", "MSFT"}
