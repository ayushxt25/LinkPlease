import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from app.core.config import settings


def webhook_bytes(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def signature(raw_body: bytes, secret: str | None = None) -> str:
    key = settings.pseudogram_api_key if secret is None else secret
    return "sha256=" + hmac.new(key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()


def post_signed_webhook(client: TestClient, payload: dict):
    raw_body = webhook_bytes(payload)
    return client.post(
        "/webhook",
        content=raw_body,
        headers={"X-PseudoGram-Signature": signature(raw_body), "Content-Type": "application/json"},
    )
