"""Unit tests for EntrySnapshotService.

Phase 2.6A - Critical Fields Data Collection
Tests the service that captures all 66 fields at trade entry for learning engine.
"""

from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
from sqlalchemy.orm import Session

from src.data.models import TradeEntrySnapshot
from src.services.entry_snapshot import EntrySnapshotService


def _make_ibkr_client():
    """Create a properly configured mock IBKR client.

    Ensures all methods return safe non-MagicMock values so that
    numeric comparisons in the source code don't blow up with
    'TypeError: > not supported between MagicMock and int'.
    """
    client = MagicMock()

    # is_market_open() must return a dict
    client.is_market_open.return_value = {"is_open": False}

    # get_option_contract() returns a mock contract object
    client.get_option_contract.return_value = Mock(name="option_contract")

    # get_market_data() returns None (no data available by default)
    client.get_market_data.return_value = None

    # get_margin_requirement() returns None by default
    client.get_margin_requirement.return_value = None

    # ib sub-object
    client.ib = MagicMock()
    # qualifyContracts returns empty list (nothing qualified) so
    # _qualify_option_contract returns None and _capture_option_data is skipped.
    client.ib.qualifyContracts.return_value = []
    client.ib.reqMktData.return_value = Mock()
    client.ib.sleep.return_value = None
    client.ib.cancelMktData.return_value = None

    # reqHistoricalData returns empty list (no historical bars)
    client.ib.reqHistoricalData.return_value = []

    return client


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client with safe default return values."""
    return _make_ibkr_client()


@pytest.fixture
def mock_session():
    """Create mock database session."""
    session = MagicMock(spec=Session)
    return session


@pytest.fixture
def entry_service(mock_ibkr_client):
    """Create EntrySnapshotService with mock client."""
    return EntrySnapshotService(mock_ibkr_client, timeout=10)


@pytest.fixture
def mock_option_ticker():
    """Create mock option ticker with Greeks and pricing."""
    ticker = Mock()
    ticker.bid = 2.45
    ticker.ask = 2.55
    ticker.volume = 500
    ticker.openInterest = 1000

    # Mock Greeks
    ticker.modelGreeks = Mock()
    ticker.modelGreeks.delta = -0.30
    ticker.modelGreeks.gamma = 0.015
    ticker.modelGreeks.theta = -0.08
    ticker.modelGreeks.vega = 0.12
    ticker.modelGreeks.impliedVol = 0.35

    return ticker


@pytest.fixture
def mock_stock_ticker():
    """Create mock stock ticker with price data."""
    ticker = Mock()
    ticker.marketPrice = Mock(return_value=160.0)
    ticker.last = 160.0
    ticker.open = 158.5
    ticker.high = 161.0
    ticker.low = 157.8
    ticker.close = 159.0
    ticker.volume = 10000000
    ticker.avgVolume = 9500000
    return ticker


@pytest.fixture
def sample_trade_params():
    """Sample trade parameters for testing."""
    return {
        "trade_id": 1,
        "opportunity_id": 100,
        "symbol": "AAPL",
        "strike": 150.0,
        "expiration": datetime(2026, 2, 28),
        "option_type": "PUT",
        "entry_premium": 2.50,
        "contracts": 5,
        "stock_price": 160.0,
        "dte": 30,
        "source": "scan",
    }


class TestEntrySnapshotServiceInitialization:
    """Test EntrySnapshotService initialization."""

    def test_initialization_with_client(self, mock_ibkr_client):
        """Test service initializes with IBKR client."""
        service = EntrySnapshotService(mock_ibkr_client, timeout=15)

        assert service.ibkr is mock_ibkr_client
        assert service.timeout == 15

    def test_initialization_default_timeout(self, mock_ibkr_client):
        """Test service uses default timeout."""
        service = EntrySnapshotService(mock_ibkr_client)

        assert service.timeout == 10


class TestCaptureEntrySnapshot:
    """Test capture_entry_snapshot main method."""

    def test_capture_creates_snapshot_with_required_fields(
        self, entry_service, sample_trade_params
    ):
        """Test snapshot is created with all required fields."""
        # Act
        snapshot = entry_service.capture_entry_snapshot(**sample_trade_params)

        # Assert
        assert isinstance(snapshot, TradeEntrySnapshot)
        assert snapshot.trade_id == 1
        assert snapshot.opportunity_id == 100
        assert snapshot.symbol == "AAPL"
        assert snapshot.strike == 150.0
        assert snapshot.option_type == "PUT"
        assert snapshot.entry_premium == 2.50
        assert snapshot.contracts == 5
        assert snapshot.stock_price == 160.0
        assert snapshot.dte == 30
        assert snapshot.source == "scan"
        assert snapshot.captured_at is not None

    def test_capture_calculates_data_quality_score(
        self, entry_service, sample_trade_params
    ):
        """Test data quality score is calculated."""
        # Act
        snapshot = entry_service.capture_entry_snapshot(**sample_trade_params)

        # Assert
        assert snapshot.data_quality_score is not None
        assert 0.0 <= snapshot.data_quality_score <= 1.0

    def test_capture_continues_on_pricing_error(
        self, entry_service, sample_trade_params, mock_ibkr_client
    ):
        """Test capture continues when pricing fails."""
        # Arrange - Make option contract lookup fail
        mock_ibkr_client.get_option_contract.side_effect = Exception("Pricing error")

        # Act - Should not raise exception
        snapshot = entry_service.capture_entry_snapshot(**sample_trade_params)

        # Assert - Snapshot created despite error
        assert isinstance(snapshot, TradeEntrySnapshot)
        assert snapshot.bid is None  # Pricing fields not populated

    def test_capture_continues_on_greeks_error(
        self, entry_service, sample_trade_params, mock_ibkr_client
    ):
        """Test capture continues when Greeks fetch fails."""
        # Arrange - Enable market open so Greeks path is taken, then make it fail
        mock_ibkr_client.is_market_open.return_value = {"is_open": True}
        mock_ibkr_client.get_option_contract.side_effect = Exception("Greeks error")

        # Act - Should not raise exception
        snapshot = entry_service.capture_entry_snapshot(**sample_trade_params)

        # Assert - Snapshot created despite error
        assert isinstance(snapshot, TradeEntrySnapshot)
        assert snapshot.delta is None  # Greeks not populated


class TestCaptureOptionData:
    """Test _capture_option_data unified method (pricing + Greeks + liquidity)."""

    def test_capture_pricing_success(
        self, entry_service, mock_option_ticker, mock_ibkr_client
    ):
        """Test successful option pricing capture."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_contract = Mock()
        mock_ibkr_client.ib.reqMktData.return_value = mock_option_ticker
        mock_ibkr_client.ib.sleep.return_value = None

        # Act — market closed so Greeks path is skipped, but pricing is captured
        entry_service._capture_option_data(snapshot, mock_contract, market_is_open=False)

        # Assert
        assert snapshot.bid == 2.45
        assert snapshot.ask == 2.55
        assert snapshot.mid == pytest.approx(2.50, abs=0.01)
        assert snapshot.spread_pct == pytest.approx(0.04, abs=0.01)  # (2.55-2.45)/2.50

    def test_capture_pricing_calculates_spread_pct(
        self, entry_service, mock_ibkr_client
    ):
        """Test spread percentage calculation."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_ticker = Mock()
        mock_ticker.bid = 1.0
        mock_ticker.ask = 1.20
        mock_ticker.modelGreeks = None
        mock_ticker.openInterest = 0
        mock_ticker.volume = 0

        mock_contract = Mock()
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker
        mock_ibkr_client.ib.sleep.return_value = None

        # Act
        entry_service._capture_option_data(snapshot, mock_contract, market_is_open=False)

        # Assert
        assert snapshot.mid == pytest.approx(1.10, abs=0.01)
        assert snapshot.spread_pct == pytest.approx(0.1818, abs=0.01)  # 0.20/1.10

    def test_capture_greeks_success(
        self, entry_service, mock_option_ticker, mock_ibkr_client
    ):
        """Test successful Greeks capture when market is open."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_contract = Mock()
        mock_ibkr_client.ib.reqMktData.return_value = mock_option_ticker
        mock_ibkr_client.ib.sleep.return_value = None

        # Act — market open, Greeks should be captured
        entry_service._capture_option_data(snapshot, mock_contract, market_is_open=True)

        # Assert - CRITICAL FIELD #1
        assert snapshot.delta == -0.30
        assert snapshot.gamma == 0.015
        assert snapshot.theta == -0.08
        assert snapshot.vega == 0.12
        # Rho not provided by IBKR modelGreeks
        assert snapshot.rho is None

    def test_capture_greeks_handles_missing_model_greeks(
        self, entry_service, mock_ibkr_client
    ):
        """Test Greeks capture when modelGreeks is None."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_ticker = Mock()
        mock_ticker.modelGreeks = None
        mock_ticker.bid = 2.0
        mock_ticker.ask = 2.2
        mock_ticker.openInterest = 0
        mock_ticker.volume = 0

        mock_contract = Mock()
        mock_ibkr_client.ib.reqMktData.return_value = mock_ticker
        mock_ibkr_client.ib.sleep.return_value = None

        # Act — market open but no Greeks available
        entry_service._capture_option_data(snapshot, mock_contract, market_is_open=True)

        # Assert - All Greeks should be None
        assert snapshot.delta is None
        assert snapshot.gamma is None
        assert snapshot.theta is None
        assert snapshot.vega is None

    def test_capture_liquidity_success(
        self, entry_service, mock_option_ticker, mock_ibkr_client
    ):
        """Test successful liquidity capture from unified method."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_contract = Mock()
        mock_ibkr_client.ib.reqMktData.return_value = mock_option_ticker
        mock_ibkr_client.ib.sleep.return_value = None

        # Act
        entry_service._capture_option_data(snapshot, mock_contract, market_is_open=False)

        # Assert
        assert snapshot.option_volume == 500
        assert snapshot.open_interest == 1000
        assert snapshot.volume_oi_ratio == pytest.approx(0.5, abs=0.01)


class TestCaptureVolatilityData:
    """Test _capture_volatility_data method."""

    def test_capture_iv_from_option_data(
        self, entry_service, mock_option_ticker, mock_ibkr_client
    ):
        """Test IV capture from _capture_option_data - CRITICAL FIELD #2.

        IV is captured from modelGreeks.impliedVol in _capture_option_data.
        """
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_contract = Mock()
        mock_ibkr_client.ib.reqMktData.return_value = mock_option_ticker
        mock_ibkr_client.ib.sleep.return_value = None

        # Act - _capture_option_data sets IV from modelGreeks.impliedVol
        entry_service._capture_option_data(snapshot, mock_contract, market_is_open=True)

        # Assert - CRITICAL FIELD #2
        assert snapshot.iv == 0.35


class TestCaptureStockData:
    """Test _capture_stock_data method."""

    def test_capture_stock_data_success(
        self, entry_service, mock_stock_ticker, mock_ibkr_client
    ):
        """Test successful stock data capture."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        # Source now calls self.ibkr.get_market_data(stock_contract)
        mock_ibkr_client.get_stock_contract.return_value = Mock(name="stock_contract")
        mock_ibkr_client.get_market_data.return_value = {
            "symbol": "AAPL",
            "last": 160.0,
            "bid": 159.9,
            "ask": 160.1,
            "volume": 10000000,
            "open": 158.5,
            "high": 161.0,
            "low": 157.8,
            "close": 159.0,
        }

        # Act
        entry_service._capture_stock_data(snapshot, "AAPL")

        # Assert
        assert snapshot.stock_open == 158.5
        assert snapshot.stock_high == 161.0
        assert snapshot.stock_low == 157.8
        assert snapshot.stock_prev_close == 159.0
        assert snapshot.stock_change_pct == pytest.approx(0.0063, abs=0.001)


class TestCaptureTrendData:
    """Test _capture_trend_data method."""

    def test_trend_direction_uptrend(self, entry_service):
        """Test trend direction detection - CRITICAL FIELD #6."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        snapshot.stock_prev_close = 155.0  # 3.2% gain
        snapshot.stock_change_pct = (160.0 - 155.0) / 155.0  # Calculate change %

        # Act
        entry_service._capture_trend_data(snapshot, "AAPL")

        # Assert - CRITICAL FIELD #6
        assert snapshot.trend_direction == "uptrend"
        assert snapshot.trend_strength > 0

    def test_trend_direction_downtrend(self, entry_service):
        """Test downtrend detection."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=150.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        snapshot.stock_prev_close = 155.0  # 3.2% loss
        snapshot.stock_change_pct = (150.0 - 155.0) / 155.0  # Calculate change %

        # Act
        entry_service._capture_trend_data(snapshot, "AAPL")

        # Assert
        assert snapshot.trend_direction == "downtrend"
        assert snapshot.trend_strength > 0

    def test_trend_direction_sideways(self, entry_service):
        """Test sideways trend detection."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=150.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        snapshot.stock_prev_close = 150.5  # 0.3% change

        # Act
        entry_service._capture_trend_data(snapshot, "AAPL")

        # Assert
        assert snapshot.trend_direction == "sideways"


class TestCaptureMarketData:
    """Test _capture_market_data method."""

    def test_capture_vix_success(self, entry_service, mock_ibkr_client):
        """Test VIX capture - CRITICAL FIELD #4."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        # Source now calls self.ibkr.get_market_data() for both SPY and VIX
        spy_data = {"symbol": "SPY", "last": 450.0, "bid": 449.0, "ask": 451.0, "close": 448.0}
        vix_data = {"symbol": "VIX", "last": 15.5, "bid": 15.4, "ask": 15.6, "close": 15.0}

        # get_market_data is called twice: first for SPY, then for VIX
        mock_ibkr_client.get_market_data.side_effect = [spy_data, vix_data]
        mock_ibkr_client.qualify_contract.return_value = Mock()

        # Act
        entry_service._capture_market_data(snapshot)

        # Assert - CRITICAL FIELD #4
        assert snapshot.vix == 15.5
        assert snapshot.vix_change_pct == pytest.approx(0.0333, abs=0.01)
        assert snapshot.spy_price == 450.0
        assert snapshot.spy_change_pct == pytest.approx(0.0045, abs=0.001)


class TestCalculateMarginAndEfficiency:
    """Test _calculate_margin_and_efficiency method."""

    def test_margin_calculation_with_whatif_order(
        self, entry_service, mock_ibkr_client
    ):
        """Test margin calculation using IBKR whatIfOrder - CRITICAL FIELD #8."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_ibkr_client.get_margin_requirement.return_value = 3750.0

        # Act
        entry_service._calculate_margin_and_efficiency(
            snapshot, "AAPL", 150.0, datetime(2026, 2, 28), "PUT", 5, 2.50
        )

        # Assert - CRITICAL FIELD #8
        assert snapshot.margin_requirement == 3750.0
        # Premium: 2.50 * 5 * 100 = 1250
        # Efficiency: 1250 / 3750 = 0.333 (33.3%)
        assert snapshot.margin_efficiency_pct == pytest.approx(0.333, abs=0.01)

    def test_margin_calculation_handles_none(self, entry_service, mock_ibkr_client):
        """Test margin calculation when whatIfOrder returns None - falls back to estimate."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_ibkr_client.get_margin_requirement.return_value = None

        # Act
        entry_service._calculate_margin_and_efficiency(
            snapshot, "AAPL", 150.0, datetime(2026, 2, 28), "PUT", 5, 2.50
        )

        # Assert - When IBKR returns None, the code falls back to estimated margin:
        # estimated_margin = (strike * 20 * 0.20 + entry_premium) * contracts
        # = (150 * 20 * 0.20 + 2.50) * 5 = (600 + 2.50) * 5 = 3012.50
        # So margin is NOT None - it uses the fallback estimate
        assert snapshot.margin_requirement == pytest.approx(3012.5, abs=0.01)
        # Efficiency: (2.50 * 5 * 100) / 3012.50 = 1250 / 3012.50 ≈ 0.4149
        assert snapshot.margin_efficiency_pct == pytest.approx(0.4149, abs=0.01)


class TestCalculateDerivedFields:
    """Test _calculate_derived_fields method."""

    def test_calculate_otm_pct(self, entry_service):
        """Test OTM percentage calculation."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )

        # Act
        entry_service._calculate_derived_fields(snapshot)

        # Assert
        # OTM% = (160 - 150) / 160 = 0.0625 (6.25%)
        assert snapshot.otm_pct == pytest.approx(0.0625, abs=0.001)
        assert snapshot.otm_dollars == pytest.approx(10.0, abs=0.01)

    def test_calculate_mid_from_bid_ask(self, entry_service):
        """Test mid price calculation from bid/ask."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        snapshot.bid = 2.40
        snapshot.ask = 2.60

        # Act
        entry_service._calculate_derived_fields(snapshot)

        # Assert
        assert snapshot.mid == pytest.approx(2.50, abs=0.01)
        assert snapshot.spread_pct == pytest.approx(0.08, abs=0.01)  # 0.20/2.50


class TestSaveSnapshot:
    """Test save_snapshot method."""

    def test_save_snapshot_success(self, entry_service, mock_session):
        """Test successful snapshot save to database."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
            data_quality_score=0.75,
        )

        # Act
        entry_service.save_snapshot(snapshot, mock_session)

        # Assert
        mock_session.add.assert_called_once_with(snapshot)
        mock_session.commit.assert_called_once()

    def test_save_snapshot_handles_error(self, entry_service, mock_session):
        """Test save_snapshot rolls back on error."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )
        mock_session.commit.side_effect = Exception("Database error")

        # Act & Assert
        with pytest.raises(Exception):
            entry_service.save_snapshot(snapshot, mock_session)

        mock_session.rollback.assert_called_once()


class TestDataQualityScoring:
    """Test data quality scoring integration."""

    def test_snapshot_with_all_critical_fields_high_score(
        self, entry_service, sample_trade_params
    ):
        """Test snapshot with all critical fields has high quality score."""
        # Arrange - Mock all capture methods to populate critical fields
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )

        # Set all 8 critical fields
        snapshot.delta = -0.30  # CRITICAL #1
        snapshot.iv = 0.35  # CRITICAL #2
        snapshot.iv_rank = 0.65  # CRITICAL #3
        snapshot.vix = 15.5  # CRITICAL #4
        # dte = 30 already set  # CRITICAL #5
        snapshot.trend_direction = "uptrend"  # CRITICAL #6
        snapshot.days_to_earnings = 45  # CRITICAL #7
        snapshot.margin_efficiency_pct = 0.33  # CRITICAL #8

        # Act
        score = snapshot.calculate_data_quality_score()

        # Assert - Should have high score with all critical fields
        assert score >= 0.40  # At least 40% (critical fields weight)

    def test_snapshot_with_missing_critical_fields_lower_score(self):
        """Test snapshot with missing critical fields has lower score."""
        # Arrange
        snapshot = TradeEntrySnapshot(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 2, 28),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=30,
            contracts=5,
            captured_at=datetime.now(),
        )

        # Only set 2 of 8 critical fields
        snapshot.delta = -0.30
        snapshot.vix = 15.5

        # Act
        score = snapshot.calculate_data_quality_score()

        # Assert - Should have lower score with missing critical fields
        assert score < 0.30  # Less than 30%


class TestScannerFallback:
    """Test scanner fallback fills missing snapshot fields from scanner/staged data."""

    def _make_snapshot(self, **overrides):
        """Create a minimal snapshot for fallback testing."""
        defaults = dict(
            trade_id=1,
            symbol="AAPL",
            strike=150.0,
            expiration=date(2026, 3, 6),
            option_type="PUT",
            entry_premium=2.50,
            stock_price=160.0,
            dte=3,
            contracts=5,
            captured_at=datetime.now(),
        )
        defaults.update(overrides)
        return TradeEntrySnapshot(**defaults)

    def test_fallback_fills_missing_greeks(self, entry_service):
        """Test that fallback fills None Greeks from scanner data."""
        # Arrange — snapshot has no Greeks (after-hours capture)
        snapshot = self._make_snapshot()
        assert snapshot.delta is None
        assert snapshot.iv is None
        assert snapshot.gamma is None

        fallback = {
            "delta": -0.25,
            "iv": 0.32,
            "gamma": 0.012,
            "theta": -0.07,
            "vega": 0.11,
            "_source": "staged_live",
        }

        # Act
        entry_service._apply_scanner_fallback(snapshot, fallback)

        # Assert — all Greeks filled
        assert snapshot.delta == -0.25
        assert snapshot.iv == 0.32
        assert snapshot.gamma == 0.012
        assert snapshot.theta == -0.07
        assert snapshot.vega == 0.11
        assert "delta" in snapshot.notes
        assert "staged_live" in snapshot.notes

    def test_fallback_does_not_overwrite_live_data(self, entry_service):
        """Test that fallback never overwrites existing live data."""
        # Arrange — snapshot already has delta and IV from live IBKR
        snapshot = self._make_snapshot()
        snapshot.delta = -0.30  # Live data
        snapshot.iv = 0.35  # Live data
        snapshot.gamma = None  # Missing

        fallback = {
            "delta": -0.25,  # Staler — should NOT overwrite
            "iv": 0.28,  # Staler — should NOT overwrite
            "gamma": 0.012,  # Missing — should fill
            "_source": "scan_opportunity",
        }

        # Act
        entry_service._apply_scanner_fallback(snapshot, fallback)

        # Assert — live data preserved, only gamma filled
        assert snapshot.delta == -0.30  # Preserved
        assert snapshot.iv == 0.35  # Preserved
        assert snapshot.gamma == 0.012  # Filled
        assert "gamma" in snapshot.notes
        assert "delta" not in snapshot.notes  # Not mentioned as filled

    def test_fallback_fixes_dte_zero_from_scanner(self, entry_service):
        """Test that DTE=0 gets fixed when scanner has a valid DTE."""
        # Arrange — snapshot has DTE=0 (bug from getattr default)
        snapshot = self._make_snapshot(dte=0)

        fallback = {
            "dte": 3,
            "_source": "scan_opportunity",
        }

        # Act
        entry_service._apply_scanner_fallback(snapshot, fallback)

        # Assert
        assert snapshot.dte == 3

    def test_fallback_preserves_nonzero_dte(self, entry_service):
        """Test that valid nonzero DTE is NOT overwritten by fallback."""
        # Arrange — snapshot has correct DTE=5
        snapshot = self._make_snapshot(dte=5)

        fallback = {
            "dte": 3,  # Different value — should NOT overwrite
            "_source": "scan_opportunity",
        }

        # Act
        entry_service._apply_scanner_fallback(snapshot, fallback)

        # Assert
        assert snapshot.dte == 5  # Preserved

    def test_fallback_fills_bid_ask_and_recalculates_mid(self, entry_service):
        """Test that bid/ask from fallback also recalculates mid."""
        # Arrange
        snapshot = self._make_snapshot()
        assert snapshot.bid is None
        assert snapshot.ask is None

        fallback = {
            "bid": 2.40,
            "ask": 2.60,
            "_source": "staged_live",
        }

        # Act
        entry_service._apply_scanner_fallback(snapshot, fallback)

        # Assert
        assert snapshot.bid == 2.40
        assert snapshot.ask == 2.60
        assert snapshot.mid == pytest.approx(2.50, abs=0.01)

    def test_fallback_no_fields_needed(self, entry_service):
        """Test graceful handling when snapshot already has all data."""
        # Arrange — snapshot already fully populated
        snapshot = self._make_snapshot(dte=3)
        snapshot.delta = -0.30
        snapshot.iv = 0.35
        snapshot.gamma = 0.015
        snapshot.theta = -0.08
        snapshot.vega = 0.12
        snapshot.bid = 2.45
        snapshot.ask = 2.55
        snapshot.option_volume = 500
        snapshot.open_interest = 1000

        fallback = {
            "delta": -0.25,
            "iv": 0.28,
            "bid": 2.30,
            "ask": 2.50,
            "_source": "scan_opportunity",
        }

        # Act
        entry_service._apply_scanner_fallback(snapshot, fallback)

        # Assert — nothing overwritten
        assert snapshot.delta == -0.30
        assert snapshot.iv == 0.35
        assert snapshot.bid == 2.45
        assert snapshot.ask == 2.55
        assert snapshot.notes is None  # No fields filled

    def test_capture_with_scanner_fallback_param(self, entry_service, sample_trade_params):
        """Test that capture_entry_snapshot accepts and applies scanner_fallback."""
        # Arrange
        fallback = {
            "delta": -0.22,
            "iv": 0.30,
            "gamma": 0.010,
            "_source": "staged_live",
        }

        # Act
        snapshot = entry_service.capture_entry_snapshot(
            **sample_trade_params,
            scanner_fallback=fallback,
        )

        # Assert — Greeks filled from fallback (market closed in mock)
        assert snapshot.delta == -0.22
        assert snapshot.iv == 0.30
        assert snapshot.gamma == 0.010
