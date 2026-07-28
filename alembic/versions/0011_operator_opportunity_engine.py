"""add opportunity-engine persistence and immutable run contracts

Revision ID: 0011_operator_opportunity_engine
Revises: 0010_search_intelligence
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_operator_opportunity_engine"
down_revision: str | None = "0010_search_intelligence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LEGACY_RUN_KIND = "legacy_planning"
INITIAL_LIFECYCLE_VERSION = "1"
JSONB = postgresql.JSONB(astext_type=sa.Text())


def _id() -> sa.Column[object]:
    return sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False)


def _tenant_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
    ]


def _tenant_constraints(table: str) -> tuple[sa.Constraint, ...]:
    return (
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name=f"fk_{table}_product_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=f"pk_{table}"),
        sa.UniqueConstraint("workspace_id", "id", name=f"uq_{table}_workspace_id_id"),
        sa.UniqueConstraint("workspace_id", "product_id", "id", name=f"uq_{table}_tenant_id"),
    )


def upgrade() -> None:
    with op.batch_alter_table("operator_runs") as batch:
        batch.add_column(
            sa.Column("run_kind", sa.String(32), server_default=LEGACY_RUN_KIND, nullable=False)
        )
        batch.add_column(sa.Column("request_fingerprint", sa.String(64)))
        batch.add_column(sa.Column("input_fingerprint", sa.String(64)))
        for name in (
            "input_schema_version",
            "detector_suite_version",
            "quality_policy_version",
            "scoring_policy_version",
            "cooldown_policy_version",
        ):
            batch.add_column(sa.Column(name, sa.String(64)))
        batch.add_column(sa.Column("as_of", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("lease_owner", sa.String(255)))
        batch.add_column(sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("failure_category", sa.String(64)))
        batch.add_column(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            )
        )
        batch.drop_constraint("uq_operator_runs_workspace_idempotency", type_="unique")
        batch.create_unique_constraint(
            "uq_operator_runs_workspace_product_id", ["workspace_id", "product_id", "id"]
        )
        batch.create_unique_constraint(
            "uq_operator_runs_workspace_product_idempotency",
            ["workspace_id", "product_id", "idempotency_key"],
        )
        batch.create_check_constraint(
            "operator_runs_run_kind", "run_kind IN ('legacy_planning', 'opportunity_detection')"
        )
        batch.create_check_constraint(
            "operator_runs_lease_pair",
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
        )
        batch.drop_constraint("operator_runs_status", type_="check")
        batch.create_check_constraint(
            "operator_runs_status",
            "status IN ('pending', 'running', 'completed', 'failed', 'stopped')",
        )

    opportunity_columns = (
        sa.Column("opportunity_type", sa.String(64)),
        sa.Column("action_family", sa.String(64)),
        sa.Column("subject_kind", sa.String(64)),
        sa.Column("subject_keys", JSONB),
        sa.Column("identity_fingerprint", sa.String(71)),
        sa.Column("observation_fingerprint", sa.String(64)),
        sa.Column("detector_id", sa.String(64)),
        sa.Column("detector_version", sa.String(64)),
        sa.Column("confidence_score", sa.Numeric(5, 2)),
        sa.Column("strategic_fit_score", sa.Numeric(5, 2)),
        sa.Column("novelty_score", sa.Numeric(5, 2)),
        sa.Column("priority_score", sa.Numeric(5, 2)),
        sa.Column("score_version", sa.String(64)),
        sa.Column("expected_metric", JSONB),
        sa.Column("effort_band", sa.String(16)),
        sa.Column("lifecycle_version", sa.String(64)),
    )
    with op.batch_alter_table("opportunities") as batch:
        for column in opportunity_columns:
            batch.add_column(column)
        batch.create_unique_constraint(
            "uq_opportunities_workspace_product_id", ["workspace_id", "product_id", "id"]
        )
        batch.drop_constraint("fk_opportunities_operator_run_id_tenant", type_="foreignkey")
        batch.create_foreign_key(
            "fk_opportunities_operator_run_id_tenant",
            "operator_runs",
            ["workspace_id", "product_id", "operator_run_id"],
            ["workspace_id", "product_id", "id"],
        )
        batch.drop_constraint("opportunities_status", type_="check")
        batch.create_check_constraint(
            "opportunities_status",
            "status IN ('open', 'planned', 'completed', 'dismissed', 'superseded')",
        )
        for score in ("confidence_score", "strategic_fit_score", "novelty_score", "priority_score"):
            batch.create_check_constraint(
                f"opportunities_{score}", f"{score} IS NULL OR {score} BETWEEN 0 AND 100"
            )
    op.execute(
        sa.text(
            "UPDATE opportunities SET lifecycle_version = :version WHERE lifecycle_version IS NULL"
        ).bindparams(version=INITIAL_LIFECYCLE_VERSION)
    )
    op.create_index(
        "ix_opportunities_product_status_priority",
        "opportunities",
        ["workspace_id", "product_id", "status", "priority_score"],
    )
    op.create_index(
        "ix_opportunities_identity_cooldown",
        "opportunities",
        ["workspace_id", "product_id", "identity_fingerprint", "cooldown_until"],
    )

    op.create_table(
        "operator_run_snapshots",
        _id(),
        *_tenant_columns(),
        sa.Column("operator_run_id", sa.Uuid(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("input_payload", JSONB, nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("source_cutoff", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_tenant_constraints("operator_run_snapshots"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_operator_run_snapshots_run_tenant",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "operator_run_id", name="uq_operator_run_snapshots_run"
        ),
    )
    op.create_table(
        "opportunity_evaluations",
        _id(),
        *_tenant_columns(),
        sa.Column("operator_run_id", sa.Uuid(), nullable=False),
        sa.Column("accepted_opportunity_id", sa.Uuid()),
        sa.Column("detector_id", sa.String(64), nullable=False),
        sa.Column("detector_version", sa.String(64), nullable=False),
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("evaluation_key", sa.String(64), nullable=False),
        sa.Column("reason_codes", JSONB, nullable=False),
        sa.Column("input_fingerprint", sa.String(64)),
        sa.Column("identity_fingerprint", sa.String(71)),
        sa.Column("observation_fingerprint", sa.String(64)),
        sa.Column("candidate_snapshot", JSONB),
        sa.Column("diagnostics", JSONB, nullable=False),
        *[
            sa.Column(name, sa.String(64), nullable=False)
            for name in (
                "input_schema_version",
                "detector_suite_version",
                "quality_policy_version",
                "scoring_policy_version",
                "cooldown_policy_version",
                "score_version",
            )
        ],
        sa.Column("priority_score", sa.Numeric(5, 2)),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_tenant_constraints("opportunity_evaluations"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_opportunity_evaluations_run_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "accepted_opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_evaluations_opportunity_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "outcome IN ('accepted','no_recommendation','stopped','suppressed_duplicate',"
            "'suppressed_cooldown','superseded_in_run')",
            name="opportunity_evaluations_outcome",
        ),
        sa.CheckConstraint(
            "priority_score IS NULL OR priority_score BETWEEN 0 AND 100",
            name="opportunity_evaluations_priority",
        ),
        sa.CheckConstraint(
            "(outcome = 'accepted' AND candidate_snapshot IS NOT NULL) OR outcome <> 'accepted'",
            name="opportunity_evaluations_accepted_candidate",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "operator_run_id",
            "evaluation_key",
            name="uq_opportunity_evaluations_run_key",
        ),
    )
    op.create_index(
        "ix_opportunity_evaluations_run",
        "opportunity_evaluations",
        ["workspace_id", "product_id", "operator_run_id"],
    )
    op.create_table(
        "opportunity_evidence",
        _id(),
        *_tenant_columns(),
        sa.Column("evaluation_id", sa.Uuid(), nullable=False),
        sa.Column("opportunity_id", sa.Uuid()),
        sa.Column("evidence_key", sa.String(255), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("source_record_type", sa.String(64), nullable=False),
        sa.Column("source_id", sa.Uuid()),
        sa.Column("source_run_id", sa.Uuid()),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period", JSONB, nullable=False),
        sa.Column("metrics", JSONB, nullable=False),
        sa.Column("public_safe_summary", sa.String(1000), nullable=False),
        sa.Column("confidence_score", sa.Numeric(5, 2), nullable=False),
        sa.Column("freshness_status", sa.String(16), nullable=False),
        sa.Column("quality_warnings", JSONB, nullable=False),
        sa.Column("source_fingerprint", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_tenant_constraints("opportunity_evidence"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "evaluation_id"],
            [
                "opportunity_evaluations.workspace_id",
                "opportunity_evaluations.product_id",
                "opportunity_evaluations.id",
            ],
            name="fk_opportunity_evidence_evaluation_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_evidence_opportunity_tenant",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "evaluation_id",
            "evidence_key",
            name="uq_opportunity_evidence_evaluation_key",
        ),
        sa.CheckConstraint(
            "confidence_score BETWEEN 0 AND 100", name="opportunity_evidence_confidence"
        ),
    )
    op.create_index(
        "ix_opportunity_evidence_opportunity",
        "opportunity_evidence",
        ["workspace_id", "product_id", "opportunity_id"],
    )
    op.create_table(
        "opportunity_feedback",
        _id(),
        *_tenant_columns(),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("evaluation_id", sa.Uuid()),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("usefulness", sa.Integer()),
        sa.Column("reason_code", sa.String(64), nullable=False),
        sa.Column("comment", sa.String(500)),
        sa.Column("evaluation_eligible", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_tenant_constraints("opportunity_feedback"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_feedback_opportunity_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "evaluation_id"],
            [
                "opportunity_evaluations.workspace_id",
                "opportunity_evaluations.product_id",
                "opportunity_evaluations.id",
            ],
            name="fk_opportunity_feedback_evaluation_tenant",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "usefulness IS NULL OR usefulness BETWEEN 1 AND 5",
            name="opportunity_feedback_usefulness",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name="fk_opportunity_feedback_actor", ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "idempotency_key",
            name="uq_opportunity_feedback_tenant_idempotency",
        ),
    )
    op.create_index(
        "ix_opportunity_feedback_opportunity",
        "opportunity_feedback",
        ["workspace_id", "product_id", "opportunity_id"],
    )
    op.create_table(
        "opportunity_status_events",
        _id(),
        *_tenant_columns(),
        sa.Column("opportunity_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("previous_status", sa.String(20), nullable=False),
        sa.Column("new_status", sa.String(20), nullable=False),
        sa.Column("reason_code", sa.String(64)),
        sa.Column("feedback_id", sa.Uuid()),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        *_tenant_constraints("opportunity_status_events"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_status_events_opportunity_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "feedback_id"],
            [
                "opportunity_feedback.workspace_id",
                "opportunity_feedback.product_id",
                "opportunity_feedback.id",
            ],
            name="fk_opportunity_status_events_feedback_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name="fk_opportunity_status_events_actor",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_opportunity_status_events_opportunity",
        "opportunity_status_events",
        ["workspace_id", "product_id", "opportunity_id"],
    )


def downgrade() -> None:
    op.execute("UPDATE opportunities SET status = 'dismissed' WHERE status = 'superseded'")
    op.execute("UPDATE operator_runs SET status = 'stopped' WHERE status = 'pending'")
    op.execute(
        """
        WITH duplicate_keys AS (
            SELECT workspace_id, idempotency_key
            FROM operator_runs
            GROUP BY workspace_id, idempotency_key
            HAVING count(*) > 1
        )
        UPDATE operator_runs AS runs
        SET idempotency_key = left(runs.product_id::text || ':' || runs.idempotency_key, 255)
        FROM duplicate_keys
        WHERE runs.workspace_id = duplicate_keys.workspace_id
          AND runs.idempotency_key = duplicate_keys.idempotency_key
        """
    )
    for table in (
        "opportunity_status_events",
        "opportunity_feedback",
        "opportunity_evidence",
        "opportunity_evaluations",
        "operator_run_snapshots",
    ):
        op.drop_table(table)
    op.drop_index("ix_opportunities_identity_cooldown", table_name="opportunities")
    op.drop_index("ix_opportunities_product_status_priority", table_name="opportunities")
    with op.batch_alter_table("opportunities") as batch:
        batch.drop_constraint("fk_opportunities_operator_run_id_tenant", type_="foreignkey")
        batch.create_foreign_key(
            "fk_opportunities_operator_run_id_tenant",
            "operator_runs",
            ["workspace_id", "operator_run_id"],
            ["workspace_id", "id"],
        )
        batch.drop_constraint("uq_opportunities_workspace_product_id", type_="unique")
        batch.drop_constraint("opportunities_status", type_="check")
        batch.create_check_constraint(
            "opportunities_status", "status IN ('open', 'planned', 'completed', 'dismissed')"
        )
        for score in ("confidence_score", "strategic_fit_score", "novelty_score", "priority_score"):
            batch.drop_constraint(f"opportunities_{score}", type_="check")
        for name in (
            "opportunity_type",
            "action_family",
            "subject_kind",
            "subject_keys",
            "identity_fingerprint",
            "observation_fingerprint",
            "detector_id",
            "detector_version",
            "confidence_score",
            "strategic_fit_score",
            "novelty_score",
            "priority_score",
            "score_version",
            "expected_metric",
            "effort_band",
            "lifecycle_version",
        ):
            batch.drop_column(name)
    with op.batch_alter_table("operator_runs") as batch:
        batch.drop_constraint("uq_operator_runs_workspace_product_idempotency", type_="unique")
        batch.drop_constraint("uq_operator_runs_workspace_product_id", type_="unique")
        batch.create_unique_constraint(
            "uq_operator_runs_workspace_idempotency", ["workspace_id", "idempotency_key"]
        )
        batch.drop_constraint("operator_runs_status", type_="check")
        batch.create_check_constraint(
            "operator_runs_status", "status IN ('running', 'completed', 'failed', 'stopped')"
        )
        batch.drop_constraint("operator_runs_run_kind", type_="check")
        batch.drop_constraint("operator_runs_lease_pair", type_="check")
        for name in (
            "run_kind",
            "request_fingerprint",
            "input_fingerprint",
            "input_schema_version",
            "detector_suite_version",
            "quality_policy_version",
            "scoring_policy_version",
            "cooldown_policy_version",
            "as_of",
            "lease_owner",
            "lease_expires_at",
            "failure_category",
            "updated_at",
        ):
            batch.drop_column(name)
