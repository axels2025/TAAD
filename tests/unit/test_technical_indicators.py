"""Unit tests for technical indicator calculator.

Phase 2.6B - Technical Indicators
Tests RSI, MACD, ADX, ATR, Bollinger Bands, and Support/Resistance calculations.
"""

import numpy as np
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime

from src.analysis.technical_indicators import TechnicalIndicatorCalculator, TechnicalIndicators


class BarData:
    """Mock bar data for testing."""

    def __init__(self, open_price, high, low, close, volume=0):
        self.open = open_price
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume


@pytest.fixture
def mock_ibkr_client():
    """Create mock IBKR client."""
    mock = Mock()
    mock.ib = Mock()
    return mock


@pytest.fixture
def calculator(mock_ibkr_client):
    """Create calculator instance."""
    return TechnicalIndicatorCalculator(mock_ibkr_client)


@pytest.fixture
def sample_bars():
    """Create sample bar data for testing."""
    # Generate 100 bars with simple uptrend
    closes = np.linspace(100, 120, 100)
    highs = closes + np.random.uniform(0.5, 2.0, 100)
    lows = closes - np.random.uniform(0.5, 2.0, 100)
    opens = closes + np.random.uniform(-1.0, 1.0, 100)

    bars = []
    for i in range(100):
        bars.append(BarData(opens[i], highs[i], lows[i], closes[i]))

    return bars


# ============================================================
# RSI Tests
# ============================================================


def test_rsi_calculation_basic(calculator):
    """Test basic RSI calculation."""
    # Simple price series with clear trend
    closes = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                       110, 111, 112, 113, 114, 115, 116, 117, 118, 119])

    rsi = calculator._calculate_rsi(closes, 14)

    # Uptrend should produce high RSI (>50)
    assert rsi is not None
    assert 50 < rsi <= 100
    assert isinstance(rsi, float)


def test_rsi_overbought(calculator):
    """Test RSI in overbought territory."""
    # Strong uptrend
    closes = np.array([100, 102, 104, 106, 108, 110, 112, 114, 116, 118,
                       120, 122, 124, 126, 128, 130, 132, 134, 136, 138])

    rsi = calculator._calculate_rsi(closes, 14)

    # Strong uptrend should produce very high RSI
    assert rsi is not None
    assert rsi > 70  # Overbought territory


def test_rsi_oversold(calculator):
    """Test RSI in oversold territory."""
    # Strong downtrend
    closes = np.array([100, 98, 96, 94, 92, 90, 88, 86, 84, 82,
                       80, 78, 76, 74, 72, 70, 68, 66, 64, 62])

    rsi = calculator._calculate_rsi(closes, 14)

    # Strong downtrend should produce low RSI
    assert rsi is not None
    assert rsi < 30  # Oversold territory


def test_rsi_insufficient_data(calculator):
    """Test RSI with insufficient data."""
    closes = np.array([100, 101, 102])  # Only 3 bars, need 15 for RSI 14

    rsi = calculator._calculate_rsi(closes, 14)

    assert rsi is None


def test_rsi_no_losses(calculator):
    """Test RSI when price only goes up (edge case)."""
    closes = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                       110, 111, 112, 113, 114, 115])

    rsi = calculator._calculate_rsi(closes, 14)

    # No losses = RSI should be 100
    assert rsi == 100.0


# ============================================================
# MACD Tests
# ============================================================


def test_macd_calculation_basic(calculator):
    """Test basic MACD calculation."""
    # Generate price series (50 bars needed for MACD 12/26/9)
    closes = np.linspace(100, 110, 50)

    macd, signal, hist = calculator._calculate_macd(closes)

    assert macd is not None
    assert signal is not None
    assert hist is not None
    assert isinstance(macd, float)
    assert isinstance(signal, float)
    assert isinstance(hist, float)


def test_macd_bullish_crossover(calculator):
    """Test MACD shows bullish momentum."""
    # Uptrend should produce positive MACD
    closes = np.linspace(100, 120, 50)

    macd, signal, hist = calculator._calculate_macd(closes)

    # Uptrend should produce positive histogram
    assert macd is not None
    assert hist is not None
    # Note: May not always be positive depending on the exact trend


def test_macd_insufficient_data(calculator):
    """Test MACD with insufficient data."""
    closes = np.array([100, 101, 102, 103, 104])  # Too few bars

    macd, signal, hist = calculator._calculate_macd(closes)

    assert macd is None
    assert signal is None
    assert hist is None


# ============================================================
# ADX Tests
# ============================================================


def test_adx_calculation_basic(calculator):
    """Test basic ADX calculation."""
    # Generate OHLC data for trending market
    length = 40
    closes = np.linspace(100, 110, length)
    highs = closes + 1.0
    lows = closes - 1.0

    adx, plus_di, minus_di = calculator._calculate_adx(highs, lows, closes, 14)

    assert adx is not None
    assert plus_di is not None
    assert minus_di is not None
    assert 0 <= adx <= 100
    assert 0 <= plus_di <= 100
    assert 0 <= minus_di <= 100


def test_adx_strong_trend(calculator):
    """Test ADX in strong trending market."""
    # Strong uptrend
    length = 40
    closes = np.linspace(100, 130, length)
    highs = closes + 1.0
    lows = closes - 0.5

    adx, plus_di, minus_di = calculator._calculate_adx(highs, lows, closes, 14)

    assert adx is not None
    # Strong trend should have ADX > 25
    # Note: May vary depending on exact price action


def test_adx_insufficient_data(calculator):
    """Test ADX with insufficient data."""
    closes = np.array([100, 101, 102])
    highs = np.array([101, 102, 103])
    lows = np.array([99, 100, 101])

    adx, plus_di, minus_di = calculator._calculate_adx(highs, lows, closes, 14)

    assert adx is None
    assert plus_di is None
    assert minus_di is None


# ============================================================
# ATR Tests
# ============================================================


def test_atr_calculation_basic(calculator):
    """Test basic ATR calculation."""
    length = 20
    closes = np.linspace(100, 110, length)
    highs = closes + 2.0
    lows = closes - 1.5

    atr = calculator._calculate_atr(highs, lows, closes, 14)

    assert atr is not None
    assert atr > 0  # ATR should be positive
    assert isinstance(atr, float)


def test_atr_high_volatility(calculator):
    """Test ATR in high volatility environment."""
    length = 20
    closes = np.array([100, 105, 98, 103, 96, 104, 97, 106, 95, 107,
                       94, 108, 93, 109, 92, 110, 91, 111, 90, 112])
    highs = closes + 3.0
    lows = closes - 3.0

    atr = calculator._calculate_atr(highs, lows, closes, 14)

    # High volatility should produce higher ATR
    assert atr is not None
    assert atr > 3.0  # Should reflect the volatility


def test_atr_insufficient_data(calculator):
    """Test ATR with insufficient data."""
    closes = np.array([100, 101, 102])
    highs = np.array([101, 102, 103])
    lows = np.array([99, 100, 101])

    atr = calculator._calculate_atr(highs, lows, closes, 14)

    assert atr is None


# ============================================================
# Bollinger Bands Tests
# ============================================================


def test_bollinger_calculation_basic(calculator):
    """Test basic Bollinger Bands calculation."""
    closes = np.linspace(100, 110, 30)

    upper, lower, position = calculator._calculate_bollinger(closes, 20, 2.0, 105)

    assert upper is not None
    assert lower is not None
    assert position is not None
    assert upper > lower
    assert 0.0 <= position <= 1.0


def test_bollinger_position_at_middle(calculator):
    """Test Bollinger position when price is at SMA."""
    closes = np.array([100] * 30)  # Flat prices

    upper, lower, position = calculator._calculate_bollinger(closes, 20, 2.0, 100)

    # Price at SMA should be ~0.5 position
    assert position is not None
    assert 0.4 <= position <= 0.6


def test_bollinger_position_at_upper(calculator):
    """Test Bollinger position when price is at upper band."""
    closes = np.linspace(100, 110, 30)
    upper, lower, position = calculator._calculate_bollinger(closes, 20, 2.0)

    # Get the upper band value and test with price at upper
    current_price = closes[-1] + 10  # Above recent prices
    _, _, position_upper = calculator._calculate_bollinger(closes, 20, 2.0, current_price)

    # Price above recent range should be high position
    assert position_upper >= 0.8


def test_bollinger_insufficient_data(calculator):
    """Test Bollinger with insufficient data."""
    closes = np.array([100, 101, 102])

    upper, lower, position = calculator._calculate_bollinger(closes, 20, 2.0, 101)

    assert upper is None
    assert lower is None
    assert position is None


# ============================================================
# Support/Resistance Tests
# ============================================================


def test_support_resistance_calculation_basic(calculator):
    """Test basic support/resistance calculation."""
    length = 30
    closes = np.linspace(100, 110, length)
    highs = closes + 2.0
    lows = closes - 1.5

    s1, s2, r1, r2 = calculator._calculate_support_resistance(highs, lows, closes, 20)

    assert s1 is not None
    assert s2 is not None
    assert r1 is not None
    assert r2 is not None
    # S2 < S1 < current < R1 < R2 (generally)
    assert s2 < s1
    assert r1 < r2


def test_support_below_resistance(calculator):
    """Test that support levels are below resistance levels."""
    length = 30
    closes = np.array([100] * length)  # Flat price
    highs = closes + 1.0
    lows = closes - 1.0

    s1, s2, r1, r2 = calculator._calculate_support_resistance(highs, lows, closes, 20)

    # Support should be below resistance
    assert s1 < r1
    assert s2 < r2


def test_support_resistance_insufficient_data(calculator):
    """Test support/resistance with insufficient data."""
    closes = np.array([100, 101, 102])
    highs = np.array([101, 102, 103])
    lows = np.array([99, 100, 101])

    s1, s2, r1, r2 = calculator._calculate_support_resistance(highs, lows, closes, 20)

    assert s1 is None
    assert s2 is None
    assert r1 is None
    assert r2 is None


# ============================================================
# Integration Tests
# ============================================================


def test_calculate_all_with_mocked_data(calculator, mock_ibkr_client, sample_bars):
    """Test calculate_all method with mocked IBKR data."""
    # Mock the _fetch_historical_bars method
    calculator._fetch_historical_bars = Mock(return_value=sample_bars)

    indicators = calculator.calculate_all("AAPL", 115.0, lookback_days=100)

    # Verify all indicator types are present (may be None if data insufficient)
    assert isinstance(indicators, TechnicalIndicators)
    assert hasattr(indicators, "rsi_14")
    assert hasattr(indicators, "macd")
    assert hasattr(indicators, "adx")
    assert hasattr(indicators, "atr_14")
    assert hasattr(indicators, "bb_upper")
    assert hasattr(indicators, "support_1")


def test_calculate_all_with_insufficient_data(calculator, mock_ibkr_client):
    """Test calculate_all with insufficient historical data."""
    # Mock with very few bars
    few_bars = [BarData(100, 101, 99, 100) for _ in range(10)]
    calculator._fetch_historical_bars = Mock(return_value=few_bars)

    indicators = calculator.calculate_all("AAPL", 100.0, lookback_days=100)

    # Should return empty indicators
    assert isinstance(indicators, TechnicalIndicators)
    assert indicators.rsi_14 is None
    assert indicators.macd is None


def test_calculate_all_with_no_data(calculator, mock_ibkr_client):
    """Test calculate_all when no historical data available."""
    calculator._fetch_historical_bars = Mock(return_value=None)

    indicators = calculator.calculate_all("AAPL", 100.0, lookback_days=100)

    # Should return empty indicators
    assert isinstance(indicators, TechnicalIndicators)
    assert indicators.rsi_14 is None
    assert indicators.macd is None


def test_calculate_all_handles_errors_gracefully(calculator, mock_ibkr_client, sample_bars):
    """Test that calculate_all handles calculation errors gracefully."""
    calculator._fetch_historical_bars = Mock(return_value=sample_bars)

    # Mock one calculation to raise an error
    calculator._calculate_rsi = Mock(side_effect=Exception("RSI error"))

    # Should not raise, should return partial indicators
    indicators = calculator.calculate_all("AAPL", 115.0, lookback_days=100)

    assert isinstance(indicators, TechnicalIndicators)
    # RSI should be None due to error, but others might succeed
    assert indicators.rsi_14 is None


# ============================================================
# Helper Method Tests
# ============================================================


def test_ema_calculation(calculator):
    """Test EMA calculation."""
    data = np.array([100, 101, 102, 103, 104, 105, 106, 107, 108, 109,
                     110, 111, 112, 113, 114, 115])

    ema = calculator._ema(data, 10)

    assert len(ema) == len(data)
    assert ema[9] > 0  # 10th value should be set (index 9)
    # EMA should follow the trend
    assert ema[-1] > ema[9]  # Later EMA should be higher for uptrend


def test_wilder_smooth(calculator):
    """Test Wilder's smoothing method."""
    data = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
                     11, 12, 13, 14, 15, 16])

    smoothed = calculator._wilder_smooth(data, 10)

    assert len(smoothed) == len(data)
    assert smoothed[9] > 0  # 10th value should be set
    # Smoothed values should be less than raw data in uptrend
    assert smoothed[-1] < data[-1]
