# Notiscope — Technical Specification

**Author:** Mayowa  
**Version:** 1.0  
**Date:** May 2026  
**Status:** Production-Ready Design

---

## Table of Contents

1. [Problem Statement](#1-problem-statement)
2. [Goals and Non-Goals](#2-goals-and-non-goals)
3. [Architecture Overview](#3-architecture-overview)
4. [Component Breakdown](#4-component-breakdown)
5. [Data Model](#5-data-model)
6. [Reliability Guarantees](#6-reliability-guarantees)
7. [Failure Scenarios](#7-failure-scenarios)
8. [Tech Stack Justifications](#8-tech-stack-justifications)
9. [API Reference](#9-api-reference)
10. [Scalability Analysis](#10-scalability-analysis)
11. [Observability](#11-observability)
12. [Future Work](#12-future-work)

---

## 1. Problem Statement

At 1 million+ users, sending push notifications, SMS, and email is not just a feature — it is a core reliability contract with your users. The naive approach (calling a provider inline and hoping it works) fails in four compounding ways:

- **Duplicates**: retries without idempotency cause users to receive the same message multiple times, destroying trust.
- **Silent drops**: a provider outage without fallback logic means messages are simply lost, with no trace.
- **Cascading failures**: synchronous sends block your API, so a slow provider degrades your entire product.
- **No observability**: without a status trail, you cannot answer "did this notification actually get sent?"

Notiscope solves all four. It is a notification dispatch service that guarantees at-least-once delivery, deduplicates using caller-supplied idempotency keys, degrades gracefully across providers, and makes every notification observable via a status API.

---

## 2. Goals and Non-Goals

### Goals

- **No duplicates**: the same logical notification (identified by its idempotency key) is sent exactly once, even if the caller retries the API call.
- **No silent drops**: every delivery failure is logged with full context, retried with exponential backoff, and surfaced to a dead letter queue if all retries are exhausted.
- **Graceful degradation**: if the primary email provider (SendGrid) fails, the system automatically falls over to the secondary (AWS SES) without caller involvement.
- **Async dispatch**: the API returns immediately (HTTP 202) so caller latency is decoupled from provider latency.
- **Observable state**: every notification has a queryable lifecycle — `pending → processing → sent | failed`.

### Non-Goals

- Real-time delivery guarantees (we target best-effort delivery within seconds to minutes).
- End-to-end encryption of message bodies at rest (out of scope for v1).
- SMS and push channels in v1 (architecture is channel-agnostic by design; adding channels is additive).
- User preference management (unsubscribe lists, frequency caps) — v2 concern.

---

## 3. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                        CALLER                               │
│            (your app, a cron job, an event)                 │
└──────────────────────┬──────────────────────────────────────┘
                       │ POST /notify  (+ idempotency-key header)
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                    FastAPI API Layer                         │
│  • Validates input                                          │
│  • Checks idempotency key in Redis (fast path return)       │
│  • Writes notification row to PostgreSQL (status=pending)   │
│  • Enqueues Celery task                                     │
│  • Returns HTTP 202 with notification_id                    │
└──────────────────────┬──────────────────────────────────────┘
                       │ task enqueue
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                 Redis (Broker + Lock Store)                  │
│  • Celery task queue                                        │
│  • Distributed locks (prevent concurrent duplicate sends)   │
│  • Short-lived idempotency key cache                        │
└──────────────────────┬──────────────────────────────────────┘
                       │ task dequeue
                       ▼
┌─────────────────────────────────────────────────────────────┐
│                   Celery Worker Pool                         │
│  1. Acquire Redis lock on idempotency key                   │
│  2. Check PostgreSQL idempotency table (durable check)      │
│  3. Update notification status → processing                 │
│  4. Call ProviderManager.send()                             │
│  5. Write result to notifications table                     │
│  6. Write idempotency key record with cached response       │
│  7. Release Redis lock                                      │
│  On failure: exponential backoff retry → dead letter queue  │
└─────┬──────────────────────────────────────────┬────────────┘
      │ primary attempt                           │ fallback
      ▼                                           ▼
┌───────────────┐                      ┌──────────────────────┐
│   SendGrid    │  (fails / timeout)   │      AWS SES         │
│  (Primary)    │ ─────────────────►  │    (Secondary)       │
└───────────────┘                      └──────────────────────┘
      │                                           │
      └──────────────────┬────────────────────────┘
                         ▼
┌─────────────────────────────────────────────────────────────┐
│               PostgreSQL (Durable Store)                     │
│  • notifications table  (lifecycle + audit trail)           │
│  • idempotency_keys table  (deduplication ledger)           │
└─────────────────────────────────────────────────────────────┘
```

**Key flow properties:**
- The API path never waits for a provider — maximum API latency is the time to write one DB row and enqueue one Redis message.
- Idempotency is checked twice: once in Redis (fast, ~1ms) and once in PostgreSQL (durable, ~5ms). The Redis check saves a DB round-trip on the hot path; the PostgreSQL check is the source of truth in case Redis is flushed.
- Locks are held only for the duration of the duplicate check + status update, not for the entire provider call.

---

## 4. Component Breakdown

### 4.1 FastAPI API Layer (`app/api/notifications.py`)

Exposes two endpoints:

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/notify` | Accept a notification request, enqueue for delivery |
| `GET` | `/notify/{id}` | Poll delivery status |

The API is intentionally thin. No business logic lives here. It validates, persists, and delegates.

**Idempotency key handling at the API layer:**
The caller supplies an `Idempotency-Key` header (a UUID they generate). The API checks Redis for this key. If found, it returns the cached response immediately with HTTP 200 — no database write, no task enqueue. This is the fast path for retrying callers. If not in Redis, normal flow proceeds.

### 4.2 Celery Worker (`app/workers/email_worker.py`)

Consumes tasks from the Redis queue. One task = one notification delivery attempt. Workers are stateless and horizontally scalable.

**Task internals:**
```
acquire_redis_lock(idempotency_key, ttl=30s)
  └─ if lock not acquired → another worker is handling this → return early

check_idempotency_keys table in PostgreSQL
  └─ if row exists → return cached_response, skip send

update notifications.status = 'processing'
call provider_manager.send(recipient, subject, body)
  └─ if SendGrid fails → try SES
  └─ if SES fails → raise ProviderExhaustedException

update notifications.status = 'sent' | 'failed'
insert idempotency_keys row (key, notification_id, cached_response, expires_at)
release_redis_lock(idempotency_key)
```

**Retry policy:**
- Max retries: 5
- Backoff: exponential, 2^retry_count seconds (2s, 4s, 8s, 16s, 32s)
- Jitter: ±20% to prevent thundering herd
- After 5 failures: task moves to dead letter queue (`notifications.failed` queue)

### 4.3 Provider Manager (`app/providers/`)

```python
class ProviderManager:
    primary: BaseProvider   # SendGrid
    fallback: BaseProvider  # AWS SES

    def send(self, recipient, subject, body) -> SendResult:
        try:
            return self.primary.send(recipient, subject, body)
        except ProviderError as e:
            log.warning("primary provider failed, trying fallback", error=e)
            return self.fallback.send(recipient, subject, body)
```

Each provider implements a single interface:

```python
class BaseProvider(ABC):
    @abstractmethod
    def send(self, recipient: str, subject: str, body: str) -> SendResult:
        ...
```

New channels (SMS via Twilio, push via FCM) are additive: implement `BaseProvider`, register in `ProviderManager`, done.

### 4.4 PostgreSQL (Durable Store)

Two tables. See [Section 5](#5-data-model).

Chosen for ACID guarantees. Idempotency key writes use `INSERT ... ON CONFLICT DO NOTHING` to prevent race conditions at the database level, providing a third layer of duplicate protection.

### 4.5 Redis (Broker + Lock Store)

Serves two roles:
1. **Celery broker**: task queue with persistence (`appendonly yes` in production).
2. **Distributed lock store**: short-lived locks using `SET NX PX` (atomic set-if-not-exists with TTL).

The dual use is intentional — it keeps the infrastructure footprint minimal without sacrificing correctness.

### 4.6 Dead Letter Queue Handler (`app/workers/dlq_handler.py`)

A scheduled Celery beat task that runs every 5 minutes. It queries for notifications with `status='failed'` and `updated_at < now() - 10 minutes` (beyond retry window). For each, it logs a structured alert entry. In production this would trigger a PagerDuty/OpsGenie alert. Nothing is silently lost.

---

## 5. Data Model

### `notifications` table

| Column | Type | Notes |
|--------|------|-------|
| `id` | `UUID` | Primary key, generated server-side |
| `idempotency_key` | `VARCHAR(255)` | Unique index; caller-supplied |
| `recipient` | `VARCHAR(255)` | Email address of recipient |
| `subject` | `TEXT` | Notification subject line |
| `body` | `TEXT` | Notification body content |
| `channel` | `VARCHAR(50)` | `'email'`, `'sms'`, `'push'` (v1: email only) |
| `status` | `VARCHAR(20)` | `pending`, `processing`, `sent`, `failed` |
| `provider_used` | `VARCHAR(50)` | Which provider actually sent it |
| `provider_response` | `JSONB` | Raw provider response for audit |
| `retry_count` | `INTEGER` | Default 0; incremented per attempt |
| `error_message` | `TEXT` | Last error if failed |
| `created_at` | `TIMESTAMPTZ` | Immutable |
| `updated_at` | `TIMESTAMPTZ` | Updated on every status change |

**Indexes:**
- `PRIMARY KEY (id)`
- `UNIQUE INDEX ON idempotency_key`
- `INDEX ON (status, updated_at)` — for DLQ handler queries
- `INDEX ON created_at` — for time-range analytics

### `idempotency_keys` table

| Column | Type | Notes |
|--------|------|-------|
| `key` | `VARCHAR(255)` | Primary key; matches caller-supplied key |
| `notification_id` | `UUID` | FK → `notifications.id` |
| `cached_response` | `JSONB` | Serialised API response to replay |
| `created_at` | `TIMESTAMPTZ` | When this key was first seen |
| `expires_at` | `TIMESTAMPTZ` | Default: 24h after creation |

**Notes:**
- `expires_at` enables a daily cleanup job to prune old keys without impacting correctness (24h TTL is longer than any caller would realistically retry).
- `ON CONFLICT DO NOTHING` on insert prevents race conditions even if two workers process the same key simultaneously.

---

## 6. Reliability Guarantees

### 6.1 No Duplicates

Three layered defences:

| Layer | Mechanism | Protects Against |
|-------|-----------|-----------------|
| 1. API (Redis cache) | Fast idempotency key lookup | Caller retries before task is processed |
| 2. Worker (Redis lock) | `SET NX PX 30s` on idempotency key | Two workers racing on the same task |
| 3. Database | `INSERT ... ON CONFLICT DO NOTHING` on `idempotency_keys` | Any gap in the lock (lock expiry, crash) |

### 6.2 No Silent Drops

| Mechanism | What it prevents |
|-----------|-----------------|
| Celery retry with backoff | Transient provider failures (network blip, rate limit) |
| Provider fallback (SES) | Primary provider (SendGrid) outage |
| DLQ handler | Notifications that exhaust all retries — they are logged and alertable |
| Notification status trail | Every state transition is written to PostgreSQL; nothing is undocumented |

### 6.3 Delivery Ordering

Notiscope does **not** guarantee ordered delivery. Notifications are independent events. If ordering matters (e.g., "welcome" before "verify email"), the caller must sequence them explicitly.

---

## 7. Failure Scenarios

| Scenario | System Behaviour | User Impact |
|----------|-----------------|-------------|
| SendGrid returns 5xx | Worker catches, logs warning, retries with SES | None — email arrives via SES |
| SendGrid rate-limited (429) | Worker treats as transient failure, retries with backoff | Slight delay (seconds to minutes) |
| SES also fails | Worker raises, Celery retries (up to 5x with backoff) | Delay proportional to retry window |
| Both providers fail all 5 retries | Task moves to DLQ, notification marked `failed`, DLQ handler logs alert | Email not delivered; engineering is notified |
| Redis goes down | Celery cannot dequeue tasks; API returns 503 on enqueue attempt | New sends blocked; in-flight tasks resume when Redis recovers |
| PostgreSQL goes down | API rejects requests (cannot write pending row); worker cannot update status | New sends blocked; task will error and retry when DB recovers |
| Worker crashes mid-send | Celery `acks_late=True` means task is re-queued; idempotency layer prevents double send | None — re-queued task is deduplicated |
| API pod restarts | Stateless; new pod picks up from Redis queue | None |
| Duplicate API call (caller retry) | Redis idempotency cache returns cached 200 immediately | None — caller gets the same response, one email sent |
| Idempotency key collision (two different callers, same key) | Second caller gets cached response from first caller's send — this is intentional and documented | Callers must use globally unique keys (UUID recommended) |

---

## 8. Tech Stack Justifications

| Component | Choice | Why |
|-----------|--------|-----|
| **API framework** | FastAPI | Async-native, automatic OpenAPI docs, Pydantic validation. Handles high concurrency with minimal overhead. |
| **Task queue** | Celery + Redis | Industry standard for Python async workers. Redis broker is operationally simple. Celery provides retry, backoff, and scheduling out of the box. |
| **Primary email provider** | SendGrid | 99.99% uptime SLA, excellent deliverability, detailed webhook events, generous free tier for development. |
| **Fallback email provider** | AWS SES | Different infrastructure from SendGrid (different DNS, different IP pools). True independence means if SendGrid has an outage, SES is unaffected. |
| **Database** | PostgreSQL | ACID guarantees for idempotency correctness. JSONB for flexible provider response storage. Battle-tested at scale. |
| **ORM + Migrations** | SQLAlchemy + Alembic | Standard Python ORM with type-safe models. Alembic gives version-controlled, reversible migrations. |
| **Containerisation** | Docker + Docker Compose | Reproducible local environment. Railway.app supports Docker Compose natively for simple deployment. |
| **Language** | Python 3.11+ | FastAPI and Celery are Python-first. Large ecosystem for email providers. |

---

## 9. API Reference

### `POST /notify`

Enqueue a notification for delivery.

**Request headers:**

| Header | Required | Description |
|--------|----------|-------------|
| `Content-Type` | Yes | `application/json` |
| `Idempotency-Key` | Yes | Caller-generated UUID. Same key = same logical notification. |

**Request body:**

```json
{
  "recipient": "user@example.com",
  "subject": "Your order has shipped",
  "body": "Hi Jane, your order #12345 has shipped and will arrive by Friday.",
  "channel": "email"
}
```

**Response (202 Accepted):**

```json
{
  "notification_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "queued",
  "message": "Notification accepted for delivery"
}
```

**Response (200 OK — idempotent replay):**

```json
{
  "notification_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "sent",
  "message": "Notification already processed (idempotency key matched)"
}
```

---

### `GET /notify/{notification_id}`

Poll the delivery status of a notification.

**Response (200 OK):**

```json
{
  "notification_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "sent",
  "channel": "email",
  "recipient": "user@example.com",
  "provider_used": "sendgrid",
  "retry_count": 0,
  "created_at": "2026-05-20T10:00:00Z",
  "updated_at": "2026-05-20T10:00:03Z"
}
```

**Status values:**

| Status | Meaning |
|--------|---------|
| `queued` | Accepted by API, task in queue |
| `processing` | Worker is attempting delivery |
| `sent` | Successfully delivered by a provider |
| `failed` | All retries exhausted; in DLQ |

---

## 10. Scalability Analysis

### At 1M users, typical load patterns:

| Scenario | Volume | System Behaviour |
|----------|--------|-----------------|
| Marketing blast | 1M emails in 1 hour (~278/sec) | Horizontal Celery worker scaling handles this. At 10 workers × 30 tasks/sec = 300 emails/sec capacity per node. |
| Transactional (order confirmations) | ~100/sec steady state | Single worker pool handles comfortably |
| Spike (flash sale) | 50k emails in 5 minutes | Redis queue absorbs burst; workers drain at their own rate; SLA is "delivered", not "delivered in 5 minutes" |

### Scaling levers:

1. **Celery workers**: add worker replicas (horizontal, stateless). No code change required.
2. **Redis**: move to Redis Cluster for queue throughput beyond ~100k tasks/sec.
3. **PostgreSQL**: read replicas for status polling; connection pooling via PgBouncer.
4. **Provider rate limits**: multiple SendGrid subusers or multiple SES sending identities for parallel throughput.

---

## 11. Observability

Every worker task emits structured log lines at each stage:

```json
{"event": "task_started", "notification_id": "...", "idempotency_key": "...", "timestamp": "..."}
{"event": "provider_attempt", "provider": "sendgrid", "notification_id": "...", "timestamp": "..."}
{"event": "provider_failed", "provider": "sendgrid", "error": "...", "fallback": true, "timestamp": "..."}
{"event": "provider_success", "provider": "ses", "notification_id": "...", "timestamp": "..."}
{"event": "task_complete", "notification_id": "...", "status": "sent", "duration_ms": 342, "timestamp": "..."}
```

In production these pipe to a log aggregator (Datadog, Grafana Loki). The key metrics to alert on:

| Metric | Alert Threshold |
|--------|----------------|
| DLQ depth | > 10 unresolved in 10 min |
| `provider_failed` rate | > 5% of sends in 5 min |
| Worker task lag | > 60 seconds behind queue |
| API error rate (5xx) | > 1% of requests |

---

## 12. Future Work

| Feature | Priority | Notes |
|---------|----------|-------|
| SMS channel (Twilio) | High | BaseProvider interface is ready; add `TwilioProvider` |
| Push notifications (FCM/APNs) | High | Same pattern; push tokens stored in user profile |
| Delivery webhooks | Medium | Providers send delivery/bounce events back; store in `notification_events` table |
| User preference management | Medium | Unsubscribe lists, frequency caps, quiet hours |
| Template engine | Medium | Store templates in DB, render at send time, support personalisation |
| Priority queues | Low | Separate Celery queues for transactional vs. marketing; workers can specialise |
| GDPR deletion | Low | Hard-delete notification bodies on user data deletion request |
| Rate limiting per recipient | Low | Prevent notification spam; bucket per user per day |

---

*This document describes the v1 production architecture. Implementation source code lives at [github.com/mayowa-id/notiscope](https://github.com/mayowa-id/notiscope).*
