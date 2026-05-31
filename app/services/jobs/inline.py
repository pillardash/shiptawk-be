from uuid import uuid4

from app.services.jobs.base import (
    JobHandle,
    TaskCallable,
    resolve_queue_name,
    resolve_task,
    validate_delay,
    validate_job_payload,
)


class InlineJobService:
    def __init__(self, *, default_queue: str, registry: dict[str, TaskCallable]) -> None:
        self.default_queue = default_queue
        self.registry = registry

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
        if delay_seconds is not None:
            raise ValueError("Inline jobs do not support delayed execution.")
        task = resolve_task(task_name, self.registry)
        task(*args, **(kwargs or {}))
        return JobHandle(
            id=str(uuid4()),
            task_name=task_name,
            queue=resolve_queue_name(queue, self.default_queue),
        )
