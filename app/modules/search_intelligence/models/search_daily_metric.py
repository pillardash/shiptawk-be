from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import BaseModel


class SearchDailyMetric(BaseModel):
    __tablename__ = "search_daily_metrics"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_daily_metrics_source_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id", "query_id"],
            [
                "search_queries.workspace_id",
                "search_queries.product_id",
                "search_queries.source_id",
                "search_queries.id",
            ],
            name="fk_search_daily_metrics_query_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id", "page_id"],
            [
                "search_pages.workspace_id",
                "search_pages.product_id",
                "search_pages.source_id",
                "search_pages.id",
            ],
            name="fk_search_daily_metrics_page_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint("grain IN ('site_total', 'query_page')", name="grain_valid"),
        CheckConstraint(
            "(grain = 'site_total' AND query_id IS NULL AND page_id IS NULL) OR "
            "(grain = 'query_page' AND query_id IS NOT NULL AND page_id IS NOT NULL)",
            name="grain_dimensions",
        ),
        CheckConstraint("clicks >= 0 AND impressions >= 0", name="counts_nonnegative"),
        CheckConstraint(
            "average_position IS NULL OR average_position >= 0", name="position_nonnegative"
        ),
        Index(
            "uq_search_daily_metrics_site_total",
            "workspace_id",
            "source_id",
            "metric_date",
            "search_type",
            unique=True,
            postgresql_where=text("grain = 'site_total'"),
            sqlite_where=text("grain = 'site_total'"),
        ),
        Index(
            "uq_search_daily_metrics_query_page",
            "workspace_id",
            "source_id",
            "metric_date",
            "search_type",
            "query_id",
            "page_id",
            unique=True,
            postgresql_where=text("grain = 'query_page'"),
            sqlite_where=text("grain = 'query_page'"),
        ),
    )

    workspace_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    product_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    source_id: Mapped[UUID] = mapped_column(nullable=False, index=True)
    query_id: Mapped[UUID | None] = mapped_column(index=True)
    page_id: Mapped[UUID | None] = mapped_column(index=True)
    metric_date: Mapped[date] = mapped_column(Date, nullable=False)
    search_type: Mapped[str] = mapped_column(String(32), nullable=False)
    grain: Mapped[str] = mapped_column(String(16), nullable=False)
    clicks: Mapped[int] = mapped_column(Integer, nullable=False)
    impressions: Mapped[int] = mapped_column(Integer, nullable=False)
    average_position: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))


search_daily_metrics = SearchDailyMetric.__table__
__all__ = ["SearchDailyMetric", "search_daily_metrics"]
