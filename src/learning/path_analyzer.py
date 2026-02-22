"""Path Analyzer for analyzing trade trajectories using position snapshots.

This module analyzes how trades evolve over time by examining daily position
snapshots. It detects patterns in exit timing, profit reversals, momentum,
Greeks evolution, and proximity to strike.

Phase 3.3: Position Snapshot Path Analysis
"""

from collections import defaultdict
from datetime import datetime
from typing import Optional

import numpy as np
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import PositionSnapshot, Trade, TradeExitSnapshot
from src.learning.models import DetectedPattern


class PathAnalyzer:
    """Analyzes trade trajectories using position snapshots.

    Uses daily position snapshot data to understand:
    - When was the optimal exit point?
    - Do profits reverse after peaking?
    - What Greeks evolution predicts success?
    - How close to strike is too close?
    """

    def __init__(self, db_session: Session, min_sample_size: int = 30):
        """Initialize path analyzer.

        Args:
            db_session: Database session for querying data
            min_sample_size: Minimum trades needed for valid pattern
        """
        self.db = db_session
        self.min_samples = min_sample_size

    def analyze_exit_timing_efficiency(self) -> list[DetectedPattern]:
        """Analyze if we're exiting at optimal times.

        For each closed trade with position snapshots:
        - Find when max profit occurred
        - Compare to actual exit time
        - Calculate efficiency = actual_profit / max_profit

        Buckets:
        - Excellent (>80% efficiency): Captured most of max profit
        - Good (60-80%): Reasonable profit capture
        - Poor (<60%): Left significant profit on table
        """
        logger.debug("Analyzing exit timing efficiency patterns")

        # Get all trades with exit snapshots and position snapshots
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .all()
        )

        if not trades_with_exit:
            logger.debug("No trades with exit data for timing analysis")
            return []

        # Analyze efficiency for each trade
        efficiency_data = []

        for trade in trades_with_exit:
            # Get position snapshots for this trade
            snapshots = (
                self.db.query(PositionSnapshot)
                .filter(PositionSnapshot.trade_id == trade.id)
                .order_by(PositionSnapshot.snapshot_date)
                .all()
            )

            if not snapshots:
                continue

            # Get exit snapshot
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot or exit_snapshot.max_profit_captured_pct is None:
                continue

            efficiency_data.append({
                'trade': trade,
                'efficiency': exit_snapshot.max_profit_captured_pct,
                'snapshots': snapshots,
            })

        if len(efficiency_data) < self.min_samples:
            logger.debug(
                f"Insufficient trades with efficiency data: "
                f"{len(efficiency_data)} < {self.min_samples}"
            )
            return []

        # Bucket trades by efficiency
        excellent = [d for d in efficiency_data if d['efficiency'] >= 0.8]
        good = [d for d in efficiency_data if 0.6 <= d['efficiency'] < 0.8]
        poor = [d for d in efficiency_data if d['efficiency'] < 0.6]

        patterns = []
        buckets = [
            ("excellent_exit_timing", excellent, "Captured >80% of max profit"),
            ("good_exit_timing", good, "Captured 60-80% of max profit"),
            ("poor_exit_timing", poor, "Captured <60% of max profit"),
        ]

        for bucket_name, bucket_data, description in buckets:
            if len(bucket_data) < self.min_samples:
                continue

            trades = [d['trade'] for d in bucket_data]
            avg_efficiency = np.mean([d['efficiency'] for d in bucket_data])

            # Calculate win rate and ROI
            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="exit_efficiency",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,  # Will be set by pattern detector
                baseline_roi=None,
                p_value=1.0,  # Path patterns don't use statistical comparison
                effect_size=avg_efficiency,  # Use efficiency as effect size
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def detect_reversal_patterns(self) -> list[DetectedPattern]:
        """Detect trades where profits reversed after hitting max.

        Reversal: Trade hit significant profit (>30%) then gave it back.

        Categories:
        - Strong reversal: Hit >50% profit, ended <20%
        - Moderate reversal: Hit >30% profit, ended <10%
        - No reversal: Profit steady or increased to exit
        """
        logger.debug("Analyzing profit reversal patterns")

        trades_with_snapshots = self._get_trades_with_snapshots()

        if len(trades_with_snapshots) < self.min_samples:
            logger.debug("Insufficient trades with snapshots for reversal analysis")
            return []

        # Categorize trades
        strong_reversals = []
        moderate_reversals = []
        no_reversals = []

        for trade, snapshots in trades_with_snapshots:
            if not snapshots or trade.profit_pct is None:
                continue

            # Find max profit during trade
            max_pnl_pct = max((s.current_pnl_pct for s in snapshots if s.current_pnl_pct), default=0)
            final_pnl_pct = trade.profit_pct

            # Categorize
            if max_pnl_pct > 0.5 and final_pnl_pct < 0.2:
                strong_reversals.append(trade)
            elif max_pnl_pct > 0.3 and final_pnl_pct < 0.1:
                moderate_reversals.append(trade)
            else:
                no_reversals.append(trade)

        patterns = []
        buckets = [
            ("strong_reversal", strong_reversals, "Hit >50% profit, ended <20%"),
            ("moderate_reversal", moderate_reversals, "Hit >30% profit, ended <10%"),
            ("no_reversal", no_reversals, "Profit steady or increased"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="profit_reversal",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def detect_momentum_patterns(self) -> list[DetectedPattern]:
        """Detect P&L momentum patterns.

        Momentum = rate of profit acceleration/deceleration

        Categories:
        - Accelerating: Profit increased faster over time
        - Steady: Consistent profit accumulation
        - Plateauing: Profit flattened before exit
        """
        logger.debug("Analyzing P&L momentum patterns")

        trades_with_snapshots = self._get_trades_with_snapshots()

        if len(trades_with_snapshots) < self.min_samples:
            logger.debug("Insufficient trades for momentum analysis")
            return []

        accelerating = []
        steady = []
        plateauing = []

        for trade, snapshots in trades_with_snapshots:
            if len(snapshots) < 3:  # Need at least 3 snapshots for momentum
                continue

            # Calculate momentum (change in P&L% over time)
            pnl_values = [s.current_pnl_pct for s in snapshots if s.current_pnl_pct is not None]

            if len(pnl_values) < 3:
                continue

            # Simple momentum: compare first half vs second half rate
            mid = len(pnl_values) // 2
            first_half_rate = (pnl_values[mid] - pnl_values[0]) / mid if mid > 0 else 0
            second_half_rate = (pnl_values[-1] - pnl_values[mid]) / (len(pnl_values) - mid)

            # Categorize
            if second_half_rate > first_half_rate * 1.2:
                accelerating.append(trade)
            elif second_half_rate < first_half_rate * 0.5:
                plateauing.append(trade)
            else:
                steady.append(trade)

        patterns = []
        buckets = [
            ("accelerating_momentum", accelerating, "Profit accelerated over time"),
            ("steady_momentum", steady, "Consistent profit accumulation"),
            ("plateauing_momentum", plateauing, "Profit flattened before exit"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="pnl_momentum",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_greeks_evolution(self) -> list[DetectedPattern]:
        """Analyze Greeks evolution patterns.

        Focus on delta evolution:
        - Delta acceleration: Delta moved away from strike (favorable)
        - Delta stable: Delta stayed relatively constant
        - Delta deterioration: Delta moved toward strike (unfavorable)
        """
        logger.debug("Analyzing Greeks evolution patterns")

        trades_with_snapshots = self._get_trades_with_snapshots()

        if len(trades_with_snapshots) < self.min_samples:
            logger.debug("Insufficient trades for Greeks analysis")
            return []

        delta_acceleration = []
        delta_stable = []
        delta_deterioration = []

        for trade, snapshots in trades_with_snapshots:
            if len(snapshots) < 2:
                continue

            # Get delta values (absolute value since we're short puts)
            deltas = [abs(s.delta) for s in snapshots if s.delta is not None]

            if len(deltas) < 2:
                continue

            # Delta change (for short puts, decreasing abs delta is good)
            delta_change = deltas[-1] - deltas[0]

            # Categorize
            if delta_change < -0.05:  # Delta decreased significantly (good)
                delta_acceleration.append(trade)
            elif abs(delta_change) <= 0.05:  # Delta stable
                delta_stable.append(trade)
            else:  # Delta increased (bad - moving toward ATM)
                delta_deterioration.append(trade)

        patterns = []
        buckets = [
            ("delta_favorable", delta_acceleration, "Delta moved away from strike"),
            ("delta_stable", delta_stable, "Delta remained stable"),
            ("delta_unfavorable", delta_deterioration, "Delta moved toward strike"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="greeks_evolution",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def detect_proximity_risk_patterns(self) -> list[DetectedPattern]:
        """Analyze how proximity to strike affects outcomes.

        Uses closest_to_strike_pct from exit snapshot.

        Categories:
        - Safe distance: Never got closer than 10% to strike
        - Moderate proximity: Got within 5-10% of strike
        - Dangerous proximity: Got within 5% of strike
        """
        logger.debug("Analyzing proximity to strike patterns")

        # Get trades with exit snapshots (has closest_to_strike_pct)
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.closest_to_strike_pct.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with proximity data")
            return []

        # Get exit snapshots and categorize
        safe_distance = []
        moderate_proximity = []
        dangerous_proximity = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            closest_pct = exit_snapshot.closest_to_strike_pct

            if closest_pct > 10.0:
                safe_distance.append(trade)
            elif closest_pct > 5.0:
                moderate_proximity.append(trade)
            else:
                dangerous_proximity.append(trade)

        patterns = []
        buckets = [
            ("safe_distance", safe_distance, "Never closer than 10% to strike"),
            ("moderate_proximity", moderate_proximity, "Got within 5-10% of strike"),
            ("dangerous_proximity", dangerous_proximity, "Got within 5% of strike"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="proximity_risk",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    # =========================================================================
    # Phase 3.4: Exit Quality Analysis
    # =========================================================================

    def analyze_by_exit_reason(self) -> list[DetectedPattern]:
        """Analyze performance by exit reason.

        Exit reasons:
        - profit_target: Exited at target profit
        - stop_loss: Stopped out at loss limit
        - expiration: Held to expiration
        - manual: Manually closed

        For naked puts, profit_target exits should perform best.
        """
        logger.debug("Analyzing patterns by exit reason")

        # Get trades with exit snapshots
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.exit_reason.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with exit reason data")
            return []

        # Group by exit reason
        by_reason = defaultdict(list)
        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )
            if exit_snapshot:
                by_reason[exit_snapshot.exit_reason].append(trade)

        patterns = []

        for reason, trades in by_reason.items():
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="exit_reason",
                pattern_name=f"exit_{reason}",
                pattern_value=reason.replace('_', ' ').title(),
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_trade_quality(self) -> list[DetectedPattern]:
        """Analyze performance by trade quality score.

        Trade quality score (0-1) measures execution quality.
        Higher scores indicate better trade management.

        Buckets:
        - High quality: Score >= 0.7
        - Medium quality: Score 0.4-0.7
        - Low quality: Score < 0.4
        """
        logger.debug("Analyzing patterns by trade quality score")

        # Get trades with quality scores
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.trade_quality_score.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with quality score data")
            return []

        # Bucket by quality
        high_quality = []
        medium_quality = []
        low_quality = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            score = exit_snapshot.trade_quality_score

            if score >= 0.7:
                high_quality.append(trade)
            elif score >= 0.4:
                medium_quality.append(trade)
            else:
                low_quality.append(trade)

        patterns = []
        buckets = [
            ("high_quality", high_quality, "Quality Score >= 0.7"),
            ("medium_quality", medium_quality, "Quality Score 0.4-0.7"),
            ("low_quality", low_quality, "Quality Score < 0.4"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="trade_quality",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_risk_adjusted_return(self) -> list[DetectedPattern]:
        """Analyze performance by risk-adjusted return.

        Risk-adjusted return = ROI / max_drawdown
        Measures return relative to maximum risk taken.

        Buckets:
        - Excellent: Risk-adjusted return > 3.0
        - Good: Risk-adjusted return 1.5-3.0
        - Poor: Risk-adjusted return < 1.5
        """
        logger.debug("Analyzing patterns by risk-adjusted return")

        # Get trades with risk-adjusted return
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.risk_adjusted_return.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with risk-adjusted return data")
            return []

        # Bucket by risk-adjusted return
        excellent = []
        good = []
        poor = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            rar = exit_snapshot.risk_adjusted_return

            if rar > 3.0:
                excellent.append(trade)
            elif rar > 1.5:
                good.append(trade)
            else:
                poor.append(trade)

        patterns = []
        buckets = [
            ("excellent_risk_adjusted", excellent, "Risk-Adjusted Return > 3.0"),
            ("good_risk_adjusted", good, "Risk-Adjusted Return 1.5-3.0"),
            ("poor_risk_adjusted", poor, "Risk-Adjusted Return < 1.5"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="risk_adjusted_return",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_iv_change(self) -> list[DetectedPattern]:
        """Analyze impact of IV changes during trade.

        IV change = exit_iv - entry_iv

        Categories:
        - IV crushed: IV decreased significantly (< -10%)
        - IV stable: IV change between -10% and +10%
        - IV expanded: IV increased significantly (> +10%)

        For naked puts, IV crush is favorable (option value decreases).
        """
        logger.debug("Analyzing patterns by IV change during trade")

        # Get trades with IV change data
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.iv_change_during_trade.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with IV change data")
            return []

        # Bucket by IV change
        iv_crushed = []
        iv_stable = []
        iv_expanded = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            iv_change = exit_snapshot.iv_change_during_trade

            if iv_change < -0.10:
                iv_crushed.append(trade)
            elif iv_change <= 0.10:
                iv_stable.append(trade)
            else:
                iv_expanded.append(trade)

        patterns = []
        buckets = [
            ("iv_crushed", iv_crushed, "IV Decreased > 10%"),
            ("iv_stable", iv_stable, "IV Change -10% to +10%"),
            ("iv_expanded", iv_expanded, "IV Increased > 10%"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="iv_change",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_stock_movement(self) -> list[DetectedPattern]:
        """Analyze correlation between stock movement and outcomes.

        Stock movement during trade (percentage change).

        Categories:
        - Strong upward: Stock up > 5%
        - Moderate upward: Stock up 2-5%
        - Neutral: Stock change -2% to +2%
        - Moderate downward: Stock down 2-5%
        - Strong downward: Stock down > 5%

        For naked puts, upward movement is favorable (option goes OTM).
        """
        logger.debug("Analyzing patterns by stock movement")

        # Get trades with stock movement data
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.stock_change_during_trade_pct.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with stock movement data")
            return []

        # Bucket by stock movement
        strong_upward = []
        moderate_upward = []
        neutral = []
        moderate_downward = []
        strong_downward = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            stock_change = exit_snapshot.stock_change_during_trade_pct

            if stock_change > 5.0:
                strong_upward.append(trade)
            elif stock_change > 2.0:
                moderate_upward.append(trade)
            elif stock_change >= -2.0:
                neutral.append(trade)
            elif stock_change >= -5.0:
                moderate_downward.append(trade)
            else:
                strong_downward.append(trade)

        patterns = []
        buckets = [
            ("stock_strong_up", strong_upward, "Stock Up > 5%"),
            ("stock_moderate_up", moderate_upward, "Stock Up 2-5%"),
            ("stock_neutral", neutral, "Stock Change -2% to +2%"),
            ("stock_moderate_down", moderate_downward, "Stock Down 2-5%"),
            ("stock_strong_down", strong_downward, "Stock Down > 5%"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="stock_movement",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_vix_change(self) -> list[DetectedPattern]:
        """Analyze impact of VIX changes during trade.

        VIX change = vix_at_exit - vix_at_entry

        Categories:
        - VIX declined: VIX down > 2 points (calmer markets)
        - VIX stable: VIX change -2 to +2 points
        - VIX spiked: VIX up > 2 points (fear increased)

        For naked puts, declining VIX is favorable (less volatility risk).
        """
        logger.debug("Analyzing patterns by VIX change")

        # Get trades with VIX change data
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.vix_change_during_trade.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with VIX change data")
            return []

        # Bucket by VIX change
        vix_declined = []
        vix_stable = []
        vix_spiked = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            vix_change = exit_snapshot.vix_change_during_trade

            if vix_change < -2.0:
                vix_declined.append(trade)
            elif vix_change <= 2.0:
                vix_stable.append(trade)
            else:
                vix_spiked.append(trade)

        patterns = []
        buckets = [
            ("vix_declined", vix_declined, "VIX Declined > 2 Points"),
            ("vix_stable", vix_stable, "VIX Change -2 to +2 Points"),
            ("vix_spiked", vix_spiked, "VIX Spiked > 2 Points"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="vix_change",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_by_max_drawdown(self) -> list[DetectedPattern]:
        """Analyze drawdown tolerance patterns.

        Max drawdown measures maximum unrealized loss during trade.

        Buckets:
        - Low drawdown: Max drawdown < 10%
        - Moderate drawdown: Max drawdown 10-25%
        - High drawdown: Max drawdown > 25%

        Higher drawdowns indicate more risk taken during trade.
        """
        logger.debug("Analyzing patterns by maximum drawdown")

        # Get trades with max drawdown data
        trades_with_exit = (
            self.db.query(Trade)
            .join(TradeExitSnapshot, Trade.id == TradeExitSnapshot.trade_id)
            .filter(Trade.exit_date.isnot(None))
            .filter(TradeExitSnapshot.max_drawdown_pct.isnot(None))
            .all()
        )

        if len(trades_with_exit) < self.min_samples:
            logger.debug("Insufficient trades with drawdown data")
            return []

        # Bucket by max drawdown
        low_dd = []
        moderate_dd = []
        high_dd = []

        for trade in trades_with_exit:
            exit_snapshot = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade.id)
                .first()
            )

            if not exit_snapshot:
                continue

            drawdown = abs(exit_snapshot.max_drawdown_pct)  # Ensure positive

            if drawdown < 0.10:
                low_dd.append(trade)
            elif drawdown <= 0.25:
                moderate_dd.append(trade)
            else:
                high_dd.append(trade)

        patterns = []
        buckets = [
            ("low_drawdown", low_dd, "Max Drawdown < 10%"),
            ("moderate_drawdown", moderate_dd, "Max Drawdown 10-25%"),
            ("high_drawdown", high_dd, "Max Drawdown > 25%"),
        ]

        for bucket_name, trades, description in buckets:
            if len(trades) < self.min_samples:
                continue

            wins = sum(1 for t in trades if t.profit_loss and t.profit_loss > 0)
            win_rate = wins / len(trades)
            rois = [t.roi for t in trades if t.roi is not None]
            avg_roi = np.mean(rois) if rois else 0.0

            pattern = DetectedPattern(
                pattern_type="max_drawdown",
                pattern_name=bucket_name,
                pattern_value=description,
                sample_size=len(trades),
                win_rate=win_rate,
                avg_roi=avg_roi,
                baseline_win_rate=None,
                baseline_roi=None,
                p_value=1.0,
                effect_size=0.0,
                confidence=min(len(trades) / self.min_samples, 1.0),
                date_detected=datetime.now(),
            )

            patterns.append(pattern)

        return patterns

    def analyze_all_paths(self) -> list[DetectedPattern]:
        """Run all path analyses and return combined results.

        Includes Phase 3.3 and Phase 3.4 analyses.

        Returns:
            List of all detected path patterns
        """
        logger.info("Running comprehensive path analysis")

        patterns = []

        # Phase 3.3: Position snapshot trajectory analysis
        patterns.extend(self.analyze_exit_timing_efficiency())
        patterns.extend(self.detect_reversal_patterns())
        patterns.extend(self.detect_momentum_patterns())
        patterns.extend(self.analyze_greeks_evolution())
        patterns.extend(self.detect_proximity_risk_patterns())

        # Phase 3.4: Exit quality analysis
        patterns.extend(self.analyze_by_exit_reason())
        patterns.extend(self.analyze_by_trade_quality())
        patterns.extend(self.analyze_by_risk_adjusted_return())
        patterns.extend(self.analyze_by_iv_change())
        patterns.extend(self.analyze_by_stock_movement())
        patterns.extend(self.analyze_by_vix_change())
        patterns.extend(self.analyze_by_max_drawdown())

        logger.info(f"Detected {len(patterns)} path patterns")

        return patterns

    # Helper methods

    def _get_trades_with_snapshots(self) -> list[tuple[Trade, list[PositionSnapshot]]]:
        """Get all closed trades with their position snapshots.

        Returns:
            List of (trade, snapshots) tuples
        """
        closed_trades = (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .all()
        )

        trades_with_snapshots = []

        for trade in closed_trades:
            snapshots = (
                self.db.query(PositionSnapshot)
                .filter(PositionSnapshot.trade_id == trade.id)
                .order_by(PositionSnapshot.snapshot_date)
                .all()
            )

            if snapshots:
                trades_with_snapshots.append((trade, snapshots))

        return trades_with_snapshots
