from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.db.base import Base
from app.db.core_tables import add_tenant_foreign_key, created_at, id_column, workspace_column

product_repositories = Table(
    "product_repositories",
    Base.metadata,
    id_column(),
    workspace_column(),
    Column("product_id", UUID(as_uuid=True), ForeignKey("products.id"), nullable=False),
    Column("repo_id", UUID(as_uuid=True), ForeignKey("repos.id"), nullable=False, unique=True),
    Column("generated_role_description", Text),
    Column("role_description_override", Text),
    Column("role_description_generated_at", DateTime(timezone=True)),
    Column("role_description_generation_version", Integer),
    Column("repo_role", String(20), nullable=False, server_default="unknown"),
    Column("is_primary_repo", Boolean, nullable=False, server_default="false"),
    created_at(),
    CheckConstraint(
        "repo_role IN ('frontend', 'backend', 'mobile', 'worker', 'infra', 'docs', "
        "'shared_lib', 'unknown')",
        name="product_repositories_repo_role",
    ),
)
Index(
    "uq_product_repositories_primary_product",
    product_repositories.c.workspace_id,
    product_repositories.c.product_id,
    unique=True,
    postgresql_where=product_repositories.c.is_primary_repo.is_(True),
    sqlite_where=product_repositories.c.is_primary_repo.is_(True),
)
add_tenant_foreign_key(product_repositories, "product_id", "products")
add_tenant_foreign_key(product_repositories, "repo_id", "repos")
