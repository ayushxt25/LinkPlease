from collections.abc import Generator
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DMJob, WebhookEvent
from app.services.delivery import process_dm_job_once, recover_queued_jobs
from tests.helpers import post_signed_webhook, signature, webhook_bytes


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
        try:
            yield db_session
        finally:
            db_session.expunge_all()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_valid_signature_accepted(client: TestClient) -> None:
    assert post_signed_webhook(client, created_payload()).status_code == 200


def test_signature_header_surrounding_whitespace_is_accepted(client: TestClient) -> None:
    raw = webhook_bytes(created_payload(event_id="evt_sig_spaces"))

    response = client.post(
        "/webhook",
        content=raw,
        headers={
            "X-PseudoGram-Signature": f"  {signature(raw)}  ",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200


def test_literal_raw_unicode_body_signature_is_accepted(client: TestClient) -> None:
    raw = (
        b'{"event_id":"evt_literal_unicode","event_type":"comment.created",'
        b'"sent_at":"2026-08-10T09:14:22.481Z","data":{"comment_id":"cmt_literal",'
        b'"post_id":"post_1","text":"PRICE please \xf0\x9f\x99\x8f",'
        b'"created_at":"2026-08-10T09:14:21.900Z",'
        b'"from":{"user_id":"usr_literal","username":"literal"}}}'
    )

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": signature(raw), "Content-Type": "application/json"},
    )

    assert response.status_code == 200


def test_signature_must_match_exact_serialized_bytes(client: TestClient) -> None:
    compact = (
        b'{"event_id":"evt_exact_bytes","event_type":"comment.deleted",'
        b'"sent_at":"2026-08-10T09:15:22.481Z","data":{"comment_id":"cmt_exact"}}'
    )
    pretty = (
        b'{\n  "event_id": "evt_exact_bytes",\n  "event_type": "comment.deleted",\n'
        b'  "sent_at": "2026-08-10T09:15:22.481Z",\n'
        b'  "data": {"comment_id": "cmt_exact"}\n}'
    )

    rejected = client.post(
        "/webhook",
        content=pretty,
        headers={"X-PseudoGram-Signature": signature(compact), "Content-Type": "application/json"},
    )
    accepted = client.post(
        "/webhook",
        content=pretty,
        headers={"X-PseudoGram-Signature": signature(pretty), "Content-Type": "application/json"},
    )

    assert signature(compact) != signature(pretty)
    assert rejected.status_code == 401
    assert accepted.status_code == 200


def test_candidate_diagnostics_do_not_accept_compact_signature_for_pretty_body(client: TestClient) -> None:
    compact = b'{"event_id":"evt_candidate","event_type":"comment.deleted","data":{"comment_id":"cmt_candidate"}}'
    pretty = b'{\n  "event_id": "evt_candidate",\n  "event_type": "comment.deleted",\n  "data": {"comment_id": "cmt_candidate"}\n}'

    response = client.post(
        "/webhook",
        content=pretty,
        headers={"X-PseudoGram-Signature": signature(compact), "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_candidate_diagnostics_do_not_accept_newline_variant(client: TestClient) -> None:
    raw = b'{"event_id":"evt_newline_variant","event_type":"comment.deleted","data":{"comment_id":"cmt_newline"}}'

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": signature(raw + b"\n"), "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_invalid_signature_rejected_and_persists_nothing(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    raw = webhook_bytes(created_payload(event_id="evt_bad_sig"))

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": "sha256=bad", "Content-Type": "application/json"},
    )

    assert response.status_code == 401
    assert db_session.get(WebhookEvent, "evt_bad_sig") is None
    assert db_session.scalars(select(DMJob)).all() == []


def test_signature_disabled_processes_invalid_signature(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "verify_webhook_signatures", False)
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    raw = webhook_bytes(created_payload(event_id="evt_sig_disabled", comment_id="cmt_sig_disabled"))

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": "sha256=bad", "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    assert db_session.get(WebhookEvent, "evt_sig_disabled") is not None
    assert db_session.scalar(select(DMJob).where(DMJob.comment_id == "cmt_sig_disabled")) is not None


def test_signature_disabled_processes_missing_signature(
    client: TestClient,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "verify_webhook_signatures", False)
    raw = webhook_bytes(created_payload(event_id="evt_no_sig_disabled", comment_id="cmt_no_sig_disabled"))

    response = client.post("/webhook", content=raw, headers={"Content-Type": "application/json"})

    assert response.status_code == 200
    assert db_session.get(WebhookEvent, "evt_no_sig_disabled") is not None


def test_malformed_signature_still_fails(client: TestClient) -> None:
    raw = webhook_bytes(created_payload(event_id="evt_malformed_sig"))

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": "sha256=not-a-valid-hmac", "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_missing_signature_rejected(client: TestClient, db_session: Session) -> None:
    response = client.post("/webhook", content=webhook_bytes(created_payload(event_id="evt_missing")))

    assert response.status_code == 401
    assert db_session.get(WebhookEvent, "evt_missing") is None


def test_missing_api_key_fails_clearly(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "pseudogram_api_key", "")
    raw = webhook_bytes(created_payload(event_id="evt_no_key"))

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": "sha256=anything", "Content-Type": "application/json"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "PSEUDOGRAM_API_KEY is required"


def test_signature_uses_exact_raw_bytes(client: TestClient) -> None:
    payload = created_payload(event_id="evt_raw")
    raw = webhook_bytes(payload)
    pretty_raw = b'{\n  "event_id": "evt_raw"\n}'

    response = client.post(
        "/webhook",
        content=raw,
        headers={"X-PseudoGram-Signature": signature(pretty_raw), "Content-Type": "application/json"},
    )

    assert response.status_code == 401


def test_deleted_cancels_queued_job_and_excludes_stats(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    post_signed_webhook(client, created_payload(comment_id="cmt_del"))

    response = post_signed_webhook(client, deleted_payload(event_id="evt_deleted", comment_id="cmt_del"))

    job = db_session.scalar(select(DMJob).where(DMJob.comment_id == "cmt_del"))
    assert response.status_code == 200
    assert job.status == "canceled"
    assert job.canceled_at is not None
    assert client.get("/stats").json() == {"sent": 0, "failed": 0, "queued": 0, "duplicates_blocked": 0}


def test_canceled_job_is_not_sent_or_recovered(db_session: Session) -> None:
    job = make_job(db_session, "canceled")

    assert process_dm_job_once(db_session, job.id, client=object(), limiter=object()) == "not_claimed"
    assert recover_queued_jobs(db_session) == []


def test_deleting_after_delivered_keeps_sent(client: TestClient, db_session: Session) -> None:
    job = make_job(db_session, "delivered")
    job.delivered_at = datetime.now(timezone.utc)
    db_session.commit()

    post_signed_webhook(client, deleted_payload(event_id="evt_delivered_delete", comment_id=job.comment_id))

    assert db_session.get(DMJob, job.id).status == "delivered"
    assert client.get("/stats").json()["sent"] == 1


def test_deleting_accepted_job_does_not_resend(client: TestClient, db_session: Session) -> None:
    job = make_job(db_session, "accepted")
    job.external_dm_id = "dm_accepted"
    db_session.commit()

    post_signed_webhook(client, deleted_payload(event_id="evt_accepted_delete", comment_id=job.comment_id))

    assert db_session.get(DMJob, job.id).status == "accepted"
    assert recover_queued_jobs(db_session) == []


def test_deleted_cancels_multiple_queued_jobs_for_comment(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    client.post("/rules", json={"keyword": "CATALOG", "dm_message": "Catalog"})
    post_signed_webhook(client, created_payload(comment_id="cmt_multi", text="price catalog"))

    post_signed_webhook(client, deleted_payload(event_id="evt_multi_del", comment_id="cmt_multi"))

    assert {job.status for job in db_session.scalars(select(DMJob)).all()} == {"canceled"}


def test_duplicate_deleted_event_id_is_harmless(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    post_signed_webhook(client, created_payload(comment_id="cmt_dup_del"))
    payload = deleted_payload(event_id="evt_dup_del", comment_id="cmt_dup_del")

    first = post_signed_webhook(client, payload)
    second = post_signed_webhook(client, payload)

    assert first.status_code == 200
    assert second.status_code == 200
    assert db_session.scalar(select(DMJob)).status == "canceled"


def test_stats_state_mapping(client: TestClient, db_session: Session) -> None:
    make_job(db_session, "queued", job_id="queued")
    make_job(db_session, "sending", job_id="sending")
    make_job(db_session, "accepted", job_id="accepted")
    make_job(db_session, "reconciling", job_id="reconciling")
    delivered = make_job(db_session, "delivered", job_id="delivered")
    delivered.delivered_at = datetime.now(timezone.utc)
    make_job(db_session, "failed", job_id="failed")
    make_job(db_session, "canceled", job_id="canceled")
    db_session.commit()

    assert client.get("/stats").json() == {"sent": 1, "failed": 1, "queued": 4, "duplicates_blocked": 0}


def test_500_event_volume_regression(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    client.post("/rules", json={"keyword": "CATALOG", "dm_message": "Catalog"})
    seen_pairs: set[tuple[str, str]] = set()
    expected_jobs = 0
    expected_duplicates = 0
    expected_events = set()

    for index in range(500):
        if index % 50 == 0 and index > 0:
            payload = deleted_payload(event_id=f"evt_del_{index}", comment_id=f"cmt_{index - 1}")
            expected_events.add(payload["event_id"])
            post_signed_webhook(client, payload)
            continue
        event_id = f"evt_{index if index % 40 else index - 1}"
        user_id = f"usr_{index % 120}"
        text = "PRICE catalog" if index % 5 == 0 else ("price please" if index % 3 == 0 else "hello")
        payload = created_payload(event_id=event_id, user_id=user_id, comment_id=f"cmt_{index}", text=text)
        is_new_event = event_id not in expected_events
        expected_events.add(event_id)
        if is_new_event:
            matched_rules = []
            if "price" in text.lower():
                matched_rules.append("PRICE")
            if "catalog" in text.lower():
                matched_rules.append("CATALOG")
            for rule in matched_rules:
                pair = (rule, user_id)
                if pair in seen_pairs:
                    expected_duplicates += 1
                else:
                    seen_pairs.add(pair)
                    expected_jobs += 1
        post_signed_webhook(client, payload)

    assert len(db_session.scalars(select(WebhookEvent)).all()) == len(expected_events)
    assert len(db_session.scalars(select(DMJob)).all()) == expected_jobs
    assert client.get("/stats").json()["duplicates_blocked"] == expected_duplicates


def created_payload(
    event_id: str = "evt_1",
    user_id: str = "usr_1",
    comment_id: str = "cmt_1",
    text: str = "PRICE please",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {
            "comment_id": comment_id,
            "post_id": "post_1",
            "text": text,
            "created_at": "2026-08-10T09:14:21.900Z",
            "from": {"user_id": user_id, "username": user_id},
        },
    }


def deleted_payload(event_id: str, comment_id: str) -> dict:
    return {
        "event_id": event_id,
        "event_type": "comment.deleted",
        "sent_at": "2026-08-10T09:15:22.481Z",
        "data": {"comment_id": comment_id},
    }


def make_job(db: Session, status: str, job_id: str = "job_1") -> DMJob:
    from app.models import Rule

    rule = Rule(id=f"rule_{job_id}", keyword="PRICE", dm_message="Price list")
    job = DMJob(
        id=job_id,
        rule_id=rule.id,
        user_id=f"usr_{job_id}",
        comment_id=f"cmt_{job_id}",
        status=status,
    )
    db.add(rule)
    db.add(job)
    db.commit()
    return job
