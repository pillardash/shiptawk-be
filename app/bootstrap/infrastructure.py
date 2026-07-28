from dataclasses import dataclass

from app.cache.dependencies import create_cache
from app.core.config import Settings
from app.services.cache import CacheService
from app.services.jobs import create_job_service
from app.services.jobs.base import JobService
from app.services.storage.base import StorageService, create_storage_service


@dataclass(frozen=True, slots=True)
class InfrastructureResources:
    cache: CacheService
    jobs: JobService
    storage: StorageService


def create_infrastructure(settings: Settings) -> InfrastructureResources:
    return InfrastructureResources(
        cache=create_cache(settings),
        jobs=create_job_service(settings),
        storage=create_storage_service(settings),
    )
