"""Unit tests for market context service.

Phase 2.6C - Market Context & Events
Tests market regime classification, calendar detection, and earnings integration.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import date, datetime

from src.services.market_context import MarketContextService, MarketContext
from src.services.earnings_service import EarningsService, EarningsInfo, get_cached_earnings
from src.utils.timezone import us_trading_date


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    mock = Mock()
    mock.ib = Mock()
    return mock


@pytest.fixture
def market_service(mock_ibkr_client):
    """Create market context service instance."""
    return MarketContextService(mock_ibkr_client)


# ============================================================
# Volatility Regime Tests
# ============================================================


def test_vol_regime_low(market_service):
    """Test low volatility regime classification."""
    regime = market_service._classify_vol_regime(12.0)
    assert regime == "low"


def test_vol_regime_normal(market_service):
    """Test normal volatility regime classification."""
    regime = market_service._classify_vol_regime(17.0)
    assert regime == "normal"


def test_vol_regime_elevated(market_service):
    """Test elevated volatility regime classification."""
    regime = market_service._classify_vol_regime(22.0)
    assert regime == "elevated"


def test_vol_regime_extreme(market_service):
    """Test extreme volatility regime classification."""
    regime = market_service._classify_vol_regime(30.0)
    assert regime == "extreme"


def test_vol_regime_boundary_low_normal(market_service):
    """Test boundary between low and normal volatility."""
    # VIX = 15 should be normal (threshold is <15 for low)
    regime = market_service._classify_vol_regime(15.0)
    assert regime == "normal"


def test_vol_regime_boundary_normal_elevated(market_service):
    """Test boundary between normal and elevated volatility."""
    # VIX = 20 should be elevated (threshold is <20 for normal)
    regime = market_service._classify_vol_regime(20.0)
    assert regime == "elevated"


# ============================================================
# Market Regime Tests
# ============================================================


def test_market_regime_bullish(market_service):
    """Test bullish market regime."""
    regime = market_service._classify_market_regime(0.015, 18.0)  # 1.5% up, normal VIX
    assert regime == "bullish"


def test_market_regime_bearish(market_service):
    """Test bearish market regime."""
    regime = market_service._classify_market_regime(-0.015, 18.0)  # 1.5% down, normal VIX
    assert regime == "bearish"


def test_market_regime_neutral(market_service):
    """Test neutral market regime."""
    regime = market_service._classify_market_regime(0.005, 18.0)  # 0.5% change, normal VIX
    assert regime == "neutral"


def test_market_regime_volatile(market_service):
    """Test volatile market regime overrides other signals."""
    regime = market_service._classify_market_regime(0.02, 30.0)  # 2% up but high VIX
    assert regime == "volatile"


def test_market_regime_neutral_no_spy_data(market_service):
    """Test neutral regime when no SPY data available."""
    regime = market_service._classify_market_regime(None, 18.0)
    assert regime == "neutral"


# ============================================================
# Calendar Tests - OpEx Week
# ============================================================


def test_is_opex_week_true_on_friday(market_service):
    """Test OpEx week detection on 3rd Friday."""
    # January 2026: 3rd Friday is 16th
    third_friday = date(2026, 1, 16)
    assert market_service._is_opex_week(third_friday) is True


def test_is_opex_week_true_on_monday_before(market_service):
    """Test OpEx week detection on Monday before 3rd Friday."""
    # January 2026: Monday of OpEx week is 12th
    monday = date(2026, 1, 12)
    assert market_service._is_opex_week(monday) is True


def test_is_opex_week_false_week_before(market_service):
    """Test OpEx week detection on week before."""
    # Week before OpEx week
    week_before = date(2026, 1, 9)
    assert market_service._is_opex_week(week_before) is False


def test_is_opex_week_false_week_after(market_service):
    """Test OpEx week detection on week after."""
    # Week after OpEx week
    week_after = date(2026, 1, 19)
    assert market_service._is_opex_week(week_after) is False


# ============================================================
# Calendar Tests - FOMC
# ============================================================


def test_days_to_next_fomc_exact_date(market_service):
    """Test days to FOMC on exact meeting date."""
    # January 29, 2026 is an FOMC date
    fomc_date = date(2026, 1, 29)
    days = market_service._days_to_next_fomc(fomc_date)
    assert days == 0


def test_days_to_next_fomc_before_meeting(market_service):
    """Test days to FOMC before meeting."""
    # 10 days before first FOMC meeting
    before = date(2026, 1, 19)
    days = market_service._days_to_next_fomc(before)
    assert days == 10


def test_days_to_next_fomc_after_meeting(market_service):
    """Test days to FOMC after one meeting (should get next)."""
    # After Jan 29 meeting, next is Mar 18
    after_first = date(2026, 2, 1)
    days = market_service._days_to_next_fomc(after_first)
    expected_days = (date(2026, 3, 18) - after_first).days
    assert days == expected_days


def test_days_to_next_fomc_end_of_year(market_service):
    """Test days to FOMC after last meeting of year."""
    # After Dec 16 meeting, no more this year
    after_last = date(2026, 12, 20)
    days = market_service._days_to_next_fomc(after_last)
    assert days == 999  # No upcoming FOMC this year


# ============================================================
# Sector Mapping Tests
# ============================================================


def test_sector_etf_mapping_technology(market_service):
    """Test sector ETF mapping for Technology."""
    etf = market_service.SECTOR_ETFS.get("Technology")
    assert etf == "XLK"


def test_sector_etf_mapping_healthcare(market_service):
    """Test sector ETF mapping for Healthcare."""
    etf = market_service.SECTOR_ETFS.get("Healthcare")
    assert etf == "XLV"


def test_sector_etf_mapping_alias(market_service):
    """Test sector ETF mapping with alias."""
    # "Information Technology" should map same as "Technology"
    etf = market_service.SECTOR_ETFS.get("Information Technology")
    assert etf == "XLK"


# ============================================================
# Earnings Service Tests
# ============================================================


def test_earnings_info_dataclass():
    """Test EarningsInfo dataclass initialization."""
    info = EarningsInfo(
        earnings_date=date(2026, 2, 15),
        days_to_earnings=10,
        earnings_timing="AMC",
        earnings_in_dte=True,
    )

    assert info.earnings_date == date(2026, 2, 15)
    assert info.days_to_earnings == 10
    assert info.earnings_timing == "AMC"
    assert info.earnings_in_dte is True


def test_earnings_info_default_values():
    """Test EarningsInfo default values."""
    info = EarningsInfo()

    assert info.earnings_date is None
    assert info.days_to_earnings is None
    assert info.earnings_timing is None
    assert info.earnings_in_dte is None


def test_earnings_service_initialization():
    """Test earnings service initialization."""
    service = EarningsService(data_source="yahoo")
    assert service.data_source == "yahoo"

    service2 = EarningsService(data_source="fmp")
    assert service2.data_source == "fmp"


def test_earnings_info_calculates_days_correctly():
    """Test that earnings service calculates days_to_earnings correctly."""
    service = EarningsService()

    # Mock the fetch method to return a fixed date
    earnings_date = us_trading_date() + __import__("datetime").timedelta(days=14)

    def mock_fetch(symbol):
        return earnings_date, "AMC"

    service._fetch_from_yahoo = mock_fetch

    info = service.get_earnings_info("AAPL")

    assert info.earnings_date == earnings_date
    assert info.days_to_earnings == 14
    assert info.earnings_timing == "AMC"


def test_earnings_info_earnings_in_dte_true():
    """Test earnings_in_dte flag when earnings before expiration."""
    service = EarningsService()

    earnings_date = us_trading_date() + __import__("datetime").timedelta(days=10)
    option_expiration = us_trading_date() + __import__("datetime").timedelta(days=20)

    def mock_fetch(symbol):
        return earnings_date, "BMO"

    service._fetch_from_yahoo = mock_fetch

    info = service.get_earnings_info("AAPL", option_expiration)

    assert info.earnings_in_dte is True  # Earnings before expiration


def test_earnings_info_earnings_in_dte_false():
    """Test earnings_in_dte flag when earnings after expiration."""
    service = EarningsService()

    earnings_date = us_trading_date() + __import__("datetime").timedelta(days=30)
    option_expiration = us_trading_date() + __import__("datetime").timedelta(days=20)

    def mock_fetch(symbol):
        return earnings_date, "AMC"

    service._fetch_from_yahoo = mock_fetch

    info = service.get_earnings_info("AAPL", option_expiration)

    assert info.earnings_in_dte is False  # Earnings after expiration


def test_earnings_fetch_error_handling():
    """Test earnings service handles fetch errors gracefully."""
    service = EarningsService()

    # Mock fetch to raise exception
    def mock_fetch(symbol):
        raise Exception("API error")

    service._fetch_from_yahoo = mock_fetch

    # Should not raise, should return empty info
    info = service.get_earnings_info("AAPL")

    assert info.earnings_date is None
    assert info.days_to_earnings is None


# ============================================================
# Earnings Cache Tests
# ============================================================


def test_earnings_cache_stores_results():
    """Test that earnings cache stores results."""
    from src.services import earnings_service

    # Clear cache
    earnings_service._earnings_cache.clear()

    try:
        # Mock service
        with patch.object(EarningsService, "get_earnings_info") as mock_fetch:
            mock_fetch.return_value = EarningsInfo(
                earnings_date=date(2026, 2, 15), days_to_earnings=10, earnings_timing="AMC"
            )

            # First call should hit the service
            info1 = get_cached_earnings("AAPL")
            assert mock_fetch.call_count == 1

            # Second call should use cache
            info2 = get_cached_earnings("AAPL")
            assert mock_fetch.call_count == 1  # Not called again

            assert info1.earnings_date == info2.earnings_date
    finally:
        # Clean up module-level cache to prevent pollution of other tests
        earnings_service._earnings_cache.clear()


# ============================================================
# Integration Tests
# ============================================================


def test_capture_context_returns_market_context(market_service):
    """Test capture_context returns MarketContext object."""
    # Mock all external calls to avoid actual API calls
    market_service._capture_indices = Mock()
    market_service._get_sector = Mock(return_value="Technology")
    market_service._capture_sector_performance = Mock()

    # Use 1.5% change to clearly trigger bullish classification
    context = market_service.capture_context("AAPL", 18.0, 0.015)

    assert isinstance(context, MarketContext)
    assert context.vol_regime == "normal"
    assert context.market_regime == "bullish"
    assert context.day_of_week is not None


def test_capture_context_handles_errors_gracefully(market_service):
    """Test capture_context handles errors in sub-methods."""
    # Mock methods to raise errors
    market_service._capture_indices = Mock(side_effect=Exception("API error"))
    market_service._get_sector = Mock(side_effect=Exception("Sector error"))

    # Should not raise, should return partial context
    context = market_service.capture_context("AAPL", 18.0, 0.01)

    assert isinstance(context, MarketContext)
    # Basic fields should still be populated
    assert context.vol_regime is not None
    assert context.day_of_week is not None
