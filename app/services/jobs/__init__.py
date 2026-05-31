from app.core.config import Settings
from app.jobs.registry import TASK_REGISTRY
from app.services.jobs.base import JobService
from app.services.jobs.inline import InlineJobService


def create_job_service(settings: Settings) -> JobService:
    if settings.jobs_backend == "inline":
        return InlineJobService(default_queue=settings.jobs_queue_name, registry=TASK_REGISTRY)

    if settings.jobs_backend == "rq":
        from app.services.jobs.rq import RqJobService

        redis_url = settings.resolved_jobs_redis_url
        if redis_url is None:
            raise ValueError("RQ jobs require JOBS_REDIS_URL or REDIS_URL.")
        return RqJobService(
            redis_url=redis_url,
            default_queue=settings.jobs_queue_name,
            registry=TASK_REGISTRY,
            default_timeout_seconds=settings.jobs_default_timeout_seconds,
            result_ttl_seconds=settings.jobs_result_ttl_seconds,
            failure_ttl_seconds=settings.jobs_failure_ttl_seconds,
        )

    raise ValueError("Unsupported jobs backend.")
