from pydantic import BaseModel, ConfigDict

from app.modules.drafts.enums.generation_enum import AngleType


class SelectedAngles(BaseModel):
    model_config = ConfigDict(frozen=True)

    best: AngleType
    alternates: list[AngleType]
