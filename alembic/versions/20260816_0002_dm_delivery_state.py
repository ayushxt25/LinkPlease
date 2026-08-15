"""dm delivery state

Revision ID: 20260816_0002
Revises: 20260816_0001
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa


revision = "20260816_0002"
down_revision = "20260816_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dm_jobs", sa.Column("delivery_attempt_number", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("dm_jobs", sa.Column("idempotency_key", sa.String(length=255), nullable=True))
    op.add_column("dm_jobs", sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("dm_jobs", "accepted_at")
    op.drop_column("dm_jobs", "idempotency_key")
    op.drop_column("dm_jobs", "delivery_attempt_number")
