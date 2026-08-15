from celery import Celery

from app.core.config import settings

celery_app = Celery("linkplease", broker=settings.redis_url, backend=settings.celery_result_backend or settings.redis_url)
celery_app.conf.timezone = "UTC"
celery_app.conf.beat_schedule = {
    "recover-queued-dm-jobs": {
        "task": "app.worker.tasks.recover_queued_dm_jobs",
        "schedule": 30.0,
    },
    "recover-accepted-dm-jobs": {
        "task": "app.worker.tasks.recover_accepted_dm_jobs",
        "schedule": 30.0,
    },
}

celery_app.autodiscover_tasks(["app.worker"])
