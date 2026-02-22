"""Tests for historical data providers."""

import pytest
from datetime import date, timedelta
from unittest.mock import MagicMock, patch, PropertyMock
import pandas as pd
import numpy as np

from src.taad.enrichment.providers import (
    OHLCV,
    OptionSnapshot,
    YFinanceProvider,
    IBKRHistoricalProvider,
    FallbackChainProvider,
)


class TestOHLCV:
    """Test OHLCV dataclass."""

    def test_creation(self):
        bar = OHLCV(date=date(2025, 1, 15), open=100, high=105, low=98, close=103, volume=1000)
        assert bar.close == 103
        assert bar.date == date(2025, 1, 15)


class TestYFinanceProviderCaching:
    """Test YFinance provider caching logic."""

    @patch("src.taad.enrichment.providers.YFinanceProvider._download")
    def test_get_stock_bar_calls_download(self, mock_download):
        """First call should trigger a download."""
        df = pd.DataFrame({
            "Open": [100.0], "High": [105.0], "Low": [98.0],
            "Close": [103.0], "Volume": [1000000],
        }, index=pd.DatetimeIndex([date(2025, 1, 15)]))
        mock_download.return_value = df

        provider = YFinanceProvider(delay=0)
        bar = provider.get_stock_bar("AAPL", date(2025, 1, 15))

        assert bar is not None
        assert bar.close == 103.0
        assert bar.date == date(2025, 1, 15)

    @patch("src.taad.enrichment.providers.YFinanceProvider._download")
    def test_weekend_fallback(self, mock_download):
        """Saturday should fall back to Friday."""
        friday = date(2025, 1, 10)
        saturday = date(2025, 1, 11)

        df = pd.DataFrame({
            "Open": [100.0], "High": [105.0], "Low": [98.0],
            "Close": [103.0], "Volume": [1000000],
        }, index=pd.DatetimeIndex([friday]))
        mock_download.return_value = df

        provider = YFinanceProvider(delay=0)
        bar = provider.get_stock_bar("AAPL", saturday)

        assert bar is not None
        assert bar.date == friday

    @patch("src.taad.enrichment.providers.YFinanceProvider._download")
    def test_no_data_returns_none(self, mock_download):
        """Missing data should return None."""
        mock_download.return_value = None

        provider = YFinanceProvider(delay=0)
        bar = provider.get_stock_bar("XYZZY", date(2025, 1, 15))

        assert bar is None

    @patch("src.taad.enrichment.providers.YFinanceProvider._download")
    def test_vix_close(self, mock_download):
        """VIX close should use ^VIX ticker."""
        df = pd.DataFrame({
            "Open": [18.0], "High": [19.5], "Low": [17.5],
            "Close": [18.5], "Volume": [0],
        }, index=pd.DatetimeIndex([date(2025, 1, 15)]))
        mock_download.return_value = df

        provider = YFinanceProvider(delay=0)
        vix = provider.get_vix_close(date(2025, 1, 15))

        assert vix == 18.5


class TestIBKRHistoricalProvider:
    """Test IBKR provider graceful degradation."""

    def test_not_connected_returns_none(self):
        """No IBKR client should return None for all methods."""
        provider = IBKRHistoricalProvider(ibkr_client=None)

        assert provider.get_stock_bar("AAPL", date(2025, 1, 15)) is None
        assert provider.get_vix_close(date(2025, 1, 15)) is None
        assert provider.get_historical_bars("AAPL", date(2025, 1, 15)) is None

    def test_disconnected_client_returns_none(self):
        """Disconnected IBKR client should return None."""
        mock_client = MagicMock()
        mock_client.ib.isConnected.return_value = False

        provider = IBKRHistoricalProvider(ibkr_client=mock_client)
        assert provider.get_stock_bar("AAPL", date(2025, 1, 15)) is None


class TestFallbackChainProvider:
    """Test fallback chain logic."""

    def test_first_provider_succeeds(self):
        """Should use first provider's result."""
        bar = OHLCV(date=date(2025, 1, 15), open=100, high=105, low=98, close=103, volume=1000)

        provider1 = MagicMock()
        provider1.get_stock_bar.return_value = bar
        provider2 = MagicMock()

        chain = FallbackChainProvider([provider1, provider2])
        result = chain.get_stock_bar("AAPL", date(2025, 1, 15))

        assert result == bar
        provider2.get_stock_bar.assert_not_called()

    def test_fallback_to_second(self):
        """Should try second provider if first returns None."""
        bar = OHLCV(date=date(2025, 1, 15), open=100, high=105, low=98, close=103, volume=1000)

        provider1 = MagicMock()
        provider1.get_stock_bar.return_value = None
        provider2 = MagicMock()
        provider2.get_stock_bar.return_value = bar

        chain = FallbackChainProvider([provider1, provider2])
        result = chain.get_stock_bar("AAPL", date(2025, 1, 15))

        assert result == bar

    def test_all_fail_returns_none(self):
        """Should return None if all providers fail."""
        provider1 = MagicMock()
        provider1.get_stock_bar.return_value = None
        provider2 = MagicMock()
        provider2.get_stock_bar.return_value = None

        chain = FallbackChainProvider([provider1, provider2])
        result = chain.get_stock_bar("AAPL", date(2025, 1, 15))

        assert result is None

    def test_exception_falls_through(self):
        """Provider exception should fall through to next."""
        bar = OHLCV(date=date(2025, 1, 15), open=100, high=105, low=98, close=103, volume=1000)

        provider1 = MagicMock()
        provider1.get_stock_bar.side_effect = Exception("API error")
        provider2 = MagicMock()
        provider2.get_stock_bar.return_value = bar

        chain = FallbackChainProvider([provider1, provider2])
        result = chain.get_stock_bar("AAPL", date(2025, 1, 15))

        assert result == bar

    def test_vix_fallback(self):
        """VIX should also use fallback chain."""
        provider1 = MagicMock()
        provider1.get_vix_close.return_value = None
        provider2 = MagicMock()
        provider2.get_vix_close.return_value = 18.5

        chain = FallbackChainProvider([provider1, provider2])
        result = chain.get_vix_close(date(2025, 1, 15))

        assert result == 18.5
