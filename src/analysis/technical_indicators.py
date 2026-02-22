"""Technical indicator calculations for trade analysis.

Phase 2.6B - Technical Indicators
Calculates momentum and volatility indicators from OHLCV data for pattern detection.
"""

import numpy as np
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Tuple, List

from ib_insync import Stock
from loguru import logger


@dataclass
class TechnicalIndicators:
    """Complete set of technical indicators.

    All indicators are calculated from historical price data and represent
    conditions at the time of trade entry.
    """
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


class TechnicalIndicatorCalculator:
    """Calculates technical indicators from OHLCV data.

    Uses IBKR historical data to compute momentum, volatility, and
    support/resistance indicators for learning engine pattern detection.
    """

    def __init__(self, ibkr_client):
        """Initialize technical indicator calculator.

        Args:
            ibkr_client: IBKR client instance for fetching historical data
        """
        self.ibkr = ibkr_client

    def calculate_all(
        self,
        symbol: str,
        current_price: float,
        lookback_days: int = 100,
    ) -> TechnicalIndicators:
        """Calculate complete indicator set for a symbol.

        Args:
            symbol: Stock symbol
            current_price: Current stock price (for support/resistance context)
            lookback_days: Days of historical data to fetch

        Returns:
            TechnicalIndicators with all calculated values
        """
        indicators = TechnicalIndicators()

        try:
            # Fetch historical bars from IBKR
            bars = self._fetch_historical_bars(symbol, lookback_days)
            if not bars or len(bars) < 50:
                logger.debug(
                    f"Insufficient historical data for {symbol}: {len(bars) if bars else 0} bars"
                )
                return indicators  # Return empty if insufficient data

            closes = np.array([b.close for b in bars])
            highs = np.array([b.high for b in bars])
            lows = np.array([b.low for b in bars])

            # Calculate each indicator (best effort - continue on errors)
            try:
                indicators.rsi_14 = self._calculate_rsi(closes, 14)
                indicators.rsi_7 = self._calculate_rsi(closes, 7)
            except Exception as e:
                logger.debug(f"RSI calculation failed: {e}")

            try:
                macd, signal, hist = self._calculate_macd(closes)
                indicators.macd = macd
                indicators.macd_signal = signal
                indicators.macd_histogram = hist
            except Exception as e:
                logger.debug(f"MACD calculation failed: {e}")

            try:
                adx, plus_di, minus_di = self._calculate_adx(highs, lows, closes, 14)
                indicators.adx = adx
                indicators.plus_di = plus_di
                indicators.minus_di = minus_di
            except Exception as e:
                logger.debug(f"ADX calculation failed: {e}")

            try:
                indicators.atr_14 = self._calculate_atr(highs, lows, closes, 14)
                if indicators.atr_14 and current_price > 0:
                    indicators.atr_pct = indicators.atr_14 / current_price
            except Exception as e:
                logger.debug(f"ATR calculation failed: {e}")

            try:
                upper, lower, position = self._calculate_bollinger(closes, 20, 2.0, current_price)
                indicators.bb_upper = upper
                indicators.bb_lower = lower
                indicators.bb_position = position
            except Exception as e:
                logger.debug(f"Bollinger calculation failed: {e}")

            try:
                s1, s2, r1, r2 = self._calculate_support_resistance(highs, lows, closes)
                indicators.support_1 = s1
                indicators.support_2 = s2
                indicators.resistance_1 = r1
                indicators.resistance_2 = r2
                if s1 and current_price > 0:
                    indicators.distance_to_support_pct = (current_price - s1) / current_price
            except Exception as e:
                logger.debug(f"Support/Resistance calculation failed: {e}")

        except Exception as e:
            logger.warning(f"Failed to calculate technical indicators for {symbol}: {e}")

        return indicators

    def _fetch_historical_bars(self, symbol: str, days: int):
        """Fetch historical daily bars from IBKR.

        Args:
            symbol: Stock symbol
            days: Number of days of historical data

        Returns:
            List of BarData objects with OHLCV data
        """
        try:
            # Create stock contract
            stock = Stock(symbol, "SMART", "USD")
            qualified = self.ibkr.ib.qualifyContracts(stock)

            if not qualified:
                logger.debug(f"Could not qualify contract for {symbol}")
                return None

            # Request historical data
            # Request slightly more days to account for weekends/holidays
            duration = f"{days + 30} D"
            bars = self.ibkr.ib.reqHistoricalData(
                qualified[0],
                endDateTime="",
                durationStr=duration,
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )

            if not bars:
                logger.debug(f"No historical data returned for {symbol}")
                return None

            # Return most recent N bars
            return bars[-days:] if len(bars) > days else bars

        except Exception as e:
            logger.debug(f"Failed to fetch historical data for {symbol}: {e}")
            return None

    def _calculate_rsi(self, closes: np.ndarray, period: int) -> Optional[float]:
        """Calculate Relative Strength Index.

        RSI measures momentum by comparing magnitude of recent gains to recent losses.
        Range: 0-100. >70 = overbought, <30 = oversold.

        Args:
            closes: Array of closing prices
            period: RSI period (typically 7 or 14)

        Returns:
            RSI value (0-100) or None if insufficient data
        """
        if len(closes) < period + 1:
            return None

        # Calculate price changes
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        # Calculate average gain and loss
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0  # No losses = maximum RSI

        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        return round(rsi, 2)

    def _calculate_macd(
        self,
        closes: np.ndarray,
        fast: int = 12,
        slow: int = 26,
        signal: int = 9,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Calculate MACD, Signal line, and Histogram.

        MACD shows relationship between two moving averages of prices.
        Positive histogram = bullish momentum, negative = bearish.

        Args:
            closes: Array of closing prices
            fast: Fast EMA period (typically 12)
            slow: Slow EMA period (typically 26)
            signal: Signal line period (typically 9)

        Returns:
            Tuple of (MACD line, Signal line, Histogram) or (None, None, None)
        """
        if len(closes) < slow + signal:
            return None, None, None

        ema_fast = self._ema(closes, fast)
        ema_slow = self._ema(closes, slow)

        macd_line = ema_fast - ema_slow
        signal_line = self._ema(macd_line[slow-1:], signal)  # Signal EMA on MACD values

        # Get most recent values
        macd_val = macd_line[-1]
        signal_val = signal_line[-1] if len(signal_line) > 0 else None
        histogram = macd_val - signal_val if signal_val is not None else None

        return (
            round(macd_val, 4) if macd_val is not None else None,
            round(signal_val, 4) if signal_val is not None else None,
            round(histogram, 4) if histogram is not None else None,
        )

    def _calculate_adx(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Calculate ADX, +DI, and -DI.

        ADX measures trend strength (not direction). >25 = strong trend, <20 = weak trend.
        +DI and -DI show trend direction: +DI > -DI = uptrend, -DI > +DI = downtrend.

        Args:
            highs: Array of high prices
            lows: Array of low prices
            closes: Array of closing prices
            period: ADX period (typically 14)

        Returns:
            Tuple of (ADX, +DI, -DI) or (None, None, None)
        """
        if len(closes) < period * 2:
            return None, None, None

        try:
            # True Range
            tr = np.maximum(
                highs[1:] - lows[1:],
                np.maximum(
                    np.abs(highs[1:] - closes[:-1]),
                    np.abs(lows[1:] - closes[:-1])
                )
            )

            # Directional Movement
            plus_dm = np.where(
                (highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
                np.maximum(highs[1:] - highs[:-1], 0),
                0
            )
            minus_dm = np.where(
                (lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
                np.maximum(lows[:-1] - lows[1:], 0),
                0
            )

            # Smoothed values using Wilder's smoothing
            atr = self._wilder_smooth(tr, period)
            smooth_plus_dm = self._wilder_smooth(plus_dm, period)
            smooth_minus_dm = self._wilder_smooth(minus_dm, period)

            # Avoid division by zero in ATR
            atr_safe = np.where(atr == 0, 1e-10, atr)

            # Directional Indicators
            plus_di = 100 * smooth_plus_dm / atr_safe
            minus_di = 100 * smooth_minus_dm / atr_safe

            # ADX calculation
            di_sum = plus_di + minus_di
            di_sum_safe = np.where(di_sum == 0, 1e-10, di_sum)
            dx = 100 * np.abs(plus_di - minus_di) / di_sum_safe
            adx = self._wilder_smooth(dx, period)

            return round(adx[-1], 2), round(plus_di[-1], 2), round(minus_di[-1], 2)

        except Exception as e:
            logger.debug(f"ADX calculation error: {e}")
            return None, None, None

    def _calculate_atr(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> Optional[float]:
        """Calculate Average True Range.

        ATR measures volatility by decomposing the entire range of price movement.
        Higher ATR = more volatile, lower ATR = less volatile.

        Args:
            highs: Array of high prices
            lows: Array of low prices
            closes: Array of closing prices
            period: ATR period (typically 14)

        Returns:
            ATR value or None if insufficient data
        """
        if len(closes) < period + 1:
            return None

        # Calculate True Range
        tr = np.maximum(
            highs[1:] - lows[1:],
            np.maximum(
                np.abs(highs[1:] - closes[:-1]),
                np.abs(lows[1:] - closes[:-1])
            )
        )

        # Average True Range
        atr = np.mean(tr[-period:])
        return round(atr, 4)

    def _calculate_bollinger(
        self,
        closes: np.ndarray,
        period: int = 20,
        std_dev: float = 2.0,
        current_price: float = None,
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Calculate Bollinger Bands and position within bands.

        Bollinger Bands show price volatility and potential reversal points.
        bb_position: 0 = at lower band, 0.5 = at SMA, 1.0 = at upper band.

        Args:
            closes: Array of closing prices
            period: Moving average period (typically 20)
            std_dev: Number of standard deviations (typically 2.0)
            current_price: Current price to calculate position

        Returns:
            Tuple of (upper_band, lower_band, position) or (None, None, None)
        """
        if len(closes) < period:
            return None, None, None

        sma = np.mean(closes[-period:])
        std = np.std(closes[-period:])

        upper = sma + (std_dev * std)
        lower = sma - (std_dev * std)

        # Calculate where current price sits within the bands (0.0 to 1.0)
        price = current_price if current_price else closes[-1]
        position = (price - lower) / (upper - lower) if upper != lower else 0.5
        position = max(0.0, min(1.0, position))  # Clamp to [0, 1]

        return round(upper, 2), round(lower, 2), round(position, 4)

    def _calculate_support_resistance(
        self,
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        lookback: int = 20,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """Calculate support and resistance levels using pivot points.

        Uses classic pivot point method:
        - Pivot = (High + Low + Close) / 3
        - R1 = 2*Pivot - Low, S1 = 2*Pivot - High
        - R2 = Pivot + (High - Low), S2 = Pivot - (High - Low)

        Args:
            highs: Array of high prices
            lows: Array of low prices
            closes: Array of closing prices
            lookback: Period for calculating levels (typically 20)

        Returns:
            Tuple of (S1, S2, R1, R2) or (None, None, None, None)
        """
        if len(closes) < lookback:
            return None, None, None, None

        recent_highs = highs[-lookback:]
        recent_lows = lows[-lookback:]
        recent_closes = closes[-lookback:]

        # Pivot point calculation
        high = np.max(recent_highs)
        low = np.min(recent_lows)
        close = recent_closes[-1]
        pivot = (high + low + close) / 3

        # Support and resistance levels
        r1 = 2 * pivot - low
        s1 = 2 * pivot - high
        r2 = pivot + (high - low)
        s2 = pivot - (high - low)

        return round(s1, 2), round(s2, 2), round(r1, 2), round(r2, 2)

    def _ema(self, data: np.ndarray, period: int) -> np.ndarray:
        """Calculate Exponential Moving Average.

        EMA gives more weight to recent prices, making it more responsive
        than a simple moving average.

        Args:
            data: Array of values
            period: EMA period

        Returns:
            Array of EMA values
        """
        multiplier = 2 / (period + 1)
        ema = np.zeros_like(data)
        ema[period-1] = np.mean(data[:period])  # Start with SMA

        for i in range(period, len(data)):
            ema[i] = (data[i] * multiplier) + (ema[i-1] * (1 - multiplier))

        return ema

    def _wilder_smooth(self, data: np.ndarray, period: int) -> np.ndarray:
        """Wilder's smoothing method (used for ADX).

        Similar to EMA but uses different smoothing constant.

        Args:
            data: Array of values
            period: Smoothing period

        Returns:
            Array of smoothed values
        """
        smoothed = np.zeros_like(data)
        smoothed[period-1] = np.sum(data[:period])

        for i in range(period, len(data)):
            smoothed[i] = smoothed[i-1] - (smoothed[i-1] / period) + data[i]

        return smoothed / period
