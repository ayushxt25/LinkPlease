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
ENVIRONMENT=production
DATABASE_URL=postgresql+psycopg://linkplease:linkplease@localhost:5432/linkplease
REDIS_URL=redis://localhost:6379/0
PSEUDOGRAM_API_KEY=replace-with-api-key
```

Managed URLs beginning with `postgres://` or `postgresql://` are normalized to
`postgresql+psycopg://`. SQLite is rejected when `ENVIRONMENT=production`.

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

Production process commands:

```bash
python -m alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port $PORT
celery -A app.worker.celery_app.celery_app worker --loglevel=info
celery -A app.worker.celery_app.celery_app beat --loglevel=info
```

Docker:

```bash
docker build -t linkplease .
docker run --env-file .env -p 8000:8000 linkplease
```

Render free-tier deployment note:

The Docker image uses `scripts/start_render.sh` to run the FastAPI web server,
one conservative Celery worker, and Celery beat in the same container:

```bash
sh scripts/start_render.sh
```

This is a deployment compromise for Render's free tier. PostgreSQL remains the
durable source of truth, and Redis/Celery are only used to wake background work.
If the free service sleeps or Redis enqueue is missed, queued database work and
accepted reconciliation work are recovered when the process resumes. The
co-located web/worker/beat setup has limited CPU and memory isolation and should
be called out honestly in `FAILURES.md` after real simulator testing.

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

## Manual Simulator Check

After deployment:

1. Run `python -m alembic upgrade head`
2. Start web, worker, and beat processes
3. Create rules:

```bash
curl -X POST https://your-app.example.com/rules ^
  -H "Content-Type: application/json" ^
  -d "{\"keyword\":\"PRICE\",\"dm_message\":\"Here's the price list\"}"
```

4. Start the simulator manually:

```bash
python scripts/run_simulation.py ^
  --webhook-url https://your-app.example.com/webhook ^
  --app-base-url https://your-app.example.com ^
  --count 500 ^
  --duration-seconds 10 ^
  --poll-seconds 60
```

5. Compare simulator truth with:

```bash
curl https://your-app.example.com/stats
```

Then inspect worker/beat/web logs and database rows for discrepancies. Do not call
the simulator from automated tests.

## API

- `POST /webhook`
- `POST /rules`
- `GET /stats`
- `GET /health`
