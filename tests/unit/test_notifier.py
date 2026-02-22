"""Tests for Notifier — pluggable notification system."""

from unittest.mock import MagicMock, patch

import pytest

from src.services.notifier import Notifier


@pytest.fixture
def notifier_no_channels():
    """Create notifier with no channels configured."""
    with patch.dict("os.environ", {}, clear=False):
        # Ensure no notification env vars
        env = {
            "NOTIFY_EMAIL_TO": "",
            "NOTIFY_WEBHOOK_URL": "",
        }
        with patch.dict("os.environ", env):
            return Notifier()


@pytest.fixture
def notifier_email():
    """Create notifier with email enabled."""
    env = {
        "NOTIFY_EMAIL_TO": "test@example.com",
        "NOTIFY_EMAIL_FROM": "bot@example.com",
        "SMTP_HOST": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USER": "user",
        "SMTP_PASSWORD": "pass",
        "NOTIFY_WEBHOOK_URL": "",
    }
    with patch.dict("os.environ", env):
        return Notifier()


class TestNotifier:
    """Test Notifier routing and channel behavior."""

    def test_no_channels_enabled(self, notifier_no_channels):
        """Notifier works with no channels (log only)."""
        assert notifier_no_channels.email_enabled is False
        assert notifier_no_channels.webhook_enabled is False

    def test_email_channel_enabled(self, notifier_email):
        """Email channel enabled when NOTIFY_EMAIL_TO is set."""
        assert notifier_email.email_enabled is True
        assert notifier_email.email_to == "test@example.com"

    def test_info_only_logs(self, notifier_no_channels):
        """INFO severity only logs, no email/webhook."""
        # Should not raise
        notifier_no_channels.notify(
            severity="INFO",
            title="Trade placed",
            message="AAPL $200 PUT placed at $0.50",
        )

    def test_warning_triggers_email(self, notifier_email):
        """WARNING severity triggers email."""
        with patch.object(notifier_email, "_send_email") as mock_email:
            notifier_email.notify(
                severity="WARNING",
                title="Delta elevated",
                message="AAPL delta at -0.35",
            )
            mock_email.assert_called_once()
            call_args = mock_email.call_args
            assert "WARNING" in call_args[1]["subject"] or "WARNING" in call_args[0][0]

    def test_critical_triggers_email_and_webhook(self):
        """CRITICAL severity triggers both email and webhook."""
        env = {
            "NOTIFY_EMAIL_TO": "test@example.com",
            "NOTIFY_WEBHOOK_URL": "https://hooks.example.com/webhook",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
        }
        with patch.dict("os.environ", env):
            notifier = Notifier()

        with (
            patch.object(notifier, "_send_email") as mock_email,
            patch.object(notifier, "_send_webhook") as mock_webhook,
        ):
            notifier.notify(
                severity="CRITICAL",
                title="Stop loss hit",
                message="AAPL at -210%",
                data={"symbol": "AAPL", "loss_pct": -2.10},
            )
            mock_email.assert_called_once()
            mock_webhook.assert_called_once()

    def test_emergency_triggers_all(self):
        """EMERGENCY severity triggers email and webhook."""
        env = {
            "NOTIFY_EMAIL_TO": "test@example.com",
            "NOTIFY_WEBHOOK_URL": "https://hooks.example.com/webhook",
            "SMTP_HOST": "smtp.example.com",
            "SMTP_PORT": "587",
        }
        with patch.dict("os.environ", env):
            notifier = Notifier()

        with (
            patch.object(notifier, "_send_email") as mock_email,
            patch.object(notifier, "_send_webhook") as mock_webhook,
        ):
            notifier.notify(
                severity="EMERGENCY",
                title="Margin call risk",
                message="ExcessLiquidity at 3%",
            )
            mock_email.assert_called_once()
            mock_webhook.assert_called_once()

    def test_email_failure_does_not_crash(self, notifier_email):
        """Email send failure is logged but doesn't crash."""
        with patch.object(
            notifier_email, "_send_email", side_effect=Exception("SMTP down")
        ):
            # Should not raise
            notifier_email.notify(
                severity="CRITICAL",
                title="Test",
                message="This should not crash",
            )

    def test_notify_with_data(self, notifier_email):
        """Data dict is included in email body."""
        with patch.object(notifier_email, "_send_email") as mock_email:
            notifier_email.notify(
                severity="WARNING",
                title="Margin warning",
                message="Margin utilization high",
                data={"utilization": "75%", "available": "$25000"},
            )
            # Verify email body contains data
            call_args = mock_email.call_args
            body = call_args[1].get("body") or call_args[0][1]
            assert "utilization" in body
            assert "75%" in body

    def test_format_email_body(self, notifier_email):
        """Email body formatting includes all fields."""
        body = notifier_email._format_email_body(
            severity="CRITICAL",
            title="Stop loss",
            message="Position closed at loss",
            data={"symbol": "AAPL", "loss": "-$500"},
            timestamp="2026-02-09 10:30:00",
        )
        assert "CRITICAL" in body
        assert "Stop loss" in body
        assert "Position closed at loss" in body
        assert "AAPL" in body
        assert "-$500" in body
        assert "2026-02-09" in body
