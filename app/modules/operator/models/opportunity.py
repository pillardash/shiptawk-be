from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin

json_type = JSONB().with_variant(JSON(), "sqlite")


class Opportunity(UuidPrimaryKeyMixin, Base):
    __tablename__ = "opportunities"
    __table_args__ = (
        CheckConstraint("confidence IN ('low', 'medium', 'high')", name="opportunities_confidence"),
        CheckConstraint(
            "approval_level IN ('safe', 'review', 'required')",
            name="opportunities_approval_level",
        ),
        CheckConstraint(
            "status IN ('open', 'planned', 'completed', 'dismissed', 'superseded')",
            name="opportunities_status",
        ),
        CheckConstraint("impact_score BETWEEN 0 AND 100", name="opportunities_impact_score"),
        CheckConstraint("effort_score BETWEEN 0 AND 100", name="opportunities_effort_score"),
        CheckConstraint("urgency_score BETWEEN 0 AND 100", name="opportunities_urgency_score"),
        CheckConstraint(
            "confidence_score IS NULL OR confidence_score BETWEEN 0 AND 100",
            name="opportunities_confidence_score",
        ),
        CheckConstraint(
            "strategic_fit_score IS NULL OR strategic_fit_score BETWEEN 0 AND 100",
            name="opportunities_strategic_fit_score",
        ),
        CheckConstraint(
            "novelty_score IS NULL OR novelty_score BETWEEN 0 AND 100",
            name="opportunities_novelty_score",
        ),
        CheckConstraint(
            "priority_score IS NULL OR priority_score BETWEEN 0 AND 100",
            name="opportunities_priority_score",
        ),
        CheckConstraint("length(cooldown_dedup_key) > 0", name="opportunities_cooldown_key"),
        UniqueConstraint("workspace_id", "id", name="uq_opportunities_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_opportunities_workspace_product_id"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_opportunities_product_id_tenant",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "operator_run_id"],
            ["operator_runs.workspace_id", "operator_runs.product_id", "operator_runs.id"],
            name="fk_opportunities_operator_run_id_tenant",
        ),
        Index(
            "ix_opportunities_product_status_priority",
            "workspace_id",
            "product_id",
            "status",
            "priority_score",
        ),
        Index(
            "ix_opportunities_identity_cooldown",
            "workspace_id",
            "product_id",
            "identity_fingerprint",
            "cooldown_until",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    operator_run_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    opportunity_type: Mapped[str | None] = mapped_column(String(64))
    action_family: Mapped[str | None] = mapped_column(String(64))
    subject_kind: Mapped[str | None] = mapped_column(String(64))
    subject_keys: Mapped[dict[str, object] | None] = mapped_column(json_type)
    identity_fingerprint: Mapped[str | None] = mapped_column(String(71))
    observation_fingerprint: Mapped[str | None] = mapped_column(String(64))
    detector_id: Mapped[str | None] = mapped_column(String(64))
    detector_version: Mapped[str | None] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_ids: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    interpretation: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(10), nullable=False)
    missing_information: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    approval_level: Mapped[str] = mapped_column(String(16), nullable=False)
    measurement_window: Mapped[str] = mapped_column(String(64), nullable=False)
    stop_conditions: Mapped[list[str]] = mapped_column(json_type, nullable=False)
    impact_score: Mapped[int] = mapped_column(Integer, nullable=False)
    effort_score: Mapped[int] = mapped_column(Integer, nullable=False)
    urgency_score: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    strategic_fit_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    novelty_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    priority_score: Mapped[float | None] = mapped_column(Numeric(5, 2))
    score_version: Mapped[str | None] = mapped_column(String(64))
    expected_metric: Mapped[dict[str, object] | None] = mapped_column(json_type)
    effort_band: Mapped[str | None] = mapped_column(String(16))
    lifecycle_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    cooldown_dedup_key: Mapped[str] = mapped_column(String(255), nullable=False)
    cooldown_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provenance: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
