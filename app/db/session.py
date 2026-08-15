from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings


def build_engine(database_url: str):
    if database_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        poolclass = StaticPool if database_url in {"sqlite://", "sqlite:///:memory:"} else None
        return create_engine(database_url, connect_args=connect_args, poolclass=poolclass)
    return create_engine(database_url)


engine = build_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
