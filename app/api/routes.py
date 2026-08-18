from uuid import uuid4
import hashlib
import hmac
import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from starlette.datastructures import Headers
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import get_db
from app.models import DMJob, Metric, Rule, WebhookEvent
from app.schemas.rule import RuleCreate, RuleResponse
from app.schemas.stats import StatsResponse
from app.schemas.webhook import WebhookPayload

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health", status_code=status.HTTP_200_OK)
def health(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ok", "database": "ok"}


@router.post("/rules", response_model=RuleResponse, status_code=status.HTTP_201_CREATED)
def create_rule(payload: RuleCreate, db: Session = Depends(get_db)) -> RuleResponse:
    rule = Rule(
        id=str(uuid4()),
        keyword=payload.keyword,
        dm_message=payload.dm_message,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return RuleResponse(rule_id=rule.id, keyword=rule.keyword, dm_message=rule.dm_message)


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def receive_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    raw_body = await request.body()
    if settings.verify_webhook_signatures:
        _verify_webhook_signature(raw_body, request.headers.get("X-PseudoGram-Signature"), request.headers)
    else:
        logger.warning("webhook_signature_verification_disabled")
    payload = WebhookPayload.model_validate_json(raw_body)
    logger.info("webhook_received", extra={"event_id": payload.event_id, "event_type": payload.event_type})
    dm_job_ids: list[str] = []
    event = _build_webhook_event(payload)
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        logger.info("webhook_duplicate_event", extra={"event_id": payload.event_id})
        return {"status": "accepted"}

    if payload.event_type == "comment.created" and payload.data.text and payload.data.from_user:
        user_id = payload.data.from_user.user_id
        text = payload.data.text.lower()
        rules = db.scalars(select(Rule)).all()
        for rule in rules:
            if rule.keyword.lower() in text:
                job_id = _create_dm_job(db, rule.id, user_id, payload.data.comment_id)
                if job_id:
                    dm_job_ids.append(job_id)
    elif payload.event_type == "comment.deleted":
        _cancel_queued_jobs_for_comment(db, payload.data.comment_id)

    db.commit()
    _enqueue_dm_jobs(dm_job_ids)
    return {"status": "accepted"}


@router.get("/stats", response_model=StatsResponse, status_code=status.HTTP_200_OK)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    sent = _count_jobs_by_status(db, "delivered")
    failed = _count_jobs_by_status(db, "failed")
    queued = _count_unresolved_jobs(db)
    duplicates_blocked = _get_metric(db, "duplicates_blocked")
    return StatsResponse(
        sent=sent,
        failed=failed,
        queued=queued,
        duplicates_blocked=duplicates_blocked,
    )


def _build_webhook_event(payload: WebhookPayload) -> WebhookEvent:
    from_user = payload.data.from_user
    return WebhookEvent(
        event_id=payload.event_id,
        event_type=payload.event_type,
        comment_id=payload.data.comment_id,
        post_id=payload.data.post_id,
        user_id=from_user.user_id if from_user else None,
        username=from_user.username if from_user else None,
        text=payload.data.text,
        sent_at=payload.sent_at,
        comment_created_at=payload.data.created_at,
        raw_payload=payload.model_dump(mode="json", by_alias=True),
    )


def _create_dm_job(db: Session, rule_id: str, user_id: str, comment_id: str) -> str | None:
    job_id = str(uuid4())
    try:
        with db.begin_nested():
            db.add(
                DMJob(
                    id=job_id,
                    rule_id=rule_id,
                    user_id=user_id,
                    comment_id=comment_id,
                    status="queued",
                )
            )
    except IntegrityError:
        _increment_metric(db, "duplicates_blocked")
        logger.info("dm_duplicate_blocked", extra={"rule_id": rule_id, "user_id": user_id})
        return None
    logger.info("dm_job_created", extra={"job_id": job_id, "rule_id": rule_id})
    return job_id


def _count_jobs_by_status(db: Session, job_status: str) -> int:
    return db.scalar(select(func.count()).select_from(DMJob).where(DMJob.status == job_status)) or 0


def _count_unresolved_jobs(db: Session) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(DMJob).where(DMJob.status.not_in(["failed", "delivered", "canceled"]))
        )
        or 0
    )


def _get_metric(db: Session, key: str) -> int:
    metric = db.get(Metric, key)
    return metric.value if metric else 0


def _increment_metric(db: Session, key: str) -> None:
    result = db.execute(update(Metric).where(Metric.key == key).values(value=Metric.value + 1))
    if result.rowcount == 0:
        db.add(Metric(key=key, value=1))


def _enqueue_dm_jobs(job_ids: list[str]) -> None:
    if not job_ids:
        return
    from app.worker.tasks import process_dm_job

    for job_id in job_ids:
        try:
            process_dm_job.delay(job_id)
        except Exception:
            logger.exception("dm_enqueue_failed", extra={"job_id": job_id})


def _verify_webhook_signature(raw_body: bytes, signature: str | None, headers: Headers | None = None) -> None:
    try:
        secret = settings.require_pseudogram_api_key()
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    expected_hex = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    expected_signature = f"sha256={expected_hex}"
    received_signature = signature.strip() if signature else None
    if not received_signature or not hmac.compare_digest(expected_signature, received_signature):
        _log_invalid_signature(raw_body, signature, expected_signature, secret, headers)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook signature")


def _log_invalid_signature(
    raw_body: bytes,
    signature: str | None,
    expected_signature: str,
    secret: str,
    headers: Headers | None,
) -> None:
    content_type = headers.get("content-type") if headers else None
    user_agent = headers.get("user-agent") if headers else None
    key_fingerprint = hashlib.sha256(secret.encode("utf-8")).hexdigest()[:12]
    logger.warning(
        "webhook_signature_invalid "
        "header_exists=%s "
        "received_length=%s "
        "starts_sha256=%s "
        "expected_length=%s "
        "api_key_length=%s "
        "api_key_fingerprint=%s "
        "body_length=%s "
        "content_type=%s "
        "user_agent=%s "
        "has_surrounding_whitespace=%s "
        "stripped_length=%s "
        "signature_equals_strip=%s",
        signature is not None,
        len(signature) if signature else 0,
        signature.strip().startswith("sha256=") if signature else False,
        len(expected_signature),
        len(secret),
        key_fingerprint,
        len(raw_body),
        content_type,
        user_agent,
        signature != signature.strip() if signature else False,
        len(signature.strip()) if signature else 0,
        signature == signature.strip() if signature else False,
    )


def _cancel_queued_jobs_for_comment(db: Session, comment_id: str) -> None:
    result = db.execute(
        update(DMJob)
        .where(DMJob.comment_id == comment_id, DMJob.status == "queued")
        .values(status="canceled", canceled_at=func.now(), updated_at=func.now(), last_error="comment_deleted")
    )
    if result.rowcount:
        logger.info("dm_jobs_canceled_for_deleted_comment", extra={"comment_id": comment_id, "count": result.rowcount})
