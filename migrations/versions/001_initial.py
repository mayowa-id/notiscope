"""Initial migration: create notifications and idempotency_keys tables

Revision ID: 001_initial
Revises: 
Create Date: 2026-05-25

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # notifications table
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("recipient", sa.String(255), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("channel", sa.String(50), nullable=False, server_default="email"),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("provider_used", sa.String(50), nullable=True),
        sa.Column("provider_response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_notifications_idempotency_key", "notifications", ["idempotency_key"], unique=True)
    op.create_index("ix_notifications_status_updated_at", "notifications", ["status", "updated_at"])
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])

    # idempotency_keys table
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(255), primary_key=True, nullable=False),
        sa.Column(
            "notification_id",
            sa.String(36),
            sa.ForeignKey("notifications.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cached_response", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now() + interval '24 hours'"),
        ),
    )


def downgrade() -> None:
    op.drop_table("idempotency_keys")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_status_updated_at", table_name="notifications")
    op.drop_index("ix_notifications_idempotency_key", table_name="notifications")
    op.drop_table("notifications")
