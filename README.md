# Notiscope

Notiscope is an asynchronous notification system designed for 1M+ users. It handles email delivery with built-in idempotency (no duplicate sends), automatic retries with exponential backoff, provider fallback routing (AWS SES → Postmark), and a Dead Letter Queue (DLQ) for unprocessable messages.

## Architecture

```
Client (POST /notify)
    |
    v
[FastAPI API] ── writes idempotency key ──> [PostgreSQL]
    |
    v  (enqueue task)
[Redis Queue]
    |
    v  (dequeue + process)
[Celery Worker]
    |
    ├── Try AWS SES (primary)
    |       |
    |       ├── Success --> Update DB (status=sent)
    |       |
    |       └── Failure --> Fallback
    |                         |
    |                         v
    |                   Try Postmark (fallback)
    |                         |
    |                         ├── Success --> Update DB (status=sent)
    |                         |
    |                         └── Failure --> Retry (exponential backoff + jitter)
    |                                             |
    |                                             └── Max retries exceeded --> DLQ
    v
[Celery Beat] ── scans DLQ every 5 min ──> Alerts on stuck notifications
```

Read the full system design in the [Technical Specification](tech-spec.md).

## Live API

**Base URL:** `http://13.60.84.255:8000`

**Swagger Docs:** [http://13.60.84.255:8000/docs](http://13.60.84.255:8000/docs)

The API is deployed on an AWS EC2 instance running the full Docker Compose stack (FastAPI + Celery Worker + Celery Beat + PostgreSQL 18 + Redis 7).

### Deployment
*(Reviewer note: This system is fully deployed on AWS infrastructure.)*

![AWS EC2 Instance Running](ec2-screenshot.png)
![Successful Email Delivery](email-screenshot.png)


## Quick Start (Local Development)

**1. Clone the repository**
```bash
git clone https://github.com/mayowa-id/notiscope.git
cd notiscope
```

**2. Setup environment variables**
```bash
cp .env.example .env
```
Fill in your `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, and `POSTMARK_SERVER_TOKEN` to enable email delivery.

**3. Start the application**
```bash
docker compose up --build
```
Database migrations run automatically on startup via the entrypoint script.

The API will be available at  [http://localhost:8000/docs](http://localhost:8000/docs).

## Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string | `postgresql://notiscope:secret@db:5432/notiscope` |
| `REDIS_URL` | Yes | Redis connection string | `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | Yes | Celery message broker | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Yes | Celery result store | `redis://redis:6379/1` |
| `AWS_ACCESS_KEY_ID` | Yes | AWS SES access key (primary provider) | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | Yes | AWS SES secret key | `...` |
| `AWS_REGION` | Yes | AWS region for SES | `eu-north-1` |
| `SES_FROM_EMAIL` | Yes | Verified sender email for SES | `noreply@yourdomain.com` |
| `POSTMARK_SERVER_TOKEN` | No* | Postmark API token (fallback provider) | `xxxx-xxxx-xxxx` |
| `POSTMARK_FROM_EMAIL` | No* | Verified sender email for Postmark | `noreply@yourdomain.com` |

*If fallback provider keys are missing, the worker will exhaust retries and route the notification to the Dead Letter Queue for manual review.*

## API Reference

### 1. Send Notification
**`POST /notify`**

Queues a notification for delivery. Requires an `Idempotency-Key` header to prevent duplicate sends.

**Headers:**
- `Idempotency-Key` (string, required): A unique key for this request.

**Body:**
```json
{
  "recipient": "user@example.com",
  "subject": "Welcome to Notiscope!",
  "body": "This is a reliable notification."
}
```

**Response (202 Accepted):**
```json
{
  "notification_id": "uuid-here",
  "status": "queued",
  "message": "Notification queued for processing."
}
```

### 2. Check Status
**`GET /notify/{id}`**

Check the current status of a queued notification.

**Response (200 OK):**
```json
{
  "id": "uuid-here",
  "status": "sent",
  "recipient": "user@example.com",
  "provider_used": "ses",
  "error_message": null,
  "created_at": "2026-05-26T09:00:00Z",
  "updated_at": "2026-05-26T09:00:02Z"
}
```

### 3. Health Check
**`GET /health`**

Returns service health status.

Live Email Capture Testing
In addition to the automated test suite, the full notification pipeline was validated end to end using Mailpit as a local mail capture tool. This involved firing real POST /notify requests and confirming delivery,idempotency enforcement, retry logic, provider fallback, and DLQ routing , all observable in the Mailpit UI without sending live emails.

## Running Tests
To run the test suite locally (uses an in-memory SQLite database):

```bash
make test-local
# or
pytest tests/ -v
```

To run tests inside the Docker container:
```bash
make test
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| API Framework | FastAPI |
| Task Queue | Celery + Redis |
| Database | PostgreSQL 18 |
| Primary Email Provider | AWS SES |
| Fallback Email Provider | Postmark |
| Containerization | Docker Compose |
| Hosting | AWS EC2 (t2.micro, Free Tier) |
