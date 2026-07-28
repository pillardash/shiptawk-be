from uuid import UUID

from sqlalchemy import ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class SearchQuery(BaseModel):
    __tablename__ = "search_queries"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_id", "source_id", "id", name="uq_search_queries_source_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "query_fingerprint",
            name="uq_search_queries_source_fingerprint",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_queries_source_tenant",
            ondelete="CASCADE",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    query_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)


search_queries = SearchQuery.__table__
__all__ = ["SearchQuery", "search_queries"]
