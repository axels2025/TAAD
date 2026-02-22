"""Pluggable notification system for critical trading events.

Sends notifications via configured channels when critical events occur.
Always logs to file; optionally sends email (SMTP) and/or webhook
(Slack/Discord) based on environment configuration.

Channels (configured via .env):
- LOG: Always active, writes to loguru
- EMAIL: SMTP-based email (NOTIFY_EMAIL_TO, SMTP_HOST, etc.)
- WEBHOOK: HTTP POST to Slack/Discord URL (NOTIFY_WEBHOOK_URL)

Severity levels:
- INFO: Trade placed, profit target hit (log only)
- WARNING: Margin approaching limit, delta elevated (log + email)
- CRITICAL: Stop loss, circuit breaker, assignment (log + email + webhook)
- EMERGENCY: System error, margin call risk (log + email + webhook)
"""

import json
import os
import smtplib
from datetime import datetime
from email.mime.text import MIMEText

from loguru import logger

try:
    import httpx

    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False


class Notifier:
    """Pluggable notification system for critical trading events.

    Gracefully degrades: if a channel fails, logs the error and
    continues. Never crashes the trading system due to notification
    failures.

    Example:
        >>> notifier = Notifier()
        >>> notifier.notify(
        ...     severity="CRITICAL",
        ...     title="Stop Loss Triggered",
        ...     message="AAPL $200 PUT hit stop loss at -210%",
        ...     data={"symbol": "AAPL", "loss_pct": -2.10}
        ... )
    """

    def __init__(self):
        """Initialize notifier from environment variables."""
        # Email config
        self.email_to = os.getenv("NOTIFY_EMAIL_TO", "")
        self.email_from = os.getenv("NOTIFY_EMAIL_FROM", "trading-agent@localhost")
        self.smtp_host = os.getenv("SMTP_HOST", "localhost")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER", "")
        self.smtp_password = os.getenv("SMTP_PASSWORD", "")
        self.email_enabled = bool(self.email_to)

        # Webhook config (Slack/Discord)
        self.webhook_url = os.getenv("NOTIFY_WEBHOOK_URL", "")
        self.webhook_enabled = bool(self.webhook_url) and HTTPX_AVAILABLE

        channels = []
        channels.append("log")
        if self.email_enabled:
            channels.append("email")
        if self.webhook_enabled:
            channels.append("webhook")

        logger.info(f"Notifier initialized: channels={channels}")

    def notify(
        self,
        severity: str,
        title: str,
        message: str,
        data: dict | None = None,
    ) -> None:
        """Send notification based on severity.

        Severity routing:
        - INFO: log only
        - WARNING: log + email (if configured)
        - CRITICAL/EMERGENCY: log + email + webhook

        Args:
            severity: INFO, WARNING, CRITICAL, or EMERGENCY
            title: Short notification title
            message: Detailed notification message
            data: Optional structured data (symbol, values, etc.)
        """
        severity = severity.upper()
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Always log
        log_msg = f"[{severity}] {title}: {message}"
        if severity == "EMERGENCY":
            logger.critical(log_msg)
        elif severity == "CRITICAL":
            logger.critical(log_msg)
        elif severity == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        # Email for WARNING and above
        if severity in ("WARNING", "CRITICAL", "EMERGENCY") and self.email_enabled:
            try:
                self._send_email(
                    subject=f"[Trading {severity}] {title}",
                    body=self._format_email_body(severity, title, message, data, timestamp),
                )
            except Exception as e:
                logger.error(f"Email notification failed: {e}")

        # Webhook for CRITICAL and above
        if severity in ("CRITICAL", "EMERGENCY") and self.webhook_enabled:
            try:
                self._send_webhook(title, message, severity, data, timestamp)
            except Exception as e:
                logger.error(f"Webhook notification failed: {e}")

    def _format_email_body(
        self,
        severity: str,
        title: str,
        message: str,
        data: dict | None,
        timestamp: str,
    ) -> str:
        """Format email body text."""
        parts = [
            f"Trading System Alert — {severity}",
            f"Time: {timestamp}",
            f"Title: {title}",
            "",
            message,
        ]
        if data:
            parts.append("")
            parts.append("Details:")
            for key, value in data.items():
                parts.append(f"  {key}: {value}")
        return "\n".join(parts)

    def _send_email(self, subject: str, body: str) -> None:
        """Send email via SMTP. Fails gracefully."""
        try:
            msg = MIMEText(body)
            msg["Subject"] = subject
            msg["From"] = self.email_from
            msg["To"] = self.email_to

            with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=10) as server:
                if self.smtp_user and self.smtp_password:
                    server.starttls()
                    server.login(self.smtp_user, self.smtp_password)
                server.send_message(msg)

            logger.debug(f"Email sent: {subject}")

        except Exception as e:
            logger.error(f"Failed to send email notification: {e}")

    def _send_webhook(
        self,
        title: str,
        message: str,
        severity: str,
        data: dict | None,
        timestamp: str,
    ) -> None:
        """Send webhook POST (Slack/Discord compatible). Fails gracefully."""
        if not HTTPX_AVAILABLE:
            return

        try:
            # Slack-compatible payload
            payload = {
                "text": f"*[{severity}] {title}*\n{message}\n_{timestamp}_",
            }

            # Add data as attachment if present
            if data:
                fields = "\n".join(f"• {k}: {v}" for k, v in data.items())
                payload["text"] += f"\n```{fields}```"

            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    self.webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()

            logger.debug(f"Webhook sent: {title}")

        except Exception as e:
            logger.error(f"Failed to send webhook notification: {e}")
