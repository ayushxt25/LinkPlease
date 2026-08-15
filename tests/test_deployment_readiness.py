from pathlib import Path

import pytest

from app.core.config import Settings
from scripts.run_simulation import build_start_payload


def test_postgres_urls_are_normalized_for_psycopg() -> None:
    assert Settings(database_url="postgres://u:p@host/db").database_url == "postgresql+psycopg://u:p@host/db"
    assert Settings(database_url="postgresql://u:p@host/db").database_url == "postgresql+psycopg://u:p@host/db"


def test_sqlite_is_rejected_in_production() -> None:
    settings = Settings(environment="production", database_url="sqlite:///bad.db")

    with pytest.raises(RuntimeError, match="SQLite"):
        settings.validate_runtime()


def test_env_file_is_ignored_and_example_has_placeholder() -> None:
    assert ".env" in Path(".gitignore").read_text()
    env_example = Path(".env.example").read_text()
    assert "replace-with-api-key" in env_example
    assert "sk-" not in env_example


def test_simulator_start_payload_shape() -> None:
    assert build_start_payload("https://app.example.com/webhook", 500, 10) == {
        "webhook_url": "https://app.example.com/webhook",
        "count": 500,
        "duration_seconds": 10,
    }
