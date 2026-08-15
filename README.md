# LinkPlease Webhook Service

FastAPI backend scaffold for receiving mock Instagram comment webhooks and managing keyword-based DM rules.

## Requirements

- Python 3.11+

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run Locally

```bash
uvicorn app.main:app --reload
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
