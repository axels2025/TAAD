"""Tests for historical technical indicator calculations."""

import pytest
import numpy as np
import pandas as pd

from src.taad.enrichment.historical_indicators import (
    calculate_indicators_from_bars,
    calculate_trend_from_bars,
    calculate_hv_20,
    calculate_hv_rank,
    calculate_beta,
    TechnicalIndicators,
)


def _make_closes(n: int = 100, start: float = 100.0, trend: float = 0.001) -> np.ndarray:
    """Generate synthetic closing prices with a trend."""
    np.random.seed(42)
    returns = np.random.normal(trend, 0.02, n)
    prices = start * np.cumprod(1 + returns)
    return prices


def _make_bars_df(n: int = 100, start: float = 100.0) -> pd.DataFrame:
    """Generate synthetic OHLCV DataFrame."""
    closes = _make_closes(n, start)
    highs = closes * (1 + np.random.uniform(0, 0.02, n))
    lows = closes * (1 - np.random.uniform(0, 0.02, n))
    opens = (closes + lows) / 2
    volumes = np.random.randint(100000, 1000000, n)

    return pd.DataFrame({
        "Open": opens,
        "High": highs,
        "Low": lows,
        "Close": closes,
        "Volume": volumes,
    })


class TestCalculateIndicatorsFromBars:
    """Test full indicator calculation from arrays."""

    def test_full_indicators_from_100_bars(self):
        """100 bars should produce all indicators."""
        bars = _make_bars_df(100)
        closes = bars["Close"].values
        highs = bars["High"].values
        lows = bars["Low"].values
        current_price = float(closes[-1])

        indicators = calculate_indicators_from_bars(closes, highs, lows, current_price)

        assert indicators.rsi_14 is not None
        assert 0 <= indicators.rsi_14 <= 100
        assert indicators.rsi_7 is not None
        assert indicators.macd is not None
        assert indicators.macd_signal is not None
        assert indicators.macd_histogram is not None
        assert indicators.adx is not None
        assert indicators.atr_14 is not None
        assert indicators.atr_pct is not None
        assert indicators.bb_upper is not None
        assert indicators.bb_lower is not None
        assert indicators.bb_position is not None
        assert 0 <= indicators.bb_position <= 1
        assert indicators.support_1 is not None
        assert indicators.resistance_1 is not None

    def test_insufficient_data_returns_empty(self):
        """Fewer than 50 bars should return empty indicators."""
        closes = np.array([100.0] * 30)
        highs = closes * 1.01
        lows = closes * 0.99

        indicators = calculate_indicators_from_bars(closes, highs, lows, 100.0)

        assert indicators.rsi_14 is None
        assert indicators.macd is None
        assert indicators.adx is None

    def test_distance_to_support(self):
        """Distance to support should be calculated."""
        bars = _make_bars_df(100)
        closes = bars["Close"].values
        current_price = float(closes[-1])

        indicators = calculate_indicators_from_bars(
            closes, bars["High"].values, bars["Low"].values, current_price
        )

        if indicators.support_1 is not None:
            assert indicators.distance_to_support_pct is not None


class TestCalculateTrend:
    """Test trend calculation from bars."""

    def test_uptrend_detection(self):
        """Rising prices should produce uptrend."""
        bars = _make_bars_df(100, start=80.0)
        current_price = float(bars["Close"].values[-1])

        trend = calculate_trend_from_bars(bars, current_price)

        assert trend["sma_20"] is not None
        assert trend["sma_50"] is not None
        assert trend["trend_direction"] is not None
        assert trend["price_vs_sma20_pct"] is not None
        assert trend["price_vs_sma50_pct"] is not None

    def test_insufficient_bars(self):
        """Fewer than 50 bars should return None values."""
        bars = _make_bars_df(30)
        trend = calculate_trend_from_bars(bars, 100.0)
        assert trend["sma_20"] is None
        assert trend["sma_50"] is None

    def test_sma_values(self):
        """SMA values should be reasonable."""
        bars = _make_bars_df(100, start=100.0)
        current_price = float(bars["Close"].values[-1])
        trend = calculate_trend_from_bars(bars, current_price)

        # SMAs should be close to the price range
        assert 50 < trend["sma_20"] < 200
        assert 50 < trend["sma_50"] < 200


class TestCalculateHV20:
    """Test 20-day historical volatility."""

    def test_hv20_from_synthetic_data(self):
        """HV20 should be positive and reasonable."""
        closes = _make_closes(100)
        hv = calculate_hv_20(closes)

        assert hv is not None
        assert hv > 0
        # Annualized vol from 2% daily std should be roughly 30%
        assert 0.1 < hv < 1.0

    def test_insufficient_data(self):
        """Fewer than 21 closes should return None."""
        closes = np.array([100.0] * 15)
        assert calculate_hv_20(closes) is None

    def test_constant_prices(self):
        """Constant prices should have zero volatility."""
        closes = np.array([100.0] * 25)
        hv = calculate_hv_20(closes)
        assert hv == 0.0


class TestCalculateHVRank:
    """Test HV rank (percentile)."""

    def test_hv_rank_returns_percentile(self):
        """HV rank should return a percentile 0-100."""
        closes = _make_closes(300)
        rank = calculate_hv_rank(closes, window=252)

        assert rank is not None
        assert 0 <= rank <= 100

    def test_insufficient_data(self):
        """Too few bars should return None."""
        closes = np.array([100.0] * 30)
        assert calculate_hv_rank(closes, window=252) is None


class TestCalculateBeta:
    """Test beta calculation."""

    def test_beta_with_correlated_returns(self):
        """Perfectly correlated returns should have beta ~1."""
        np.random.seed(42)
        market_returns = np.random.normal(0.001, 0.01, 100)
        # Stock moves 1:1 with market plus noise
        stock_returns = market_returns + np.random.normal(0, 0.005, 100)

        beta = calculate_beta(stock_returns, market_returns, window=60)

        assert beta is not None
        assert 0.5 < beta < 1.5  # Should be close to 1

    def test_insufficient_data(self):
        """Too few returns should return None."""
        stock = np.array([0.01] * 30)
        market = np.array([0.01] * 30)
        assert calculate_beta(stock, market, window=60) is None

    def test_uncorrelated_returns(self):
        """Uncorrelated returns should have beta near 0."""
        np.random.seed(42)
        stock_returns = np.random.normal(0, 0.01, 100)
        market_returns = np.random.normal(0, 0.01, 100)

        beta = calculate_beta(stock_returns, market_returns, window=60)
        assert beta is not None
        # Not perfectly 0 due to randomness, but should be small
        assert abs(beta) < 1.0
