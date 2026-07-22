from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.shared.schemas import to_camel


class WorkflowMetadata(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        frozen=True,
        populate_by_name=True,
    )


class GitHubEventMetadata(WorkflowMetadata):
    consumer_id: UUID
    schema_version: Literal[1]
    correlation_id: UUID


class OnboardingEventMetadata(WorkflowMetadata):
    user_id: UUID
    workspace_id: UUID
    schema_version: Literal[1]
