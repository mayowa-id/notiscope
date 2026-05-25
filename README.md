# Notiscope

Notiscope is a highly reliable, asynchronous notification system capable of processing millions of messages. It handles email delivery with built-in idempotency (no duplicate sends), automatic retries, provider fallback routing (SendGrid → AWS SES), and a Dead Letter Queue (DLQ) for unprocessable messages.

## Architecture

![Notiscope Architecture](C:\Users\NEW USER\.gemini\antigravity-ide\brain\588145e3-cbda-46ea-83b6-3a72fc3735aa\notiscope_architecture_1779706645728.png)

Read the full system design in the [Technical Specification](docs/tech-spec.md) (or [View on Notion/Gist](your-link-here)).

## Live Demo

**API Base URL:** `https://notiscope.vercel.app` *(Replace with your actual Vercel URL)*

> **Note on Vercel:** Vercel is a serverless platform, which means it runs the FastAPI endpoint perfectly to accept requests and queue them. However, to actually process the queue and send the emails, the Celery `worker` and `beat` processes need to be running (either locally or on a container platform like Railway/Fly.io).

## Quick Start (Local Development)

The easiest way to run the full stack (FastAPI, Celery Worker, Celery Beat, Redis, PostgreSQL) is using Docker Compose.

**1. Clone the repository**
```bash
git clone https://github.com/mayowa-id/notiscope.git
cd notiscope
```

**2. Setup environment variables**
```bash
cp .env.example .env
```
*Fill in your `SENDGRID_API_KEY` and `AWS_ACCESS_KEY_ID` if you want real emails to send.*

**3. Start the application**
```bash
docker compose up --build
```
*The database migrations will run automatically on startup.*

The API will be available at [http://localhost:8000/docs](http://localhost:8000/docs).

## Environment Variables

| Variable | Required | Description | Example |
|----------|----------|-------------|---------|
| `DATABASE_URL` | Yes | PostgreSQL connection string | `postgresql://notiscope:secret@db:5432/notiscope` |
| `REDIS_URL` | Yes | Redis connection string | `redis://redis:6379/0` |
| `CELERY_BROKER_URL` | Yes | Celery message broker | `redis://redis:6379/0` |
| `CELERY_RESULT_BACKEND` | Yes | Celery result store | `redis://redis:6379/1` |
| `SENDGRID_API_KEY` | No* | Primary email provider API key | `SG.xxxxxxxxxx` |
| `AWS_ACCESS_KEY_ID` | No* | Fallback provider AWS key | `AKIA...` |
| `AWS_SECRET_ACCESS_KEY` | No* | Fallback provider AWS secret | `...` |

*\*If provider keys are missing, the worker will gracefully fail the task, queue it for retry, and eventually route it to the Dead Letter Queue.*

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
  "provider_used": "sendgrid",
  "error_message": null,
  "created_at": "2026-05-25T10:00:00Z",
  "updated_at": "2026-05-25T10:00:05Z"
}
```

## Running Tests

To run the test suite locally (uses an in-memory SQLite database):

```bash
make test-local
# or
pytest tests/ -v
```

To run tests inside the Docker container against the full environment:
```bash
make test
```
