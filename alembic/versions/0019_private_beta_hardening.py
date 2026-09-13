"""add private beta feedback, first-party events, and plan retention

Revision ID: 0019_private_beta
Revises: 0018_email_outcomes
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_private_beta"
down_revision: str | None = "0018_email_outcomes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "repos",
        sa.Column("tracking_generation", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_table(
        "recommendation_usefulness_feedback",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid()),
        sa.Column("recommendation_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(16), nullable=False),
        sa.Column("reason", sa.String(32)),
        sa.Column("comment", sa.Text()),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.CheckConstraint(
            "schema_version = 1",
            name="ck_recommendation_usefulness_feedback_recommendation_usefulness_feedback_schema_version",
        ),
        sa.CheckConstraint(
            "rating IN ('useful','not_useful','unsure')",
            name="ck_recommendation_usefulness_feedback_recommendation_usefulness_feedback_rating",
        ),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "recommendation_id"],
            [
                "operator_recommendations.workspace_id",
                "operator_recommendations.product_id",
                "operator_recommendations.id",
            ],
            ondelete="CASCADE",
            name="fk_recommendation_feedback_recommendation_tenant",
        ),
        sa.UniqueConstraint(
            "workspace_id", "product_id", "id", name="uq_recommendation_feedback_tenant_id"
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "recommendation_id",
            "actor_id",
            name="uq_recommendation_feedback_actor",
        ),
    )
    op.create_table(
        "plan_views",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("plan_id", sa.Uuid(), nullable=False),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("report_ordinal", sa.Integer(), nullable=False),
        sa.Column("second_report_return", sa.Boolean(), nullable=False),
        sa.Column("fourth_report_return", sa.Boolean(), nullable=False),
        sa.Column("valid_no_recommendation_report", sa.Boolean(), nullable=False),
        sa.Column(
            "first_viewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id", "plan_id"],
            ["marketing_plans.workspace_id", "marketing_plans.product_id", "marketing_plans.id"],
            ondelete="CASCADE",
            name="fk_plan_views_plan_tenant",
        ),
        sa.UniqueConstraint("workspace_id", "product_id", "id", name="uq_plan_views_tenant_id"),
        sa.UniqueConstraint(
            "workspace_id",
            "product_id",
            "actor_id",
            "plan_id",
            "plan_revision",
            name="uq_plan_views_actor_plan_revision",
        ),
    )
    op.create_index(
        "ix_plan_views_actor_product_ordinal",
        "plan_views",
        ["workspace_id", "product_id", "actor_id", "report_ordinal"],
    )
    op.create_table(
        "product_events",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid()),
        sa.Column("event_name", sa.String(64), nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("source", sa.String(16), server_default="server", nullable=False),
        sa.Column("resource_type", sa.String(32)),
        sa.Column("resource_id", sa.Uuid()),
        sa.Column("resource_revision", sa.Integer()),
        sa.Column("report_ordinal", sa.Integer()),
        sa.Column("second_report_return", sa.Boolean()),
        sa.Column("fourth_report_return", sa.Boolean()),
        sa.Column("valid_no_recommendation_report", sa.Boolean()),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("payload_fingerprint", sa.String(64), nullable=False),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.CheckConstraint(
            "schema_version = 1", name="ck_product_events_product_events_schema_version"
        ),
        sa.CheckConstraint("source = 'server'", name="ck_product_events_product_events_source"),
        sa.CheckConstraint(
            "(event_name IN ('github_installation_attached','repository_monitoring_enabled') "
            "AND product_id IS NULL) OR "
            "(event_name NOT IN ('github_installation_attached','repository_monitoring_enabled') "
            "AND product_id IS NOT NULL)",
            name="ck_product_events_product_events_scope",
        ),
        sa.CheckConstraint(
            "event_name IN ('product_created','github_installation_attached',"
            "'repository_monitoring_enabled',"
            "'product_profile_approved','website_connected','website_initial_crawl_completed',"
            "'search_console_connected','search_property_selected','search_baseline_viewed',"
            "'weekly_report_generated','weekly_report_viewed','recommendation_detail_viewed',"
            "'recommendation_feedback_submitted','recommendation_accepted','recommendation_dismissed',"
            "'asset_edited','asset_approved','asset_rejected','action_dismissed','action_started',"
            "'action_completed','measurement_viewed','measurement_completed','weekly_email_delivered',"
            "'compatibility_route_viewed')",
            name="ck_product_events_product_events_event_name",
        ),
        sa.CheckConstraint(
            "length(idempotency_key) > 0", name="ck_product_events_product_events_idempotency_key"
        ),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["workspace_id", "product_id"],
            ["products.workspace_id", "products.id"],
            ondelete="CASCADE",
            name="fk_product_events_product_tenant",
        ),
        sa.UniqueConstraint("workspace_id", "product_id", "id", name="uq_product_events_tenant_id"),
        sa.UniqueConstraint(
            "workspace_id", "actor_id", "idempotency_key", name="uq_product_events_actor_key"
        ),
    )
    op.create_index(
        "ix_product_events_product_name_occurred",
        "product_events",
        ["workspace_id", "product_id", "event_name", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_table("product_events")
    op.drop_table("plan_views")
    op.drop_table("recommendation_usefulness_feedback")
    op.drop_column("repos", "tracking_generation")
