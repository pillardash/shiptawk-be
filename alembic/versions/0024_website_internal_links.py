"""preserve deterministic website internal links

Revision ID: 0024_website_links
Revises: 0023_publication_replay
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_website_links"
down_revision: str | None = "0023_publication_replay"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "website_crawl_results",
        sa.Column("internal_links", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("website_crawl_results", "internal_links")
