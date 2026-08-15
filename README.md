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

## Test

```bash
pytest
```

## API

- `POST /webhook`
- `POST /rules`
- `GET /stats`
- `GET /health`
