from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, BaseModel
from app.modules.workspaces.enums import WorkspaceRole as WorkspaceRole


class Workspace(BaseModel):
    __tablename__ = "workspaces"
    __table_args__ = (
        ForeignKeyConstraint(
            ["id", "activation_product_id"],
            ["products.workspace_id", "products.id"],
            name="fk_workspaces_activation_product_tenant",
            ondelete="RESTRICT",
            use_alter=True,
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    activation_product_id: Mapped[UUID | None] = mapped_column(Uuid, nullable=True)


class WorkspaceMembership(Base):
    __tablename__ = "workspace_memberships"
    workspace_id: Mapped[UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(
        Enum(WorkspaceRole, name="workspace_role"), nullable=False, default=WorkspaceRole.viewer
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
