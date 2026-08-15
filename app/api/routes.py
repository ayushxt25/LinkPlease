from uuid import uuid4

from fastapi import APIRouter, Depends, status
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import DMJob, Metric, Rule, WebhookEvent
from app.schemas.rule import RuleCreate, RuleResponse
from app.schemas.stats import StatsResponse
from app.schemas.webhook import WebhookPayload

router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
def health() -> dict[str, str]:
    return {"status": "ok"}


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
def receive_webhook(payload: WebhookPayload, db: Session = Depends(get_db)) -> dict[str, str]:
    event = _build_webhook_event(payload)
    db.add(event)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return {"status": "accepted"}

    if payload.event_type == "comment.created" and payload.data.text and payload.data.from_user:
        user_id = payload.data.from_user.user_id
        text = payload.data.text.lower()
        rules = db.scalars(select(Rule)).all()
        for rule in rules:
            if rule.keyword.lower() in text:
                _enqueue_dm_job(db, rule.id, user_id, payload.data.comment_id)

    db.commit()
    return {"status": "accepted"}


@router.get("/stats", response_model=StatsResponse, status_code=status.HTTP_200_OK)
def get_stats(db: Session = Depends(get_db)) -> StatsResponse:
    sent = _count_jobs_by_status(db, "sent")
    failed = _count_jobs_by_status(db, "failed")
    queued = _count_jobs_by_status(db, "queued")
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


def _enqueue_dm_job(db: Session, rule_id: str, user_id: str, comment_id: str) -> None:
    try:
        with db.begin_nested():
            db.add(
                DMJob(
                    id=str(uuid4()),
                    rule_id=rule_id,
                    user_id=user_id,
                    comment_id=comment_id,
                    status="queued",
                )
            )
    except IntegrityError:
        _increment_metric(db, "duplicates_blocked")


def _count_jobs_by_status(db: Session, job_status: str) -> int:
    return db.scalar(select(func.count()).select_from(DMJob).where(DMJob.status == job_status)) or 0


def _get_metric(db: Session, key: str) -> int:
    metric = db.get(Metric, key)
    return metric.value if metric else 0


def _increment_metric(db: Session, key: str) -> None:
    result = db.execute(update(Metric).where(Metric.key == key).values(value=Metric.value + 1))
    if result.rowcount == 0:
        db.add(Metric(key=key, value=1))
