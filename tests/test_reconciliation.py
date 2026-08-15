from collections.abc import Generator
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DMJob, Rule
from app.services.delivery import (
    claim_reconciliation,
    process_dm_job_once,
    reconcile_dm_job_once,
    recover_accepted_jobs,
    recover_queued_jobs,
)


class FakeReconcileClient:
    def __init__(self, response: httpx.Response | None = None, exc: Exception | None = None) -> None:
        self.response = response
        self.exc = exc
        self.get_calls = 0
        self.send_calls = 0

    def get_dm(self, dm_id: str):
        self.get_calls += 1
        if self.exc:
            raise self.exc
        return self.response

    def send_dm(self, recipient_user_id: str, message: str, comment_id: str, idempotency_key: str):
        self.send_calls += 1
        return httpx.Response(202, json={"dm_id": "dm_new", "status": "queued"})


class ExplodingLimiter:
    def acquire(self, member: str) -> int:
        raise AssertionError("GET reconciliation must not use POST send limiter")


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
def client(db_session: Session) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        try:
            yield db_session
        finally:
            db_session.expunge_all()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_remote_queued_remains_accepted_and_schedules_later(db_session: Session, client: TestClient) -> None:
    job = make_accepted_job(db_session)
    scheduled: list[int] = []
    fake = FakeReconcileClient(httpx.Response(200, json={"dm_id": "dm_1", "status": "queued"}))

    result = reconcile_dm_job_once(
        db_session,
        job.id,
        client=fake,
        schedule_reconcile=lambda job_id, countdown: scheduled.append(countdown),
    )

    db_session.refresh(job)
    assert result == "retry"
    assert job.status == "accepted"
    assert job.next_reconcile_at is not None
    assert scheduled
    assert fake.send_calls == 0
    assert client.get("/stats").json()["sent"] == 0


def test_remote_delivered_updates_stats(db_session: Session, client: TestClient) -> None:
    job = make_accepted_job(db_session)
    fake = FakeReconcileClient(httpx.Response(200, json={"dm_id": "dm_1", "status": "delivered"}))

    result = reconcile_dm_job_once(db_session, job.id, client=fake)

    db_session.refresh(job)
    assert result == "delivered"
    assert job.status == "delivered"
    assert job.delivered_at is not None
    assert client.get("/stats").json()["sent"] == 1
    assert client.get("/stats").json()["queued"] == 0


def test_remote_failed_creates_new_logical_delivery_attempt(db_session: Session) -> None:
    job = make_accepted_job(db_session)
    old_key = job.idempotency_key
    sent: list[str] = []
    fake = FakeReconcileClient(httpx.Response(200, json={"dm_id": "dm_1", "status": "failed"}))

    result = reconcile_dm_job_once(
        db_session,
        job.id,
        client=fake,
        schedule_send=lambda job_id, countdown: sent.append(job_id),
    )

    db_session.refresh(job)
    assert result == "retry_send"
    assert job.status == "queued"
    assert job.delivery_attempt_number == 2
    assert job.idempotency_key != old_key
    assert job.idempotency_key == f"dm:{job.id}:attempt:2"
    assert job.external_dm_id is None
    assert sent == [job.id]


def test_remote_failed_exhausted_marks_failed(db_session: Session, client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.delivery.settings.dm_max_delivery_attempts", 1)
    job = make_accepted_job(db_session)

    reconcile_dm_job_once(
        db_session,
        job.id,
        client=FakeReconcileClient(httpx.Response(200, json={"dm_id": "dm_1", "status": "failed"})),
    )

    assert db_session.get(DMJob, job.id).status == "failed"
    assert client.get("/stats").json()["failed"] == 1


def test_reconciliation_http_failure_keeps_accepted(db_session: Session) -> None:
    job = make_accepted_job(db_session)

    reconcile_dm_job_once(db_session, job.id, client=FakeReconcileClient(exc=httpx.ReadTimeout("slow")))

    db_session.refresh(job)
    assert job.status == "accepted"
    assert job.external_dm_id == "dm_1"
    assert job.next_reconcile_at is not None


def test_two_reconciliation_workers_cannot_claim_same_job(db_session: Session) -> None:
    job = make_accepted_job(db_session)

    first = claim_reconciliation(db_session, job.id)
    second = claim_reconciliation(db_session, job.id)

    assert first is not None
    assert second is None


def test_accepted_jobs_recovered_by_periodic_scan(db_session: Session) -> None:
    job = make_accepted_job(db_session)

    assert recover_accepted_jobs(db_session) == [job.id]


def test_delivered_jobs_are_not_recovered_for_send_or_reconcile(db_session: Session) -> None:
    job = make_accepted_job(db_session)
    job.status = "delivered"
    db_session.commit()

    assert recover_queued_jobs(db_session) == []
    assert recover_accepted_jobs(db_session) == []


def test_reconciliation_get_does_not_use_send_rate_limiter(db_session: Session) -> None:
    job = make_accepted_job(db_session)

    result = reconcile_dm_job_once(
        db_session,
        job.id,
        client=FakeReconcileClient(httpx.Response(200, json={"dm_id": "dm_1", "status": "queued"})),
    )

    assert result == "retry"
    assert process_dm_job_once(db_session, job.id, client=FakeReconcileClient(), limiter=ExplodingLimiter()) == "not_claimed"


def test_fresh_send_after_remote_failure_uses_new_idempotency_key(db_session: Session) -> None:
    job = make_accepted_job(db_session)
    reconcile_dm_job_once(
        db_session,
        job.id,
        client=FakeReconcileClient(httpx.Response(200, json={"dm_id": "dm_1", "status": "failed"})),
    )
    fake = FakeReconcileClient()

    process_dm_job_once(db_session, job.id, client=fake, limiter=type("L", (), {"acquire": lambda self, member: 0})())

    assert db_session.get(DMJob, job.id).external_dm_id == "dm_new"


def make_accepted_job(db: Session) -> DMJob:
    rule = Rule(id="rule_r", keyword="PRICE", dm_message="Price list")
    job = DMJob(
        id="job_r",
        rule_id=rule.id,
        user_id="usr_1",
        comment_id="cmt_1",
        status="accepted",
        external_dm_id="dm_1",
        accepted_at=datetime.now(timezone.utc),
        next_reconcile_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        delivery_attempt_number=1,
        idempotency_key="dm:job_r:attempt:1",
    )
    db.add(rule)
    db.add(job)
    db.commit()
    return job
