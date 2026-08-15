import logging

from app.db.session import SessionLocal
from app.services.delivery import (
    process_dm_job_once,
    reconcile_dm_job_once,
    recover_accepted_jobs,
    recover_queued_jobs,
)
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.worker.tasks.process_dm_job")
def process_dm_job(job_id: str) -> str:
    with SessionLocal() as db:
        return process_dm_job_once(db, job_id, schedule_retry=_schedule_retry)


@celery_app.task(name="app.worker.tasks.recover_queued_dm_jobs")
def recover_queued_dm_jobs() -> int:
    with SessionLocal() as db:
        job_ids = recover_queued_jobs(db)
    for job_id in job_ids:
        try:
            process_dm_job.delay(job_id)
        except Exception:
            logger.exception("dm_recovery_enqueue_failed", extra={"job_id": job_id})
    logger.info("dm_recovery_enqueued", extra={"count": len(job_ids)})
    return len(job_ids)


@celery_app.task(name="app.worker.tasks.reconcile_dm_job")
def reconcile_dm_job(job_id: str) -> str:
    with SessionLocal() as db:
        return reconcile_dm_job_once(
            db,
            job_id,
            schedule_reconcile=_schedule_reconcile,
            schedule_send=_schedule_retry,
        )


@celery_app.task(name="app.worker.tasks.recover_accepted_dm_jobs")
def recover_accepted_dm_jobs() -> int:
    with SessionLocal() as db:
        job_ids = recover_accepted_jobs(db)
    for job_id in job_ids:
        try:
            reconcile_dm_job.delay(job_id)
        except Exception:
            logger.exception("dm_reconciliation_enqueue_failed", extra={"job_id": job_id})
    logger.info("dm_reconciliation_recovery_enqueued", extra={"count": len(job_ids)})
    return len(job_ids)


def _schedule_retry(job_id: str, countdown: int) -> None:
    process_dm_job.apply_async(args=[job_id], countdown=countdown)


def _schedule_reconcile(job_id: str, countdown: int) -> None:
    reconcile_dm_job.apply_async(args=[job_id], countdown=countdown)
