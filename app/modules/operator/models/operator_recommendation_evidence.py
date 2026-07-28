from sqlalchemy import Column, ForeignKeyConstraint, Table, UniqueConstraint, Uuid

from app.db.base import Base

operator_recommendation_evidence = Table(
    "operator_recommendation_evidence",
    Base.metadata,
    Column("workspace_id", Uuid(as_uuid=True), nullable=False),
    Column("product_id", Uuid(as_uuid=True), nullable=False),
    Column("recommendation_id", Uuid(as_uuid=True), nullable=False),
    Column("evidence_id", Uuid(as_uuid=True), nullable=False),
    UniqueConstraint(
        "workspace_id",
        "product_id",
        "recommendation_id",
        "evidence_id",
        name="uq_operator_recommendation_evidence_link",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "product_id", "recommendation_id"],
        [
            "operator_recommendations.workspace_id",
            "operator_recommendations.product_id",
            "operator_recommendations.id",
        ],
        ondelete="CASCADE",
        name="fk_operator_recommendation_evidence_recommendation_tenant",
    ),
    ForeignKeyConstraint(
        ["workspace_id", "product_id", "evidence_id"],
        [
            "opportunity_evidence.workspace_id",
            "opportunity_evidence.product_id",
            "opportunity_evidence.id",
        ],
        ondelete="RESTRICT",
        name="fk_operator_recommendation_evidence_evidence_tenant",
    ),
)
