"""Tests for TAAD Barchart historical options scraper."""

import json
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.taad.enrichment.barchart_scraper import (
    BARCHART_EARLIEST_DATE,
    BarchartHistoricalCache,
    BarchartScraperProvider,
    _build_option_symbol,
    _parse_barchart_response,
)


class TestBuildOptionSymbol:
    """Tests for OCC option symbol construction."""

    def test_put_symbol(self):
        symbol = _build_option_symbol("AAPL", date(2024, 1, 19), "P", 185.0)
        assert symbol == "AAPL240119P00185000"

    def test_call_symbol(self):
        symbol = _build_option_symbol("MSFT", date(2024, 3, 15), "C", 400.0)
        assert symbol == "MSFT240315C00400000"

    def test_fractional_strike(self):
        symbol = _build_option_symbol("SPY", date(2024, 6, 21), "P", 512.5)
        assert symbol == "SPY240621P00512500"

    def test_put_full_word(self):
        """Accepts 'PUT' as well as 'P'."""
        symbol = _build_option_symbol("AAPL", date(2024, 1, 19), "PUT", 185.0)
        assert symbol == "AAPL240119P00185000"


class TestParseBarchartResponse:
    """Tests for Barchart API response parsing."""

    def test_full_response(self):
        data = {
            "results": [
                {
                    "bidPrice": 1.25,
                    "askPrice": 1.50,
                    "volume": 1234,
                    "openInterest": 5678,
                    "impliedVolatility": 0.32,
                    "delta": -0.25,
                    "gamma": 0.015,
                    "theta": -0.05,
                    "vega": 0.12,
                    "rho": -0.03,
                }
            ]
        }
        parsed = _parse_barchart_response(data)
        assert parsed is not None
        assert parsed["bid"] == 1.25
        assert parsed["ask"] == 1.50
        assert parsed["volume"] == 1234
        assert parsed["iv"] == 0.32
        assert parsed["delta"] == -0.25

    def test_empty_results(self):
        assert _parse_barchart_response({"results": []}) is None

    def test_missing_results(self):
        assert _parse_barchart_response({}) is None

    def test_partial_data(self):
        data = {
            "results": [{"impliedVolatility": 0.28, "delta": -0.20}]
        }
        parsed = _parse_barchart_response(data)
        assert parsed is not None
        assert parsed["iv"] == 0.28
        assert parsed["delta"] == -0.20
        assert "bid" not in parsed


class TestBarchartHistoricalCache:
    """Tests for the SQLite option data cache."""

    def test_put_and_get(self, tmp_path):
        cache = BarchartHistoricalCache(tmp_path / "test_cache.db")
        data = {"iv": 0.30, "delta": -0.25}

        cache.put("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5), data)

        result = cache.get("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5))
        assert result is not None
        assert result["iv"] == 0.30

    def test_cache_miss(self, tmp_path):
        cache = BarchartHistoricalCache(tmp_path / "test_cache.db")
        result = cache.get("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5))
        assert result is None

    def test_has(self, tmp_path):
        cache = BarchartHistoricalCache(tmp_path / "test_cache.db")
        data = {"iv": 0.30}

        assert not cache.has("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5))
        cache.put("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5), data)
        assert cache.has("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5))

    def test_count(self, tmp_path):
        cache = BarchartHistoricalCache(tmp_path / "test_cache.db")
        assert cache.count() == 0

        cache.put("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5), {"iv": 0.3})
        cache.put("MSFT", 400.0, date(2024, 3, 15), "P", date(2024, 3, 1), {"iv": 0.25})
        assert cache.count() == 2

    def test_upsert(self, tmp_path):
        cache = BarchartHistoricalCache(tmp_path / "test_cache.db")
        cache.put("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5), {"iv": 0.30})
        cache.put("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5), {"iv": 0.35})

        result = cache.get("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5))
        assert result["iv"] == 0.35
        assert cache.count() == 1


class TestBarchartScraperProvider:
    """Tests for the BarchartScraperProvider."""

    def test_no_api_key_returns_none(self, tmp_path):
        provider = BarchartScraperProvider(api_key="", cache_path=tmp_path / "c.db")
        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
        )
        assert result is None

    def test_pre_2023_returns_none(self, tmp_path):
        provider = BarchartScraperProvider(
            api_key="test_key", cache_path=tmp_path / "c.db"
        )
        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2022, 1, 19), "P", date(2022, 1, 5)
        )
        assert result is None

    def test_cache_hit(self, tmp_path):
        provider = BarchartScraperProvider(
            api_key="test_key", cache_path=tmp_path / "c.db"
        )
        # Pre-populate cache
        provider.cache.put(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5),
            {"iv": 0.32, "delta": -0.25, "bid": 1.25, "ask": 1.50,
             "volume": 1000, "open_interest": 5000},
        )

        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
        )
        assert result is not None
        assert result.iv == 0.32
        assert result.delta == -0.25
        assert result.source == "barchart"
        assert result.mid == pytest.approx(1.375, abs=0.001)

    def test_stock_bar_returns_none(self, tmp_path):
        """Non-option methods return None."""
        provider = BarchartScraperProvider(
            api_key="test_key", cache_path=tmp_path / "c.db"
        )
        assert provider.get_stock_bar("AAPL", date(2024, 1, 5)) is None
        assert provider.get_vix_close(date(2024, 1, 5)) is None
        assert provider.get_index_bar("SPY", date(2024, 1, 5)) is None
        assert provider.get_historical_bars("AAPL", date(2024, 1, 5)) is None
        assert provider.get_sector_etf_bars("XLK", date(2024, 1, 5)) is None

    @patch("src.taad.enrichment.barchart_scraper.httpx.Client")
    def test_api_fetch_and_cache(self, mock_client_cls, tmp_path):
        """Test that API fetch populates the cache."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "results": [
                {
                    "bidPrice": 2.00,
                    "askPrice": 2.30,
                    "volume": 500,
                    "openInterest": 3000,
                    "impliedVolatility": 0.35,
                    "delta": -0.30,
                    "gamma": 0.02,
                    "theta": -0.06,
                    "vega": 0.15,
                }
            ]
        }
        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        provider = BarchartScraperProvider(
            api_key="test_key",
            cache_path=tmp_path / "c.db",
            rate_limit_seconds=0.0,
        )

        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
        )

        assert result is not None
        assert result.iv == 0.35
        assert result.delta == -0.30
        assert result.source == "barchart"

        # Verify cached
        assert provider.cache.has("AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5))

    def test_earliest_date_constant(self):
        assert BARCHART_EARLIEST_DATE == date(2023, 3, 1)
