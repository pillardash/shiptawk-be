from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from app.modules.products.enums.product_profile_enum import (
    ProductProfileAuditAction,
    ProductProfileConversionGoal,
    ProductProfileStatus,
)
from app.modules.products.policies.product_policy import validate_public_website_url
from app.shared.schemas import ApiSchema

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=2000)]
ListItem = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class ProductProfileDraftWrite(ApiSchema):
    product_name: Text | None = Field(default=None, max_length=120)
    short_description: Text | None = Field(default=None, max_length=1200)
    primary_audience: Text | None = Field(default=None, max_length=500)
    primary_customer_problem: Text | None = Field(default=None, max_length=1000)
    main_value_proposition: Text | None = Field(default=None, max_length=1000)
    primary_conversion_goal: ProductProfileConversionGoal | None = None
    primary_conversion_url: str | None = Field(default=None, max_length=500)
    main_market: Text | None = Field(default=None, max_length=300)
    differentiation: Text | None = Field(default=None, max_length=1000)
    important_capabilities: list[ListItem] = Field(default_factory=list, max_length=30)
    quarterly_objective: Text | None = Field(default=None, max_length=1000)
    restricted_topics: list[ListItem] = Field(default_factory=list, max_length=80)
    restricted_claims: list[ListItem] = Field(default_factory=list, max_length=80)
    restricted_language: list[ListItem] = Field(default_factory=list, max_length=80)
    brand_voice_guidance: Text | None = Field(default=None, max_length=1000)

    @field_validator("primary_conversion_url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        return validate_public_website_url(value)


class ProductProfileDraftUpdate(ProductProfileDraftWrite):
    expected_revision: int | None = Field(default=None, ge=1)


class ProductProfileApproveRequest(ApiSchema):
    expected_revision: int = Field(ge=1)


class ProductProfileVersionResponse(ProductProfileDraftWrite):
    id: UUID
    workspace_id: UUID
    product_id: UUID
    status: ProductProfileStatus
    version: int | None
    draft_revision: int
    schema_version: int
    completeness_score: int
    based_on_profile_id: UUID | None
    approved_at: datetime | None
    approved_by: UUID | None
    created_at: datetime
    updated_at: datetime


class ProductProfileCurrentResponse(ApiSchema):
    draft: ProductProfileVersionResponse | None
    approved: ProductProfileVersionResponse | None


class ProductProfileAuditEventResponse(ApiSchema):
    id: UUID
    actor_id: UUID
    action: ProductProfileAuditAction
    profile_version: int | None
    draft_revision: int
    changed_fields: list[str]
    created_at: datetime


class ProductProfileHistoryResponse(ApiSchema):
    versions: list[ProductProfileVersionResponse]


class ApprovedProductProfileContext(ProductProfileDraftWrite):
    schema_version: Literal[1]
    profile_id: UUID
    profile_version: int
    product_id: UUID
