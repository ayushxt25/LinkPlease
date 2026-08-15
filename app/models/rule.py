from datetime import datetime, timezone

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Rule(Base):
    __tablename__ = "rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    keyword: Mapped[str] = mapped_column(String(255), nullable=False)
    dm_message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    dm_jobs = relationship("DMJob", back_populates="rule")
