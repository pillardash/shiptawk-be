"""add publication command payload replay protection

Revision ID: 0023_publication_replay
Revises: 0022_tenant_ownership
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_publication_replay"
down_revision: str | None = "0022_tenant_ownership"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("publication_attempts", sa.Column("payload_fingerprint", sa.String(64)))


def downgrade() -> None:
    op.drop_column("publication_attempts", "payload_fingerprint")
