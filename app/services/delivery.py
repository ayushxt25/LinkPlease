import logging
import random
from datetime import datetime, timedelta, timezone
from typing import Protocol

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import DMJob, Rule
from app.services.pseudogram import PseudoGramClient
from app.services.rate_limiter import RedisRateLimiter

logger = logging.getLogger(__name__)


class Scheduler(Protocol):
    def __call__(self, job_id: str, countdown: int) -> None: ...


def process_dm_job_once(
    db: Session,
    job_id: str,
    client: PseudoGramClient | None = None,
    limiter: RedisRateLimiter | None = None,
    schedule_retry: Scheduler | None = None,
) -> str:
    job = claim_job(db, job_id)
    if job is None:
        return "not_claimed"

    retry_after = (limiter or RedisRateLimiter()).acquire(f"{job.id}:{job.attempt_count}")
    if retry_after > 0:
        _retry_later(db, job, retry_after, "application_rate_limited", schedule_retry)
        logger.info("dm_rate_limited", extra={"job_id": job.id, "retry_after": retry_after})
        return "retry"

    logger.info("dm_send_attempt", extra={"job_id": job.id, "attempt_count": job.attempt_count})
    try:
        response = (client or PseudoGramClient()).send_dm(
            recipient_user_id=job.user_id,
            message=job.rule.dm_message,
            comment_id=job.comment_id,
            idempotency_key=job.idempotency_key or _idempotency_key(job),
        )
    except httpx.TimeoutException as exc:
        return _transient_failure(db, job, f"timeout: {exc}", schedule_retry)
    except httpx.TransportError as exc:
        return _transient_failure(db, job, f"network_error: {exc}", schedule_retry)

    if response.status_code in {200, 202}:
        body = response.json()
        now = _now()
        job.status = "accepted"
        job.external_dm_id = body.get("dm_id")
        job.accepted_at = now
        job.updated_at = now
        job.next_reconcile_at = now + timedelta(seconds=settings.dm_reconcile_delay_seconds)
        job.next_attempt_at = None
        job.last_error = None
        db.commit()
        logger.info("dm_accepted", extra={"job_id": job.id, "external_dm_id": job.external_dm_id})
        return "accepted"

    if response.status_code == 429:
        retry_after = _retry_after(response) or 60
        _retry_later(db, job, retry_after, "rate_limited", schedule_retry)
        logger.info("dm_429", extra={"job_id": job.id, "retry_after": retry_after})
        return "retry"

    if response.status_code == 400:
        _mark_failed(db, job, _response_error(response, "invalid_request"))
        return "failed"

    if 500 <= response.status_code < 600:
        return _transient_failure(db, job, _response_error(response, "server_error"), schedule_retry)

    if response.status_code in {408, 409, 425} or response.status_code == 503:
        return _transient_failure(db, job, _response_error(response, "transient_http_error"), schedule_retry)

    _mark_failed(db, job, _response_error(response, f"unexpected_status_{response.status_code}"))
    return "failed"


def claim_job(db: Session, job_id: str) -> DMJob | None:
    now = _now()
    result = db.execute(
        update(DMJob)
        .where(
            DMJob.id == job_id,
            DMJob.status == "queued",
            (DMJob.next_attempt_at.is_(None)) | (DMJob.next_attempt_at <= now),
        )
        .values(
            status="sending",
            attempt_count=DMJob.attempt_count + 1,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    job = db.get(DMJob, job_id)
    if job and job.idempotency_key is None:
        job.idempotency_key = _idempotency_key(job)
    db.commit()
    logger.info("dm_job_claimed", extra={"job_id": job_id})
    return db.get(DMJob, job_id)


def recover_queued_jobs(db: Session, limit: int = 100) -> list[str]:
    now = _now()
    stale_before = now - timedelta(seconds=settings.dm_sending_stale_seconds)
    db.execute(
        update(DMJob)
        .where(DMJob.status == "sending", DMJob.updated_at <= stale_before)
        .values(status="queued", next_attempt_at=now, updated_at=now, last_error="recovered_stale_sending")
    )
    db.commit()
    return list(
        db.scalars(
            select(DMJob.id)
            .where(
                DMJob.status == "queued",
                (DMJob.next_attempt_at.is_(None)) | (DMJob.next_attempt_at <= now),
            )
            .order_by(DMJob.created_at)
            .limit(limit)
        )
    )


def reconcile_dm_job_once(
    db: Session,
    job_id: str,
    client: PseudoGramClient | None = None,
    schedule_reconcile: Scheduler | None = None,
    schedule_send: Scheduler | None = None,
) -> str:
    job = claim_reconciliation(db, job_id)
    if job is None:
        return "not_claimed"

    logger.info("dm_reconciliation_started", extra={"job_id": job.id, "external_dm_id": job.external_dm_id})
    try:
        response = (client or PseudoGramClient()).get_dm(job.external_dm_id)
    except httpx.TimeoutException as exc:
        return _reconcile_later(db, job, f"reconcile_timeout: {exc}", schedule_reconcile)
    except httpx.TransportError as exc:
        return _reconcile_later(db, job, f"reconcile_network_error: {exc}", schedule_reconcile)

    if response.status_code == 200:
        status_value = response.json().get("status")
        if status_value == "queued":
            logger.info("dm_remote_still_queued", extra={"job_id": job.id})
            return _reconcile_later(db, job, "remote_queued", schedule_reconcile)
        if status_value == "delivered":
            now = _now()
            job.status = "delivered"
            job.delivered_at = now
            job.last_reconciled_at = now
            job.next_reconcile_at = None
            job.next_attempt_at = None
            job.updated_at = now
            job.last_error = None
            db.commit()
            logger.info("dm_delivered", extra={"job_id": job.id})
            return "delivered"
        if status_value == "failed":
            return _remote_delivery_failed(db, job, schedule_send)
        return _reconcile_later(db, job, f"unexpected_remote_status:{status_value}", schedule_reconcile)

    if response.status_code >= 500 or response.status_code in {408, 429}:
        return _reconcile_later(db, job, _response_error(response, "reconcile_transient_http_error"), schedule_reconcile)

    return _reconcile_later(db, job, _response_error(response, f"reconcile_unexpected_status_{response.status_code}"), schedule_reconcile)


def claim_reconciliation(db: Session, job_id: str) -> DMJob | None:
    now = _now()
    result = db.execute(
        update(DMJob)
        .where(
            DMJob.id == job_id,
            DMJob.status == "accepted",
            DMJob.external_dm_id.is_not(None),
            (DMJob.next_reconcile_at.is_(None)) | (DMJob.next_reconcile_at <= now),
        )
        .values(
            status="reconciling",
            reconciliation_attempt_count=DMJob.reconciliation_attempt_count + 1,
            last_reconciled_at=now,
            updated_at=now,
        )
    )
    if result.rowcount != 1:
        db.rollback()
        return None
    db.commit()
    return db.get(DMJob, job_id)


def recover_accepted_jobs(db: Session, limit: int = 100) -> list[str]:
    now = _now()
    stale_before = now - timedelta(seconds=settings.dm_reconciling_stale_seconds)
    db.execute(
        update(DMJob)
        .where(DMJob.status == "reconciling", DMJob.updated_at <= stale_before)
        .values(status="accepted", next_reconcile_at=now, updated_at=now, last_error="recovered_stale_reconciling")
    )
    db.commit()
    return list(
        db.scalars(
            select(DMJob.id)
            .where(
                DMJob.status == "accepted",
                DMJob.external_dm_id.is_not(None),
                (DMJob.next_reconcile_at.is_(None)) | (DMJob.next_reconcile_at <= now),
            )
            .order_by(DMJob.accepted_at)
            .limit(limit)
        )
    )


def _transient_failure(db: Session, job: DMJob, error: str, schedule_retry: Scheduler | None) -> str:
    if job.attempt_count >= settings.dm_max_attempts:
        _mark_failed(db, job, f"retry_exhausted: {error}")
        return "failed"
    delay = _backoff_seconds(job.attempt_count)
    _retry_later(db, job, delay, error, schedule_retry)
    logger.info("dm_transient_retry", extra={"job_id": job.id, "retry_after": delay})
    return "retry"


def _reconcile_later(db: Session, job: DMJob, error: str, schedule_reconcile: Scheduler | None) -> str:
    now = _now()
    delay = settings.dm_reconcile_delay_seconds
    job.status = "accepted"
    job.next_reconcile_at = now + timedelta(seconds=delay)
    job.last_error = error
    job.updated_at = now
    db.commit()
    if schedule_reconcile:
        schedule_reconcile(job.id, delay)
    logger.info("dm_reconciliation_retry", extra={"job_id": job.id, "retry_after": delay})
    return "retry"


def _remote_delivery_failed(db: Session, job: DMJob, schedule_send: Scheduler | None) -> str:
    now = _now()
    if job.delivery_attempt_number >= settings.dm_max_delivery_attempts:
        job.status = "failed"
        job.last_error = "remote_delivery_failed: delivery_attempts_exhausted"
        job.next_reconcile_at = None
        job.updated_at = now
        db.commit()
        logger.info("dm_remote_failed_exhausted", extra={"job_id": job.id})
        return "failed"

    job.delivery_attempt_number += 1
    job.idempotency_key = _idempotency_key(job)
    job.external_dm_id = None
    job.accepted_at = None
    job.next_reconcile_at = None
    job.next_attempt_at = now
    job.status = "queued"
    job.last_error = "remote_delivery_failed: scheduling_new_delivery_attempt"
    job.updated_at = now
    db.commit()
    if schedule_send:
        schedule_send(job.id, 0)
    logger.info("dm_new_delivery_attempt_scheduled", extra={"job_id": job.id, "attempt": job.delivery_attempt_number})
    return "retry_send"


def _retry_later(
    db: Session,
    job: DMJob,
    delay_seconds: int,
    error: str,
    schedule_retry: Scheduler | None,
) -> None:
    now = _now()
    job.status = "queued"
    job.next_attempt_at = now + timedelta(seconds=delay_seconds)
    job.last_error = error
    job.updated_at = now
    db.commit()
    if schedule_retry:
        schedule_retry(job.id, delay_seconds)


def _mark_failed(db: Session, job: DMJob, error: str) -> None:
    now = _now()
    job.status = "failed"
    job.last_error = error
    job.updated_at = now
    db.commit()
    logger.info("dm_permanent_failure", extra={"job_id": job.id, "error": error})


def _backoff_seconds(attempt_count: int) -> int:
    base = min(300, 2 ** max(0, attempt_count - 1))
    return base + random.randint(0, min(base, 10))


def _retry_after(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(1, int(value))
    except ValueError:
        return None


def _response_error(response: httpx.Response, fallback: str) -> str:
    try:
        body = response.json()
    except ValueError:
        return fallback
    return body.get("detail") or body.get("error") or fallback


def _idempotency_key(job: DMJob) -> str:
    return f"dm:{job.id}:attempt:{job.delivery_attempt_number}"


def _now() -> datetime:
    return datetime.now(timezone.utc)
