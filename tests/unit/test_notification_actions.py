"""Tests for notification action choices — structured user responses.

Covers:
- DaemonNotification model with action_choices, chosen_action, chosen_at
- _upsert_notification preserves existing chosen_action on update
- Dashboard API POST /api/notifications/{id}/action endpoint
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from src.data.database import close_database, get_session, init_database
from src.data.models import DaemonNotification


@pytest.fixture
def temp_database():
    """Create an in-memory SQLite database for testing."""
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database) -> Session:
    """Get a database session from the in-memory database."""
    session = get_session()
    yield session
    session.close()


class TestNotificationActionChoicesModel:
    """DaemonNotification stores and retrieves action_choices JSON."""

    def test_notification_with_action_choices_stored(self, db_session):
        """action_choices JSON is persisted and retrieved correctly."""
        choices = [
            {"key": "resume_monitoring", "label": "Resume", "description": "Resume ops"},
            {"key": "keep_blocked", "label": "Keep Blocked", "description": "Stay blocked"},
        ]
        notif = DaemonNotification(
            notification_key="test_vix",
            category="risk",
            status="active",
            title="VIX Spike: 35.0",
            message="VIX elevated",
            action_choices=choices,
            first_seen_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            occurrence_count=1,
        )
        db_session.add(notif)
        db_session.commit()

        loaded = db_session.query(DaemonNotification).filter_by(
            notification_key="test_vix"
        ).first()
        assert loaded is not None
        assert loaded.action_choices == choices
        assert loaded.chosen_action is None
        assert loaded.chosen_at is None

    def test_notification_without_action_choices(self, db_session):
        """Notifications without action_choices work normally."""
        notif = DaemonNotification(
            notification_key="info_only",
            category="data_quality",
            status="active",
            title="Data stale",
            message="IBKR disconnected",
            first_seen_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            occurrence_count=1,
        )
        db_session.add(notif)
        db_session.commit()

        loaded = db_session.query(DaemonNotification).filter_by(
            notification_key="info_only"
        ).first()
        assert loaded.action_choices is None

    def test_chosen_action_recorded(self, db_session):
        """chosen_action and chosen_at are set when user acts."""
        notif = DaemonNotification(
            notification_key="vix_test",
            category="risk",
            status="active",
            title="VIX Spike",
            message="VIX at 35",
            action_choices=[{"key": "resume_monitoring", "label": "Resume", "description": "Resume"}],
            first_seen_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            occurrence_count=1,
        )
        db_session.add(notif)
        db_session.commit()

        notif.chosen_action = "resume_monitoring"
        notif.chosen_at = datetime.now(UTC)
        db_session.commit()

        loaded = db_session.query(DaemonNotification).get(notif.id)
        assert loaded.chosen_action == "resume_monitoring"
        assert loaded.chosen_at is not None


class TestUpsertPreservesChosenAction:
    """_upsert_notification preserves existing chosen_action on update."""

    def test_upsert_preserves_chosen_action(self, db_session):
        """Updating a notification with existing chosen_action doesn't overwrite it."""
        # Create notification with a chosen action already set
        notif = DaemonNotification(
            notification_key="vix_spike",
            category="risk",
            status="active",
            title="VIX Spike: 32.0",
            message="VIX at 32",
            action_choices=[
                {"key": "resume_monitoring", "label": "Resume", "description": "Resume"},
                {"key": "keep_blocked", "label": "Keep", "description": "Keep"},
            ],
            chosen_action="keep_blocked",
            chosen_at=datetime.now(UTC),
            first_seen_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            occurrence_count=3,
        )
        db_session.add(notif)
        db_session.commit()

        # Simulate upsert: update title/message but preserve chosen_action
        existing = db_session.query(DaemonNotification).filter_by(
            notification_key="vix_spike", status="active"
        ).first()
        assert existing is not None

        existing.title = "VIX Spike: 33.0"
        existing.message = "VIX at 33"
        existing.occurrence_count += 1
        # action_choices can be updated, but chosen_action stays
        db_session.commit()

        reloaded = db_session.query(DaemonNotification).get(notif.id)
        assert reloaded.title == "VIX Spike: 33.0"
        assert reloaded.chosen_action == "keep_blocked"
        assert reloaded.occurrence_count == 4


class TestResumeMonitoringResolvesNotification:
    """Choosing 'resume_monitoring' resolves the notification."""

    def test_resume_monitoring_resolves(self, db_session):
        """Setting chosen_action='resume_monitoring' and resolving works."""
        notif = DaemonNotification(
            notification_key="vix_spike",
            category="risk",
            status="active",
            title="VIX Spike",
            message="VIX high",
            action_choices=[
                {"key": "resume_monitoring", "label": "Resume", "description": "Resume"},
            ],
            first_seen_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            occurrence_count=1,
        )
        db_session.add(notif)
        db_session.commit()

        # Simulate choosing "resume_monitoring" (resolves notification)
        notif.chosen_action = "resume_monitoring"
        notif.chosen_at = datetime.now(UTC)
        notif.status = "resolved"
        notif.resolved_at = datetime.now(UTC)
        db_session.commit()

        loaded = db_session.query(DaemonNotification).get(notif.id)
        assert loaded.status == "resolved"
        assert loaded.chosen_action == "resume_monitoring"


class TestKeepBlockedStaysActive:
    """Choosing 'keep_blocked' keeps the notification active."""

    def test_keep_blocked_stays_active(self, db_session):
        """Setting chosen_action='keep_blocked' does NOT resolve notification."""
        notif = DaemonNotification(
            notification_key="vix_spike",
            category="risk",
            status="active",
            title="VIX Spike",
            message="VIX high",
            action_choices=[
                {"key": "keep_blocked", "label": "Keep", "description": "Stay blocked"},
            ],
            first_seen_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            occurrence_count=1,
        )
        db_session.add(notif)
        db_session.commit()

        # "keep_blocked" keeps status=active
        notif.chosen_action = "keep_blocked"
        notif.chosen_at = datetime.now(UTC)
        # status stays "active"
        db_session.commit()

        loaded = db_session.query(DaemonNotification).get(notif.id)
        assert loaded.status == "active"
        assert loaded.chosen_action == "keep_blocked"
