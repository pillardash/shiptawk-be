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


class OpportunityEvidence(UuidPrimaryKeyMixin, Base):
    __tablename__ = "opportunity_evidence"
    __table_args__ = (
        CheckConstraint(
            "confidence_score BETWEEN 0 AND 100", name="opportunity_evidence_confidence"
        ),
        UniqueConstraint("workspace_id", "id", name="uq_opportunity_evidence_workspace_id_id"),
        UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_opportunity_evidence_tenant_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "product_id",
            "evaluation_id",
            "evidence_key",
            name="uq_opportunity_evidence_evaluation_key",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_opportunity_evidence_product_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "evaluation_id"],
            [
                "opportunity_evaluations.workspace_id",
                "opportunity_evaluations.product_id",
                "opportunity_evaluations.id",
            ],
            name="fk_opportunity_evidence_evaluation_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "opportunity_id"],
            ["opportunities.workspace_id", "opportunities.product_id", "opportunities.id"],
            name="fk_opportunity_evidence_opportunity_tenant",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_opportunity_evidence_opportunity",
            "workspace_id",
            "product_id",
            "opportunity_id",
        ),
    )
    workspace_id: Mapped[UUID] = mapped_column(nullable=False)
    product_id: Mapped[UUID] = mapped_column(nullable=False)
    evaluation_id: Mapped[UUID] = mapped_column(nullable=False)
    opportunity_id: Mapped[UUID | None] = mapped_column()
    evidence_key: Mapped[str] = mapped_column(String(255), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_record_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[UUID | None] = mapped_column()
    source_run_id: Mapped[UUID | None] = mapped_column()
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    metrics: Mapped[dict[str, object]] = mapped_column(json_type, nullable=False, default=dict)
    public_safe_summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    confidence_score: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_warnings: Mapped[list[str]] = mapped_column(json_type, nullable=False, default=list)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
