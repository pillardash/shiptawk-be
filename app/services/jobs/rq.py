from datetime import timedelta
from importlib import import_module
from typing import Any

from app.jobs.runner import run_registered_task
from app.services.jobs.base import (
    JobHandle,
    TaskCallable,
    resolve_queue_name,
    resolve_task,
    validate_delay,
    validate_job_payload,
)


class RqJobService:
    def __init__(
        self,
        *,
        redis_url: str,
        default_queue: str,
        registry: dict[str, TaskCallable],
        default_timeout_seconds: int,
        result_ttl_seconds: int,
        failure_ttl_seconds: int,
    ) -> None:
        redis_module = import_module("redis")
        rq_module = import_module("rq")
        self.connection: Any = redis_module.Redis.from_url(redis_url)
        self.queue_class: Any = rq_module.Queue
        self.default_queue = default_queue
        self.registry = registry
        self.default_timeout_seconds = default_timeout_seconds
        self.result_ttl_seconds = result_ttl_seconds
        self.failure_ttl_seconds = failure_ttl_seconds

    def enqueue(
        self,
        task_name: str,
        *,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        queue: str | None = None,
        delay_seconds: int | None = None,
    ) -> JobHandle:
        validate_delay(delay_seconds)
        validate_job_payload(args, kwargs)
        resolve_task(task_name, self.registry)
        queue_name = resolve_queue_name(queue, self.default_queue)
        rq_queue = self.queue_class(queue_name, connection=self.connection)
        enqueue_kwargs = {
            "args": (task_name, args, kwargs or {}),
            "job_timeout": self.default_timeout_seconds,
            "result_ttl": self.result_ttl_seconds,
            "failure_ttl": self.failure_ttl_seconds,
        }
        if delay_seconds is None:
            job = rq_queue.enqueue(run_registered_task, **enqueue_kwargs)
        else:
            job = rq_queue.enqueue_in(
                timedelta(seconds=delay_seconds),
                run_registered_task,
                **enqueue_kwargs,
            )
        return JobHandle(id=str(job.id), task_name=task_name, queue=queue_name)
