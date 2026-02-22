"""Technical indicator calculations from historical bar data.

Reuses the pure numpy math from src/analysis/technical_indicators.py but accepts
numpy arrays / DataFrames instead of IBKR bar objects. This allows enrichment
of historical trades without requiring a live TWS connection.
"""

from dataclasses import dataclass, fields
from typing import Optional, Tuple

import numpy as np
import pandas as pd
from loguru import logger


@dataclass
class TechnicalIndicators:
    """Complete set of technical indicators (mirrors src/analysis/technical_indicators.py)."""

    # RSI
    rsi_14: Optional[float] = None
    rsi_7: Optional[float] = None
    # MACD
    macd: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_histogram: Optional[float] = None
    # ADX
    adx: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    # ATR
    atr_14: Optional[float] = None
    atr_pct: Optional[float] = None
    # Bollinger
    bb_upper: Optional[float] = None
    bb_lower: Optional[float] = None
    bb_position: Optional[float] = None
    # Support/Resistance
    support_1: Optional[float] = None
    support_2: Optional[float] = None
    resistance_1: Optional[float] = None
    resistance_2: Optional[float] = None
    distance_to_support_pct: Optional[float] = None


def calculate_indicators_from_bars(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    current_price: float,
) -> TechnicalIndicators:
    """Calculate complete indicator set from OHLCV arrays.

    Args:
        closes: Array of closing prices (oldest first)
        highs: Array of high prices
        lows: Array of low prices
        current_price: Current/entry stock price

    Returns:
        TechnicalIndicators with all calculated values
    """
    indicators = TechnicalIndicators()

    if len(closes) < 50:
        logger.debug(f"Insufficient data for indicators: {len(closes)} bars (need 50+)")
        return indicators

    try:
        indicators.rsi_14 = _calculate_rsi(closes, 14)
        indicators.rsi_7 = _calculate_rsi(closes, 7)
    except Exception as e:
        logger.debug(f"RSI calculation failed: {e}")

    try:
        macd_val, signal_val, hist_val = _calculate_macd(closes)
        indicators.macd = macd_val
        indicators.macd_signal = signal_val
        indicators.macd_histogram = hist_val
    except Exception as e:
        logger.debug(f"MACD calculation failed: {e}")

    try:
        adx_val, plus_di, minus_di = _calculate_adx(highs, lows, closes, 14)
        indicators.adx = adx_val
        indicators.plus_di = plus_di
        indicators.minus_di = minus_di
    except Exception as e:
        logger.debug(f"ADX calculation failed: {e}")

    try:
        indicators.atr_14 = _calculate_atr(highs, lows, closes, 14)
        if indicators.atr_14 and current_price > 0:
            indicators.atr_pct = round(indicators.atr_14 / current_price, 6)
    except Exception as e:
        logger.debug(f"ATR calculation failed: {e}")

    try:
        upper, lower, position = _calculate_bollinger(closes, 20, 2.0, current_price)
        indicators.bb_upper = upper
        indicators.bb_lower = lower
        indicators.bb_position = position
    except Exception as e:
        logger.debug(f"Bollinger calculation failed: {e}")

    try:
        s1, s2, r1, r2 = _calculate_support_resistance(highs, lows, closes)
        indicators.support_1 = s1
        indicators.support_2 = s2
        indicators.resistance_1 = r1
        indicators.resistance_2 = r2
        if s1 and current_price > 0:
            indicators.distance_to_support_pct = round((current_price - s1) / current_price, 6)
    except Exception as e:
        logger.debug(f"Support/Resistance calculation failed: {e}")

    return indicators


def calculate_trend_from_bars(
    bars: pd.DataFrame, current_price: float
) -> dict:
    """Calculate trend metrics from historical bars.

    Args:
        bars: DataFrame with 'Close' column (at least 50 rows)
        current_price: Current/entry stock price

    Returns:
        Dict with sma_20, sma_50, trend_direction, trend_strength,
        price_vs_sma20_pct, price_vs_sma50_pct
    """
    result = {
        "sma_20": None,
        "sma_50": None,
        "trend_direction": None,
        "trend_strength": None,
        "price_vs_sma20_pct": None,
        "price_vs_sma50_pct": None,
    }

    if bars is None or len(bars) < 50:
        return result

    closes = bars["Close"].values

    # SMAs
    sma_20 = float(np.mean(closes[-20:]))
    sma_50 = float(np.mean(closes[-50:]))

    result["sma_20"] = round(sma_20, 2)
    result["sma_50"] = round(sma_50, 2)

    # Price vs SMA percentages
    if sma_20 > 0:
        result["price_vs_sma20_pct"] = round((current_price - sma_20) / sma_20, 6)
    if sma_50 > 0:
        result["price_vs_sma50_pct"] = round((current_price - sma_50) / sma_50, 6)

    # Trend direction and strength
    if sma_20 > 0 and sma_50 > 0:
        if current_price > sma_20 > sma_50:
            result["trend_direction"] = "strong_uptrend"
            result["trend_strength"] = min(1.0, round((current_price / sma_50 - 1) * 5, 2))
        elif current_price > sma_20:
            result["trend_direction"] = "uptrend"
            result["trend_strength"] = min(1.0, round((current_price / sma_20 - 1) * 10, 2))
        elif current_price < sma_20 < sma_50:
            result["trend_direction"] = "strong_downtrend"
            result["trend_strength"] = min(1.0, round((1 - current_price / sma_50) * 5, 2))
        elif current_price < sma_20:
            result["trend_direction"] = "downtrend"
            result["trend_strength"] = min(1.0, round((1 - current_price / sma_20) * 10, 2))
        else:
            result["trend_direction"] = "sideways"
            result["trend_strength"] = 0.0

    return result


def calculate_hv_20(closes: np.ndarray) -> Optional[float]:
    """Calculate 20-day historical volatility (annualized).

    Args:
        closes: Array of closing prices (at least 21 values)

    Returns:
        Annualized historical volatility or None
    """
    if len(closes) < 21:
        return None

    # Use last 21 closes to get 20 log returns
    log_returns = np.log(closes[-21:][1:] / closes[-21:][:-1])
    hv = float(np.std(log_returns) * np.sqrt(252))
    return round(hv, 4)


def calculate_hv_rank(closes: np.ndarray, window: int = 252) -> Optional[float]:
    """Calculate HV rank — where current HV20 sits in trailing year range.

    Args:
        closes: Array of closing prices (ideally 252+ values)
        window: Lookback window for percentile calculation

    Returns:
        Percentile 0-100 or None if insufficient data
    """
    if len(closes) < max(window, 42):
        return None

    # Calculate rolling 20-day HV over the window
    hvs = []
    for i in range(21, min(len(closes), window) + 1):
        subset = closes[max(0, i - 21):i]
        if len(subset) >= 21:
            log_ret = np.log(subset[1:] / subset[:-1])
            hvs.append(float(np.std(log_ret) * np.sqrt(252)))

    if len(hvs) < 10:
        return None

    current_hv = hvs[-1]
    rank = sum(1 for h in hvs if h <= current_hv) / len(hvs) * 100
    return round(rank, 1)


def calculate_beta(
    stock_returns: np.ndarray, market_returns: np.ndarray, window: int = 60
) -> Optional[float]:
    """Calculate beta of stock relative to market (SPY).

    Args:
        stock_returns: Array of daily stock returns
        market_returns: Array of daily market (SPY) returns
        window: Rolling window for beta calculation

    Returns:
        Beta value or None if insufficient data
    """
    if len(stock_returns) < window or len(market_returns) < window:
        return None

    stock_r = stock_returns[-window:]
    market_r = market_returns[-window:]

    # Beta = Cov(stock, market) / Var(market)
    cov = np.cov(stock_r, market_r)
    if cov.shape == (2, 2) and cov[1, 1] != 0:
        beta = cov[0, 1] / cov[1, 1]
        return round(float(beta), 3)

    return None


# ---------------------------------------------------------------
# Pure math functions (copied from TechnicalIndicatorCalculator)
# ---------------------------------------------------------------

def _calculate_rsi(closes: np.ndarray, period: int) -> Optional[float]:
    """Calculate RSI. See TechnicalIndicatorCalculator._calculate_rsi."""
    if len(closes) < period + 1:
        return None
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _calculate_macd(
    closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate MACD. See TechnicalIndicatorCalculator._calculate_macd."""
    if len(closes) < slow + signal:
        return None, None, None
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    macd_line = ema_fast - ema_slow
    signal_line = _ema(macd_line[slow - 1:], signal)
    macd_val = macd_line[-1]
    signal_val = signal_line[-1] if len(signal_line) > 0 else None
    histogram = macd_val - signal_val if signal_val is not None else None
    return (
        round(macd_val, 4) if macd_val is not None else None,
        round(signal_val, 4) if signal_val is not None else None,
        round(histogram, 4) if histogram is not None else None,
    )


def _calculate_adx(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate ADX. See TechnicalIndicatorCalculator._calculate_adx."""
    if len(closes) < period * 2:
        return None, None, None
    try:
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1]),
            ),
        )
        plus_dm = np.where(
            (highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
            np.maximum(highs[1:] - highs[:-1], 0),
            0,
        )
        minus_dm = np.where(
            (lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
            np.maximum(lows[:-1] - lows[1:], 0),
            0,
        )
        atr = _wilder_smooth(tr, period)
        smooth_plus_dm = _wilder_smooth(plus_dm, period)
        smooth_minus_dm = _wilder_smooth(minus_dm, period)
        atr_safe = np.where(atr == 0, 1e-10, atr)
        plus_di = 100 * smooth_plus_dm / atr_safe
        minus_di = 100 * smooth_minus_dm / atr_safe
        di_sum = plus_di + minus_di
        di_sum_safe = np.where(di_sum == 0, 1e-10, di_sum)
        dx = 100 * np.abs(plus_di - minus_di) / di_sum_safe
        adx = _wilder_smooth(dx, period)
        return round(adx[-1], 2), round(plus_di[-1], 2), round(minus_di[-1], 2)
    except Exception as e:
        logger.debug(f"ADX calculation error: {e}")
        return None, None, None


def _calculate_atr(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14
) -> Optional[float]:
    """Calculate ATR. See TechnicalIndicatorCalculator._calculate_atr."""
    if len(closes) < period + 1:
        return None
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    return round(float(np.mean(tr[-period:])), 4)


def _calculate_bollinger(
    closes: np.ndarray, period: int = 20, std_dev: float = 2.0, current_price: float = None
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Calculate Bollinger Bands. See TechnicalIndicatorCalculator._calculate_bollinger."""
    if len(closes) < period:
        return None, None, None
    sma = np.mean(closes[-period:])
    std = np.std(closes[-period:])
    upper = sma + (std_dev * std)
    lower = sma - (std_dev * std)
    price = current_price if current_price else closes[-1]
    position = (price - lower) / (upper - lower) if upper != lower else 0.5
    position = max(0.0, min(1.0, position))
    return round(upper, 2), round(lower, 2), round(position, 4)


def _calculate_support_resistance(
    highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, lookback: int = 20
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Calculate S/R levels. See TechnicalIndicatorCalculator._calculate_support_resistance."""
    if len(closes) < lookback:
        return None, None, None, None
    high = np.max(highs[-lookback:])
    low = np.min(lows[-lookback:])
    close = closes[-1]
    pivot = (high + low + close) / 3
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high
    r2 = pivot + (high - low)
    s2 = pivot - (high - low)
    return round(s1, 2), round(s2, 2), round(r1, 2), round(r2, 2)


def _ema(data: np.ndarray, period: int) -> np.ndarray:
    """Calculate EMA. See TechnicalIndicatorCalculator._ema."""
    multiplier = 2 / (period + 1)
    ema = np.zeros_like(data, dtype=float)
    ema[period - 1] = np.mean(data[:period])
    for i in range(period, len(data)):
        ema[i] = (data[i] * multiplier) + (ema[i - 1] * (1 - multiplier))
    return ema


def _wilder_smooth(data: np.ndarray, period: int) -> np.ndarray:
    """Wilder's smoothing. See TechnicalIndicatorCalculator._wilder_smooth."""
    smoothed = np.zeros_like(data, dtype=float)
    smoothed[period - 1] = np.sum(data[:period])
    for i in range(period, len(data)):
        smoothed[i] = smoothed[i - 1] - (smoothed[i - 1] / period) + data[i]
    return smoothed / period
