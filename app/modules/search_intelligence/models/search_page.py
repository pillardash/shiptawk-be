from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class SearchPage(BaseModel):
    __tablename__ = "search_pages"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "product_id", "source_id", "id", name="uq_search_pages_source_id"
        ),
        UniqueConstraint(
            "workspace_id",
            "source_id",
            "url_fingerprint",
            name="uq_search_pages_source_fingerprint",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_pages_source_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "website_source_id", "website_page_id"],
            [
                "website_pages.workspace_id",
                "website_pages.product_id",
                "website_pages.source_id",
                "website_pages.id",
            ],
            name="fk_search_pages_website_page_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "match_status IN ('matched', 'unmatched', 'ambiguous')", name="match_status_valid"
        ),
        CheckConstraint(
            "(website_source_id IS NULL) = (website_page_id IS NULL)",
            name="website_page_pair_valid",
        ),
        CheckConstraint(
            "(match_status = 'matched' AND website_page_id IS NOT NULL) OR "
            "(match_status <> 'matched' AND website_page_id IS NULL)",
            name="match_target_valid",
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    original_url: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    url_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    website_source_id: Mapped[UUID | None] = mapped_column(index=True)
    website_page_id: Mapped[UUID | None] = mapped_column(index=True)
    match_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="unmatched"
    )
    match_method: Mapped[str | None] = mapped_column(String(64))
    match_policy_version: Mapped[str] = mapped_column(String(64), nullable=False)


search_pages = SearchPage.__table__
__all__ = ["SearchPage", "search_pages"]
