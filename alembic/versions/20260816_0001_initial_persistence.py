"""initial persistence

Revision ID: 20260816_0001
Revises:
Create Date: 2026-08-16
"""
from alembic import op
import sqlalchemy as sa


revision = "20260816_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "metrics",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.bulk_insert(
        sa.table(
            "metrics",
            sa.column("key", sa.String(length=64)),
            sa.column("value", sa.Integer()),
        ),
        [{"key": "duplicates_blocked", "value": 0}],
    )
    op.create_table(
        "rules",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("keyword", sa.String(length=255), nullable=False),
        sa.Column("dm_message", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "webhook_events",
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("comment_id", sa.String(length=128), nullable=False),
        sa.Column("post_id", sa.String(length=128), nullable=True),
        sa.Column("user_id", sa.String(length=128), nullable=True),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("comment_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    op.create_table(
        "dm_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("rule_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("comment_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("external_dm_id", sa.String(length=255), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("canceled_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["rule_id"], ["rules.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("rule_id", "user_id", name="uq_dm_jobs_rule_user"),
    )


def downgrade() -> None:
    op.drop_table("dm_jobs")
    op.drop_table("webhook_events")
    op.drop_table("rules")
    op.drop_table("metrics")
