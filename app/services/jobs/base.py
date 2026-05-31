from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

TaskCallable = Callable[..., None]


@dataclass(frozen=True)
class JobHandle:
    id: str
    task_name: str
    queue: str


class JobService(Protocol):
    def enqueue(
        self,
        task_name: str,
        *,
        args: tuple[object, ...] = (),
        kwargs: dict[str, object] | None = None,
        queue: str | None = None,
        delay_seconds: int | None = None,
    ) -> JobHandle: ...


def resolve_task(task_name: str, registry: dict[str, TaskCallable]) -> TaskCallable:
    try:
        return registry[task_name]
    except KeyError as exc:
        raise ValueError(f"Unknown job task: {task_name}") from exc


def resolve_queue_name(queue: str | None, default_queue: str) -> str:
    queue_name = queue or default_queue
    normalized = queue_name.strip()
    if not normalized:
        raise ValueError("Job queue name must not be empty.")
    return normalized


def validate_delay(delay_seconds: int | None) -> None:
    if delay_seconds is not None and delay_seconds < 1:
        raise ValueError("Job delay must be at least 1 second.")


def validate_job_payload(args: tuple[object, ...], kwargs: dict[str, object] | None) -> None:
    for arg in args:
        validate_job_value(arg)
    for key, value in (kwargs or {}).items():
        if not isinstance(key, str):
            raise ValueError("Job keyword argument names must be strings.")
        validate_job_value(value)


def validate_job_value(value: object) -> None:
    if value is None or isinstance(value, str | int | float | bool):
        return
    if isinstance(value, tuple | list):
        for item in value:
            validate_job_value(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("Job payload dictionaries must use string keys.")
            validate_job_value(item)
        return
    raise ValueError(f"Job payload value is not serializable: {type(value).__name__}")
