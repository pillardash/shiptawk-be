"""add generic tenant-scoped LLM execution ledger

Revision ID: 0012_llm_executions
Revises: 0011_operator_opportunity_engine
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012_llm_executions"
down_revision: str | None = "0011_operator_opportunity_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "llm_executions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("use_case", sa.String(100), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("route_name", sa.String(100), nullable=False),
        sa.Column("route_snapshot", JSONB, nullable=False),
        sa.Column("sanitized_metadata", JSONB, nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(255)),
        sa.Column("lease_token", sa.Uuid()),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("result_fingerprint", sa.String(64)),
        sa.Column("validated_output_snapshot", JSONB),
        sa.Column("failure_category", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_executions"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_llm_executions_product_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("workspace_id", "product_id", "id", name="uq_llm_executions_tenant_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "use_case",
            "idempotency_key",
            name="uq_llm_executions_tenant_use_case_idempotency",
        ),
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')", name="llm_executions_status"
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_token IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_token IS NOT NULL AND "
            "lease_expires_at IS NOT NULL)",
            name="llm_executions_lease_triplet",
        ),
    )
    op.create_index("ix_llm_executions_workspace_id", "llm_executions", ["workspace_id"])
    op.create_index("ix_llm_executions_product_id", "llm_executions", ["product_id"])
    op.create_table(
        "llm_execution_attempts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("model", sa.String(255), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("provider_request_id", sa.String(255)),
        sa.Column("finish_reason", sa.String(64)),
        sa.Column("refusal", sa.Boolean(), nullable=False),
        sa.Column("usage", JSONB, nullable=False),
        sa.Column("estimated_cost", sa.Numeric(20, 12)),
        sa.Column("pricing_version", sa.String(64), nullable=False),
        sa.Column("latency_ms", sa.Integer()),
        sa.Column("error_category", sa.String(64)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_llm_execution_attempts"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "execution_id"],
            ["llm_executions.workspace_id", "llm_executions.product_id", "llm_executions.id"],
            name="fk_llm_execution_attempts_execution_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "execution_id",
            "attempt_number",
            name="uq_llm_execution_attempts_execution_number",
        ),
    )
    op.create_index(
        "ix_llm_execution_attempts_workspace_id", "llm_execution_attempts", ["workspace_id"]
    )
    op.create_index(
        "ix_llm_execution_attempts_product_id", "llm_execution_attempts", ["product_id"]
    )
    op.create_index(
        "ix_llm_execution_attempts_execution_id", "llm_execution_attempts", ["execution_id"]
    )
    op.execute(
        """
        CREATE FUNCTION reject_llm_execution_attempt_mutation() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'llm_execution_attempts is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER llm_execution_attempts_append_only
        BEFORE UPDATE OR DELETE ON llm_execution_attempts
        FOR EACH ROW EXECUTE FUNCTION reject_llm_execution_attempt_mutation()
        """
    )


def downgrade() -> None:
    op.drop_table("llm_execution_attempts")
    op.execute("DROP FUNCTION reject_llm_execution_attempt_mutation()")
    op.drop_table("llm_executions")
