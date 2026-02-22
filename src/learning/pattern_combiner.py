"""Pattern Combiner for multi-dimensional pattern analysis.

This module combines patterns from multiple dimensions to discover powerful
multi-factor insights. It analyzes how entry conditions, trade trajectories,
and exit quality interact to produce superior outcomes.

Phase 3.5: Multi-dimensional Pattern Combinations
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import PositionSnapshot, Trade, TradeEntrySnapshot, TradeExitSnapshot
from src.learning.models import DetectedPattern


class PatternCombiner:
    """Combines patterns across multiple dimensions for advanced insights.

    Analyzes interactions between:
    - Entry conditions (technical indicators + market context)
    - Trade trajectories (path evolution)
    - Exit quality (outcomes and market changes)
    """

    def __init__(self, db_session: Session, min_sample_size: int = 30):
        """Initialize pattern combiner.

        Args:
            db_session: Database session for querying data
            min_sample_size: Minimum trades needed for valid combination
        """
        self.db = db_session
        self.min_samples = min_sample_size

    def analyze_entry_trajectory_combinations(self) -> list[DetectedPattern]:
        """Analyze combinations of entry conditions and trajectory patterns.

        Finds combinations like:
        - RSI oversold + accelerating momentum = high win rate
        - High IV + delta favorable evolution = excellent outcomes
        - Uptrend + no reversal = consistent profits

        Returns:
            List of combined patterns
        """
        logger.debug("Analyzing entry condition + trajectory combinations")

        trades_with_data = self._get_trades_with_complete_data()

        if len(trades_with_data) < self.min_samples:
            logger.debug("Insufficient trades for combination analysis")
            return []

        patterns = []

        # Combination 1: RSI regime + momentum pattern
        rsi_momentum = self._combine_rsi_momentum(trades_with_data)
        patterns.extend(rsi_momentum)

        # Combination 2: IV rank + IV change outcome
        iv_patterns = self._combine_iv_entry_exit(trades_with_data)
        patterns.extend(iv_patterns)

        # Combination 3: Trend + Greeks evolution
        trend_greeks = self._combine_trend_greeks(trades_with_data)
        patterns.extend(trend_greeks)

        # Combination 4: Market breadth + stock movement
        breadth_stock = self._combine_breadth_stock(trades_with_data)
        patterns.extend(breadth_stock)

        return patterns

    def analyze_entry_exit_combinations(self) -> list[DetectedPattern]:
        """Analyze combinations of entry conditions and exit quality.

        Finds combinations like:
        - High quality entry + profit target exit = best outcomes
        - Low IV rank + IV crush = predictable profits
        - OpEx week + early exit = risk management

        Returns:
            List of combined patterns
        """
        logger.debug("Analyzing entry condition + exit quality combinations")

        trades_with_data = self._get_trades_with_complete_data()

        if len(trades_with_data) < self.min_samples:
            return []

        patterns = []

        # Combination 1: Sector + exit reason
        sector_exit = self._combine_sector_exit_reason(trades_with_data)
        patterns.extend(sector_exit)

        # Combination 2: VIX regime + VIX change
        vix_patterns = self._combine_vix_entry_exit(trades_with_data)
        patterns.extend(vix_patterns)

        # Combination 3: Support proximity + max drawdown
        support_dd = self._combine_support_drawdown(trades_with_data)
        patterns.extend(support_dd)

        return patterns

    def analyze_triple_combinations(self) -> list[DetectedPattern]:
        """Analyze three-way combinations across entry, trajectory, and exit.

        Finds powerful combinations like:
        - High IV + IV crushed + profit target = optimal setup
        - Oversold RSI + accelerating momentum + high quality = best trades
        - Uptrend + delta favorable + low drawdown = consistent winners

        Returns:
            List of triple-combination patterns
        """
        logger.debug("Analyzing entry + trajectory + exit triple combinations")

        trades_with_data = self._get_trades_with_complete_data()

        if len(trades_with_data) < self.min_samples:
            return []

        patterns = []

        # Combination 1: IV entry + IV change + exit reason
        iv_triple = self._combine_iv_triple(trades_with_data)
        patterns.extend(iv_triple)

        # Combination 2: RSI + momentum + quality
        rsi_triple = self._combine_rsi_momentum_quality(trades_with_data)
        patterns.extend(rsi_triple)

        # Combination 3: Trend + Greeks + drawdown
        trend_triple = self._combine_trend_greeks_drawdown(trades_with_data)
        patterns.extend(trend_triple)

        return patterns

    def create_composite_scores(self) -> dict[str, float]:
        """Create composite opportunity scores based on pattern combinations.

        Generates scoring rules like:
        - High IV + oversold RSI + uptrend = score 9/10
        - Low IV + neutral RSI + downtrend = score 3/10

        Returns:
            Dictionary mapping trade characteristics to composite scores
        """
        logger.info("Creating composite opportunity scoring model")

        # This will be used by the strategy to rank opportunities
        # For now, return scoring weights for key factors
        composite_weights = {
            # Entry technical (Phase 3.1)
            "rsi_oversold": 1.5,
            "rsi_neutral": 1.0,
            "rsi_overbought": 0.5,
            "high_iv": 1.3,
            "macd_bullish": 1.2,
            "strong_trend": 1.1,
            "bb_oversold": 1.4,
            "near_support": 1.3,
            "high_volatility": 0.8,
            # Entry context (Phase 3.2)
            "technology_sector": 1.1,
            "uptrend": 1.3,
            "normal_vol_regime": 1.0,
            "bullish_market": 1.2,
            "no_earnings": 1.1,
            "risk_on_breadth": 1.2,
            # Expected trajectory (Phase 3.3)
            "expect_no_reversal": 1.2,
            "expect_acceleration": 1.1,
            "expect_delta_favorable": 1.2,
            "safe_distance_expected": 1.0,
            # Expected exit quality (Phase 3.4)
            "expect_profit_target": 1.3,
            "expect_high_quality": 1.4,
            "expect_iv_crush": 1.5,
            "expect_stock_up": 1.3,
            "expect_vix_decline": 1.2,
            "expect_low_drawdown": 1.1,
        }

        return composite_weights

    # =========================================================================
    # Two-way combination implementations
    # =========================================================================

    def _combine_rsi_momentum(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine RSI regime at entry with P&L momentum pattern."""
        patterns = []

        # Categorize by RSI and momentum
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            if not entry_snapshot or entry_snapshot.rsi_14 is None:
                continue

            # Get position snapshots for momentum
            snapshots = self._get_position_snapshots(trade)
            if len(snapshots) < 3:
                continue

            # Categorize RSI
            rsi = entry_snapshot.rsi_14
            if rsi < 30:
                rsi_cat = "oversold"
            elif rsi < 70:
                rsi_cat = "neutral"
            else:
                rsi_cat = "overbought"

            # Categorize momentum
            pnl_values = [s.current_pnl_pct for s in snapshots if s.current_pnl_pct is not None]
            if len(pnl_values) < 3:
                continue

            mid = len(pnl_values) // 2
            first_half_rate = (pnl_values[mid] - pnl_values[0]) / mid if mid > 0 else 0
            second_half_rate = (pnl_values[-1] - pnl_values[mid]) / (len(pnl_values) - mid)

            if second_half_rate > first_half_rate * 1.2:
                momentum_cat = "accelerating"
            elif second_half_rate < first_half_rate * 0.5:
                momentum_cat = "plateauing"
            else:
                momentum_cat = "steady"

            combo_key = f"{rsi_cat}_rsi_{momentum_cat}_momentum"
            combinations[combo_key].append(trade)

        # Create patterns for significant combinations
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="rsi_momentum_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_iv_entry_exit(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine IV rank at entry with IV change during trade."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if entry_snapshot.iv_rank is None or exit_snapshot.iv_change_during_trade is None:
                continue

            # Categorize IV rank at entry
            iv_rank = entry_snapshot.iv_rank
            if iv_rank > 0.75:
                iv_entry_cat = "very_high_iv"
            elif iv_rank > 0.50:
                iv_entry_cat = "high_iv"
            else:
                iv_entry_cat = "normal_iv"

            # Categorize IV change
            iv_change = exit_snapshot.iv_change_during_trade
            if iv_change < -0.10:
                iv_exit_cat = "crushed"
            elif iv_change <= 0.10:
                iv_exit_cat = "stable"
            else:
                iv_exit_cat = "expanded"

            combo_key = f"{iv_entry_cat}_{iv_exit_cat}"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="iv_entry_exit_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_trend_greeks(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine trend direction with Greeks evolution."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            if not entry_snapshot or not entry_snapshot.trend_direction:
                continue

            snapshots = self._get_position_snapshots(trade)
            if len(snapshots) < 2:
                continue

            # Get delta evolution
            deltas = [abs(s.delta) for s in snapshots if s.delta is not None]
            if len(deltas) < 2:
                continue

            delta_change = deltas[-1] - deltas[0]

            # Categorize Greeks evolution
            if delta_change < -0.05:
                greeks_cat = "favorable"
            elif abs(delta_change) <= 0.05:
                greeks_cat = "stable"
            else:
                greeks_cat = "unfavorable"

            combo_key = f"{entry_snapshot.trend_direction}_{greeks_cat}_greeks"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="trend_greeks_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_breadth_stock(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine market breadth with stock movement."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if (entry_snapshot.qqq_change_pct is None or
                entry_snapshot.iwm_change_pct is None or
                exit_snapshot.stock_change_during_trade_pct is None):
                continue

            # Categorize market breadth
            qqq = entry_snapshot.qqq_change_pct
            iwm = entry_snapshot.iwm_change_pct

            if iwm > qqq and qqq > 0:
                breadth_cat = "risk_on"
            elif qqq > 0 and iwm > 0:
                breadth_cat = "broad_strength"
            elif qqq < 0 and iwm < 0:
                breadth_cat = "broad_weakness"
            else:
                breadth_cat = "risk_off"

            # Categorize stock movement
            stock_change = exit_snapshot.stock_change_during_trade_pct
            if stock_change > 5.0:
                stock_cat = "strong_up"
            elif stock_change > 2.0:
                stock_cat = "moderate_up"
            elif stock_change >= -2.0:
                stock_cat = "neutral"
            else:
                stock_cat = "down"

            combo_key = f"{breadth_cat}_{stock_cat}"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="breadth_stock_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_sector_exit_reason(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine sector with exit reason."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if not entry_snapshot.sector or not exit_snapshot.exit_reason:
                continue

            combo_key = f"{entry_snapshot.sector}_{exit_snapshot.exit_reason}"
            combinations[combo_key].append(trade)

        # Create patterns for top sectors
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="sector_exit_combo",
                pattern_name=combo_name.lower().replace(' ', '_'),
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_vix_entry_exit(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine VIX regime at entry with VIX change during trade."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            if trade.vix_at_entry is None:
                continue

            exit_snapshot = self._get_exit_snapshot(trade)
            if not exit_snapshot or exit_snapshot.vix_change_during_trade is None:
                continue

            # Categorize VIX entry
            vix_entry = trade.vix_at_entry
            if vix_entry < 15:
                vix_entry_cat = "low"
            elif vix_entry < 20:
                vix_entry_cat = "normal"
            elif vix_entry < 25:
                vix_entry_cat = "elevated"
            else:
                vix_entry_cat = "high"

            # Categorize VIX change
            vix_change = exit_snapshot.vix_change_during_trade
            if vix_change < -2.0:
                vix_change_cat = "declined"
            elif vix_change <= 2.0:
                vix_change_cat = "stable"
            else:
                vix_change_cat = "spiked"

            combo_key = f"vix_{vix_entry_cat}_{vix_change_cat}"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="vix_entry_exit_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_support_drawdown(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Combine distance to support with maximum drawdown."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if (entry_snapshot.distance_to_support_pct is None or
                exit_snapshot.max_drawdown_pct is None):
                continue

            # Categorize support proximity
            dist_support = entry_snapshot.distance_to_support_pct
            if dist_support < 5.0:
                support_cat = "near"
            elif dist_support < 15.0:
                support_cat = "moderate"
            else:
                support_cat = "far"

            # Categorize drawdown
            drawdown = abs(exit_snapshot.max_drawdown_pct)
            if drawdown < 0.10:
                dd_cat = "low"
            elif drawdown <= 0.25:
                dd_cat = "moderate"
            else:
                dd_cat = "high"

            combo_key = f"{support_cat}_support_{dd_cat}_dd"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="support_drawdown_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    # =========================================================================
    # Three-way combination implementations
    # =========================================================================

    def _combine_iv_triple(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Triple combination: IV entry + IV change + exit reason."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if (entry_snapshot.iv_rank is None or
                exit_snapshot.iv_change_during_trade is None or
                not exit_snapshot.exit_reason):
                continue

            # IV entry
            iv_rank = entry_snapshot.iv_rank
            iv_entry_cat = "high_iv" if iv_rank > 0.50 else "normal_iv"

            # IV change
            iv_change = exit_snapshot.iv_change_during_trade
            if iv_change < -0.10:
                iv_change_cat = "crushed"
            else:
                iv_change_cat = "stable_or_up"

            # Exit reason (simplified)
            exit_cat = "profit_target" if exit_snapshot.exit_reason == "profit_target" else "other_exit"

            combo_key = f"{iv_entry_cat}_{iv_change_cat}_{exit_cat}"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="iv_triple_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_rsi_momentum_quality(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Triple combination: RSI + momentum + trade quality."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if (entry_snapshot.rsi_14 is None or
                exit_snapshot.trade_quality_score is None):
                continue

            snapshots = self._get_position_snapshots(trade)
            if len(snapshots) < 3:
                continue

            # RSI
            rsi = entry_snapshot.rsi_14
            rsi_cat = "oversold" if rsi < 30 else "neutral_or_over"

            # Momentum
            pnl_values = [s.current_pnl_pct for s in snapshots if s.current_pnl_pct is not None]
            if len(pnl_values) < 3:
                continue

            mid = len(pnl_values) // 2
            first_half_rate = (pnl_values[mid] - pnl_values[0]) / mid if mid > 0 else 0
            second_half_rate = (pnl_values[-1] - pnl_values[mid]) / (len(pnl_values) - mid)

            momentum_cat = "accelerating" if second_half_rate > first_half_rate * 1.2 else "other"

            # Quality
            quality = exit_snapshot.trade_quality_score
            quality_cat = "high_quality" if quality >= 0.7 else "lower_quality"

            combo_key = f"{rsi_cat}_{momentum_cat}_{quality_cat}"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="rsi_momentum_quality_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def _combine_trend_greeks_drawdown(self, trades: list[Trade]) -> list[DetectedPattern]:
        """Triple combination: Trend + Greeks evolution + drawdown."""
        patterns = []
        combinations = defaultdict(list)

        for trade in trades:
            entry_snapshot = self._get_entry_snapshot(trade)
            exit_snapshot = self._get_exit_snapshot(trade)

            if not entry_snapshot or not exit_snapshot:
                continue
            if (not entry_snapshot.trend_direction or
                exit_snapshot.max_drawdown_pct is None):
                continue

            snapshots = self._get_position_snapshots(trade)
            if len(snapshots) < 2:
                continue

            # Trend
            trend_cat = "uptrend" if entry_snapshot.trend_direction == "uptrend" else "other_trend"

            # Greeks evolution
            deltas = [abs(s.delta) for s in snapshots if s.delta is not None]
            if len(deltas) < 2:
                continue

            delta_change = deltas[-1] - deltas[0]
            greeks_cat = "favorable" if delta_change < -0.05 else "unfavorable"

            # Drawdown
            drawdown = abs(exit_snapshot.max_drawdown_pct)
            dd_cat = "low_dd" if drawdown < 0.10 else "higher_dd"

            combo_key = f"{trend_cat}_{greeks_cat}_{dd_cat}"
            combinations[combo_key].append(trade)

        # Create patterns
        for combo_name, combo_trades in combinations.items():
            if len(combo_trades) < self.min_samples:
                continue

            wins = sum(1 for t in combo_trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(combo_trades)
            rois = [t.roi for t in combo_trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="trend_greeks_drawdown_combo",
                pattern_name=combo_name,
                pattern_value=combo_name.replace('_', ' ').title(),
                sample_size=len(combo_trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(combo_trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_all_combinations(self) -> list[DetectedPattern]:
        """Run all combination analyses and return combined results.

        Returns:
            List of all detected combination patterns
        """
        logger.info("Running comprehensive multi-dimensional pattern combination analysis")

        patterns = []

        # Two-way combinations
        patterns.extend(self.analyze_entry_trajectory_combinations())
        patterns.extend(self.analyze_entry_exit_combinations())

        # Three-way combinations
        patterns.extend(self.analyze_triple_combinations())

        logger.info(f"Detected {len(patterns)} combination patterns")

        return patterns

    # =========================================================================
    # Helper methods
    # =========================================================================

    def _get_trades_with_complete_data(self) -> list[Trade]:
        """Get closed trades that have complete data across all dimensions.

        Returns:
            List of trades with entry snapshot, position snapshots, and exit snapshot
        """
        closed_trades = (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .all()
        )

        complete_trades = []

        for trade in closed_trades:
            # Check for entry snapshot
            entry_snapshot = (
                self.db.query(TradeEntrySnapshot)
                .filter(TradeEntrySnapshot.trade_id == trade.id)
                .first()
            )

            # Check for exit snapshot
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if entry_snapshot and exit_snapshot:
                complete_trades.append(trade)

        return complete_trades

    def _get_entry_snapshot(self, trade: Trade) -> Optional[TradeEntrySnapshot]:
        """Get entry snapshot for a trade."""
        return (
            self.db.query(TradeEntrySnapshot)
            .filter(TradeEntrySnapshot.trade_id == trade.id)
            .first()
        )

    def _get_exit_snapshot(self, trade: Trade) -> Optional[TradeExitSnapshot]:
        """Get exit snapshot for a trade."""
        return (
            self.db.query(TradeExitSnapshot)
            .filter(TradeExitSnapshot.trade_id == trade.id)
            .first()
        )

    def _get_position_snapshots(self, trade: Trade) -> list[PositionSnapshot]:
        """Get position snapshots for a trade."""
        return (
            self.db.query(PositionSnapshot)
            .filter(PositionSnapshot.trade_id == trade.id)
            .order_by(PositionSnapshot.snapshot_date)
            .all()
        )
