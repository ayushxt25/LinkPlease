import httpx

from app.core.config import settings


class PseudoGramClient:
    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.pseudogram_api_key
        self.base_url = (base_url or settings.pseudogram_base_url).rstrip("/")

    def send_dm(
        self,
        recipient_user_id: str,
        message: str,
        comment_id: str,
        idempotency_key: str,
    ) -> httpx.Response:
        headers = {
            "X-API-Key": self.api_key,
            "Idempotency-Key": idempotency_key,
        }
        payload = {
            "recipient_user_id": recipient_user_id,
            "message": message,
            "comment_id": comment_id,
        }
        with httpx.Client(timeout=10.0) as client:
            return client.post(f"{self.base_url}/v1/dm/send", json=payload, headers=headers)
