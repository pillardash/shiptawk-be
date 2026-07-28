from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UuidPrimaryKeyMixin


class EvidenceItem(UuidPrimaryKeyMixin, Base):
    __tablename__ = "evidence_items"
    __table_args__ = (
        CheckConstraint(
            "evidence_type IN ('manual_note', 'url', 'customer_quote')",
            name="evidence_items_evidence_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'stale', 'contradicted')",
            name="evidence_items_status",
        ),
        CheckConstraint(
            "classification IN ('public', 'restricted', 'confidential', 'blocked')",
            name="evidence_items_classification",
        ),
        CheckConstraint(
            "confidence IN ('low', 'medium', 'high')", name="evidence_items_confidence"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_evidence_items_product_id_tenant",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_evidence_items_workspace_id_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    evidence_type: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    public_safe_summary: Mapped[str | None] = mapped_column(Text)
    what_happened: Mapped[str | None] = mapped_column(Text)
    why_it_matters: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_reference: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confidence: Mapped[str] = mapped_column(
        Text, nullable=False, default="medium", server_default="medium"
    )
    classification: Mapped[str] = mapped_column(
        Text, nullable=False, default="public", server_default="public"
    )
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="pending", server_default="pending"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EvidenceExcerpt(UuidPrimaryKeyMixin, Base):
    __tablename__ = "evidence_excerpts"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "evidence_item_id"],
            ["evidence_items.workspace_id", "evidence_items.id"],
            name="fk_evidence_excerpts_evidence_item_id_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_evidence_excerpts_workspace_id_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_item_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[str | None] = mapped_column(Text)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class EvidenceReview(UuidPrimaryKeyMixin, Base):
    __tablename__ = "evidence_reviews"
    __table_args__ = (
        CheckConstraint("decision IN ('approved', 'rejected')", name="evidence_reviews_decision"),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_item_id"],
            ["evidence_items.workspace_id", "evidence_items.id"],
            name="fk_evidence_reviews_evidence_item_id_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    evidence_item_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    reviewer_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Claim(UuidPrimaryKeyMixin, Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'stale', 'contradicted')",
            name="claims_status",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_claims_product_id_tenant",
        ),
        UniqueConstraint("workspace_id", "id", name="uq_claims_workspace_id_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[UUID | None] = mapped_column(nullable=True, index=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, default="draft", server_default="draft"
    )
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ClaimEvidenceLink(UuidPrimaryKeyMixin, Base):
    __tablename__ = "claim_evidence_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "claim_id"],
            ["claims.workspace_id", "claims.id"],
            name="fk_claim_evidence_links_claim_id_tenant",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "claim_id", "evidence_excerpt_id", name="uq_claim_evidence_links_claim_excerpt"
        ),
        ForeignKeyConstraint(
            ["workspace_id", "evidence_excerpt_id"],
            ["evidence_excerpts.workspace_id", "evidence_excerpts.id"],
            name="fk_claim_evidence_links_evidence_excerpt_id_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    claim_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    evidence_excerpt_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    created_by_actor_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
