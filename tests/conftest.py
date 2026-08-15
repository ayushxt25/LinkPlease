import pytest

from app.core.config import settings


@pytest.fixture(autouse=True)
def pseudogram_test_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "pseudogram_api_key", "test-secret")
