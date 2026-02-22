"""Tests for historical market context reconstruction."""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

from src.taad.enrichment.historical_context import (
    classify_vol_regime,
    classify_market_regime,
    is_opex_week,
    days_to_next_fomc,
    get_sector_for_symbol,
    get_sector_etf,
    get_historical_earnings_date,
    build_historical_context,
    FOMC_DATES,
)


class TestClassifyVolRegime:
    """Test VIX-based volatility regime classification."""

    def test_low_regime(self):
        assert classify_vol_regime(12.5) == "low"

    def test_normal_regime(self):
        assert classify_vol_regime(17.0) == "normal"

    def test_elevated_regime(self):
        assert classify_vol_regime(22.5) == "elevated"

    def test_extreme_regime(self):
        assert classify_vol_regime(35.0) == "extreme"

    def test_boundary_15(self):
        assert classify_vol_regime(15.0) == "normal"

    def test_boundary_25(self):
        assert classify_vol_regime(25.0) == "extreme"


class TestClassifyMarketRegime:
    """Test market regime classification."""

    def test_volatile_overrides(self):
        """High VIX should always return volatile regardless of SPY change."""
        assert classify_market_regime(0.05, 30.0) == "volatile"

    def test_bullish(self):
        assert classify_market_regime(0.015, 18.0) == "bullish"

    def test_bearish(self):
        assert classify_market_regime(-0.015, 18.0) == "bearish"

    def test_neutral(self):
        assert classify_market_regime(0.005, 18.0) == "neutral"

    def test_none_spy_change(self):
        assert classify_market_regime(None, 18.0) == "neutral"


class TestIsOpexWeek:
    """Test OpEx week detection."""

    def test_opex_week_jan_2025(self):
        """January 2025: 3rd Friday is Jan 17."""
        # Week of Jan 13-17 is OpEx week
        assert is_opex_week(date(2025, 1, 17)) is True
        assert is_opex_week(date(2025, 1, 13)) is True

    def test_non_opex_week(self):
        """First week of a month should not be OpEx."""
        assert is_opex_week(date(2025, 1, 6)) is False

    def test_opex_friday(self):
        """3rd Friday itself should be in OpEx week."""
        # March 2025: 3rd Friday is March 21
        assert is_opex_week(date(2025, 3, 21)) is True


class TestDaysToNextFOMC:
    """Test FOMC date lookup across all years."""

    def test_fomc_2025_before_first(self):
        """Before first 2025 FOMC should find Jan 29."""
        days = days_to_next_fomc(date(2025, 1, 15))
        expected = (date(2025, 1, 29) - date(2025, 1, 15)).days
        assert days == expected

    def test_fomc_2019_coverage(self):
        """2019 FOMC dates should be available."""
        assert 2019 in FOMC_DATES
        assert len(FOMC_DATES[2019]) == 8

    def test_fomc_2020_extra_meetings(self):
        """2020 had emergency meetings (9 total)."""
        assert 2020 in FOMC_DATES
        assert len(FOMC_DATES[2020]) == 9

    def test_fomc_all_years_present(self):
        """All years 2019-2026 should have FOMC dates."""
        for year in range(2019, 2027):
            assert year in FOMC_DATES, f"Missing FOMC dates for {year}"
            assert len(FOMC_DATES[year]) >= 8, f"Insufficient FOMC dates for {year}"

    def test_fomc_cross_year(self):
        """Date in December should find next year's FOMC."""
        days = days_to_next_fomc(date(2024, 12, 20))
        # Next FOMC after Dec 18 2024 is Jan 29 2025
        expected = (date(2025, 1, 29) - date(2024, 12, 20)).days
        assert days == expected

    def test_no_future_fomc_returns_999(self):
        """Date beyond all known FOMC dates should return 999."""
        days = days_to_next_fomc(date(2027, 6, 1))
        assert days == 999


class TestSectorLookup:
    """Test sector and ETF lookup."""

    def test_known_symbol(self):
        """Known symbol should return sector."""
        sector = get_sector_for_symbol("AAPL")
        assert sector == "Technology"

    def test_unknown_symbol(self):
        """Unknown symbol should return None."""
        sector = get_sector_for_symbol("XYZZY")
        assert sector is None

    def test_sector_etf_mapping(self):
        """Known sector should map to ETF."""
        assert get_sector_etf("Technology") == "XLK"
        assert get_sector_etf("Healthcare") == "XLV"
        assert get_sector_etf("Financials") == "XLF"

    def test_sector_etf_none(self):
        """None sector should return None."""
        assert get_sector_etf(None) is None
        assert get_sector_etf("NonexistentSector") is None


class TestBuildHistoricalContext:
    """Test full historical context building with mock provider."""

    def test_basic_context(self):
        """Build context should populate index and calendar fields."""
        from src.taad.enrichment.providers import OHLCV

        mock_provider = MagicMock()

        # Mock SPY
        mock_provider.get_index_bar.side_effect = lambda sym, dt: OHLCV(
            date=dt, open=450.0, high=455.0, low=448.0, close=452.0, volume=50000000
        ) if sym == "SPY" else OHLCV(
            date=dt, open=380.0, high=385.0, low=378.0, close=382.0, volume=30000000
        )

        mock_provider.get_vix_close.return_value = 18.5
        mock_provider.get_sector_etf_bars.return_value = None

        ctx = build_historical_context(
            symbol="AAPL",
            trade_date=date(2025, 3, 10),
            expiration=date(2025, 4, 18),
            provider=mock_provider,
        )

        assert ctx.spy_price == 452.0
        assert ctx.vix == 18.5
        assert ctx.vol_regime == "normal"
        assert ctx.sector == "Technology"
        assert ctx.sector_etf == "XLK"
        assert ctx.day_of_week == 0  # Monday
        assert ctx.days_to_fomc is not None
