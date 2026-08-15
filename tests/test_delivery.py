from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DMJob, Rule
from app.services.delivery import claim_job, process_dm_job_once, recover_queued_jobs
from app.services.pseudogram import PseudoGramClient


class AllowLimiter:
    def acquire(self, member: str) -> int:
        return 0


class BlockLimiter:
    def __init__(self, retry_after: int) -> None:
        self.retry_after = retry_after

    def acquire(self, member: str) -> int:
        return self.retry_after


class FakeClient:
    def __init__(self, responses: list[httpx.Response] | None = None, exc: Exception | None = None) -> None:
        self.responses = responses or []
        self.exc = exc
        self.calls: list[dict] = []

    def send_dm(self, recipient_user_id: str, message: str, comment_id: str, idempotency_key: str):
        self.calls.append(
            {
                "recipient_user_id": recipient_user_id,
                "message": message,
                "comment_id": comment_id,
                "idempotency_key": idempotency_key,
            }
        )
        if self.exc:
            raise self.exc
        return self.responses.pop(0)


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture()
def client(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    from app.worker.tasks import process_dm_job

    monkeypatch.setattr(process_dm_job, "delay", lambda job_id: None)

    def override_get_db() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_successful_202_stores_dm_id_and_keeps_stats_unresolved(
    db_session: Session, client: TestClient
) -> None:
    job = make_job(db_session)
    fake = FakeClient([httpx.Response(202, json={"dm_id": "dm_7c1f0a", "status": "queued"})])

    result = process_dm_job_once(db_session, job.id, client=fake, limiter=AllowLimiter())

    db_session.refresh(job)
    assert result == "accepted"
    assert fake.calls[0]["recipient_user_id"] == "usr_1"
    assert fake.calls[0]["message"] == "Price list"
    assert fake.calls[0]["comment_id"] == "cmt_1"
    assert fake.calls[0]["idempotency_key"] == f"dm:{job.id}:attempt:1"
    assert job.external_dm_id == "dm_7c1f0a"
    assert job.status == "accepted"
    assert client.get("/stats").json()["sent"] == 0
    assert client.get("/stats").json()["queued"] == 1


def test_http_500_retries_with_same_idempotency_key(db_session: Session) -> None:
    job = make_job(db_session)
    scheduled: list[tuple[str, int]] = []

    process_dm_job_once(
        db_session,
        job.id,
        client=FakeClient([httpx.Response(500, json={"error": "internal_error"})]),
        limiter=AllowLimiter(),
        schedule_retry=lambda job_id, countdown: scheduled.append((job_id, countdown)),
    )
    key = db_session.get(DMJob, job.id).idempotency_key
    due_now(db_session, job.id)
    process_dm_job_once(
        db_session,
        job.id,
        client=FakeClient([httpx.Response(500, json={"error": "internal_error"})]),
        limiter=AllowLimiter(),
        schedule_retry=lambda job_id, countdown: scheduled.append((job_id, countdown)),
    )

    assert db_session.get(DMJob, job.id).status == "queued"
    assert db_session.get(DMJob, job.id).idempotency_key == key
    assert scheduled[0][0] == job.id


def test_network_timeout_retries_with_same_idempotency_key(db_session: Session) -> None:
    job = make_job(db_session)

    process_dm_job_once(db_session, job.id, client=FakeClient(exc=httpx.ReadTimeout("slow")), limiter=AllowLimiter())
    key = db_session.get(DMJob, job.id).idempotency_key
    due_now(db_session, job.id)
    process_dm_job_once(db_session, job.id, client=FakeClient(exc=httpx.ReadTimeout("slow")), limiter=AllowLimiter())

    assert db_session.get(DMJob, job.id).status == "queued"
    assert db_session.get(DMJob, job.id).idempotency_key == key


def test_429_honors_retry_after(db_session: Session) -> None:
    job = make_job(db_session)
    scheduled: list[int] = []

    process_dm_job_once(
        db_session,
        job.id,
        client=FakeClient([httpx.Response(429, headers={"Retry-After": "17"}, json={"error": "rate_limited"})]),
        limiter=AllowLimiter(),
        schedule_retry=lambda job_id, countdown: scheduled.append(countdown),
    )

    db_session.refresh(job)
    assert job.status == "queued"
    assert scheduled == [17]
    assert job.next_attempt_at is not None


def test_400_permanently_fails_without_retry(db_session: Session, client: TestClient) -> None:
    job = make_job(db_session)
    scheduled: list[int] = []

    process_dm_job_once(
        db_session,
        job.id,
        client=FakeClient([httpx.Response(400, json={"error": "invalid_request", "detail": "bad"})]),
        limiter=AllowLimiter(),
        schedule_retry=lambda job_id, countdown: scheduled.append(countdown),
    )

    assert db_session.get(DMJob, job.id).status == "failed"
    assert scheduled == []
    assert client.get("/stats").json()["failed"] == 1


def test_retry_exhaustion_marks_failed(db_session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.delivery.settings.dm_max_attempts", 1)
    job = make_job(db_session)

    process_dm_job_once(
        db_session,
        job.id,
        client=FakeClient([httpx.Response(500, json={"error": "internal_error"})]),
        limiter=AllowLimiter(),
    )

    assert db_session.get(DMJob, job.id).status == "failed"


def test_client_reads_api_key_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    class DummyHttpxClient:
        def __init__(self, timeout: float) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> None:
            pass

        def post(self, url: str, json: dict, headers: dict):
            captured.update(headers=headers, json=json, url=url)
            return httpx.Response(202, json={"dm_id": "dm_1", "status": "queued"})

    monkeypatch.setattr("app.services.pseudogram.settings.pseudogram_api_key", "test-key")
    monkeypatch.setattr("app.services.pseudogram.httpx.Client", DummyHttpxClient)

    PseudoGramClient(base_url="https://example.test").send_dm("usr", "msg", "cmt", "idem")

    assert captured["headers"]["X-API-Key"] == "test-key"
    assert captured["headers"]["Idempotency-Key"] == "idem"


def test_webhook_does_not_synchronously_send_external_dm(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    called = False

    def fail_if_called(*args, **kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(PseudoGramClient, "send_dm", fail_if_called)
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    response = client.post("/webhook", json=created_payload())

    assert response.status_code == 200
    assert called is False


def test_recovery_finds_queued_db_job(db_session: Session) -> None:
    job = make_job(db_session)

    assert recover_queued_jobs(db_session) == [job.id]


def test_recovery_requeues_stale_sending_job(db_session: Session) -> None:
    job = make_job(db_session)
    job.status = "sending"
    job.updated_at = datetime.now(timezone.utc) - timedelta(seconds=600)
    db_session.commit()

    assert recover_queued_jobs(db_session) == [job.id]
    assert db_session.get(DMJob, job.id).status == "queued"


def test_two_workers_cannot_claim_same_job(db_session: Session) -> None:
    job = make_job(db_session)

    first = claim_job(db_session, job.id)
    second = claim_job(db_session, job.id)

    assert first is not None
    assert second is None


def test_application_rate_limiter_defers_without_http_call(db_session: Session) -> None:
    job = make_job(db_session)
    fake = FakeClient([httpx.Response(202, json={"dm_id": "dm_1", "status": "queued"})])

    process_dm_job_once(db_session, job.id, client=fake, limiter=BlockLimiter(11))

    assert fake.calls == []
    assert db_session.get(DMJob, job.id).status == "queued"


def make_job(db: Session) -> DMJob:
    rule = Rule(id="rule_1", keyword="PRICE", dm_message="Price list")
    job = DMJob(id="job_1", rule_id=rule.id, user_id="usr_1", comment_id="cmt_1", status="queued")
    db.add(rule)
    db.add(job)
    db.commit()
    return job


def due_now(db: Session, job_id: str) -> None:
    job = db.get(DMJob, job_id)
    job.next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()


def created_payload() -> dict:
    return {
        "event_id": "evt_delivery",
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {
            "comment_id": "cmt_delivery",
            "post_id": "post_44de1b",
            "text": "PRICE please",
            "created_at": "2026-08-10T09:14:21.900Z",
            "from": {"user_id": "usr_delivery", "username": "user"},
        },
    }
