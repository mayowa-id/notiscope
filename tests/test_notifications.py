"""
Tests for the Notiscope notification system.

Three core tests:
  1. Notification is persisted as 'pending' on POST /notify
  2. Duplicate idempotency key → only one email dispatched
  3. SendGrid failure → system falls back to SES
"""
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.core.database import get_db
from app.models.base import Base
from app.models.notification import Notification, NotificationStatus
from app.providers.base import SendResult, ProviderError

# ─── Test database (SQLite in-memory) ────────────────────────────────────────
TEST_DATABASE_URL = "sqlite:///./test_notiscope.db"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_database():
    """Create all tables before each test, drop after."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client():
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


# Test 1: Notification is persisted as pending
def test_notification_created_as_pending(client, db):
    """
    POSTing to /notify should:
    - Return HTTP 202
    - Create a Notification row with status='pending'
    - Include a notification_id in the response
    """
    with patch("app.api.notifications.send_notification") as mock_task:
        mock_task.apply_async = MagicMock()

        response = client.post(
            "/notify",
            json={
                "recipient": "test@example.com",
                "subject": "Test notification",
                "body": "This is a test message.",
                "channel": "email",
            },
            headers={"Idempotency-Key": "test-key-001"},
        )

    assert response.status_code == 202
    data = response.json()
    assert "notification_id" in data
    assert data["status"] == "queued"

    # Verify the DB row was created
    notification = (
        db.query(Notification)
        .filter(Notification.id == data["notification_id"])
        .first()
    )
    assert notification is not None
    assert notification.status == NotificationStatus.PENDING
    assert notification.recipient == "test@example.com"
    assert notification.idempotency_key == "test-key-001"


# Test 2: Idempotency — duplicate key sends only once
def test_duplicate_idempotency_key_sends_only_once(client, db):
    """
    Sending the same idempotency key twice should:
    - Return the same notification_id both times
    - Only enqueue ONE Celery task (not two)
    """
    with patch("app.api.notifications.send_notification") as mock_task:
        mock_task.apply_async = MagicMock()

        # First request
        resp1 = client.post(
            "/notify",
            json={
                "recipient": "jane@example.com",
                "subject": "Order shipped",
                "body": "Your order has shipped!",
            },
            headers={"Idempotency-Key": "idem-key-duplicate-test"},
        )

        # Second request — same idempotency key
        resp2 = client.post(
            "/notify",
            json={
                "recipient": "jane@example.com",
                "subject": "Order shipped",
                "body": "Your order has shipped!",
            },
            headers={"Idempotency-Key": "idem-key-duplicate-test"},
        )

    assert resp1.status_code == 202
    # Second call should return 202 as well (same notification, already queued)
    assert resp2.status_code == 202

    # Both responses should reference the same notification_id
    assert resp1.json()["notification_id"] == resp2.json()["notification_id"]

    # Celery task should have been enqueued exactly ONCE
    assert mock_task.apply_async.call_count == 1

    # Only one Notification row should exist
    notifications = (
        db.query(Notification)
        .filter(Notification.idempotency_key == "idem-key-duplicate-test")
        .all()
    )
    assert len(notifications) == 1


# Test 3: SendGrid failure → fallback to SES
def test_sendgrid_failure_falls_back_to_ses():
    """
    When SendGrid raises a ProviderError, the ProviderManager should
    automatically retry with SES and return a successful SendResult.
    """
    from app.providers.manager import ProviderManager
    from app.providers.base import BaseProvider

    # Mock SendGrid — always fails
    mock_sendgrid = MagicMock(spec=BaseProvider)
    mock_sendgrid.name = "sendgrid"
    mock_sendgrid.send.side_effect = ProviderError("sendgrid", "503 Service Unavailable")

    # Mock SES — always succeeds
    mock_ses = MagicMock(spec=BaseProvider)
    mock_ses.name = "ses"
    mock_ses.send.return_value = SendResult(
        success=True,
        provider_name="ses",
        provider_response={"message_id": "test-message-id"},
    )

    manager = ProviderManager(primary=mock_sendgrid, fallback=mock_ses)
    result = manager.send(
        recipient="bob@example.com",
        subject="Fallback test",
        body="Testing SES fallback",
    )

    # SendGrid was attempted
    mock_sendgrid.send.assert_called_once_with(
        "bob@example.com", "Fallback test", "Testing SES fallback"
    )
    # SES was used as fallback
    mock_ses.send.assert_called_once_with(
        "bob@example.com", "Fallback test", "Testing SES fallback"
    )

    assert result.success is True
    assert result.provider_name == "ses"
    assert result.provider_response["message_id"] == "test-message-id"
