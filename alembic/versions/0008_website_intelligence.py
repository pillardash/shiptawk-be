"""add website intelligence persistence

Revision ID: 0008_website_intelligence
Revises: 0007_product_profiles
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_website_intelligence"
down_revision: str | None = "0007_product_profiles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _audit_columns() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    ]


def _create_audit_indexes(table_name: str) -> None:
    op.create_index(f"ix_{table_name}_created_by", table_name, ["created_by"])
    op.create_index(f"ix_{table_name}_updated_by", table_name, ["updated_by"])
    op.create_index(f"ix_{table_name}_deleted_at", table_name, ["deleted_at"])


def upgrade() -> None:
    op.create_table(
        "website_sources",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("normalized_base_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "sitemap_urls",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("blog_url", sa.Text(), nullable=True),
        sa.Column("documentation_url", sa.Text(), nullable=True),
        sa.Column("last_successful_crawl_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "status IN ('active', 'paused', 'disconnected')",
            name="ck_website_sources_source_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'disconnected' AND disconnected_at IS NOT NULL) OR "
            "(status <> 'disconnected' AND disconnected_at IS NULL)",
            name="ck_website_sources_source_lifecycle_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_website_sources"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_website_sources_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_sources_workspace_product_id",
        ),
        sa.UniqueConstraint("workspace_id", "product_id", name="uq_website_sources_product"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_sources_product_tenant",
            ondelete="CASCADE",
        ),
    )
    op.create_index("ix_website_sources_workspace_id", "website_sources", ["workspace_id"])
    op.create_index("ix_website_sources_product_id", "website_sources", ["product_id"])
    _create_audit_indexes("website_sources")

    op.create_table(
        "website_crawl_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("page_limit", sa.Integer(), nullable=False),
        sa.Column("pages_discovered", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pages_succeeded", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("pages_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_website_crawl_runs_crawl_status_valid",
        ),
        sa.CheckConstraint(
            "schema_version > 0", name="ck_website_crawl_runs_crawl_schema_version_positive"
        ),
        sa.CheckConstraint(
            "page_limit > 0", name="ck_website_crawl_runs_crawl_page_limit_positive"
        ),
        sa.CheckConstraint(
            "pages_discovered >= 0 AND pages_succeeded >= 0 AND pages_failed >= 0",
            name="ck_website_crawl_runs_crawl_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "pages_succeeded + pages_failed <= pages_discovered",
            name="ck_website_crawl_runs_crawl_counts_valid",
        ),
        sa.CheckConstraint(
            "(status = 'pending' AND started_at IS NULL AND completed_at IS NULL "
            "AND error_code IS NULL) OR "
            "(status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL "
            "AND error_code IS NULL) OR "
            "(status = 'succeeded' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NULL) OR "
            "(status = 'failed' AND started_at IS NOT NULL AND completed_at IS NOT NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'cancelled' AND completed_at IS NOT NULL AND error_code IS NULL)",
            name="ck_website_crawl_runs_crawl_lifecycle_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_website_crawl_runs"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_website_crawl_runs_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_crawl_runs_workspace_product_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "source_id",
            "id",
            name="uq_website_crawl_runs_source_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "idempotency_key",
            name="uq_website_crawl_runs_idempotency",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_crawl_runs_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_crawl_runs_source_tenant",
            ondelete="CASCADE",
        ),
    )
    for column in ("workspace_id", "product_id", "source_id"):
        op.create_index(f"ix_website_crawl_runs_{column}", "website_crawl_runs", [column])
    _create_audit_indexes("website_crawl_runs")
    op.create_index(
        "uq_website_crawl_runs_active_source",
        "website_crawl_runs",
        ["workspace_id", "source_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )

    op.create_table(
        "website_pages",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("first_discovered_run_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("url_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(
            "status IN ('active', 'redirected', 'blocked', 'missing', 'failed')",
            name="ck_website_pages_page_status_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_website_pages"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_website_pages_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_website_pages_workspace_product_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "source_id",
            "id",
            name="uq_website_pages_source_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "source_id",
            "canonical_url",
            name="uq_website_pages_canonical_url",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_pages_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_pages_source_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "first_discovered_run_id"],
            [
                "website_crawl_runs.workspace_id",
                "website_crawl_runs.product_id",
                "website_crawl_runs.id",
            ],
            name="fk_website_pages_first_run_tenant",
            ondelete="RESTRICT",
        ),
    )
    for column in ("workspace_id", "product_id", "source_id", "first_discovered_run_id"):
        op.create_index(f"ix_website_pages_{column}", "website_pages", [column])
    _create_audit_indexes("website_pages")

    op.create_table(
        "website_crawl_results",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("crawl_run_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("final_url", sa.Text(), nullable=True),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("content_type", sa.String(length=255), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("meta_description", sa.Text(), nullable=True),
        sa.Column(
            "headings",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("is_indexable", sa.Boolean(), nullable=True),
        sa.Column(
            "indexability_reasons",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        *_audit_columns(),
        sa.CheckConstraint(
            "status IN ('succeeded', 'blocked', 'failed', 'skipped')",
            name="ck_website_crawl_results_result_status_valid",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name="ck_website_crawl_results_result_http_status_valid",
        ),
        sa.CheckConstraint(
            "(status = 'succeeded' AND fetched_at IS NOT NULL AND http_status IS NOT NULL "
            "AND error_code IS NULL AND content_fingerprint IS NOT NULL) OR "
            "(status = 'blocked' AND fetched_at IS NOT NULL AND error_code IS NOT NULL) OR "
            "(status = 'failed' AND fetched_at IS NOT NULL AND error_code IS NOT NULL) OR "
            "(status = 'skipped' AND fetched_at IS NULL AND http_status IS NULL "
            "AND error_code IS NOT NULL)",
            name="ck_website_crawl_results_result_lifecycle_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_website_crawl_results"),
        sa.UniqueConstraint("workspace_id", "id", name="uq_website_crawl_results_workspace_id_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "id",
            name="uq_website_crawl_results_workspace_product_id",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "crawl_run_id",
            "page_id",
            name="uq_website_crawl_results_run_page",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_crawl_results_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_crawl_results_source_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "crawl_run_id"],
            [
                "website_crawl_runs.workspace_id",
                "website_crawl_runs.product_id",
                "website_crawl_runs.id",
            ],
            name="fk_website_crawl_results_run_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "page_id"],
            ["website_pages.workspace_id", "website_pages.product_id", "website_pages.id"],
            name="fk_website_crawl_results_page_tenant",
            ondelete="CASCADE",
        ),
    )
    for column in ("workspace_id", "product_id", "source_id", "crawl_run_id", "page_id"):
        op.create_index(f"ix_website_crawl_results_{column}", "website_crawl_results", [column])
    _create_audit_indexes("website_crawl_results")

    op.create_table(
        "website_capability_mappings",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("crawl_run_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=True),
        sa.Column("capability_key", sa.String(length=128), nullable=False),
        sa.Column("capability_name", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("relevance_score", sa.Integer(), nullable=False),
        sa.Column("confidence_score", sa.Integer(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("mapping_version", sa.String(length=64), nullable=False),
        *_audit_columns(),
        sa.CheckConstraint(
            "status IN ('represented', 'partial', 'missing')",
            name="ck_website_capability_mappings_coverage_status_valid",
        ),
        sa.CheckConstraint(
            "relevance_score BETWEEN 0 AND 100 AND confidence_score BETWEEN 0 AND 100",
            name="ck_website_capability_mappings_mapping_scores_valid",
        ),
        sa.CheckConstraint(
            "(status IN ('represented', 'partial') AND page_id IS NOT NULL) OR "
            "(status = 'missing' AND page_id IS NULL)",
            name="ck_website_capability_mappings_mapping_page_lifecycle_valid",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_website_capability_mappings"),
        sa.UniqueConstraint(
            "workspace_id",
            "crawl_run_id",
            "capability_key",
            name="uq_website_capability_mappings_run_capability",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_website_capability_mappings_product_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "source_id"],
            ["website_sources.workspace_id", "website_sources.product_id", "website_sources.id"],
            name="fk_website_capability_mappings_source_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "crawl_run_id"],
            [
                "website_crawl_runs.workspace_id",
                "website_crawl_runs.product_id",
                "website_crawl_runs.id",
            ],
            name="fk_website_capability_mappings_run_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "page_id"],
            ["website_pages.workspace_id", "website_pages.product_id", "website_pages.id"],
            name="fk_website_capability_mappings_page_tenant",
            ondelete="CASCADE",
        ),
    )
    for column in ("workspace_id", "product_id", "source_id", "crawl_run_id", "page_id"):
        op.create_index(
            f"ix_website_capability_mappings_{column}", "website_capability_mappings", [column]
        )
    _create_audit_indexes("website_capability_mappings")


def downgrade() -> None:
    op.drop_table("website_capability_mappings")
    op.drop_table("website_crawl_results")
    op.drop_table("website_pages")
    op.drop_index("uq_website_crawl_runs_active_source", table_name="website_crawl_runs")
    op.drop_table("website_crawl_runs")
    op.drop_table("website_sources")
