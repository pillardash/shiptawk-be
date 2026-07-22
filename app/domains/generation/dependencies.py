from typing import Annotated, cast

from fastapi import Depends, Request

from app.domains.generation.provider import GenerationProvider
from app.shared.exceptions import ServiceUnavailableError


def get_generation_provider(request: Request) -> GenerationProvider:
    provider = getattr(request.app.state, "generation_provider", None)
    if provider is None:
        raise ServiceUnavailableError(
            "Generation provider is not configured.", code="generation_provider_not_configured"
        )
    return cast(GenerationProvider, provider)


def get_generation_model(request: Request) -> str:
    return cast(str, getattr(request.app.state, "generation_model", "default"))


GenerationProviderDep = Annotated[GenerationProvider, Depends(get_generation_provider)]
GenerationModelDep = Annotated[str, Depends(get_generation_model)]
