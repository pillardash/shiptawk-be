"""add provider-neutral search intelligence foundations

Revision ID: 0010_search_intelligence
Revises: 0009_generic_integrations
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_search_intelligence"
down_revision: str | None = "0009_generic_integrations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _base_columns() -> list[sa.Column[object]]:
    return [
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def upgrade() -> None:
    op.create_table(
        "search_provider_properties",
        *_base_columns(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("provider_property_id", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("property_type", sa.String(16), nullable=False),
        sa.Column("permission_level", sa.String(64), nullable=False),
        sa.Column("compatibility_status", sa.String(16), server_default="unknown", nullable=False),
        sa.Column("first_discovered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "property_type IN ('domain', 'url_prefix')",
            name="ck_search_provider_properties_property_type_valid",
        ),
        sa.CheckConstraint(
            "compatibility_status IN ('compatible', 'incompatible', 'unknown')",
            name="ck_search_provider_properties_compatibility_valid",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "integration_connection_id"],
            ["integration_connections.workspace_id", "integration_connections.id"],
            name="fk_search_provider_properties_connection_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_provider_properties"),
        sa.UniqueConstraint(
            "workspace_id", "id", name="uq_search_provider_properties_workspace_id_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "integration_connection_id",
            "id",
            name="uq_search_provider_properties_connection_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "integration_connection_id",
            "provider_property_id",
            name="uq_search_provider_properties_connection_property",
        ),
    )
    _common_indexes("search_provider_properties", ["workspace_id", "integration_connection_id"])

    op.create_table(
        "search_sources",
        *_base_columns(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("integration_connection_id", sa.Uuid(), nullable=False),
        sa.Column("property_id", sa.Uuid(), nullable=False),
        sa.Column("website_source_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), server_default="active", nullable=False),
        sa.Column("selected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("latest_successful_data_date", sa.Date(), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'disconnected')", name="ck_search_sources_status_valid"
        ),
        sa.CheckConstraint(
            "(status = 'disconnected' AND disconnected_at IS NOT NULL) OR "
            "(status = 'active' AND disconnected_at IS NULL)",
            name="ck_search_sources_lifecycle_valid",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_search_sources_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "integration_connection_id"],
            ["integration_connections.workspace_id", "integration_connections.id"],
            name="fk_search_sources_connection_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "integration_connection_id", "property_id"],
            [
                "search_provider_properties.workspace_id",
                "search_provider_properties.integration_connection_id",
                "search_provider_properties.id",
            ],
            name="fk_search_sources_property_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "website_source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_search_sources_website_source_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_sources"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_search_sources_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_search_sources_workspace_product_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            "website_source_id",
            name="uq_search_sources_website_source_id",
        ),
    )
    _common_indexes(
        "search_sources",
        [
            "workspace_id",
            "product_id",
            "integration_connection_id",
            "property_id",
            "website_source_id",
        ],
    )
    op.create_index(
        "uq_search_sources_active_product_provider",
        "search_sources",
        ["workspace_id", "product_id", "provider"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )

    op.create_table(
        "search_sync_runs",
        *_base_columns(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("trigger", sa.String(16), nullable=False),
        sa.Column("status", sa.String(16), server_default="pending", nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False),
        sa.Column("requested_start_date", sa.Date(), nullable=False),
        sa.Column("requested_end_date", sa.Date(), nullable=False),
        sa.Column("progress_date", sa.Date(), nullable=True),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("rows_received", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_written", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_complete", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("is_truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("lease_owner", sa.String(255), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_category", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "trigger IN ('initial', 'scheduled', 'manual')",
            name="ck_search_sync_runs_trigger_valid",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_search_sync_runs_status_valid",
        ),
        sa.CheckConstraint(
            "requested_start_date <= requested_end_date",
            name="ck_search_sync_runs_date_range_valid",
        ),
        sa.CheckConstraint(
            "policy_version <> '' AND schema_version > 0", name="ck_search_sync_runs_versions_valid"
        ),
        sa.CheckConstraint(
            "rows_received >= 0 AND rows_written >= 0",
            name="ck_search_sync_runs_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "progress_date IS NULL OR (progress_date >= requested_start_date "
            "AND progress_date <= requested_end_date)",
            name="ck_search_sync_runs_progress_valid",
        ),
        sa.CheckConstraint(
            "(lease_owner IS NULL AND lease_expires_at IS NULL) OR "
            "(lease_owner IS NOT NULL AND lease_expires_at IS NOT NULL)",
            name="ck_search_sync_runs_lease_valid",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_sync_runs_source_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_sync_runs"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "source_id", "id", name="uq_search_sync_runs_source_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "idempotency_key",
            name="uq_search_sync_runs_source_idempotency",
        ),
    )
    _common_indexes("search_sync_runs", ["workspace_id", "product_id", "source_id"])
    op.create_index(
        "uq_search_sync_runs_active_source",
        "search_sync_runs",
        ["workspace_id", "source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    _create_identity_tables()
    _create_daily_metrics()


def _create_identity_tables() -> None:
    op.create_table(
        "search_queries",
        *_base_columns(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("query_text", sa.Text(), nullable=False),
        sa.Column("query_fingerprint", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_queries_source_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_search_queries"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "source_id", "id", name="uq_search_queries_source_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "query_fingerprint",
            name="uq_search_queries_source_fingerprint",
        ),
    )
    _common_indexes("search_queries", ["workspace_id", "product_id", "source_id"])
    op.create_table(
        "search_pages",
        *_base_columns(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("original_url", sa.Text(), nullable=False),
        sa.Column("normalized_url", sa.Text(), nullable=False),
        sa.Column("url_fingerprint", sa.String(64), nullable=False),
        sa.Column("website_source_id", sa.Uuid(), nullable=True),
        sa.Column("website_page_id", sa.Uuid(), nullable=True),
        sa.Column("match_status", sa.String(16), server_default="unmatched", nullable=False),
        sa.Column("match_method", sa.String(64), nullable=True),
        sa.Column("match_policy_version", sa.String(64), nullable=False),
        sa.CheckConstraint(
            "match_status IN ('matched', 'unmatched', 'ambiguous')",
            name="ck_search_pages_match_status_valid",
        ),
        sa.CheckConstraint(
            "(website_source_id IS NULL) = (website_page_id IS NULL)",
            name="ck_search_pages_website_page_pair_valid",
        ),
        sa.CheckConstraint(
            "(match_status = 'matched' AND website_page_id IS NOT NULL) OR "
            "(match_status <> 'matched' AND website_page_id IS NULL)",
            name="ck_search_pages_match_target_valid",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_pages_source_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id", name="pk_search_pages"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "source_id", "id", name="uq_search_pages_source_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "url_fingerprint",
            name="uq_search_pages_source_fingerprint",
        ),
    )
    _common_indexes(
        "search_pages",
        ["workspace_id", "product_id", "source_id", "website_source_id", "website_page_id"],
    )


def _create_daily_metrics() -> None:
    op.create_table(
        "search_daily_metrics",
        *_base_columns(),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("query_id", sa.Uuid(), nullable=True),
        sa.Column("page_id", sa.Uuid(), nullable=True),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("search_type", sa.String(32), nullable=False),
        sa.Column("grain", sa.String(16), nullable=False),
        sa.Column("clicks", sa.Integer(), nullable=False),
        sa.Column("impressions", sa.Integer(), nullable=False),
        sa.Column("average_position", sa.Numeric(12, 6), nullable=True),
        sa.CheckConstraint(
            "grain IN ('site_total', 'query_page')", name="ck_search_daily_metrics_grain_valid"
        ),
        sa.CheckConstraint(
            "(grain = 'site_total' AND query_id IS NULL AND page_id IS NULL) OR "
            "(grain = 'query_page' AND query_id IS NOT NULL AND page_id IS NOT NULL)",
            name="ck_search_daily_metrics_grain_dimensions",
        ),
        sa.CheckConstraint(
            "clicks >= 0 AND impressions >= 0", name="ck_search_daily_metrics_counts_nonnegative"
        ),
        sa.CheckConstraint(
            "average_position IS NULL OR average_position >= 0",
            name="ck_search_daily_metrics_position_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["search_sources.workspace_id", "search_sources.product_id", "search_sources.id"],
            name="fk_search_daily_metrics_source_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id", name="pk_search_daily_metrics"),
    )
    _common_indexes(
        "search_daily_metrics", ["workspace_id", "product_id", "source_id", "query_id", "page_id"]
    )
    op.create_index(
        "uq_search_daily_metrics_site_total",
        "search_daily_metrics",
        ["workspace_id", "source_id", "metric_date", "search_type"],
        unique=True,
        postgresql_where=sa.text("grain = 'site_total'"),
    )
    op.create_index(
        "uq_search_daily_metrics_query_page",
        "search_daily_metrics",
        ["workspace_id", "source_id", "metric_date", "search_type", "query_id", "page_id"],
        unique=True,
        postgresql_where=sa.text("grain = 'query_page'"),
    )


def _common_indexes(table: str, columns: list[str]) -> None:
    for column in [*columns, "created_by", "updated_by", "deleted_at"]:
        op.create_index(f"ix_{table}_{column}", table, [column])


def downgrade() -> None:
    op.drop_table("search_daily_metrics")
    op.drop_table("search_pages")
    op.drop_table("search_queries")
    op.drop_table("search_sync_runs")
    op.drop_table("search_sources")
    op.drop_table("search_provider_properties")
