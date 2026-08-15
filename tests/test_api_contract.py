from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models import DMJob, Rule, WebhookEvent
from tests.helpers import post_signed_webhook


@pytest.fixture()
def db_session() -> Generator[Session, None, None]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    session = testing_session_local()
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


def created_payload(
    event_id: str = "evt_01J8ZQ4K2N7RXA",
    user_id: str = "usr_3b91fe",
    username: str = "arjun.shoots",
    comment_id: str = "cmt_9f2a7c",
    text: str = "PRICE please",
) -> dict:
    return {
        "event_id": event_id,
        "event_type": "comment.created",
        "sent_at": "2026-08-10T09:14:22.481Z",
        "data": {
            "comment_id": comment_id,
            "post_id": "post_44de1b",
            "text": text,
            "created_at": "2026-08-10T09:14:21.900Z",
            "from": {
                "user_id": user_id,
                "username": username,
            },
        },
    }


def test_create_rule_returns_201_and_required_fields(client: TestClient) -> None:
    response = client.post(
        "/rules",
        json={"keyword": "PRICE", "dm_message": "Here's the price list: ..."},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["rule_id"]
    assert body["keyword"] == "PRICE"
    assert body["dm_message"] == "Here's the price list: ..."


def test_stats_returns_exact_required_fields(client: TestClient) -> None:
    response = client.get("/stats")

    assert response.status_code == 200
    assert response.json() == {
        "sent": 0,
        "failed": 0,
        "queued": 0,
        "duplicates_blocked": 0,
    }


def test_webhook_accepts_comment_created_payload(client: TestClient) -> None:
    response = post_signed_webhook(client, created_payload())

    assert response.status_code == 200


def test_webhook_accepts_comment_deleted_payload_with_only_comment_id(client: TestClient) -> None:
    response = post_signed_webhook(
        client,
        {
            "event_id": "evt_deleted_123",
            "event_type": "comment.deleted",
            "sent_at": "2026-08-10T09:15:22.481Z",
            "data": {
                "comment_id": "cmt_9f2a7c",
            },
        },
    )

    assert response.status_code == 200


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200


def test_rule_persists_and_can_be_matched(client: TestClient, db_session: Session) -> None:
    rule_response = client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})

    assert rule_response.status_code == 201
    assert db_session.get(Rule, rule_response.json()["rule_id"]) is not None

    webhook_response = post_signed_webhook(client, created_payload(text="PRICE please"))

    assert webhook_response.status_code == 200
    assert db_session.scalar(select(DMJob)) is not None


def test_keyword_matching_is_case_insensitive(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "price", "dm_message": "Price list"})

    post_signed_webhook(client, created_payload(text="Can I get the PRICE?"))

    assert _job_count(db_session) == 1


def test_keyword_matching_allows_substrings(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})

    post_signed_webhook(client, created_payload(text="send price-list"))

    assert _job_count(db_session) == 1


def test_same_user_and_rule_across_comments_creates_one_job(
    client: TestClient, db_session: Session
) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})

    post_signed_webhook(client, created_payload(event_id="evt_1", comment_id="cmt_1"))
    post_signed_webhook(client, created_payload(event_id="evt_2", comment_id="cmt_2"))

    assert _job_count(db_session) == 1
    assert client.get("/stats").json()["duplicates_blocked"] == 1


def test_same_event_id_redelivery_does_not_create_duplicate_work(
    client: TestClient, db_session: Session
) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    payload = created_payload(event_id="evt_same")

    post_signed_webhook(client, payload)
    post_signed_webhook(client, payload)

    assert _job_count(db_session) == 1
    assert client.get("/stats").json()["duplicates_blocked"] == 0


def test_same_user_can_match_two_different_rules(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})
    client.post("/rules", json={"keyword": "CATALOG", "dm_message": "Catalog"})

    post_signed_webhook(client, created_payload(text="price and catalog please"))

    assert _job_count(db_session) == 2


def test_duplicate_for_one_rule_does_not_block_new_job_for_another_rule(
    client: TestClient, db_session: Session
) -> None:
    rule_a = client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"}).json()
    rule_b = client.post("/rules", json={"keyword": "CATALOG", "dm_message": "Catalog"}).json()

    post_signed_webhook(client, created_payload(event_id="evt_existing", comment_id="cmt_existing", text="price"))
    response = post_signed_webhook(
        client,
        created_payload(
            event_id="evt_mixed",
            comment_id="cmt_mixed",
            text="price and catalog please",
        ),
    )

    assert response.status_code == 200
    assert _job_count_for_rule(db_session, rule_a["rule_id"]) == 1
    assert _job_count_for_rule(db_session, rule_b["rule_id"]) == 1
    assert client.get("/stats").json()["duplicates_blocked"] == 1
    assert db_session.get(WebhookEvent, "evt_mixed") is not None


def test_two_users_can_each_match_same_rule(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})

    post_signed_webhook(client, created_payload(event_id="evt_1", user_id="usr_1", comment_id="cmt_1"))
    post_signed_webhook(client, created_payload(event_id="evt_2", user_id="usr_2", comment_id="cmt_2"))

    assert _job_count(db_session) == 2


def test_stats_counts_queued_and_duplicates(client: TestClient) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})

    post_signed_webhook(client, created_payload(event_id="evt_1", comment_id="cmt_1"))
    post_signed_webhook(client, created_payload(event_id="evt_2", comment_id="cmt_2"))

    assert client.get("/stats").json() == {
        "sent": 0,
        "failed": 0,
        "queued": 1,
        "duplicates_blocked": 1,
    }


def test_comment_deleted_does_not_create_dm_job(client: TestClient, db_session: Session) -> None:
    client.post("/rules", json={"keyword": "PRICE", "dm_message": "Price list"})

    response = post_signed_webhook(
        client,
        {
            "event_id": "evt_deleted_456",
            "event_type": "comment.deleted",
            "sent_at": "2026-08-10T09:15:22.481Z",
            "data": {"comment_id": "cmt_9f2a7c"},
        },
    )

    assert response.status_code == 200
    assert _job_count(db_session) == 0


def _job_count(db_session: Session) -> int:
    return len(db_session.scalars(select(DMJob)).all())


def _job_count_for_rule(db_session: Session, rule_id: str) -> int:
    return len(db_session.scalars(select(DMJob).where(DMJob.rule_id == rule_id)).all())
