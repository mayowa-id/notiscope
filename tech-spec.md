# Notiscope — Technical Specification

## Overview

Notiscope is a reliable, scalable notification delivery system built to serve 1M+ users. The initial scope covers email notifications. The system is designed around three non-negotiable guarantees: no duplicate sends, no missed sends, and graceful degradation when providers fail.

---

## Problem Statement

At scale, notification systems fail in predictable ways. Workers crash after sending but before confirming. Providers go down mid-batch. Retry logic re-sends what was already delivered. The goal of Notiscope is to make every one of these failure modes safe by design, not by luck.

---

## Architecture Overview

![Notiscope Architecture Diagram](docs/assets/Excalidraw-Screenshot.png)

```
Caller → POST /notify → FastAPI → PostgreSQL (pending) + Redis (lock)
                                        ↓
                               Redis Streams / Celery Queue
                          ┌────────────┴────────────┐
                     Email Worker              Email Worker (replica)
                          ↓
                  Check idempotency key (PostgreSQL)
                          ↓
               Provider Manager (SES primary → Postmark fallback)
                          ↓
                  Write status back to PostgreSQL
                          ↓
              Dead Letter Queue (if all retries exhausted)
```

---

## Component Breakdown

### FastAPI (Ingestion Layer)
Receives notification requests via `POST /notify`. Writes a `pending` record to PostgreSQL immediately. Enqueues a Celery task. Returns `202 Accepted` with the notification id. Does not wait for delivery.

**Why FastAPI:** Async by default, typed request validation with Pydantic, fast enough to handle high ingestion volume without blocking.

### PostgreSQL (Primary Store)
Holds the canonical state of every notification. The idempotency key table enforces uniqueness at the database level with a unique constraint — this is the hard guarantee against duplicates regardless of application logic.

**Why PostgreSQL:** Durable, ACID compliant, unique constraints are enforced at the engine level. Redis alone cannot provide this guarantee.

### Redis (Lock Store + Celery Broker)
Serves two roles. As a Celery broker it queues tasks between the API and the workers. As a lock store it holds a short-lived distributed lock per idempotency key to absorb concurrent duplicate requests arriving before the database record is written.

**Why Redis for locking:** The window between receiving a request and writing the DB record is where concurrent duplicates slip through. A Redis lock closes that window cheaply without adding a round trip to PostgreSQL.

### Celery Workers (Async Delivery)
Each worker picks up a notification task, checks the idempotency key in PostgreSQL, calls the provider manager, writes the result back, and stores the idempotency record. Retries with exponential backoff on failure. Gives up after a configurable maximum and routes to the dead letter queue.

### Provider Manager (Delivery Abstraction)
Sits between the worker and the actual email providers. Tries AWS SES first. On failure logs the error and tries Postmark. If both fail raises an exception back to the worker to trigger retry. Neither provider knows about the other.

**Why two providers:** The graceful degradation requirement is not just about retrying the same provider. If SES has an outage, retrying SES ten times is not graceful degradation. Postmark as a fallback is.

### Dead Letter Queue
Any notification that exhausts all retries across both providers lands here. The DLQ handler logs the full context — notification id, recipient, all error payloads — and alerts. Nothing is silently dropped.

---

## Data Model

### notifications
| Column | Type | Notes |

| id | UUID | Primary key |
| recipient_email | VARCHAR | Recipient address |
| subject | VARCHAR | Email subject |
| body | TEXT | Email body |
| status | ENUM | pending, processing, sent, failed |
| retry_count | INTEGER | Default 0 |
| error_detail | TEXT | Last error payload if failed |
| created_at | TIMESTAMP | Auto set |
| updated_at | TIMESTAMP | Auto updated |

### idempotency_keys
| Column | Type | Notes |

| id | UUID | Primary key |
| idempotency_key | VARCHAR | Unique constraint |
| notification_id | UUID | Foreign key → notifications.id |
| response_payload | JSONB | Cached response returned on duplicate |
| expires_at | TIMESTAMP | 24 hour window |
| created_at | TIMESTAMP | Auto set |

---

## Reliability Guarantees

### No duplicates
Every request carries a caller-supplied idempotency key. A Redis lock is acquired on arrival. The key is written to PostgreSQL with a unique constraint. Any retry — automatic or manual — hits the same key, finds the existing record, and returns the cached response without re-processing. Even if two workers pick up the same task simultaneously, the unique constraint at the database level rejects the second write.

### No missed sends
Notifications are written to PostgreSQL before anything is enqueued. If the worker crashes after sending but before confirming, the notification is still in `processing` state in the database. A recovery job picks up stale processing records and re-queues them. The idempotency key prevents a double send on the re-queue.

### Graceful degradation
The provider manager tries AWS SES first. On any failure — timeout, rate limit, provider outage — it falls back to Postmark automatically without the worker retrying. If Postmark also fails the worker retries the whole flow with exponential backoff. After the retry limit is hit the notification moves to the dead letter queue with full error context preserved.

---

## Failure Scenarios

| Scenario | System behaviour |

| AWS SES is down | Provider manager falls back to Postmark automatically |
| Both providers are down | Worker retries with exponential backoff, then routes to DLQ |
| Worker crashes mid-send | Notification stays in processing, recovery job re-queues it, idempotency prevents double send |
| Duplicate request arrives | Redis lock blocks it at the door, DB unique constraint catches anything that slips through |
| Database is slow | API still accepts requests and enqueues tasks, delivery is delayed not lost |
| Redis is down | Celery broker fails, API returns 503, no silent data loss because nothing was written yet |
| Idempotency key expires | After 24 hours the same key is treated as a new request |

---

## Tech Stack

| Layer | Technology | Reason |

| API | FastAPI | Async, typed, fast |
| ORM | SQLAlchemy | Mature, migration-friendly |
| Migrations | Alembic | Pairs with SQLAlchemy |
| Task queue | Celery | Battle tested async workers |
| Broker | Redis | Fast, simple Celery integration |
| Lock store | Redis | Short-lived distributed locks |
| Database | PostgreSQL | ACID, unique constraints |
| Email primary | AWS SES | Cheap, reliable, scalable primary |
| Email fallback | Postmark | High deliverability fallback |
| Containerisation | Docker Compose | Consistent local and production environment |
| Deployment | AWS EC2 (t2.micro) | Full control, runs entire stack |

---

## API Reference

### POST /notify
Enqueue a notification for delivery.

**Request body**
```json
{
  "recipient_email": "user@example.com",
  "subject": "Your transaction was successful",
  "body": "You have received ₦50,000.",
  "idempotency_key": "txn-abc123-notify"
}
```

**Response 202**
```json
{
  "notification_id": "uuid",
  "status": "queued"
}
```

### GET /notify/{id}
Poll the delivery status of a notification.

**Response 200**
```json
{
  "notification_id": "uuid",
  "status": "sent",
  "created_at": "2025-01-01T12:00:00Z",
  "updated_at": "2025-01-01T12:00:04Z"
}
```

---

## Out of Scope (v1)

SMS and push notifications are intentionally excluded from v1. The provider abstraction is designed to support them, adding a channel is a matter of adding a new worker and a new provider implementation without touching existing code.
