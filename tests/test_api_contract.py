from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def test_create_rule_returns_201_and_required_fields() -> None:
    response = client.post(
        "/rules",
        json={"keyword": "PRICE", "dm_message": "Here's the price list: ..."},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["rule_id"]
    assert body["keyword"] == "PRICE"
    assert body["dm_message"] == "Here's the price list: ..."


def test_stats_returns_exact_required_fields() -> None:
    response = client.get("/stats")

    assert response.status_code == 200
    assert response.json() == {
        "sent": 0,
        "failed": 0,
        "queued": 0,
        "duplicates_blocked": 0,
    }


def test_webhook_accepts_comment_created_payload() -> None:
    response = client.post(
        "/webhook",
        json={
            "event_id": "evt_01J8ZQ4K2N7RXA",
            "event_type": "comment.created",
            "sent_at": "2026-08-10T09:14:22.481Z",
            "data": {
                "comment_id": "cmt_9f2a7c",
                "post_id": "post_44de1b",
                "text": "PRICE please 🙏",
                "created_at": "2026-08-10T09:14:21.900Z",
                "from": {
                    "user_id": "usr_3b91fe",
                    "username": "arjun.shoots",
                },
            },
        },
    )

    assert response.status_code == 200


def test_webhook_accepts_comment_deleted_payload_with_only_comment_id() -> None:
    response = client.post(
        "/webhook",
        json={
            "event_id": "evt_deleted_123",
            "event_type": "comment.deleted",
            "sent_at": "2026-08-10T09:15:22.481Z",
            "data": {
                "comment_id": "cmt_9f2a7c",
            },
        },
    )

    assert response.status_code == 200


def test_health_returns_200() -> None:
    response = client.get("/health")

    assert response.status_code == 200
