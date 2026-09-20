from importlib import import_module

from app.core.config import get_settings
from app.core.logging import configure_ai_content_logging, configure_logging
from app.core.sentry import configure_sentry


def main() -> None:
    settings = get_settings()
    configure_logging(
        level=settings.log_level,
        log_format=settings.log_format,
        file_enabled=settings.log_file_enabled,
        file_path=settings.log_file_path,
    )
    configure_ai_content_logging(
        enabled=settings.ai_content_log_enabled,
        file_path=settings.ai_content_log_path,
    )
    configure_sentry(settings)
    redis_url = settings.resolved_jobs_redis_url
    if settings.jobs_backend != "rq" or redis_url is None:
        raise RuntimeError("Job worker requires JOBS_BACKEND=rq and a Redis URL.")

    redis_module = import_module("redis")
    rq_module = import_module("rq")
    connection = redis_module.Redis.from_url(redis_url)
    worker = rq_module.Worker([settings.jobs_queue_name], connection=connection)
    worker.work()


if __name__ == "__main__":
    main()
