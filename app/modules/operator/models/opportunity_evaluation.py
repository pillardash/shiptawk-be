from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class OpportunityEvaluation(UuidPrimaryKeyMixin, Base):
    __tablename__ = "opportunity_evaluations"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('accepted','no_recommendation','stopped','suppressed_duplicate',"
            "'suppressed_cooldown','superseded_in_run')",
            name="opportunity_evaluations_outcome",
        ),
        CheckConstraint(
            "priority_score IS NULL OR priority_score BETWEEN 0 AND 100",
            name="opportunity_evaluations_priority",
        ),
        CheckConstraint(
            "(outcome = 'accepted' AND candidate_snapshot IS NOT NULL) OR outcome <> 'accepted'",
            name="opportunity_evaluations_accepted_candidate",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_opportunity_evaluations_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_opportunity_evaluations_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "operator_run_id",
            "evaluation_key",
            name="uq_opportunity_evaluations_run_key",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_opportunity_evaluations_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_opportunity_evaluations_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "accepted_opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_evaluations_opportunity_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_opportunity_evaluations_run",
            "workspace_id",
            "product_id",
            "operator_run_id",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    operator_run_id: Mapped[UUID] = mapped_column(nullable=False)
    accepted_opportunity_id: Mapped[UUID | None] = mapped_column()
    detector_id: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    evaluation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(json_type, nullable=False, default=list)
    input_fingerprint: Mapped[str | None] = mapped_column(String(64))
    identity_fingerprint: Mapped[str | None] = mapped_column(String(71))
    observation_fingerprint: Mapped[str | None] = mapped_column(String(64))
    candidate_snapshot: Mapped[dict[str, object] | None] = mapped_column(json_type)
    diagnostics: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    input_schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    detector_suite_version: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    scoring_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    cooldown_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    score_version: Mapped[str] = mapped_column(String(64), nullable=False)
    priority_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
