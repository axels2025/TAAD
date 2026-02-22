"""Tests for guardrail metric enhancements.

Covers:
- Confidence calibration feedback loop (_calibrate_closed_trades)
- Metric persistence (_persist_guardrail_metrics)
- Idempotency of persistence
- Guardrail metrics API endpoints
"""

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agentic.guardrails.monitoring import (
    ConfidenceCalibrator,
    ReasoningEntropyMonitor,
)
from src.data.database import close_database, get_session, init_database
from src.data.models import DecisionAudit, GuardrailMetric, Trade


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_database():
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db(temp_database):
    session = get_session()
    yield session
    session.close()


@pytest.fixture
def calibrator():
    return ConfidenceCalibrator()


@pytest.fixture
def entropy_monitor():
    return ReasoningEntropyMonitor()


def _make_daemon_stub(calibrator, entropy_monitor):
    """Create a minimal daemon-like object with the methods under test.

    Rather than instantiating the full TAADDaemon (which requires IBKR, etc.),
    we import the unbound methods and bind them to a stub that has the
    required attributes.
    """
    from src.agentic.daemon import TAADDaemon

    stub = MagicMock(spec=TAADDaemon)
    stub.confidence_calibrator = calibrator
    stub.entropy_monitor = entropy_monitor

    # Bind the real methods to the stub
    stub._calibrate_closed_trades = TAADDaemon._calibrate_closed_trades.__get__(stub)
    stub._persist_guardrail_metrics = TAADDaemon._persist_guardrail_metrics.__get__(stub)
    stub._log_guardrail_daily_report = TAADDaemon._log_guardrail_daily_report.__get__(stub)

    return stub


# ---------------------------------------------------------------------------
# Calibration feedback loop
# ---------------------------------------------------------------------------


class TestCalibrateFeedbackLoop:

    def test_calibrate_profitable_trade(self, db, calibrator, entropy_monitor):
        """Profitable trade records (confidence, True) into calibrator."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        trade = Trade(
            trade_id="T001",
            symbol="AAPL",
            strike=150.0,
            expiration=date.today(),
            entry_date=datetime(2026, 2, 22, 10, 0),
            entry_premium=1.50,
            contracts=1,
            dte=1,
            exit_date=datetime.now(),
            exit_premium=0.50,
            profit_loss=100.0,
            ai_confidence=0.85,
        )
        db.add(trade)
        db.commit()

        count = daemon._calibrate_closed_trades(db)
        assert count == 1
        assert len(calibrator._outcomes) == 1
        assert calibrator._outcomes[0] == (0.85, True)

    def test_calibrate_losing_trade(self, db, calibrator, entropy_monitor):
        """Losing trade records (confidence, False) into calibrator."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        trade = Trade(
            trade_id="T002",
            symbol="MSFT",
            strike=300.0,
            expiration=date.today(),
            entry_date=datetime(2026, 2, 22, 10, 0),
            entry_premium=1.50,
            contracts=1,
            dte=1,
            exit_date=datetime.now(),
            exit_premium=2.50,
            profit_loss=-100.0,
            ai_confidence=0.60,
        )
        db.add(trade)
        db.commit()

        count = daemon._calibrate_closed_trades(db)
        assert count == 1
        assert calibrator._outcomes[0] == (0.60, False)

    def test_skip_trade_without_confidence(self, db, calibrator, entropy_monitor):
        """Trades with ai_confidence=None should be skipped."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        trade = Trade(
            trade_id="T003",
            symbol="TSLA",
            strike=200.0,
            expiration=date.today(),
            entry_date=datetime(2026, 2, 22, 10, 0),
            entry_premium=2.00,
            contracts=1,
            dte=1,
            exit_date=datetime.now(),
            exit_premium=1.00,
            profit_loss=100.0,
            ai_confidence=None,  # No confidence
        )
        db.add(trade)
        db.commit()

        count = daemon._calibrate_closed_trades(db)
        assert count == 0
        assert len(calibrator._outcomes) == 0

    def test_skip_open_trade(self, db, calibrator, entropy_monitor):
        """Open trades (exit_date=None) should not appear in today's closed."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        trade = Trade(
            trade_id="T004",
            symbol="AMZN",
            strike=180.0,
            expiration=date.today(),
            entry_date=datetime(2026, 2, 22, 10, 0),
            entry_premium=1.00,
            contracts=1,
            dte=1,
            exit_date=None,  # Still open
            profit_loss=None,
            ai_confidence=0.75,
        )
        db.add(trade)
        db.commit()

        count = daemon._calibrate_closed_trades(db)
        assert count == 0


# ---------------------------------------------------------------------------
# Metric persistence
# ---------------------------------------------------------------------------


class TestPersistGuardrailMetrics:

    def test_persist_calibration_rows(self, db, calibrator, entropy_monitor):
        """Calibration bucket rows are written to GuardrailMetric table."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        # Feed calibrator some data
        calibrator.record_outcome(0.80, True)
        calibrator.record_outcome(0.80, False)
        calibrator.record_outcome(0.55, True)

        daemon._persist_guardrail_metrics(db)

        cal_rows = (
            db.query(GuardrailMetric)
            .filter(GuardrailMetric.metric_type == "calibration")
            .all()
        )
        assert len(cal_rows) == 2  # Two buckets: [0.5, 0.7) and [0.7, 0.85)
        buckets = {r.confidence_bucket for r in cal_rows}
        assert "0.50-0.70" in buckets
        assert "0.70-0.85" in buckets

    def test_persist_entropy_row(self, db, calibrator, entropy_monitor):
        """Entropy metrics row is written to GuardrailMetric table."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        # Feed entropy monitor some reasoning
        entropy_monitor.record_reasoning("Market is calm, VIX low", ["vix_low"])
        entropy_monitor.record_reasoning("AAPL near profit target", ["profit_target"])

        daemon._persist_guardrail_metrics(db)

        entropy_rows = (
            db.query(GuardrailMetric)
            .filter(GuardrailMetric.metric_type == "entropy")
            .all()
        )
        assert len(entropy_rows) == 1
        row = entropy_rows[0]
        assert row.avg_reasoning_length > 0
        assert row.unique_key_factors_ratio is not None
        assert row.reasoning_similarity_score is not None

    def test_persist_daily_audit_row(self, db, calibrator, entropy_monitor):
        """Daily audit row is written with correct counts."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        # Add some decision audits for today
        d1 = DecisionAudit(
            timestamp=datetime.now(),
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="MONITOR_ONLY",
            confidence=0.90,
            autonomy_approved=True,
            guardrail_flags=[
                {"passed": False, "guard_name": "context_validator", "severity": "warning", "reason": "stale data"},
                {"passed": True, "guard_name": "output_validator", "severity": "info", "reason": "ok"},
            ],
        )
        d2 = DecisionAudit(
            timestamp=datetime.now(),
            autonomy_level=2,
            event_type="MARKET_OPEN",
            action="STAGE_CANDIDATES",
            confidence=0.75,
            autonomy_approved=True,
            guardrail_flags=[
                {"passed": False, "guard_name": "numerical_grounding", "severity": "block", "reason": "bad numbers"},
            ],
        )
        db.add_all([d1, d2])
        db.commit()

        daemon._persist_guardrail_metrics(db)

        audit_rows = (
            db.query(GuardrailMetric)
            .filter(GuardrailMetric.metric_type == "daily_audit")
            .all()
        )
        assert len(audit_rows) == 1
        row = audit_rows[0]
        assert row.total_decisions == 2
        assert row.guardrail_blocks == 1
        assert row.guardrail_warnings == 1

    def test_persist_is_idempotent(self, db, calibrator, entropy_monitor):
        """Running persist twice for the same day doesn't duplicate rows."""
        daemon = _make_daemon_stub(calibrator, entropy_monitor)

        calibrator.record_outcome(0.80, True)

        daemon._persist_guardrail_metrics(db)
        daemon._persist_guardrail_metrics(db)

        all_rows = (
            db.query(GuardrailMetric)
            .filter(GuardrailMetric.metric_date == date.today())
            .all()
        )

        # Should have exactly: 1 calibration + 1 entropy + 1 daily_audit = 3
        type_counts = {}
        for row in all_rows:
            type_counts[row.metric_type] = type_counts.get(row.metric_type, 0) + 1

        assert type_counts.get("calibration", 0) == 1  # one bucket for 0.70-0.85
        assert type_counts.get("entropy", 0) == 1
        assert type_counts.get("daily_audit", 0) == 1


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------


class TestGuardrailMetricsAPI:

    @pytest.fixture
    def client(self, temp_database):
        """Create a test client for the guardrails API."""
        try:
            from fastapi import FastAPI
            from fastapi.testclient import TestClient
        except ImportError:
            pytest.skip("FastAPI not installed")

        from src.agentic.guardrails_api import create_guardrails_router

        app = FastAPI()
        app.include_router(create_guardrails_router(lambda: None))

        return TestClient(app)

    def test_guardrail_metrics_today_endpoint(self, client, temp_database):
        """API /today returns correct structure."""
        db = get_session()

        # Add a decision with guardrail flags
        d = DecisionAudit(
            timestamp=datetime.now(),
            autonomy_level=2,
            event_type="SCHEDULED_CHECK",
            action="MONITOR_ONLY",
            confidence=0.90,
            autonomy_approved=True,
            guardrail_flags=[
                {"passed": False, "guard_name": "context_validator", "severity": "warning", "reason": "stale data"},
            ],
        )
        db.add(d)
        db.commit()
        db.close()

        response = client.get("/api/guardrail-metrics/today")
        assert response.status_code == 200

        data = response.json()
        assert "total_decisions" in data
        assert "guardrail_blocks" in data
        assert "guardrail_warnings" in data
        assert "guard_breakdown" in data
        assert "recent_findings" in data
        assert data["total_decisions"] >= 1
        assert data["guardrail_warnings"] >= 1

    def test_guardrail_metrics_history_endpoint(self, client, temp_database):
        """API /history returns historical data grouped by date."""
        db = get_session()

        # Add a historical metric row
        m = GuardrailMetric(
            metric_date=date.today(),
            metric_type="daily_audit",
            total_decisions=5,
            guardrail_blocks=1,
            guardrail_warnings=2,
            symbols_flagged=0,
            numbers_flagged=0,
            calibration_error=0.12,
            sample_size=10,
        )
        db.add(m)
        db.commit()
        db.close()

        response = client.get("/api/guardrail-metrics/history?days=7")
        assert response.status_code == 200

        data = response.json()
        assert "history" in data
        assert len(data["history"]) >= 1

        entry = data["history"][0]
        assert "date" in entry
        assert "daily_audit" in entry
        assert entry["daily_audit"]["total_decisions"] == 5
