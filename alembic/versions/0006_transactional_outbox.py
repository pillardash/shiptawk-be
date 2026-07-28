"""add transactional outbox

Revision ID: 0006_transactional_outbox
Revises: 0005_oauth_audit_indexes
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_transactional_outbox"
down_revision: str | None = "0005_oauth_audit_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("event_name", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(), nullable=True),
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("product_id", sa.Uuid(), nullable=True),
        sa.Column("metadata_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_category", sa.String(length=64), nullable=True),
        sa.CheckConstraint("schema_version > 0", name="ck_outbox_events_schema_version_positive"),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_events_attempts_nonnegative"),
        sa.CheckConstraint(
            "status IN ('pending', 'publishing', 'published', 'failed')",
            name="ck_outbox_events_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'publishing' AND lease_expires_at IS NOT NULL) OR "
            "(status <> 'publishing' AND lease_expires_at IS NULL)",
            name="ck_outbox_events_lease_matches_status",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(metadata_payload) = 'object'",
            name="ck_outbox_events_metadata_object",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
        sa.UniqueConstraint(
            "event_name",
            "idempotency_key",
            name="uq_outbox_events_event_idempotency",
        ),
    )
    op.create_index("ix_outbox_events_aggregate_id", "outbox_events", ["aggregate_id"])
    op.create_index("ix_outbox_events_workspace_id", "outbox_events", ["workspace_id"])
    op.create_index("ix_outbox_events_product_id", "outbox_events", ["product_id"])
    op.create_index(
        "ix_outbox_events_claim",
        "outbox_events",
        ["status", "available_at", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_claim", table_name="outbox_events")
    op.drop_index("ix_outbox_events_product_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_workspace_id", table_name="outbox_events")
    op.drop_index("ix_outbox_events_aggregate_id", table_name="outbox_events")
    op.drop_table("outbox_events")
