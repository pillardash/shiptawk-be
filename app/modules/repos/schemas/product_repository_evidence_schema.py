from pydantic import BaseModel, ConfigDict


class ProductRepositoryEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, populate_by_name=True)

    name: str
    full_name: str
    description: str | None
    role: str
    role_description: str | None
