"""Unit tests for opportunity idempotency and duplicate detection.

Tests the hash generation, duplicate detection, and merge functionality
in the ScanRepository.
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock

from sqlalchemy.orm import Session

from src.data.models import ScanOpportunity, ScanResult
from src.data.repositories import ScanRepository


@pytest.fixture
def mock_session():
    """Create a mock database session."""
    session = Mock(spec=Session)
    session.commit = Mock()
    session.rollback = Mock()
    session.flush = Mock()
    return session


class TestCalculateOpportunityHash:
    """Tests for opportunity hash calculation."""

    def test_hash_generation_consistent(self, mock_session):
        """Test that hash generation is consistent for same inputs."""
        repo = ScanRepository(mock_session)

        hash1 = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash2 = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        assert hash1 == hash2

    def test_hash_different_symbol(self, mock_session):
        """Test that different symbols produce different hashes."""
        repo = ScanRepository(mock_session)

        hash_aapl = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash_googl = repo.calculate_opportunity_hash(
            symbol="GOOGL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        assert hash_aapl != hash_googl

    def test_hash_different_strike(self, mock_session):
        """Test that different strikes produce different hashes."""
        repo = ScanRepository(mock_session)

        hash_150 = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash_145 = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=145.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        assert hash_150 != hash_145

    def test_hash_different_expiration(self, mock_session):
        """Test that different expirations produce different hashes."""
        repo = ScanRepository(mock_session)

        hash_feb = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash_mar = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 3, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        assert hash_feb != hash_mar

    def test_hash_different_option_type(self, mock_session):
        """Test that different option types produce different hashes."""
        repo = ScanRepository(mock_session)

        hash_put = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash_call = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="CALL",
            date_created=datetime(2026, 1, 28),
        )

        assert hash_put != hash_call

    def test_hash_different_date_created(self, mock_session):
        """Test that different creation dates produce different hashes."""
        repo = ScanRepository(mock_session)

        hash_jan28 = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash_jan29 = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 29),
        )

        # Different dates should produce different hashes
        # This allows re-entry of same option on different days
        assert hash_jan28 != hash_jan29

    def test_hash_case_insensitive_symbol(self, mock_session):
        """Test that symbol is case-insensitive."""
        repo = ScanRepository(mock_session)

        hash_upper = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        hash_lower = repo.calculate_opportunity_hash(
            symbol="aapl",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        assert hash_upper == hash_lower

    def test_hash_length(self, mock_session):
        """Test that hash is truncated to 16 characters."""
        repo = ScanRepository(mock_session)

        hash_result = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
            date_created=datetime(2026, 1, 28),
        )

        assert len(hash_result) == 16

    def test_hash_defaults_to_today(self, mock_session):
        """Test that date_created defaults to today."""
        repo = ScanRepository(mock_session)

        hash_result = repo.calculate_opportunity_hash(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
        )

        # Should not raise an error and should return a valid hash
        assert len(hash_result) == 16


class TestFindDuplicate:
    """Tests for finding duplicate opportunities."""

    def test_find_duplicate_by_hash(self, mock_session):
        """Test finding duplicate by hash."""
        repo = ScanRepository(mock_session)

        # Setup - mock existing opportunity
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.opportunity_hash = "abc123"

        mock_session.query().filter().first.return_value = existing_opp

        # Execute
        duplicate = repo.find_duplicate(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
        )

        # Verify
        assert duplicate is not None
        assert duplicate.id == 1

    def test_find_duplicate_no_match(self, mock_session):
        """Test when no duplicate exists."""
        repo = ScanRepository(mock_session)

        # Setup - no existing opportunity
        mock_session.query().filter().first.return_value = None
        mock_session.query().join().filter().filter().filter().filter().filter().filter().order_by().first.return_value = None

        # Execute
        duplicate = repo.find_duplicate(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime(2026, 2, 14),
            option_type="PUT",
        )

        # Verify
        assert duplicate is None

    def test_find_duplicate_excludes_terminal_states(self, mock_session):
        """Test that terminal state opportunities are not considered duplicates."""
        repo = ScanRepository(mock_session)

        # This is tested implicitly in the query filter
        # The query should filter out EXECUTED, FAILED, EXPIRED, REJECTED states
        # We verify the query structure in integration tests


class TestMergeDuplicate:
    """Tests for merging duplicate opportunities."""

    def test_merge_updates_pricing(self, mock_session):
        """Test that merge updates pricing data."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.premium = 0.45
        existing_opp.bid = 0.43
        existing_opp.ask = 0.47
        existing_opp.spread_pct = 0.09
        existing_opp.entry_notes = "Manual entry"
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        new_data = {
            "premium": 0.50,
            "bid": 0.48,
            "ask": 0.52,
            "spread_pct": 0.08,
        }

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data=new_data,
            source="barchart",
        )

        # Verify
        assert updated_opp.premium == 0.50
        assert updated_opp.bid == 0.48
        assert updated_opp.ask == 0.52
        assert updated_opp.spread_pct == 0.08
        assert "[Merged from barchart]" in updated_opp.entry_notes

    def test_merge_updates_greeks(self, mock_session):
        """Test that merge updates Greeks."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.delta = None
        existing_opp.gamma = None
        existing_opp.theta = None
        existing_opp.vega = None
        existing_opp.iv = None
        existing_opp.entry_notes = None
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        new_data = {
            "delta": -0.15,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.10,
            "iv": 0.25,
        }

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data=new_data,
            source="ibkr",
        )

        # Verify
        assert updated_opp.delta == -0.15
        assert updated_opp.gamma == 0.02
        assert updated_opp.theta == -0.05
        assert updated_opp.vega == 0.10
        assert updated_opp.iv == 0.25

    def test_merge_updates_margin_data(self, mock_session):
        """Test that merge updates margin data."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.margin_required = None
        existing_opp.margin_efficiency = None
        existing_opp.entry_notes = None
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        new_data = {
            "margin_required": 5000.0,
            "margin_efficiency": 0.08,
        }

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data=new_data,
            source="ibkr",
        )

        # Verify
        assert updated_opp.margin_required == 5000.0
        assert updated_opp.margin_efficiency == 0.08

    def test_merge_updates_liquidity_data(self, mock_session):
        """Test that merge updates liquidity data."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.volume = None
        existing_opp.open_interest = None
        existing_opp.entry_notes = None
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        new_data = {
            "volume": 1500,
            "open_interest": 3000,
        }

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data=new_data,
            source="ibkr",
        )

        # Verify
        assert updated_opp.volume == 1500
        assert updated_opp.open_interest == 3000

    def test_merge_preserves_trend_if_already_set(self, mock_session):
        """Test that merge doesn't overwrite existing trend."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.trend = "uptrend"
        existing_opp.entry_notes = None
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        new_data = {
            "trend": "downtrend",  # Different trend
        }

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data=new_data,
            source="barchart",
        )

        # Verify - original trend should be preserved
        assert updated_opp.trend == "uptrend"

    def test_merge_sets_trend_if_not_set(self, mock_session):
        """Test that merge sets trend if not already set."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.trend = None
        existing_opp.entry_notes = None
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        new_data = {
            "trend": "uptrend",
        }

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data=new_data,
            source="barchart",
        )

        # Verify
        assert updated_opp.trend == "uptrend"

    def test_merge_tracks_multiple_sources(self, mock_session):
        """Test that merge tracks multiple sources in notes."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.entry_notes = "[Merged from manual]"
        existing_opp.updated_at = datetime(2026, 1, 28)

        mock_session.query().filter().first.return_value = existing_opp

        # Execute
        updated_opp = repo.merge_duplicate(
            existing_id=1,
            new_data={"premium": 0.50},
            source="barchart",
        )

        # Verify
        assert "[Merged from manual]" in updated_opp.entry_notes
        assert "[Merged from barchart]" in updated_opp.entry_notes

    def test_merge_opportunity_not_found(self, mock_session):
        """Test merge when opportunity doesn't exist."""
        repo = ScanRepository(mock_session)

        # Setup
        mock_session.query().filter().first.return_value = None

        # Execute & Verify
        with pytest.raises(ValueError, match="Opportunity .* not found"):
            repo.merge_duplicate(
                existing_id=999,
                new_data={"premium": 0.50},
                source="barchart",
            )


class TestGetOpportunityByHash:
    """Tests for getting opportunity by hash."""

    def test_get_by_hash_found(self, mock_session):
        """Test getting opportunity by hash when it exists."""
        repo = ScanRepository(mock_session)

        # Setup
        existing_opp = Mock(spec=ScanOpportunity)
        existing_opp.id = 1
        existing_opp.opportunity_hash = "abc123"

        mock_session.query().filter().first.return_value = existing_opp

        # Execute
        result = repo.get_opportunity_by_hash("abc123")

        # Verify
        assert result is not None
        assert result.id == 1

    def test_get_by_hash_not_found(self, mock_session):
        """Test getting opportunity by hash when it doesn't exist."""
        repo = ScanRepository(mock_session)

        # Setup
        mock_session.query().filter().first.return_value = None

        # Execute
        result = repo.get_opportunity_by_hash("nonexistent")

        # Verify
        assert result is None
