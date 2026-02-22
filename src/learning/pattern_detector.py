"""Pattern Detector for identifying what works and what doesn't.

Analyzes closed trades to find statistically significant patterns across
multiple dimensions: delta, IV rank, DTE, VIX regime, trend, sector, timing.
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np
import sqlalchemy as sa
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import Trade, TradeEntrySnapshot
from src.learning.models import DetectedPattern
from src.learning.path_analyzer import PathAnalyzer
from src.learning.pattern_combiner import PatternCombiner


class PatternDetector:
    """Identifies profitable patterns from trade history.

    Analyzes closed trades across multiple dimensions to find what
    consistently works and what doesn't. Returns patterns with
    statistical significance metrics.
    """

    def __init__(self, db_session: Session, min_sample_size: int = 30):
        """Initialize pattern detector.

        Args:
            db_session: Database session for querying trades
            min_sample_size: Minimum trades needed for valid pattern (default: 30)
        """
        self.db = db_session
        self.min_samples = min_sample_size
        self.baseline_win_rate: Optional[float] = None
        self.baseline_roi: Optional[float] = None

    def detect_patterns(self) -> list[DetectedPattern]:
        """Run all pattern analyses and return significant findings.

        Returns:
            List of detected patterns that meet minimum sample size
        """
        logger.info("Starting pattern detection across all dimensions")

        # Calculate baseline metrics
        self._calculate_baseline()

        if self.baseline_win_rate is None:
            logger.warning("Insufficient trade data for pattern detection")
            return []

        patterns = []

        # Run all pattern analyses
        patterns.extend(self.analyze_by_delta_bucket())
        patterns.extend(self.analyze_by_iv_rank_bucket())
        patterns.extend(self.analyze_by_dte_bucket())
        patterns.extend(self.analyze_by_vix_regime())
        patterns.extend(self.analyze_by_trend_direction())
        patterns.extend(self.analyze_by_sector())
        patterns.extend(self.analyze_by_day_of_week())

        # Phase 3.1: Technical Indicators Integration
        patterns.extend(self.analyze_by_rsi_regime())
        patterns.extend(self.analyze_by_macd_histogram())
        patterns.extend(self.analyze_by_trend_strength())
        patterns.extend(self.analyze_by_bb_position())
        patterns.extend(self.analyze_by_support_proximity())
        patterns.extend(self.analyze_by_atr_volatility())

        # Phase 3.2: Market Context Integration
        patterns.extend(self.analyze_by_vol_regime())
        patterns.extend(self.analyze_by_market_regime())
        patterns.extend(self.analyze_by_opex_week())
        patterns.extend(self.analyze_by_fomc_proximity())
        patterns.extend(self.analyze_by_earnings_timing())
        patterns.extend(self.analyze_by_market_breadth())

        # Phase 3.3: Path Analysis Integration (Position Snapshots)
        path_analyzer = PathAnalyzer(self.db, self.min_samples)
        patterns.extend(path_analyzer.analyze_all_paths())

        # Phase 3.5: Multi-dimensional Pattern Combinations
        pattern_combiner = PatternCombiner(self.db, self.min_samples)
        patterns.extend(pattern_combiner.analyze_all_combinations())

        logger.info(f"Detected {len(patterns)} patterns across all dimensions")

        return patterns

    def _calculate_baseline(self) -> None:
        """Calculate overall baseline win rate and ROI."""
        closed_trades = (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .filter(
                sa.or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held")
            )
            .all()
        )

        if len(closed_trades) < self.min_samples:
            logger.warning(
                f"Only {len(closed_trades)} closed trades - need {self.min_samples} for baseline"
            )
            return

        wins = sum(1 for t in closed_trades if t.profit_loss and t.profit_loss > 0)
        self.baseline_win_rate = wins / len(closed_trades)

        rois = [t.roi for t in closed_trades if t.roi is not None]
        self.baseline_roi = np.mean(rois) if rois else 0.0

        logger.info(
            f"Baseline: {len(closed_trades)} trades, "
            f"{self.baseline_win_rate:.1%} win rate, "
            f"{self.baseline_roi:.2%} avg ROI"
        )

    def analyze_by_delta_bucket(self) -> list[DetectedPattern]:
        """Find optimal delta ranges.

        Buckets trades by delta: 0-10%, 10-15%, 15-20%, 20-25%, 25%+
        Returns patterns where performance differs from baseline.
        """
        logger.debug("Analyzing patterns by delta bucket")

        # Define buckets
        buckets = {
            "0-10%": (0.0, 0.10),
            "10-15%": (0.10, 0.15),
            "15-20%": (0.15, 0.20),
            "20-25%": (0.20, 0.25),
            "25%+": (0.25, 1.0),
        }

        patterns = []

        for bucket_name, (min_delta, max_delta) in buckets.items():
            trades = self._get_trades_in_delta_range(min_delta, max_delta)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="delta_bucket",
                pattern_name=f"delta_{bucket_name.replace('%', 'pct').replace('-', '_').replace('+', 'plus')}",
                pattern_value=bucket_name,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)
            logger.debug(
                f"Delta {bucket_name}: {len(trades)} trades, "
                f"{win_rate:.1%} win rate, {avg_roi:.2%} ROI"
            )

        return patterns

    def analyze_by_iv_rank_bucket(self) -> list[DetectedPattern]:
        """Analyze performance by IV rank buckets.

        Buckets: <25% (low), 25-50% (medium), 50-75% (high), 75%+ (very high)
        """
        logger.debug("Analyzing patterns by IV rank bucket")

        buckets = {
            "low_iv": (0.0, 0.25),
            "medium_iv": (0.25, 0.50),
            "high_iv": (0.50, 0.75),
            "very_high_iv": (0.75, 1.0),
        }

        patterns = []

        for bucket_name, (min_iv, max_iv) in buckets.items():
            trades = self._get_trades_in_iv_range(min_iv, max_iv)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="iv_rank_bucket",
                pattern_name=bucket_name,
                pattern_value=f"{min_iv*100:.0f}-{max_iv*100:.0f}%",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_dte_bucket(self) -> list[DetectedPattern]:
        """Analyze performance by days to expiration buckets.

        Buckets: 0-7 days, 7-14 days, 14-21 days, 21-30 days, 30+ days
        """
        logger.debug("Analyzing patterns by DTE bucket")

        buckets = {
            "0-7_days": (0, 7),
            "7-14_days": (7, 14),
            "14-21_days": (14, 21),
            "21-30_days": (21, 30),
            "30plus_days": (30, 365),
        }

        patterns = []

        for bucket_name, (min_dte, max_dte) in buckets.items():
            trades = self._get_trades_in_dte_range(min_dte, max_dte)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="dte_bucket",
                pattern_name=bucket_name,
                pattern_value=f"{min_dte}-{max_dte} days",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_vix_regime(self) -> list[DetectedPattern]:
        """Analyze how VIX level affects outcomes.

        Buckets: <15 (low), 15-20 (normal), 20-25 (elevated), 25+ (high)
        """
        logger.debug("Analyzing patterns by VIX regime")

        buckets = {
            "low_vix": (0.0, 15.0),
            "normal_vix": (15.0, 20.0),
            "elevated_vix": (20.0, 25.0),
            "high_vix": (25.0, 100.0),
        }

        patterns = []

        for bucket_name, (min_vix, max_vix) in buckets.items():
            trades = self._get_trades_in_vix_range(min_vix, max_vix)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="vix_regime",
                pattern_name=bucket_name,
                pattern_value=f"VIX {min_vix:.0f}-{max_vix:.0f}",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
                market_regime=bucket_name,
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_trend_direction(self) -> list[DetectedPattern]:
        """Analyze performance by underlying trend.

        Categories: uptrend, downtrend, sideways, unknown
        """
        logger.debug("Analyzing patterns by trend direction")

        trends = ["uptrend", "downtrend", "sideways", "unknown"]
        patterns = []

        for trend in trends:
            trades = self._get_trades_by_trend(trend)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="trend_direction",
                pattern_name=f"trend_{trend}",
                pattern_value=trend,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_sector(self) -> list[DetectedPattern]:
        """Analyze performance by stock sector.

        Uses sector information from TradeEntrySnapshot.
        Identifies which sectors perform better for naked put strategy.

        Common sectors:
        - Technology, Healthcare, Financial, Consumer, Industrial, Energy, etc.
        """
        logger.debug("Analyzing patterns by sector")

        # Get all sectors that have data
        sectors_query = (
            self.db.query(TradeEntrySnapshot.sector)
            .join(Trade, TradeEntrySnapshot.trade_id == Trade.id)
            .filter(Trade.exit_date.isnot(None))
            .filter(
                sa.or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held")
            )
            .filter(TradeEntrySnapshot.sector.isnot(None))
            .distinct()
            .all()
        )

        sectors = [s[0] for s in sectors_query]

        if not sectors:
            logger.debug("No sector data available")
            return []

        patterns = []

        for sector in sectors:
            trades = self._get_trades_by_sector(sector)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="sector",
                pattern_name=f"sector_{sector.lower().replace(' ', '_').replace('&', 'and')}",
                pattern_value=sector,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_day_of_week(self) -> list[DetectedPattern]:
        """Analyze performance by day of week for trade entry.

        Identifies if certain days of week are better for entering trades.
        """
        logger.debug("Analyzing patterns by day of week")

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        patterns = []

        for day_name in days:
            trades = self._get_trades_by_entry_day(day_name)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="entry_day",
                pattern_name=f"entry_{day_name.lower()}",
                pattern_value=day_name,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    # =========================================================================
    # Phase 3.1: Technical Indicators Pattern Analysis
    # =========================================================================

    def analyze_by_rsi_regime(self) -> list[DetectedPattern]:
        """Analyze performance by RSI regime at entry.

        Buckets:
        - Oversold: RSI < 30 (potential bounce)
        - Neutral: RSI 30-70 (normal range)
        - Overbought: RSI > 70 (potential pullback)

        For naked puts, overbought conditions might be favorable
        (stock already extended, pullback supports put).
        """
        logger.debug("Analyzing patterns by RSI regime")

        buckets = {
            "rsi_oversold": (0.0, 30.0),
            "rsi_neutral": (30.0, 70.0),
            "rsi_overbought": (70.0, 100.0),
        }

        patterns = []

        for bucket_name, (min_rsi, max_rsi) in buckets.items():
            trades = self._get_trades_in_rsi_range(min_rsi, max_rsi)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="rsi_regime",
                pattern_name=bucket_name,
                pattern_value=f"RSI {min_rsi:.0f}-{max_rsi:.0f}",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_macd_histogram(self) -> list[DetectedPattern]:
        """Analyze performance by MACD histogram momentum at entry.

        MACD histogram = MACD line - Signal line
        - Large positive: Strong bullish momentum
        - Small positive: Weak bullish momentum
        - Near zero: Neutral/transition
        - Small negative: Weak bearish momentum
        - Large negative: Strong bearish momentum

        For naked puts, strong bearish momentum might create opportunities
        (sell puts when stocks are oversold).
        """
        logger.debug("Analyzing patterns by MACD histogram")

        buckets = {
            "macd_strong_bearish": (-100.0, -0.5),
            "macd_weak_bearish": (-0.5, -0.1),
            "macd_neutral": (-0.1, 0.1),
            "macd_weak_bullish": (0.1, 0.5),
            "macd_strong_bullish": (0.5, 100.0),
        }

        patterns = []

        for bucket_name, (min_hist, max_hist) in buckets.items():
            trades = self._get_trades_in_macd_histogram_range(min_hist, max_hist)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="macd_momentum",
                pattern_name=bucket_name,
                pattern_value=f"MACD Hist {min_hist:.1f} to {max_hist:.1f}",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_trend_strength(self) -> list[DetectedPattern]:
        """Analyze performance by ADX trend strength at entry.

        ADX measures trend strength (not direction):
        - Weak: ADX < 20 (ranging/choppy market)
        - Moderate: ADX 20-40 (developing trend)
        - Strong: ADX > 40 (strong trending market)

        For naked puts, moderate trends might be ideal
        (enough direction but not parabolic).
        """
        logger.debug("Analyzing patterns by ADX trend strength")

        buckets = {
            "adx_weak_trend": (0.0, 20.0),
            "adx_moderate_trend": (20.0, 40.0),
            "adx_strong_trend": (40.0, 100.0),
        }

        patterns = []

        for bucket_name, (min_adx, max_adx) in buckets.items():
            trades = self._get_trades_in_adx_range(min_adx, max_adx)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="trend_strength",
                pattern_name=bucket_name,
                pattern_value=f"ADX {min_adx:.0f}-{max_adx:.0f}",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_bb_position(self) -> list[DetectedPattern]:
        """Analyze performance by Bollinger Band position at entry.

        BB position = (price - lower_band) / (upper_band - lower_band)
        - Near lower band (0-0.2): Oversold, potential bounce
        - Middle range (0.2-0.8): Normal trading range
        - Near upper band (0.8-1.0): Overbought, potential pullback

        For naked puts, near lower band might be favorable
        (stock oversold, likely to bounce).
        """
        logger.debug("Analyzing patterns by Bollinger Band position")

        buckets = {
            "bb_near_lower": (0.0, 0.2),
            "bb_middle": (0.2, 0.8),
            "bb_near_upper": (0.8, 1.0),
        }

        patterns = []

        for bucket_name, (min_pos, max_pos) in buckets.items():
            trades = self._get_trades_in_bb_position_range(min_pos, max_pos)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="bb_position",
                pattern_name=bucket_name,
                pattern_value=f"BB Position {min_pos:.1f}-{max_pos:.1f}",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_support_proximity(self) -> list[DetectedPattern]:
        """Analyze performance by proximity to support levels at entry.

        Distance to support (percentage):
        - Near support (0-5%): Close to support, strong risk/reward
        - Moderate distance (5-15%): Normal range
        - Far from support (15%+): Weak support protection

        For naked puts, near support is favorable (limited downside).
        """
        logger.debug("Analyzing patterns by support proximity")

        buckets = {
            "near_support": (0.0, 5.0),
            "moderate_support": (5.0, 15.0),
            "far_support": (15.0, 100.0),
        }

        patterns = []

        for bucket_name, (min_dist, max_dist) in buckets.items():
            trades = self._get_trades_in_support_proximity_range(min_dist, max_dist)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="support_proximity",
                pattern_name=bucket_name,
                pattern_value=f"{min_dist:.0f}-{max_dist:.0f}% from support",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_atr_volatility(self) -> list[DetectedPattern]:
        """Analyze performance by ATR volatility at entry.

        ATR as percentage of stock price:
        - Low volatility: ATR < 2% (stable, low premium)
        - Medium volatility: ATR 2-5% (normal, good premium)
        - High volatility: ATR > 5% (unstable, high premium but risky)

        For naked puts, medium volatility might be optimal
        (decent premium without excessive risk).
        """
        logger.debug("Analyzing patterns by ATR volatility")

        buckets = {
            "atr_low_vol": (0.0, 2.0),
            "atr_medium_vol": (2.0, 5.0),
            "atr_high_vol": (5.0, 100.0),
        }

        patterns = []

        for bucket_name, (min_atr, max_atr) in buckets.items():
            trades = self._get_trades_in_atr_range(min_atr, max_atr)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="atr_volatility",
                pattern_name=bucket_name,
                pattern_value=f"ATR {min_atr:.1f}-{max_atr:.1f}%",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    # =========================================================================
    # Phase 3.2: Market Context Pattern Analysis
    # =========================================================================

    def analyze_by_vol_regime(self) -> list[DetectedPattern]:
        """Analyze performance by volatility regime at entry.

        Volatility regimes (based on VIX or IV conditions):
        - Low: Calm markets, low premiums
        - Normal: Typical market conditions
        - Elevated: Heightened uncertainty
        - Extreme: Crisis/panic conditions

        For naked puts, normal to elevated vol may be optimal
        (decent premiums without excessive risk).
        """
        logger.debug("Analyzing patterns by volatility regime")

        regimes = ["low", "normal", "elevated", "extreme"]
        patterns = []

        for regime in regimes:
            trades = self._get_trades_by_vol_regime(regime)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="vol_regime",
                pattern_name=f"vol_{regime}",
                pattern_value=regime.capitalize(),
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
                market_regime=regime,
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_market_regime(self) -> list[DetectedPattern]:
        """Analyze performance by market regime at entry.

        Market regimes:
        - Bullish: Strong upward trend, good for naked puts
        - Bearish: Downward trend, risky for puts
        - Neutral: Sideways/consolidating
        - Volatile: High uncertainty, large swings

        For naked puts, bullish and neutral regimes typically favorable.
        """
        logger.debug("Analyzing patterns by market regime")

        regimes = ["bullish", "bearish", "neutral", "volatile"]
        patterns = []

        for regime in regimes:
            trades = self._get_trades_by_market_regime(regime)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="market_regime",
                pattern_name=f"market_{regime}",
                pattern_value=regime.capitalize(),
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
                market_regime=regime,
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_opex_week(self) -> list[DetectedPattern]:
        """Analyze performance during option expiration weeks.

        OpEx week (3rd Friday of month) often has:
        - Increased volatility
        - Pin risk near strikes
        - Abnormal price action

        Pattern determines if OpEx week trades perform differently.
        """
        logger.debug("Analyzing patterns by OpEx week")

        # Compare OpEx week vs non-OpEx week
        categories = [
            ("opex_week", True),
            ("non_opex_week", False),
        ]

        patterns = []

        for category_name, is_opex in categories:
            trades = self._get_trades_by_opex_week(is_opex)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="calendar_event",
                pattern_name=category_name,
                pattern_value="OpEx Week" if is_opex else "Non-OpEx Week",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_fomc_proximity(self) -> list[DetectedPattern]:
        """Analyze performance by proximity to FOMC meetings.

        FOMC meetings create uncertainty and volatility.
        Buckets:
        - Far from FOMC (>14 days): Normal conditions
        - Moderate proximity (7-14 days): Some positioning
        - Near FOMC (<7 days): High uncertainty

        Pattern determines if avoiding FOMC proximity improves performance.
        """
        logger.debug("Analyzing patterns by FOMC proximity")

        buckets = {
            "far_from_fomc": (14, 365),
            "moderate_fomc_proximity": (7, 14),
            "near_fomc": (0, 7),
        }

        patterns = []

        for bucket_name, (min_days, max_days) in buckets.items():
            trades = self._get_trades_by_fomc_proximity(min_days, max_days)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="calendar_event",
                pattern_name=bucket_name,
                pattern_value=f"{min_days}-{max_days} days to FOMC",
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_earnings_timing(self) -> list[DetectedPattern]:
        """Analyze performance by earnings announcement timing.

        Earnings timing:
        - BMO (Before Market Open): Overnight risk
        - AMC (After Market Close): Less overnight risk
        - No earnings in DTE: Safest

        For naked puts, avoiding earnings (or preferring AMC) may be better.
        """
        logger.debug("Analyzing patterns by earnings timing")

        # Categories: BMO, AMC, or no earnings in trade window
        categories = []

        # Trades with BMO earnings
        bmo_trades = self._get_trades_by_earnings_timing("BMO")
        if len(bmo_trades) >= self.min_samples:
            categories.append(("earnings_bmo", bmo_trades))

        # Trades with AMC earnings
        amc_trades = self._get_trades_by_earnings_timing("AMC")
        if len(amc_trades) >= self.min_samples:
            categories.append(("earnings_amc", amc_trades))

        # Trades with no earnings in window
        no_earnings_trades = self._get_trades_by_earnings_timing(None)
        if len(no_earnings_trades) >= self.min_samples:
            categories.append(("no_earnings_in_dte", no_earnings_trades))

        patterns = []

        for category_name, trades in categories:
            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            if category_name == "earnings_bmo":
                pattern_value = "Before Market Open"
            elif category_name == "earnings_amc":
                pattern_value = "After Market Close"
            else:
                pattern_value = "No Earnings in DTE"

            pattern = DetectedPattern(
                pattern_type="earnings_timing",
                pattern_name=category_name,
                pattern_value=pattern_value,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_market_breadth(self) -> list[DetectedPattern]:
        """Analyze performance by broad market breadth/divergence.

        Market breadth indicators:
        - Small caps outperforming (IWM > QQQ): Risk-on
        - Large caps outperforming (QQQ > IWM): Risk-off/defensive
        - Both positive: Strong market
        - Both negative: Weak market

        For naked puts, risk-on conditions may be favorable.
        """
        logger.debug("Analyzing patterns by market breadth")

        # Define breadth conditions based on QQQ and IWM daily changes
        buckets = {
            "risk_on": "small_caps_strong",      # IWM outperforming
            "risk_off": "large_caps_defensive",   # QQQ outperforming
            "broad_strength": "both_positive",    # Both indices up
            "broad_weakness": "both_negative",    # Both indices down
        }

        patterns = []

        for bucket_name, description in buckets.items():
            trades = self._get_trades_by_market_breadth(bucket_name)

            if len(trades) < self.min_samples:
                continue

            win_rate, avg_roi = self._calculate_metrics(trades)
            p_value, effect_size = self._compare_to_baseline(trades)

            pattern = DetectedPattern(
                pattern_type="market_breadth",
                pattern_name=bucket_name,
                pattern_value=description.replace('_', ' ').title(),
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=self.baseline_win_rate,
                baseline_roi=self.baseline_roi,
                p_value=p_value,
                effect_size=effect_size,
                confidence=self._calculate_confidence(p_value, effect_size, len(trades)),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    # Helper methods for filtering trades

    def _closed_trades_query(self):
        """Base query for closed trades, excluding stock_held (incomplete P&L).

        Trades with lifecycle_status='stock_held' have incomplete P&L because
        the stock hasn't been sold yet. They are included again once
        lifecycle_status='fully_closed'.
        """
        return (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .filter(
                sa.or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held")
            )
        )

    def _get_trades_in_delta_range(self, min_delta: float, max_delta: float) -> list[Trade]:
        """Get closed trades within delta range.

        Uses TradeEntrySnapshot to filter by entry delta value.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.delta.isnot(None))
            .filter(TradeEntrySnapshot.delta >= min_delta)
            .filter(TradeEntrySnapshot.delta < max_delta)
            .all()
        )
        return closed_trades

    def _get_trades_in_iv_range(self, min_iv: float, max_iv: float) -> list[Trade]:
        """Get closed trades within IV rank range.

        Uses TradeEntrySnapshot to filter by entry IV rank value.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.iv_rank.isnot(None))
            .filter(TradeEntrySnapshot.iv_rank >= min_iv)
            .filter(TradeEntrySnapshot.iv_rank < max_iv)
            .all()
        )
        return closed_trades

    def _get_trades_in_dte_range(self, min_dte: int, max_dte: int) -> list[Trade]:
        """Get closed trades within DTE range."""
        closed_trades = (
            self._closed_trades_query()
            .filter(Trade.dte >= min_dte)
            .filter(Trade.dte <= max_dte)
            .all()
        )
        return closed_trades

    def _get_trades_in_vix_range(self, min_vix: float, max_vix: float) -> list[Trade]:
        """Get closed trades within VIX range."""
        closed_trades = (
            self._closed_trades_query()
            .filter(Trade.vix_at_entry >= min_vix)
            .filter(Trade.vix_at_entry < max_vix)
            .all()
        )
        return closed_trades

    def _get_trades_by_trend(self, trend: str) -> list[Trade]:
        """Get closed trades with specific trend direction.

        Uses TradeEntrySnapshot to filter by trend_direction at entry.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.trend_direction == trend)
            .all()
        )
        return closed_trades

    def _get_trades_by_entry_day(self, day_name: str) -> list[Trade]:
        """Get closed trades entered on specific day of week."""
        # Map day name to number (0=Monday, 4=Friday)
        day_map = {
            "Monday": 0,
            "Tuesday": 1,
            "Wednesday": 2,
            "Thursday": 3,
            "Friday": 4,
        }
        day_num = day_map.get(day_name)

        closed_trades = self._closed_trades_query().all()

        # Filter by day of week
        filtered = [
            t for t in closed_trades if t.entry_date.weekday() == day_num
        ]

        return filtered

    # Phase 3.1: Technical Indicator Helper Methods

    def _get_trades_in_rsi_range(self, min_rsi: float, max_rsi: float) -> list[Trade]:
        """Get closed trades within RSI range at entry.

        Uses TradeEntrySnapshot.rsi_14 field.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.rsi_14.isnot(None))
            .filter(TradeEntrySnapshot.rsi_14 >= min_rsi)
            .filter(TradeEntrySnapshot.rsi_14 < max_rsi)
            .all()
        )
        return closed_trades

    def _get_trades_in_macd_histogram_range(
        self, min_hist: float, max_hist: float
    ) -> list[Trade]:
        """Get closed trades within MACD histogram range at entry.

        Uses TradeEntrySnapshot.macd_histogram field.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.macd_histogram.isnot(None))
            .filter(TradeEntrySnapshot.macd_histogram >= min_hist)
            .filter(TradeEntrySnapshot.macd_histogram < max_hist)
            .all()
        )
        return closed_trades

    def _get_trades_in_adx_range(self, min_adx: float, max_adx: float) -> list[Trade]:
        """Get closed trades within ADX range at entry.

        Uses TradeEntrySnapshot.adx field.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.adx.isnot(None))
            .filter(TradeEntrySnapshot.adx >= min_adx)
            .filter(TradeEntrySnapshot.adx < max_adx)
            .all()
        )
        return closed_trades

    def _get_trades_in_bb_position_range(
        self, min_pos: float, max_pos: float
    ) -> list[Trade]:
        """Get closed trades within Bollinger Band position range at entry.

        Uses TradeEntrySnapshot.bb_position field.
        BB position ranges from 0.0 (at lower band) to 1.0 (at upper band).
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.bb_position.isnot(None))
            .filter(TradeEntrySnapshot.bb_position >= min_pos)
            .filter(TradeEntrySnapshot.bb_position < max_pos)
            .all()
        )
        return closed_trades

    def _get_trades_in_support_proximity_range(
        self, min_dist: float, max_dist: float
    ) -> list[Trade]:
        """Get closed trades within distance to support range at entry.

        Uses TradeEntrySnapshot.distance_to_support_pct field.
        Distance is percentage from current price to nearest support.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.distance_to_support_pct.isnot(None))
            .filter(TradeEntrySnapshot.distance_to_support_pct >= min_dist)
            .filter(TradeEntrySnapshot.distance_to_support_pct < max_dist)
            .all()
        )
        return closed_trades

    def _get_trades_in_atr_range(self, min_atr: float, max_atr: float) -> list[Trade]:
        """Get closed trades within ATR percentage range at entry.

        Uses TradeEntrySnapshot.atr_pct field (ATR as % of stock price).
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.atr_pct.isnot(None))
            .filter(TradeEntrySnapshot.atr_pct >= min_atr)
            .filter(TradeEntrySnapshot.atr_pct < max_atr)
            .all()
        )
        return closed_trades

    # Phase 3.2: Market Context Helper Methods

    def _get_trades_by_sector(self, sector: str) -> list[Trade]:
        """Get closed trades in specific sector.

        Uses TradeEntrySnapshot.sector field.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.sector == sector)
            .all()
        )
        return closed_trades

    def _get_trades_by_vol_regime(self, regime: str) -> list[Trade]:
        """Get closed trades in specific volatility regime.

        Uses TradeEntrySnapshot.vol_regime field.
        Regimes: "low", "normal", "elevated", "extreme"
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.vol_regime == regime)
            .all()
        )
        return closed_trades

    def _get_trades_by_market_regime(self, regime: str) -> list[Trade]:
        """Get closed trades in specific market regime.

        Uses TradeEntrySnapshot.market_regime field.
        Regimes: "bullish", "bearish", "neutral", "volatile"
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.market_regime == regime)
            .all()
        )
        return closed_trades

    def _get_trades_by_opex_week(self, is_opex: bool) -> list[Trade]:
        """Get closed trades entered during/outside OpEx week.

        Uses TradeEntrySnapshot.is_opex_week field.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.is_opex_week == is_opex)
            .all()
        )
        return closed_trades

    def _get_trades_by_fomc_proximity(self, min_days: int, max_days: int) -> list[Trade]:
        """Get closed trades by days to next FOMC meeting.

        Uses TradeEntrySnapshot.days_to_fomc field.
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.days_to_fomc.isnot(None))
            .filter(TradeEntrySnapshot.days_to_fomc >= min_days)
            .filter(TradeEntrySnapshot.days_to_fomc < max_days)
            .all()
        )
        return closed_trades

    def _get_trades_by_earnings_timing(self, timing: str | None) -> list[Trade]:
        """Get closed trades by earnings timing.

        Uses TradeEntrySnapshot.earnings_timing field.
        - timing="BMO": Before Market Open earnings
        - timing="AMC": After Market Close earnings
        - timing=None: No earnings in DTE window
        """
        if timing is None:
            # Get trades with no earnings in DTE (earnings_in_dte = False or NULL)
            closed_trades = (
                self._closed_trades_query()
                .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
                .filter(
                    (TradeEntrySnapshot.earnings_in_dte == False) |
                    (TradeEntrySnapshot.earnings_in_dte.is_(None))
                )
                .all()
            )
        else:
            # Get trades with specific earnings timing
            closed_trades = (
                self._closed_trades_query()
                .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
                .filter(TradeEntrySnapshot.earnings_timing == timing)
                .all()
            )

        return closed_trades

    def _get_trades_by_market_breadth(self, breadth_type: str) -> list[Trade]:
        """Get closed trades by market breadth condition.

        Uses TradeEntrySnapshot qqq_change_pct and iwm_change_pct fields.

        Breadth types:
        - "risk_on": IWM > QQQ (small caps outperforming)
        - "risk_off": QQQ > IWM (large caps defensive)
        - "broad_strength": Both QQQ and IWM positive
        - "broad_weakness": Both QQQ and IWM negative
        """
        closed_trades = (
            self._closed_trades_query()
            .join(TradeEntrySnapshot, Trade.id == TradeEntrySnapshot.trade_id)
            .filter(TradeEntrySnapshot.qqq_change_pct.isnot(None))
            .filter(TradeEntrySnapshot.iwm_change_pct.isnot(None))
            .all()
        )

        # Filter by breadth condition
        filtered_trades = []

        for trade in closed_trades:
            snapshot = trade.entry_snapshot
            qqq_change = snapshot.qqq_change_pct
            iwm_change = snapshot.iwm_change_pct

            if breadth_type == "risk_on":
                # Small caps outperforming large caps
                if iwm_change > qqq_change:
                    filtered_trades.append(trade)

            elif breadth_type == "risk_off":
                # Large caps outperforming small caps
                if qqq_change > iwm_change:
                    filtered_trades.append(trade)

            elif breadth_type == "broad_strength":
                # Both indices positive
                if qqq_change > 0 and iwm_change > 0:
                    filtered_trades.append(trade)

            elif breadth_type == "broad_weakness":
                # Both indices negative
                if qqq_change < 0 and iwm_change < 0:
                    filtered_trades.append(trade)

        return filtered_trades

    # Metric calculation helpers

    def _calculate_metrics(self, trades: list[Trade]) -> tuple[float, float]:
        """Calculate win rate and average ROI for trade list.

        Returns:
            (win_rate, avg_roi) tuple
        """
        if not trades:
            return (0.0, 0.0)

        wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
        win_rate = wins / len(trades)

        rois = [t.roi for t in trades if t.roi is not None]
        avg_roi = np.mean(rois) if rois else 0.0

        return (win_rate, avg_roi)

    def _compare_to_baseline(self, trades: list[Trade]) -> tuple[float, float]:
        """Compare trade group to baseline using t-test.

        Returns:
            (p_value, effect_size) tuple
        """
        from scipy import stats

        if not trades or self.baseline_roi is None:
            return (1.0, 0.0)  # No significance

        # Get ROIs for this group
        group_rois = [t.roi for t in trades if t.roi is not None]

        if len(group_rois) < 2:
            return (1.0, 0.0)

        # Get all ROIs for baseline comparison
        all_trades = self._closed_trades_query().all()
        baseline_rois = [t.roi for t in all_trades if t.roi is not None]

        # Remove group trades from baseline to avoid overlap
        trade_ids = {t.trade_id for t in trades}
        baseline_rois = [
            t.roi for t in all_trades if t.roi is not None and t.trade_id not in trade_ids
        ]

        if len(baseline_rois) < 2:
            return (1.0, 0.0)

        # Independent samples t-test
        t_stat, p_value = stats.ttest_ind(group_rois, baseline_rois)

        # Calculate Cohen's d effect size
        group_mean = np.mean(group_rois)
        baseline_mean = np.mean(baseline_rois)
        pooled_std = np.sqrt(
            (np.var(group_rois) + np.var(baseline_rois)) / 2
        )

        effect_size = (group_mean - baseline_mean) / pooled_std if pooled_std > 0 else 0.0

        return (p_value, effect_size)

    def _calculate_confidence(
        self, p_value: float, effect_size: float, sample_size: int
    ) -> float:
        """Calculate overall confidence score for pattern.

        Combines p-value, effect size, and sample size into confidence score.

        Returns:
            Confidence between 0.0 and 1.0
        """
        # Base confidence from p-value (lower p = higher confidence)
        p_confidence = max(0, 1 - (p_value / 0.05))  # 0.05 is threshold

        # Effect size confidence (larger effect = higher confidence)
        effect_confidence = min(1.0, abs(effect_size) / 1.0)  # Normalized to 1.0

        # Sample size confidence (more samples = higher confidence)
        # Use logarithmic scale
        sample_confidence = min(1.0, np.log(sample_size) / np.log(100))  # 100 samples = 1.0

        # Weighted average
        confidence = (p_confidence * 0.4 + effect_confidence * 0.4 + sample_confidence * 0.2)

        return max(0.0, min(1.0, confidence))
