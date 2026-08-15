# LinkPlease Webhook Service

FastAPI backend scaffold for receiving mock Instagram comment webhooks and managing keyword-based DM rules.

## Requirements

- Python 3.11+

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

Set `DATABASE_URL` in `.env` for PostgreSQL, for example:

```text
DATABASE_URL=postgresql+psycopg://linkplease:linkplease@localhost:5432/linkplease
```

Run migrations before starting the app:

```bash
alembic upgrade head
```

## Run Locally

Start PostgreSQL and Redis, then run:

```bash
uvicorn app.main:app --reload
```

In another terminal, start the Celery worker:

```bash
celery -A app.worker.celery_app.celery_app worker --loglevel=info
```

For periodic recovery of queued DB jobs, start Celery beat:

```bash
celery -A app.worker.celery_app.celery_app beat --loglevel=info
```

Celery beat also scans accepted DM jobs for delivery reconciliation. A PseudoGram `202`
means accepted for delivery, not delivered. The worker later calls `GET /v1/dm/{dm_id}`
until the remote status becomes `delivered` or `failed`.

Webhook requests must include `X-PseudoGram-Signature: sha256=<hex>`, computed as
HMAC-SHA256 over the exact raw request body using `PSEUDOGRAM_API_KEY`.

DM job states:

- `queued`: waiting to send or retry
- `sending`: claimed by a sender worker
- `accepted`: PseudoGram accepted the DM, final delivery unknown
- `reconciling`: claimed by a reconciliation worker
- `delivered`: confirmed delivered by reconciliation
- `failed`: permanently abandoned
- `canceled`: local queued job canceled by `comment.deleted`

`comment.deleted` cancels only `queued` jobs for the deleted `comment_id`. `sending`
is treated as potentially in-flight, and `accepted`/`reconciling`/`delivered` jobs are
not unsent or marked failed.

Stats map to states as follows:

- `sent`: `delivered`
- `failed`: `failed`
- `queued`: `queued`, `sending`, `accepted`, `reconciling`
- `duplicates_blocked`: durable duplicate DM attempts blocked by `(rule_id, user_id)`

## Test

```bash
pytest
```

## API

- `POST /webhook`
- `POST /rules`
- `GET /stats`
- `GET /health`
