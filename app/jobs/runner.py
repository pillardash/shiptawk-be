from app.jobs.registry import TASK_REGISTRY
from app.services.jobs.base import resolve_task


def run_registered_task(
    task_name: str,
    args: tuple[object, ...] = (),
    kwargs: dict[str, object] | None = None,
) -> None:
    task = resolve_task(task_name, TASK_REGISTRY)
    task(*args, **(kwargs or {}))
