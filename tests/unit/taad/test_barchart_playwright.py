"""Tests for TAAD Barchart Playwright historical options scraper."""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.taad.enrichment.barchart_playwright import (
    PLAYWRIGHT_EARLIEST_DATE,
    PlaywrightBarchartProvider,
    _build_barchart_page_symbol,
    _parse_date,
    _parse_numeric,
    _parse_table_row,
)


class TestBuildBarchartPageSymbol:
    """Tests for Barchart page-format symbol construction."""

    def test_put_symbol(self):
        result = _build_barchart_page_symbol("AAPL", date(2024, 7, 19), "P", 150.0)
        assert result == "AAPL|20240719|150.00P"

    def test_call_symbol(self):
        result = _build_barchart_page_symbol("MSFT", date(2024, 3, 15), "C", 400.0)
        assert result == "MSFT|20240315|400.00C"

    def test_fractional_strike(self):
        result = _build_barchart_page_symbol("SPY", date(2024, 6, 21), "P", 512.5)
        assert result == "SPY|20240621|512.50P"

    def test_put_full_word(self):
        """Accepts 'PUT' as well as 'P'."""
        result = _build_barchart_page_symbol("AAPL", date(2024, 1, 19), "PUT", 185.0)
        assert result == "AAPL|20240119|185.00P"

    def test_call_full_word(self):
        """Accepts 'CALL' as well as 'C'."""
        result = _build_barchart_page_symbol("TSLA", date(2024, 2, 16), "CALL", 250.0)
        assert result == "TSLA|20240216|250.00C"

    def test_small_strike(self):
        result = _build_barchart_page_symbol("F", date(2024, 1, 19), "P", 12.0)
        assert result == "F|20240119|12.00P"

    def test_large_strike(self):
        result = _build_barchart_page_symbol("AMZN", date(2024, 12, 20), "C", 2150.0)
        assert result == "AMZN|20241220|2150.00C"


class TestParseNumeric:
    """Tests for numeric cell text parsing."""

    def test_simple_float(self):
        assert _parse_numeric("1.25") == 1.25

    def test_negative_float(self):
        assert _parse_numeric("-0.25") == -0.25

    def test_integer(self):
        assert _parse_numeric("500") == 500.0

    def test_commas(self):
        assert _parse_numeric("1,234") == 1234.0

    def test_large_commas(self):
        assert _parse_numeric("12,345,678") == 12345678.0

    def test_percentage(self):
        assert _parse_numeric("32.5%") == pytest.approx(0.325)

    def test_percentage_flag(self):
        assert _parse_numeric("32.5", is_percentage=True) == pytest.approx(0.325)

    def test_na(self):
        assert _parse_numeric("N/A") is None

    def test_dash(self):
        assert _parse_numeric("-") is None

    def test_double_dash(self):
        assert _parse_numeric("--") is None

    def test_empty(self):
        assert _parse_numeric("") is None

    def test_whitespace(self):
        assert _parse_numeric("  ") is None

    def test_unch(self):
        assert _parse_numeric("unch") is None

    def test_na_lowercase(self):
        assert _parse_numeric("n/a") is None

    def test_whitespace_around_value(self):
        assert _parse_numeric("  1.50  ") == 1.50


class TestParseDate:
    """Tests for date text parsing."""

    def test_mm_dd_yyyy(self):
        assert _parse_date("01/05/2024") == date(2024, 1, 5)

    def test_mm_dd_yy(self):
        assert _parse_date("01/05/24") == date(2024, 1, 5)

    def test_iso_format(self):
        assert _parse_date("2024-01-05") == date(2024, 1, 5)

    def test_empty(self):
        assert _parse_date("") is None

    def test_whitespace(self):
        assert _parse_date("  ") is None

    def test_invalid(self):
        assert _parse_date("not-a-date") is None

    def test_whitespace_around(self):
        assert _parse_date("  01/05/2024  ") == date(2024, 1, 5)


class TestParseTableRow:
    """Tests for HTML table row parsing."""

    def test_full_row(self):
        headers = [
            "time", "open", "high", "low", "last", "volume", "open int",
            "impl vol", "delta", "gamma", "theta", "vega", "rho",
        ]
        cells = [
            "01/05/2024", "1.50", "1.75", "1.25", "1.60", "1,234", "5,678",
            "32.5%", "-0.25", "0.015", "-0.05", "0.12", "-0.03",
        ]
        parsed = _parse_table_row(headers, cells)
        assert parsed is not None
        assert parsed["open"] == 1.50
        assert parsed["high"] == 1.75
        assert parsed["low"] == 1.25
        assert parsed["last"] == 1.60
        assert parsed["volume"] == 1234
        assert parsed["open_interest"] == 5678
        assert parsed["iv"] == pytest.approx(0.325)
        assert parsed["delta"] == -0.25
        assert parsed["gamma"] == 0.015
        assert parsed["theta"] == -0.05
        assert parsed["vega"] == 0.12
        assert parsed["rho"] == -0.03

    def test_partial_data(self):
        headers = ["time", "last", "volume", "impl vol", "delta"]
        cells = ["01/05/2024", "1.60", "500", "28.0%", "-0.20"]
        parsed = _parse_table_row(headers, cells)
        assert parsed is not None
        assert parsed["last"] == 1.60
        assert parsed["volume"] == 500
        assert parsed["iv"] == pytest.approx(0.28)
        assert parsed["delta"] == -0.20
        assert "gamma" not in parsed

    def test_missing_greeks(self):
        headers = ["time", "last", "volume"]
        cells = ["01/05/2024", "1.60", "500"]
        parsed = _parse_table_row(headers, cells)
        assert parsed is not None
        assert "delta" not in parsed
        assert parsed["volume"] == 500

    def test_na_values(self):
        headers = ["time", "last", "volume", "impl vol"]
        cells = ["01/05/2024", "1.60", "N/A", "--"]
        parsed = _parse_table_row(headers, cells)
        assert parsed is not None
        assert parsed["last"] == 1.60
        assert "volume" not in parsed
        assert "iv" not in parsed

    def test_empty_row(self):
        assert _parse_table_row(["time"], ["01/05/2024"]) is None

    def test_single_cell(self):
        assert _parse_table_row(["time"], [""]) is None

    def test_unknown_headers_ignored(self):
        headers = ["time", "change", "last", "theo"]
        cells = ["01/05/2024", "+0.10", "1.60", "1.55"]
        parsed = _parse_table_row(headers, cells)
        assert parsed is not None
        assert parsed["last"] == 1.60
        assert "change" not in parsed
        assert "theo" not in parsed


class TestPlaywrightBarchartProvider:
    """Tests for PlaywrightBarchartProvider."""

    def test_earliest_date_constant(self):
        assert PLAYWRIGHT_EARLIEST_DATE == date(2017, 1, 3)

    def test_pre_2017_returns_none(self, tmp_path):
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2016, 12, 30), "P", date(2016, 12, 15)
        )
        assert result is None

    def test_cache_hit_no_browser(self, tmp_path):
        """Cache hit should return data without launching Playwright."""
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        # Pre-populate shared cache
        provider.cache.put(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5),
            {
                "iv": 0.32, "delta": -0.25, "bid": 1.25, "ask": 1.50,
                "volume": 1000, "open_interest": 5000,
                "gamma": 0.015, "theta": -0.05, "vega": 0.12,
            },
        )

        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
        )
        assert result is not None
        assert result.iv == 0.32
        assert result.delta == -0.25
        assert result.source == "barchart_playwright"
        assert result.mid == pytest.approx(1.375, abs=0.001)

    def test_cache_hit_handles_put_full_word(self, tmp_path):
        """'PUT' should be normalized to 'P' for cache lookup."""
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        provider.cache.put(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5),
            {"iv": 0.30, "delta": -0.20},
        )
        result = provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "PUT", date(2024, 1, 5)
        )
        assert result is not None
        assert result.iv == 0.30

    def test_has_valid_session_false_when_no_file(self, tmp_path):
        provider = PlaywrightBarchartProvider(
            storage_state_path=tmp_path / "nonexistent.json"
        )
        assert provider.has_valid_session() is False

    def test_has_valid_session_true_when_file_exists(self, tmp_path):
        state_path = tmp_path / "state.json"
        state_path.write_text("{}")
        provider = PlaywrightBarchartProvider(storage_state_path=state_path)
        assert provider.has_valid_session() is True

    def test_stub_methods_return_none(self, tmp_path):
        """Non-option methods should return None."""
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        assert provider.get_stock_bar("AAPL", date(2024, 1, 5)) is None
        assert provider.get_vix_close(date(2024, 1, 5)) is None
        assert provider.get_index_bar("SPY", date(2024, 1, 5)) is None
        assert provider.get_historical_bars("AAPL", date(2024, 1, 5)) is None
        assert provider.get_sector_etf_bars("XLK", date(2024, 1, 5)) is None

    def test_close_when_not_initialized(self, tmp_path):
        """close() should be safe to call even if browser was never started."""
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        provider.close()  # Should not raise

    def test_dict_to_snapshot_empty(self, tmp_path):
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        assert provider._dict_to_snapshot({}) is None
        assert provider._dict_to_snapshot(None) is None

    def test_dict_to_snapshot_full(self, tmp_path):
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        data = {
            "bid": 2.00,
            "ask": 2.30,
            "volume": 500,
            "open_interest": 3000,
            "iv": 0.35,
            "delta": -0.30,
            "gamma": 0.02,
            "theta": -0.06,
            "vega": 0.15,
            "rho": -0.04,
        }
        snap = provider._dict_to_snapshot(data)
        assert snap is not None
        assert snap.bid == 2.00
        assert snap.ask == 2.30
        assert snap.mid == pytest.approx(2.15, abs=0.001)
        assert snap.spread_pct == pytest.approx(0.1395, abs=0.001)
        assert snap.volume == 500
        assert snap.open_interest == 3000
        assert snap.iv == 0.35
        assert snap.delta == -0.30
        assert snap.source == "barchart_playwright"

    def test_dict_to_snapshot_no_bid_ask(self, tmp_path):
        """Mid and spread should be None if bid/ask not available."""
        provider = PlaywrightBarchartProvider(cache_path=tmp_path / "c.db")
        data = {"iv": 0.28, "delta": -0.20}
        snap = provider._dict_to_snapshot(data)
        assert snap is not None
        assert snap.mid is None
        assert snap.spread_pct is None
        assert snap.iv == 0.28

    @patch("playwright.sync_api.sync_playwright")
    def test_ensure_browser_loads_storage_state(self, mock_pw_factory, tmp_path):
        """Browser should load storage state when file exists."""
        # Create a fake storage state file
        state_path = tmp_path / "state.json"
        state_path.write_text('{"cookies": []}')

        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_pw_factory.return_value.start.return_value = mock_pw

        provider = PlaywrightBarchartProvider(
            cache_path=tmp_path / "c.db",
            storage_state_path=state_path,
        )

        result = provider._ensure_browser()

        assert result is True
        mock_browser.new_context.assert_called_once_with(
            storage_state=str(state_path)
        )

    @patch("playwright.sync_api.sync_playwright")
    def test_ensure_browser_no_storage_state(self, mock_pw_factory, tmp_path):
        """Browser should create plain context if no storage state file."""
        mock_page = MagicMock()
        mock_context = MagicMock()
        mock_context.new_page.return_value = mock_page
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_pw = MagicMock()
        mock_pw.chromium.launch.return_value = mock_browser
        mock_pw_factory.return_value.start.return_value = mock_pw

        provider = PlaywrightBarchartProvider(
            cache_path=tmp_path / "c.db",
            storage_state_path=tmp_path / "nonexistent.json",
        )

        result = provider._ensure_browser()

        assert result is True
        mock_browser.new_context.assert_called_once_with()

    def test_shared_cache_with_api_provider(self, tmp_path):
        """Playwright and API providers should share the same cache."""
        from src.taad.enrichment.barchart_scraper import BarchartScraperProvider

        cache_db = tmp_path / "shared.db"

        # API provider writes to cache
        api_provider = BarchartScraperProvider(
            api_key="", cache_path=cache_db
        )
        api_provider.cache.put(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5),
            {"iv": 0.32, "delta": -0.25},
        )

        # Playwright provider reads from same cache
        pw_provider = PlaywrightBarchartProvider(cache_path=cache_db)
        result = pw_provider.get_option_snapshot(
            "AAPL", 185.0, date(2024, 1, 19), "P", date(2024, 1, 5)
        )
        assert result is not None
        assert result.iv == 0.32
