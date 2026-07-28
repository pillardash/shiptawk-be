"""Shared SQLAlchemy Core types, columns, and tenant constraints."""

from typing import Any

from sqlalchemy import (
    ARRAY,
    JSON,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

json_type = JSONB().with_variant(JSON, "sqlite")
uuid_list_type = ARRAY(UUID(as_uuid=True)).with_variant(JSON, "sqlite")
text_list_type = ARRAY(Text).with_variant(JSON, "sqlite")


def id_column() -> Column[Any]:
    return Column(
        "id", UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


def workspace_column() -> Column[Any]:
    return Column(
        "workspace_id",
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


def user_column() -> Column[Any]:
    return Column(
        "user_id",
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="Temporary legacy owner field; authorize by workspace_id.",
    )


def created_at() -> Column[Any]:
    return Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now())


def add_tenant_parent_key(table: Table) -> None:
    table.append_constraint(
        UniqueConstraint("workspace_id", "id", name=f"uq_{table.name}_workspace_id_id")
    )


def add_tenant_foreign_key(table: Table, column: str, parent_table: str) -> None:
    table.append_constraint(
        ForeignKeyConstraint(
            ["workspace_id", column],
            [f"{parent_table}.workspace_id", f"{parent_table}.id"],
            name=f"fk_{table.name}_{column}_tenant",
        )
    )
