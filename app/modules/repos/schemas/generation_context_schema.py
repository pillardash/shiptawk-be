from pydantic import BaseModel, ConfigDict


class RepositoryContextInput(BaseModel):
    model_config = ConfigDict(frozen=True)

    repo_name: str
    repo_full_name: str
    description: str | None = None
