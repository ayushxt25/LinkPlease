"""delivery reconciliation

Revision ID: 20260816_0003
Revises: 20260816_0002
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa


revision = "20260816_0003"
down_revision = "20260816_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("dm_jobs", sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dm_jobs", sa.Column("next_reconcile_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("dm_jobs", sa.Column("last_reconciled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "dm_jobs",
        sa.Column("reconciliation_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("dm_jobs", "reconciliation_attempt_count")
    op.drop_column("dm_jobs", "last_reconciled_at")
    op.drop_column("dm_jobs", "next_reconcile_at")
    op.drop_column("dm_jobs", "delivered_at")
