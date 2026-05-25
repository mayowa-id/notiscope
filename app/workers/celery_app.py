import logging
import random
from datetime import datetime, timezone, timedelta

import redis
from celery import Celery
from celery.utils.log import get_task_logger
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.models.notification import Notification, NotificationStatus
from app.models.idempotency import IdempotencyKey
from app.providers.manager import build_provider_manager, ProviderExhaustedException

settings = get_settings()
logger = get_task_logger(__name__)

# Celery app 
celery_app = Celery("notiscope")

celery_app.conf.update(
    broker_url=settings.celery_broker_url,
    result_backend=settings.celery_result_backend,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Reliability settings
    task_acks_late=True,           # re-queue task if worker crashes mid-execution
    task_reject_on_worker_lost=True,
    task_soft_time_limit=settings.celery_task_soft_time_limit,
    task_time_limit=settings.celery_task_time_limit,
    # Retry defaults (overridden per-task)
    task_max_retries=settings.celery_max_retries,
    # Queues
    task_default_queue="notifications",
    task_queues={
        "notifications": {"exchange": "notifications"},
        "dlq": {"exchange": "dlq"},
    },
    # Beat schedule (DLQ handler)
    beat_schedule={
        "process-dlq": {
            "task": "app.workers.celery_app.process_dlq",
            "schedule": settings.dlq_check_interval_minutes * 60,
            "options": {"queue": "dlq"},
        }
    },
)

# Redis client for distributed locks
_redis_client = redis.from_url(settings.redis_url, decode_responses=True)


def _acquire_lock(key: str, ttl: int) -> bool:
    """Atomic SET NX PX — returns True if lock acquired."""
    lock_key = f"lock:idempotency:{key}"
    return bool(_redis_client.set(lock_key, "1", nx=True, px=ttl * 1000))


def _release_lock(key: str) -> None:
    _redis_client.delete(f"lock:idempotency:{key}")


# send_notification task
@celery_app.task(
    bind=True,
    name="app.workers.celery_app.send_notification",
    queue="notifications",
    max_retries=settings.celery_max_retries,
    acks_late=True,
)
def send_notification(self, notification_id: str, idempotency_key: str) -> dict:
    """
    Delivery task.

    1. Acquire Redis lock on idempotency key (prevents concurrent duplicates).
    2. Check PostgreSQL idempotency table (durable check).
    3. Update status → processing.
    4. Call ProviderManager (SendGrid → SES fallback).
    5. Write result + idempotency key record.
    6. Release lock.

    On ProviderExhaustedException: retry with exponential backoff + jitter.
    After max_retries: task lands in DLQ (status = failed in DB).
    """
    logger.info("task_started", extra={"notification_id": notification_id})

    # Step 1: acquire distributed lock
    lock_acquired = _acquire_lock(
        idempotency_key, settings.redis_lock_ttl_seconds
    )
    if not lock_acquired:
        logger.info(
            "lock_not_acquired_duplicate_in_flight",
            extra={"notification_id": notification_id},
        )
        return {"skipped": True, "reason": "duplicate_in_flight"}

    db = SessionLocal()
    try:
        # Step 2: durable idempotency check
        existing = (
            db.query(IdempotencyKey)
            .filter(IdempotencyKey.key == idempotency_key)
            .first()
        )
        if existing:
            logger.info(
                "idempotency_key_exists_skip",
                extra={"notification_id": notification_id, "key": idempotency_key},
            )
            return existing.cached_response

        # Step 3: fetch notification and mark as processing
        notification = db.query(Notification).filter(
            Notification.id == notification_id
        ).first()

        if not notification:
            logger.error("notification_not_found", extra={"id": notification_id})
            return {"error": "notification_not_found"}

        notification.status = NotificationStatus.PROCESSING
        db.commit()

        # Step 4: attempt delivery
        manager = build_provider_manager()
        try:
            result = manager.send(
                recipient=notification.recipient,
                subject=notification.subject,
                body=notification.body,
            )

            # Step 5a: success — update notification record
            notification.status = NotificationStatus.SENT
            notification.provider_used = result.provider_name
            notification.provider_response = result.provider_response
            db.commit()

            cached_response = {
                "notification_id": notification_id,
                "status": NotificationStatus.SENT,
                "provider_used": result.provider_name,
            }

            # Step 5b: write idempotency key record
            stmt = pg_insert(IdempotencyKey).values(
                key=idempotency_key,
                notification_id=notification_id,
                cached_response=cached_response,
                expires_at=datetime.now(timezone.utc)
                + timedelta(hours=settings.idempotency_key_ttl_hours),
            )
            stmt = stmt.on_conflict_do_nothing(index_elements=["key"])
            db.execute(stmt)
            db.commit()

            logger.info(
                "task_complete",
                extra={
                    "notification_id": notification_id,
                    "status": "sent",
                    "provider": result.provider_name,
                },
            )
            return cached_response

        except ProviderExhaustedException as exc:
            # All providers failed — record error and retry
            notification.status = NotificationStatus.FAILED
            notification.error_message = str(exc)
            notification.retry_count = self.request.retries + 1
            db.commit()

            logger.error(
                "all_providers_failed",
                extra={
                    "notification_id": notification_id,
                    "retry": self.request.retries,
                    "errors": exc.errors,
                },
            )

            # Exponential backoff with ±20% jitter
            base_delay = settings.celery_retry_backoff ** (self.request.retries + 1)
            jitter = base_delay * 0.2 * (random.random() * 2 - 1)
            delay = max(1, base_delay + jitter)

            raise self.retry(exc=exc, countdown=delay)

    finally:
        db.close()
        _release_lock(idempotency_key)


# ─── Dead Letter Queue handler (runs on schedule via Celery beat) ─────────────
@celery_app.task(
    name="app.workers.celery_app.process_dlq",
    queue="dlq",
)
def process_dlq() -> dict:
    """
    Scheduled task: scan for notifications stuck in 'failed' status beyond
    the threshold window. Log them for alerting. Nothing is silently lost.
    """
    threshold = datetime.now(timezone.utc) - timedelta(
        minutes=settings.dlq_failed_threshold_minutes
    )

    db = SessionLocal()
    try:
        stuck = (
            db.query(Notification)
            .filter(
                Notification.status == NotificationStatus.FAILED,
                Notification.updated_at < threshold,
            )
            .all()
        )

        if not stuck:
            return {"dlq_count": 0}

        for notification in stuck:
            # In production: trigger PagerDuty / OpsGenie / Slack alert here
            logger.error(
                "DLQ_ALERT: notification_undeliverable",
                extra={
                    "notification_id": notification.id,
                    "recipient": notification.recipient,
                    "subject": notification.subject,
                    "retry_count": notification.retry_count,
                    "last_error": notification.error_message,
                    "created_at": notification.created_at.isoformat(),
                    "updated_at": notification.updated_at.isoformat(),
                    "action_required": "Manual review or re-queue required",
                },
            )

        return {"dlq_count": len(stuck), "notification_ids": [n.id for n in stuck]}

    finally:
        db.close()
