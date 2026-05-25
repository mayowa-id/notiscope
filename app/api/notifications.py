
import logging
from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.config import get_settings
from app.models.notification import Notification, NotificationStatus
from app.models.idempotency import IdempotencyKey
from app.workers.celery_app import send_notification

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/notify", tags=["Notifications"])


# Request / Response schemas

class NotifyRequest(BaseModel):
    recipient: EmailStr
    subject: str
    body: str
    channel: str = "email"


class NotifyResponse(BaseModel):
    notification_id: str
    status: str
    message: str


class NotificationStatusResponse(BaseModel):
    notification_id: str
    status: str
    channel: str
    recipient: str
    provider_used: str | None
    retry_count: int
    created_at: str
    updated_at: str


# Endpoints

@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=NotifyResponse,
    summary="Enqueue a notification for delivery",
    description=(
        "Accepts a notification request and enqueues it for async delivery. "
        "Provide an `Idempotency-Key` header (UUID) to ensure exactly-once delivery. "
        "Repeating the same key returns the cached response without sending again."
    ),
)
def create_notification(
    payload: NotifyRequest,
    idempotency_key: str = Header(..., alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> NotifyResponse:
    # Fast-path: check if idempotency key already has a completed record
    existing_key = (
        db.query(IdempotencyKey)
        .filter(IdempotencyKey.key == idempotency_key)
        .first()
    )
    if existing_key:
        logger.info(
            "idempotency_replay",
            extra={"idempotency_key": idempotency_key},
        )
        cached = existing_key.cached_response
        return NotifyResponse(
            notification_id=cached.get("notification_id", existing_key.notification_id),
            status=cached.get("status", "sent"),
            message="Notification already processed (idempotency key matched)",
        )

    # Check if there's already a pending/processing notification with this key
    existing_notification = (
        db.query(Notification)
        .filter(Notification.idempotency_key == idempotency_key)
        .first()
    )
    if existing_notification:
        return NotifyResponse(
            notification_id=existing_notification.id,
            status=existing_notification.status,
            message="Notification already queued",
        )

    # Write pending notification record
    notification = Notification(
        idempotency_key=idempotency_key,
        recipient=str(payload.recipient),
        subject=payload.subject,
        body=payload.body,
        channel=payload.channel,
        status=NotificationStatus.PENDING,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)

    # Enqueue Celery task
    send_notification.apply_async(
        args=[notification.id, idempotency_key],
        queue="notifications",
    )

    logger.info(
        "notification_queued",
        extra={"notification_id": notification.id, "recipient": payload.recipient},
    )

    return NotifyResponse(
        notification_id=notification.id,
        status="queued",
        message="Notification accepted for delivery",
    )


@router.get(
    "/{notification_id}",
    response_model=NotificationStatusResponse,
    summary="Get notification delivery status",
)
def get_notification(
    notification_id: str,
    db: Session = Depends(get_db),
) -> NotificationStatusResponse:
    notification = (
        db.query(Notification)
        .filter(Notification.id == notification_id)
        .first()
    )
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notification {notification_id} not found",
        )

    return NotificationStatusResponse(
        notification_id=notification.id,
        status=notification.status,
        channel=notification.channel,
        recipient=notification.recipient,
        provider_used=notification.provider_used,
        retry_count=notification.retry_count,
        created_at=notification.created_at.isoformat(),
        updated_at=notification.updated_at.isoformat(),
    )
