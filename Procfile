web: uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}
worker: celery -A app.worker.celery_app.celery_app worker --loglevel=info
beat: celery -A app.worker.celery_app.celery_app beat --loglevel=info
