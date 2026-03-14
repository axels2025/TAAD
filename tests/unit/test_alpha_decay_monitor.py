"""Tests for the Alpha Decay Monitor (Phase B)."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from src.learning.alpha_decay_monitor import (
    AlphaDecayMonitor,
    AlphaDecayReport,
    CUSUMAlert,
    RegimePerformance,
    RollingMetrics,
)


def _make_trade(exit_date, profit_loss, roi, vix_at_entry=18.0, market_regime="neutral"):
    """Create a mock trade object."""
    trade = MagicMock()
    trade.exit_date = exit_date
    trade.profit_loss = profit_loss
    trade.roi = roi
    trade.vix_at_entry = vix_at_entry
    trade.market_regime = market_regime
    trade.lifecycle_status = None
    return trade


@pytest.fixture
def mock_db():
    """Create a mock database session."""
    return MagicMock()


@pytest.fixture
def healthy_trades():
    """Generate 200 trades with consistent, uniformly distributed profitability."""
    now = datetime(2026, 3, 14)
    rng = np.random.RandomState(42)
    trades = []
    for i in range(200):
        exit_date = now - timedelta(days=200 - i)
        # 85% win rate, randomly distributed (same distribution throughout)
        is_win = rng.random() < 0.85
        pnl = 50.0 if is_win else -150.0
        roi = 0.10 if is_win else -0.30
        trades.append(_make_trade(exit_date, pnl, roi))
    return trades


@pytest.fixture
def degrading_trades():
    """Generate trades where recent performance degrades."""
    now = datetime(2026, 3, 14)
    trades = []
    # First 70 trades: good (90% win rate)
    for i in range(70):
        exit_date = now - timedelta(days=100 - i)
        is_win = i % 10 < 9
        pnl = 50.0 if is_win else -150.0
        roi = 0.10 if is_win else -0.30
        trades.append(_make_trade(exit_date, pnl, roi))
    # Last 30 trades: degraded (40% win rate)
    for i in range(30):
        exit_date = now - timedelta(days=30 - i)
        is_win = i % 10 < 4
        pnl = 50.0 if is_win else -150.0
        roi = 0.10 if is_win else -0.30
        trades.append(_make_trade(exit_date, pnl, roi))
    return trades


class TestRollingMetrics:
    """Tests for rolling metric computation."""

    def test_30_day_window(self, mock_db, healthy_trades):
        monitor = AlphaDecayMonitor(mock_db, rolling_windows=[30])
        metrics = monitor._compute_rolling_metrics(healthy_trades, 30)

        assert metrics.window_days == 30
        assert metrics.trade_count > 0
        assert 0 <= metrics.win_rate <= 1
        assert metrics.total_pnl != 0

    def test_empty_window(self, mock_db):
        monitor = AlphaDecayMonitor(mock_db, rolling_windows=[30])
        metrics = monitor._compute_rolling_metrics([], 30)

        assert metrics.trade_count == 0
        assert metrics.win_rate == 0

    def test_sharpe_ratio_positive_for_profitable(self, mock_db, healthy_trades):
        monitor = AlphaDecayMonitor(mock_db)
        metrics = monitor._compute_rolling_metrics(healthy_trades, 365)

        assert metrics.sharpe_ratio > 0

    def test_loss_streak_detection(self, mock_db):
        now = datetime(2026, 3, 14)
        trades = [
            _make_trade(now - timedelta(days=5), 50.0, 0.10),
            _make_trade(now - timedelta(days=4), -50.0, -0.10),
            _make_trade(now - timedelta(days=3), -50.0, -0.10),
            _make_trade(now - timedelta(days=2), -50.0, -0.10),
            _make_trade(now - timedelta(days=1), -50.0, -0.10),
        ]
        monitor = AlphaDecayMonitor(mock_db)
        metrics = monitor._compute_rolling_metrics(trades, 30)

        assert metrics.loss_streak == 4

    def test_to_dict_serialization(self):
        m = RollingMetrics(window_days=30, trade_count=50, win_rate=0.85, avg_roi=0.12)
        d = m.to_dict()
        assert d["window_days"] == 30
        assert d["win_rate"] == 0.85


class TestRegimeSplits:
    """Tests for VIX regime performance splits."""

    def test_classifies_vix_regimes(self, mock_db):
        monitor = AlphaDecayMonitor(mock_db)

        assert monitor._classify_vix_regime(12.0) == "low"
        assert monitor._classify_vix_regime(17.5) == "normal"
        assert monitor._classify_vix_regime(22.0) == "elevated"
        assert monitor._classify_vix_regime(30.0) == "high"
        assert monitor._classify_vix_regime(40.0) == "extreme"

    def test_regime_splits_with_mixed_data(self, mock_db):
        now = datetime(2026, 3, 14)
        trades = [
            _make_trade(now - timedelta(days=i), 50.0, 0.10, vix_at_entry=12.0)
            for i in range(20)
        ] + [
            _make_trade(now - timedelta(days=i), -50.0, -0.10, vix_at_entry=30.0)
            for i in range(20, 40)
        ]

        monitor = AlphaDecayMonitor(mock_db)
        splits = monitor._compute_regime_splits(trades)

        assert len(splits) == 2
        low_regime = next(s for s in splits if s.regime == "low")
        high_regime = next(s for s in splits if s.regime == "high")

        assert low_regime.win_rate == 1.0
        assert high_regime.win_rate == 0.0

    def test_regime_with_no_vix_data(self, mock_db):
        now = datetime(2026, 3, 14)
        trades = [_make_trade(now - timedelta(days=i), 50.0, 0.10, vix_at_entry=None) for i in range(10)]

        monitor = AlphaDecayMonitor(mock_db)
        splits = monitor._compute_regime_splits(trades)

        assert len(splits) == 0


class TestCUSUM:
    """Tests for CUSUM change detection."""

    def test_no_alert_for_stable_process(self, mock_db, healthy_trades):
        # Higher threshold avoids false positives from random sampling noise
        monitor = AlphaDecayMonitor(mock_db, cusum_threshold=10.0)
        alerts = monitor._run_cusum(healthy_trades)

        # Stable process shouldn't trigger degradation with a reasonable threshold
        degradation_alerts = [a for a in alerts if a.direction == "degradation"]
        assert len(degradation_alerts) == 0

    def test_detects_degradation(self, mock_db, degrading_trades):
        monitor = AlphaDecayMonitor(mock_db, cusum_threshold=3.0)
        alerts = monitor._run_cusum(degrading_trades)

        degradation_alerts = [a for a in alerts if a.direction == "degradation"]
        assert len(degradation_alerts) >= 1
        assert degradation_alerts[0].cusum_value > 3.0

    def test_insufficient_data(self, mock_db):
        now = datetime(2026, 3, 14)
        trades = [_make_trade(now - timedelta(days=i), 50.0, 0.10) for i in range(10)]

        monitor = AlphaDecayMonitor(mock_db)
        alerts = monitor._run_cusum(trades)

        assert len(alerts) == 0

    def test_alert_serialization(self):
        alert = CUSUMAlert(
            direction="degradation",
            cusum_value=5.2,
            threshold=4.0,
            consecutive_trades=15,
        )
        d = alert.to_dict()
        assert d["direction"] == "degradation"
        assert d["cusum_value"] == 5.2


class TestHealthAssessment:
    """Tests for overall health assessment."""

    def test_healthy_assessment(self, mock_db):
        monitor = AlphaDecayMonitor(mock_db)
        report = AlphaDecayReport(timestamp=datetime.now())

        # Good metrics
        report.rolling_metrics = [
            RollingMetrics(window_days=30, trade_count=20, win_rate=0.85, avg_roi=0.12, sharpe_ratio=1.5),
            RollingMetrics(window_days=365, trade_count=200, win_rate=0.85, avg_roi=0.12, sharpe_ratio=1.5),
        ]
        report.regime_performance = []
        report.cusum_alerts = []

        monitor._assess_health(report)
        assert report.overall_health == "HEALTHY"

    def test_warning_on_win_rate_drop(self, mock_db):
        monitor = AlphaDecayMonitor(mock_db)
        report = AlphaDecayReport(timestamp=datetime.now())

        report.rolling_metrics = [
            RollingMetrics(window_days=30, trade_count=20, win_rate=0.60, avg_roi=0.05, sharpe_ratio=0.5),
            RollingMetrics(window_days=365, trade_count=200, win_rate=0.85, avg_roi=0.12, sharpe_ratio=1.5),
        ]
        report.cusum_alerts = []

        monitor._assess_health(report)
        assert report.overall_health in ("WARNING", "CRITICAL")
        assert any("win rate" in r for r in report.health_reasons)

    def test_critical_on_cusum_degradation(self, mock_db):
        monitor = AlphaDecayMonitor(mock_db, cusum_threshold=4.0)
        report = AlphaDecayReport(timestamp=datetime.now())

        report.rolling_metrics = [
            RollingMetrics(window_days=30, trade_count=20, win_rate=0.80, avg_roi=0.10),
            RollingMetrics(window_days=365, trade_count=200, win_rate=0.85, avg_roi=0.12),
        ]
        report.cusum_alerts = [
            CUSUMAlert(direction="degradation", cusum_value=9.0, threshold=4.0, consecutive_trades=20),
        ]

        monitor._assess_health(report)
        assert report.overall_health == "CRITICAL"

    def test_watch_on_loss_streak(self, mock_db):
        monitor = AlphaDecayMonitor(mock_db)
        report = AlphaDecayReport(timestamp=datetime.now())

        report.rolling_metrics = [
            RollingMetrics(window_days=30, trade_count=20, win_rate=0.80, avg_roi=0.10, loss_streak=3),
            RollingMetrics(window_days=365, trade_count=200, win_rate=0.85, avg_roi=0.12),
        ]
        report.cusum_alerts = []

        monitor._assess_health(report)
        assert report.overall_health == "WATCH"


class TestFullAnalysis:
    """Tests for the complete analysis pipeline."""

    def test_run_analysis_insufficient_data(self, mock_db):
        mock_db.query.return_value.filter.return_value.filter.return_value.order_by.return_value.all.return_value = []

        monitor = AlphaDecayMonitor(mock_db)
        report = monitor.run_analysis()

        assert report.overall_health == "INSUFFICIENT_DATA"

    def test_report_to_dict(self):
        report = AlphaDecayReport(
            timestamp=datetime(2026, 3, 14),
            overall_health="WATCH",
            health_reasons=["Loss streak: 3"],
            rolling_metrics=[RollingMetrics(window_days=30, trade_count=20)],
            regime_performance=[RegimePerformance(regime="normal", trade_count=100, win_rate=0.85)],
        )
        d = report.to_dict()
        assert d["overall_health"] == "WATCH"
        assert len(d["rolling_metrics"]) == 1
        assert len(d["regime_performance"]) == 1
