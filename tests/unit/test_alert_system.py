"""Tests for AlertSystem which routes alerts by severity level.

Covers:
- alert() with LOW level only logs
- alert() with MEDIUM maps to WARNING
- alert() with HIGH maps to CRITICAL
- alert() with CRITICAL maps to EMERGENCY
- EVENT_ALERT_LEVELS has correct mappings
- level_override works
"""

from unittest.mock import MagicMock, patch

import pytest

from src.agentic.alert_system import (
    AlertLevel,
    AlertSystem,
    EVENT_ALERT_LEVELS,
)


@pytest.fixture
def mock_notifier():
    """Provide a mock Notifier to avoid real email/webhook calls."""
    with patch("src.agentic.alert_system.Notifier") as MockNotifier:
        mock_instance = MagicMock()
        MockNotifier.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def alert_system(mock_notifier):
    """Create an AlertSystem with mocked Notifier."""
    system = AlertSystem()
    # Replace the real notifier with the mock
    system._notifier = mock_notifier
    return system


class TestAlertLevels:
    """Tests for AlertLevel enum."""

    def test_level_ordering(self):
        """AlertLevel values should be ordered LOW < MEDIUM < HIGH < CRITICAL."""
        assert AlertLevel.LOW < AlertLevel.MEDIUM
        assert AlertLevel.MEDIUM < AlertLevel.HIGH
        assert AlertLevel.HIGH < AlertLevel.CRITICAL

    def test_level_values(self):
        """AlertLevel values should be 1 through 4."""
        assert AlertLevel.LOW == 1
        assert AlertLevel.MEDIUM == 2
        assert AlertLevel.HIGH == 3
        assert AlertLevel.CRITICAL == 4


class TestEventAlertLevels:
    """Tests for EVENT_ALERT_LEVELS mapping."""

    def test_heartbeat_is_low(self):
        """Heartbeat events should be LOW severity."""
        assert EVENT_ALERT_LEVELS["heartbeat"] == AlertLevel.LOW

    def test_scheduled_check_is_low(self):
        """Scheduled check events should be LOW severity."""
        assert EVENT_ALERT_LEVELS["scheduled_check"] == AlertLevel.LOW

    def test_trade_executed_is_medium(self):
        """Trade execution events should be MEDIUM severity."""
        assert EVENT_ALERT_LEVELS["trade_executed"] == AlertLevel.MEDIUM

    def test_position_closed_is_medium(self):
        """Position closed events should be MEDIUM severity."""
        assert EVENT_ALERT_LEVELS["position_closed"] == AlertLevel.MEDIUM

    def test_tws_disconnected_is_medium(self):
        """TWS disconnect events should be MEDIUM severity."""
        assert EVENT_ALERT_LEVELS["tws_disconnected"] == AlertLevel.MEDIUM

    def test_anomaly_detected_is_high(self):
        """Anomaly detection events should be HIGH severity."""
        assert EVENT_ALERT_LEVELS["anomaly_detected"] == AlertLevel.HIGH

    def test_loss_streak_is_high(self):
        """Loss streak events should be HIGH severity."""
        assert EVENT_ALERT_LEVELS["loss_streak"] == AlertLevel.HIGH

    def test_margin_warning_is_high(self):
        """Margin warning events should be HIGH severity."""
        assert EVENT_ALERT_LEVELS["margin_warning"] == AlertLevel.HIGH

    def test_vix_spike_is_high(self):
        """VIX spike events should be HIGH severity."""
        assert EVENT_ALERT_LEVELS["vix_spike"] == AlertLevel.HIGH

    def test_human_review_required_is_critical(self):
        """Human review required events should be CRITICAL severity."""
        assert EVENT_ALERT_LEVELS["human_review_required"] == AlertLevel.CRITICAL

    def test_emergency_stop_is_critical(self):
        """Emergency stop events should be CRITICAL severity."""
        assert EVENT_ALERT_LEVELS["emergency_stop"] == AlertLevel.CRITICAL

    def test_risk_limit_breach_is_critical(self):
        """Risk limit breach events should be CRITICAL severity."""
        assert EVENT_ALERT_LEVELS["risk_limit_breach"] == AlertLevel.CRITICAL

    def test_demotion_is_high(self):
        """Demotion events should be HIGH severity."""
        assert EVENT_ALERT_LEVELS["demotion"] == AlertLevel.HIGH

    def test_promotion_is_medium(self):
        """Promotion events should be MEDIUM severity."""
        assert EVENT_ALERT_LEVELS["promotion"] == AlertLevel.MEDIUM

    def test_cost_cap_warning_is_high(self):
        """Cost cap warning events should be HIGH severity."""
        assert EVENT_ALERT_LEVELS["cost_cap_warning"] == AlertLevel.HIGH


class TestAlertLow:
    """Tests for alert() with LOW level."""

    def test_low_level_calls_notifier_with_info(self, alert_system, mock_notifier):
        """LOW level events should map to INFO severity on the Notifier."""
        alert_system.alert(
            event="heartbeat",
            title="Heartbeat OK",
            message="Daemon is healthy",
        )

        mock_notifier.notify.assert_called_once_with(
            severity="INFO",
            title="Heartbeat OK",
            message="Daemon is healthy",
            data=None,
        )

    def test_low_level_passes_data(self, alert_system, mock_notifier):
        """LOW level events should forward data to Notifier."""
        data = {"uptime": 3600, "events_processed": 42}

        alert_system.alert(
            event="heartbeat",
            title="Heartbeat OK",
            message="All good",
            data=data,
        )

        mock_notifier.notify.assert_called_once_with(
            severity="INFO",
            title="Heartbeat OK",
            message="All good",
            data=data,
        )

    def test_unknown_event_defaults_to_low(self, alert_system, mock_notifier):
        """Unknown event types should default to LOW (INFO) severity."""
        alert_system.alert(
            event="unknown_event_type",
            title="Something happened",
            message="Details here",
        )

        mock_notifier.notify.assert_called_once_with(
            severity="INFO",
            title="Something happened",
            message="Details here",
            data=None,
        )


class TestAlertMedium:
    """Tests for alert() with MEDIUM level."""

    def test_medium_level_maps_to_warning(self, alert_system, mock_notifier):
        """MEDIUM level events should map to WARNING severity."""
        alert_system.alert(
            event="trade_executed",
            title="Trade Executed",
            message="AAPL $200 PUT sold for $0.50",
        )

        mock_notifier.notify.assert_called_once_with(
            severity="WARNING",
            title="Trade Executed",
            message="AAPL $200 PUT sold for $0.50",
            data=None,
        )

    def test_tws_disconnected_maps_to_warning(self, alert_system, mock_notifier):
        """TWS disconnected event should map to WARNING severity."""
        alert_system.alert(
            event="tws_disconnected",
            title="TWS Disconnected",
            message="Connection lost to IB Gateway",
        )

        call_kwargs = mock_notifier.notify.call_args
        assert call_kwargs[1]["severity"] == "WARNING" or call_kwargs[0][0] == "WARNING"


class TestAlertHigh:
    """Tests for alert() with HIGH level."""

    def test_high_level_maps_to_critical(self, alert_system, mock_notifier):
        """HIGH level events should map to CRITICAL severity."""
        alert_system.alert(
            event="anomaly_detected",
            title="Anomaly Detected",
            message="Unusual P&L deviation detected",
        )

        mock_notifier.notify.assert_called_once_with(
            severity="CRITICAL",
            title="Anomaly Detected",
            message="Unusual P&L deviation detected",
            data=None,
        )

    def test_margin_warning_maps_to_critical(self, alert_system, mock_notifier):
        """Margin warning should map to CRITICAL severity."""
        alert_system.alert(
            event="margin_warning",
            title="Margin Warning",
            message="Margin utilization at 65%",
            data={"utilization": 0.65},
        )

        mock_notifier.notify.assert_called_once_with(
            severity="CRITICAL",
            title="Margin Warning",
            message="Margin utilization at 65%",
            data={"utilization": 0.65},
        )


class TestAlertCritical:
    """Tests for alert() with CRITICAL level."""

    def test_critical_level_maps_to_emergency(self, alert_system, mock_notifier):
        """CRITICAL level events should map to EMERGENCY severity."""
        alert_system.alert(
            event="emergency_stop",
            title="Emergency Stop",
            message="Trading halted due to excessive losses",
        )

        mock_notifier.notify.assert_called_once_with(
            severity="EMERGENCY",
            title="Emergency Stop",
            message="Trading halted due to excessive losses",
            data=None,
        )

    def test_risk_limit_breach_maps_to_emergency(self, alert_system, mock_notifier):
        """Risk limit breach should map to EMERGENCY severity."""
        alert_system.alert(
            event="risk_limit_breach",
            title="Risk Limit Breach",
            message="Daily loss limit exceeded",
            data={"loss_pct": -0.035},
        )

        mock_notifier.notify.assert_called_once_with(
            severity="EMERGENCY",
            title="Risk Limit Breach",
            message="Daily loss limit exceeded",
            data={"loss_pct": -0.035},
        )


class TestLevelOverride:
    """Tests for level_override parameter."""

    def test_override_low_to_critical(self, alert_system, mock_notifier):
        """level_override should override the default event level."""
        # heartbeat is normally LOW -> INFO, but override to CRITICAL -> EMERGENCY
        alert_system.alert(
            event="heartbeat",
            title="Heartbeat Failed",
            message="No heartbeat for 5 minutes",
            level_override=AlertLevel.CRITICAL,
        )

        mock_notifier.notify.assert_called_once_with(
            severity="EMERGENCY",
            title="Heartbeat Failed",
            message="No heartbeat for 5 minutes",
            data=None,
        )

    def test_override_high_to_low(self, alert_system, mock_notifier):
        """level_override can also downgrade severity."""
        # anomaly_detected is normally HIGH -> CRITICAL, but override to LOW -> INFO
        alert_system.alert(
            event="anomaly_detected",
            title="Minor Anomaly",
            message="Very small deviation",
            level_override=AlertLevel.LOW,
        )

        mock_notifier.notify.assert_called_once_with(
            severity="INFO",
            title="Minor Anomaly",
            message="Very small deviation",
            data=None,
        )

    def test_override_to_medium(self, alert_system, mock_notifier):
        """level_override to MEDIUM should map to WARNING."""
        alert_system.alert(
            event="emergency_stop",
            title="Soft Stop",
            message="Paused for review",
            level_override=AlertLevel.MEDIUM,
        )

        mock_notifier.notify.assert_called_once_with(
            severity="WARNING",
            title="Soft Stop",
            message="Paused for review",
            data=None,
        )

    def test_override_none_uses_default(self, alert_system, mock_notifier):
        """Passing level_override=None should use the default event mapping."""
        alert_system.alert(
            event="vix_spike",
            title="VIX Spike",
            message="VIX above 30",
            level_override=None,
        )

        mock_notifier.notify.assert_called_once_with(
            severity="CRITICAL",
            title="VIX Spike",
            message="VIX above 30",
            data=None,
        )
