"""Alpha Decay Monitor — early warning for strategy degradation.

Tracks rolling performance metrics, regime-specific performance,
and CUSUM change detection to flag edge erosion before drawdowns.

Phase B of the Learning Module Improvement Plan.
"""

import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import sqlalchemy as sa
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import LearningHistory, Trade
from src.utils.timezone import utc_now


@dataclass
class RollingMetrics:
    """Performance metrics over a rolling window."""

    window_days: int
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    trade_count: int = 0
    win_rate: float = 0.0
    avg_roi: float = 0.0
    total_pnl: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    loss_streak: int = 0

    def to_dict(self) -> dict:
        return {
            "window_days": self.window_days,
            "start_date": self.start_date.isoformat() if self.start_date else None,
            "end_date": self.end_date.isoformat() if self.end_date else None,
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "avg_roi": round(self.avg_roi, 4),
            "total_pnl": round(self.total_pnl, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
            "max_drawdown": round(self.max_drawdown, 4),
            "loss_streak": self.loss_streak,
        }


@dataclass
class RegimePerformance:
    """Performance metrics for a specific VIX regime."""

    regime: str
    trade_count: int = 0
    win_rate: float = 0.0
    avg_roi: float = 0.0
    avg_pnl: float = 0.0
    sharpe_ratio: float = 0.0

    def to_dict(self) -> dict:
        return {
            "regime": self.regime,
            "trade_count": self.trade_count,
            "win_rate": round(self.win_rate, 4),
            "avg_roi": round(self.avg_roi, 4),
            "avg_pnl": round(self.avg_pnl, 2),
            "sharpe_ratio": round(self.sharpe_ratio, 3),
        }


@dataclass
class CUSUMAlert:
    """Alert from CUSUM change detection."""

    direction: str  # "degradation" or "improvement"
    cusum_value: float
    threshold: float
    triggered_at: Optional[datetime] = None
    consecutive_trades: int = 0

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "cusum_value": round(self.cusum_value, 4),
            "threshold": round(self.threshold, 4),
            "triggered_at": self.triggered_at.isoformat() if self.triggered_at else None,
            "consecutive_trades": self.consecutive_trades,
        }


@dataclass
class AlphaDecayReport:
    """Complete alpha decay analysis report."""

    timestamp: datetime
    rolling_metrics: list[RollingMetrics] = field(default_factory=list)
    regime_performance: list[RegimePerformance] = field(default_factory=list)
    cusum_alerts: list[CUSUMAlert] = field(default_factory=list)
    overall_health: str = "HEALTHY"  # HEALTHY, WATCH, WARNING, CRITICAL
    health_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp.isoformat(),
            "overall_health": self.overall_health,
            "health_reasons": self.health_reasons,
            "rolling_metrics": [m.to_dict() for m in self.rolling_metrics],
            "regime_performance": [r.to_dict() for r in self.regime_performance],
            "cusum_alerts": [a.to_dict() for a in self.cusum_alerts],
        }


class AlphaDecayMonitor:
    """Monitors strategy performance for signs of alpha decay.

    Computes rolling metrics, regime splits, and CUSUM statistics
    to provide early warning of strategy degradation.
    """

    # VIX-based regime thresholds
    VIX_REGIMES = {
        "low": (0, 15),
        "normal": (15, 20),
        "elevated": (20, 25),
        "high": (25, 35),
        "extreme": (35, 100),
    }

    def __init__(
        self,
        db_session: Session,
        rolling_windows: Optional[list[int]] = None,
        cusum_threshold: float = 4.0,
        cusum_drift: float = 0.5,
    ):
        """Initialize alpha decay monitor.

        Args:
            db_session: Database session for querying trades
            rolling_windows: List of rolling window sizes in days (default: [30, 90, 365])
            cusum_threshold: CUSUM threshold for alerting (in std devs, default: 4.0)
            cusum_drift: CUSUM drift parameter (allowance before accumulating, default: 0.5)
        """
        self.db = db_session
        self.rolling_windows = rolling_windows or [30, 90, 365]
        self.cusum_threshold = cusum_threshold
        self.cusum_drift = cusum_drift

    def run_analysis(self) -> AlphaDecayReport:
        """Run complete alpha decay analysis.

        Returns:
            AlphaDecayReport with rolling metrics, regime splits, and alerts
        """
        logger.info("Running alpha decay analysis")

        report = AlphaDecayReport(timestamp=utc_now())

        # Get all closed trades sorted chronologically
        trades = self._get_closed_trades()

        if len(trades) < 10:
            logger.warning(f"Only {len(trades)} closed trades — insufficient for decay analysis")
            report.overall_health = "INSUFFICIENT_DATA"
            report.health_reasons.append(f"Only {len(trades)} closed trades")
            return report

        # B1: Rolling performance metrics
        for window in self.rolling_windows:
            metrics = self._compute_rolling_metrics(trades, window)
            report.rolling_metrics.append(metrics)

        # B2: Regime performance splits
        report.regime_performance = self._compute_regime_splits(trades)

        # B3: CUSUM change detection
        report.cusum_alerts = self._run_cusum(trades)

        # Determine overall health
        self._assess_health(report)

        # Persist the report
        self._save_report(report)

        logger.info(
            f"Alpha decay analysis complete: {report.overall_health} "
            f"({len(report.cusum_alerts)} alerts)"
        )

        return report

    def _get_closed_trades(self) -> list[Trade]:
        """Get all closed trades sorted by exit date."""
        return (
            self.db.query(Trade)
            .filter(Trade.exit_date.isnot(None))
            .filter(
                sa.or_(Trade.lifecycle_status.is_(None), Trade.lifecycle_status != "stock_held")
            )
            .order_by(Trade.exit_date.asc())
            .all()
        )

    def _compute_rolling_metrics(self, trades: list[Trade], window_days: int) -> RollingMetrics:
        """Compute performance metrics over a rolling window.

        Args:
            trades: Chronologically sorted closed trades
            window_days: Number of days for the rolling window

        Returns:
            RollingMetrics for the window
        """
        now = utc_now()
        cutoff = now - timedelta(days=window_days)

        window_trades = [t for t in trades if t.exit_date and t.exit_date >= cutoff]

        metrics = RollingMetrics(window_days=window_days)

        if not window_trades:
            return metrics

        metrics.start_date = window_trades[0].exit_date
        metrics.end_date = window_trades[-1].exit_date
        metrics.trade_count = len(window_trades)

        # Win rate
        wins = sum(1 for t in window_trades if t.profit_loss and t.profit_loss > 0)
        metrics.win_rate = wins / len(window_trades)

        # ROI
        rois = [t.roi for t in window_trades if t.roi is not None]
        if rois:
            metrics.avg_roi = float(np.mean(rois))

        # Total P&L
        pnls = [t.profit_loss for t in window_trades if t.profit_loss is not None]
        metrics.total_pnl = sum(pnls)

        # Sharpe ratio (annualized, assuming daily-ish trades)
        if rois and len(rois) > 1:
            roi_std = float(np.std(rois, ddof=1))
            if roi_std > 0:
                # Annualize: multiply by sqrt of trades per year
                trades_per_year = len(rois) * (365 / max(window_days, 1))
                metrics.sharpe_ratio = float(np.mean(rois) / roi_std * np.sqrt(trades_per_year))

        # Max drawdown (cumulative P&L based)
        if pnls:
            cumulative = np.cumsum(pnls)
            running_max = np.maximum.accumulate(cumulative)
            drawdowns = cumulative - running_max
            if len(drawdowns) > 0 and running_max.max() > 0:
                metrics.max_drawdown = float(drawdowns.min() / max(running_max.max(), 1))

        # Current loss streak
        streak = 0
        for t in reversed(window_trades):
            if t.profit_loss is not None and t.profit_loss <= 0:
                streak += 1
            else:
                break
        metrics.loss_streak = streak

        return metrics

    def _compute_regime_splits(self, trades: list[Trade]) -> list[RegimePerformance]:
        """Compute performance metrics split by VIX regime.

        Uses vix_at_entry to bucket trades into VIX regimes, then
        computes win rate, avg ROI, and Sharpe per regime.

        Args:
            trades: All closed trades

        Returns:
            List of RegimePerformance, one per regime with trades
        """
        regime_trades: dict[str, list[Trade]] = defaultdict(list)

        for t in trades:
            if t.vix_at_entry is not None:
                regime = self._classify_vix_regime(t.vix_at_entry)
                regime_trades[regime].append(t)

        results = []
        for regime_name in ["low", "normal", "elevated", "high", "extreme"]:
            bucket = regime_trades.get(regime_name, [])
            if not bucket:
                continue

            rp = RegimePerformance(regime=regime_name, trade_count=len(bucket))

            wins = sum(1 for t in bucket if t.profit_loss and t.profit_loss > 0)
            rp.win_rate = wins / len(bucket)

            rois = [t.roi for t in bucket if t.roi is not None]
            if rois:
                rp.avg_roi = float(np.mean(rois))
                if len(rois) > 1:
                    std = float(np.std(rois, ddof=1))
                    if std > 0:
                        rp.sharpe_ratio = float(np.mean(rois) / std)

            pnls = [t.profit_loss for t in bucket if t.profit_loss is not None]
            if pnls:
                rp.avg_pnl = float(np.mean(pnls))

            results.append(rp)

        return results

    def _classify_vix_regime(self, vix: float) -> str:
        """Classify a VIX value into a regime bucket."""
        for regime, (lo, hi) in self.VIX_REGIMES.items():
            if lo <= vix < hi:
                return regime
        return "extreme"

    def _run_cusum(self, trades: list[Trade]) -> list[CUSUMAlert]:
        """Run CUSUM (Cumulative Sum) change detection on trade ROIs.

        CUSUM detects shifts in the mean of a process by accumulating
        deviations from the target mean. A drift parameter provides
        tolerance for normal variation.

        Args:
            trades: Chronologically sorted closed trades

        Returns:
            List of CUSUMAlert if thresholds breached
        """
        rois = [(t.roi, t.exit_date) for t in trades if t.roi is not None and t.exit_date]

        if len(rois) < 30:
            return []

        values = np.array([r[0] for r in rois])
        dates = [r[1] for r in rois]

        # Use first half as reference period for mean and std
        ref_size = len(values) // 2
        ref_mean = float(np.mean(values[:ref_size]))
        ref_std = float(np.std(values[:ref_size], ddof=1))

        if ref_std == 0:
            return []

        # Standardize
        standardized = (values - ref_mean) / ref_std

        # CUSUM: track positive (improvement) and negative (degradation) shifts
        cusum_pos = np.zeros(len(standardized))
        cusum_neg = np.zeros(len(standardized))

        for i in range(1, len(standardized)):
            cusum_pos[i] = max(0, cusum_pos[i - 1] + standardized[i] - self.cusum_drift)
            cusum_neg[i] = max(0, cusum_neg[i - 1] - standardized[i] - self.cusum_drift)

        alerts = []

        # Check for degradation (negative shift)
        neg_max_idx = int(np.argmax(cusum_neg))
        if cusum_neg[neg_max_idx] > self.cusum_threshold:
            # Count consecutive trades above threshold
            consecutive = 0
            for i in range(len(cusum_neg) - 1, -1, -1):
                if cusum_neg[i] > self.cusum_threshold:
                    consecutive += 1
                else:
                    break

            alerts.append(CUSUMAlert(
                direction="degradation",
                cusum_value=float(cusum_neg[neg_max_idx]),
                threshold=self.cusum_threshold,
                triggered_at=dates[neg_max_idx] if neg_max_idx < len(dates) else None,
                consecutive_trades=consecutive,
            ))

        # Check for improvement (positive shift)
        pos_max_idx = int(np.argmax(cusum_pos))
        if cusum_pos[pos_max_idx] > self.cusum_threshold:
            consecutive = 0
            for i in range(len(cusum_pos) - 1, -1, -1):
                if cusum_pos[i] > self.cusum_threshold:
                    consecutive += 1
                else:
                    break

            alerts.append(CUSUMAlert(
                direction="improvement",
                cusum_value=float(cusum_pos[pos_max_idx]),
                threshold=self.cusum_threshold,
                triggered_at=dates[pos_max_idx] if pos_max_idx < len(dates) else None,
                consecutive_trades=consecutive,
            ))

        return alerts

    def _assess_health(self, report: AlphaDecayReport) -> None:
        """Assess overall strategy health from the analysis components.

        Health levels:
        - HEALTHY: No concerning signals
        - WATCH: Minor signals worth monitoring
        - WARNING: Clear degradation signals
        - CRITICAL: Severe degradation, consider pausing
        """
        reasons = []
        severity = 0  # 0=healthy, 1=watch, 2=warning, 3=critical

        # Check 30-day rolling metrics
        recent = next((m for m in report.rolling_metrics if m.window_days == 30), None)
        historical = next((m for m in report.rolling_metrics if m.window_days == 365), None)

        if recent and recent.trade_count >= 5:
            # Win rate drop
            if historical and historical.trade_count >= 30:
                wr_delta = recent.win_rate - historical.win_rate
                if wr_delta < -0.15:
                    reasons.append(f"30d win rate {recent.win_rate:.0%} vs historical {historical.win_rate:.0%} (Δ{wr_delta:+.0%})")
                    severity = max(severity, 3)
                elif wr_delta < -0.10:
                    reasons.append(f"30d win rate dropping: {recent.win_rate:.0%} vs {historical.win_rate:.0%}")
                    severity = max(severity, 2)
                elif wr_delta < -0.05:
                    reasons.append(f"30d win rate slightly below historical: {recent.win_rate:.0%} vs {historical.win_rate:.0%}")
                    severity = max(severity, 1)

                # ROI compression
                if historical.avg_roi > 0:
                    roi_ratio = recent.avg_roi / historical.avg_roi if historical.avg_roi != 0 else 1.0
                    if roi_ratio < 0.3:
                        reasons.append(f"30d ROI severely compressed: {recent.avg_roi:.1%} vs historical {historical.avg_roi:.1%}")
                        severity = max(severity, 3)
                    elif roi_ratio < 0.5:
                        reasons.append(f"30d ROI compressed: {recent.avg_roi:.1%} vs historical {historical.avg_roi:.1%}")
                        severity = max(severity, 2)

            # Loss streak
            if recent.loss_streak >= 5:
                reasons.append(f"Current loss streak: {recent.loss_streak} consecutive losses")
                severity = max(severity, 2)
            elif recent.loss_streak >= 3:
                reasons.append(f"Loss streak: {recent.loss_streak} consecutive losses")
                severity = max(severity, 1)

            # Sharpe
            if recent.sharpe_ratio < 0 and recent.trade_count >= 10:
                reasons.append(f"30d Sharpe ratio negative: {recent.sharpe_ratio:.2f}")
                severity = max(severity, 2)

        # Check CUSUM alerts
        for alert in report.cusum_alerts:
            if alert.direction == "degradation":
                if alert.cusum_value > self.cusum_threshold * 2:
                    reasons.append(f"CUSUM degradation signal: {alert.cusum_value:.1f} (threshold: {alert.threshold:.1f})")
                    severity = max(severity, 3)
                else:
                    reasons.append(f"CUSUM shift detected: {alert.cusum_value:.1f}")
                    severity = max(severity, 2)

        # Check regime-specific issues
        for rp in report.regime_performance:
            if rp.trade_count >= 20 and rp.win_rate < 0.50:
                reasons.append(f"Losing strategy in {rp.regime} VIX regime: {rp.win_rate:.0%} win rate ({rp.trade_count} trades)")
                severity = max(severity, 2)

        # Set health level
        health_map = {0: "HEALTHY", 1: "WATCH", 2: "WARNING", 3: "CRITICAL"}
        report.overall_health = health_map.get(severity, "HEALTHY")
        report.health_reasons = reasons

        if not reasons:
            report.health_reasons = ["All metrics within normal ranges"]

    def _save_report(self, report: AlphaDecayReport) -> None:
        """Persist alpha decay report to learning_history."""
        event = LearningHistory(
            event_type="alpha_decay_analysis",
            event_date=utc_now(),
            reasoning=json.dumps(report.to_dict()),
            pattern_name=report.overall_health,
            confidence={"HEALTHY": 1.0, "WATCH": 0.7, "WARNING": 0.4, "CRITICAL": 0.1}.get(
                report.overall_health, 0.5
            ),
        )
        self.db.add(event)
        self.db.commit()

    def get_latest_report(self) -> Optional[AlphaDecayReport]:
        """Get the most recent alpha decay report from the database."""
        event = (
            self.db.query(LearningHistory)
            .filter(LearningHistory.event_type == "alpha_decay_analysis")
            .order_by(LearningHistory.event_date.desc())
            .first()
        )

        if not event or not event.reasoning:
            return None

        try:
            data = json.loads(event.reasoning)
            report = AlphaDecayReport(
                timestamp=datetime.fromisoformat(data["timestamp"]),
                overall_health=data.get("overall_health", "UNKNOWN"),
                health_reasons=data.get("health_reasons", []),
            )

            for m in data.get("rolling_metrics", []):
                report.rolling_metrics.append(RollingMetrics(
                    window_days=m["window_days"],
                    trade_count=m["trade_count"],
                    win_rate=m["win_rate"],
                    avg_roi=m["avg_roi"],
                    total_pnl=m["total_pnl"],
                    sharpe_ratio=m["sharpe_ratio"],
                    max_drawdown=m["max_drawdown"],
                    loss_streak=m["loss_streak"],
                ))

            for r in data.get("regime_performance", []):
                report.regime_performance.append(RegimePerformance(
                    regime=r["regime"],
                    trade_count=r["trade_count"],
                    win_rate=r["win_rate"],
                    avg_roi=r["avg_roi"],
                    avg_pnl=r["avg_pnl"],
                    sharpe_ratio=r["sharpe_ratio"],
                ))

            for a in data.get("cusum_alerts", []):
                report.cusum_alerts.append(CUSUMAlert(
                    direction=a["direction"],
                    cusum_value=a["cusum_value"],
                    threshold=a["threshold"],
                    consecutive_trades=a.get("consecutive_trades", 0),
                ))

            return report
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.warning(f"Failed to parse alpha decay report: {e}")
            return None
