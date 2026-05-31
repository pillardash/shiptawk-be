import sys
from types import ModuleType

import pytest

from app.core.config import Settings
from app.jobs.registry import TASK_REGISTRY
from app.jobs.runner import run_registered_task
from app.services.jobs import create_job_service
from app.services.jobs.base import resolve_task
from app.services.jobs.inline import InlineJobService
from app.services.jobs.rq import RqJobService


def test_inline_job_service_executes_registered_task() -> None:
    calls: list[tuple[object, object]] = []

    def task(value: object, *, flag: object) -> None:
        calls.append((value, flag))

    service = InlineJobService(default_queue="default", registry={"test.task": task})

    handle = service.enqueue("test.task", args=("value",), kwargs={"flag": True})

    assert calls == [("value", True)]
    assert handle.task_name == "test.task"
    assert handle.queue == "default"


def test_inline_job_service_honors_explicit_queue() -> None:
    service = InlineJobService(default_queue="default", registry={"test.task": lambda: None})

    handle = service.enqueue("test.task", queue="email")

    assert handle.queue == "email"


def test_inline_job_service_rejects_empty_queue() -> None:
    service = InlineJobService(default_queue="default", registry={"test.task": lambda: None})

    with pytest.raises(ValueError):
        service.enqueue("test.task", queue=" ")


def test_inline_job_service_rejects_non_positive_delay() -> None:
    service = InlineJobService(default_queue="default", registry={"test.task": lambda: None})

    with pytest.raises(ValueError):
        service.enqueue("test.task", delay_seconds=0)


def test_inline_job_service_rejects_non_serializable_payload() -> None:
    service = InlineJobService(default_queue="default", registry={"test.task": lambda value: None})

    with pytest.raises(ValueError):
        service.enqueue("test.task", args=(object(),))


def test_unknown_job_task_is_rejected() -> None:
    with pytest.raises(ValueError):
        resolve_task("missing.task", {})


def test_registered_task_runner_executes_stable_task_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def task(*, value: str) -> None:
        calls.append(value)

    monkeypatch.setitem(TASK_REGISTRY, "test.runner_task", task)

    run_registered_task("test.runner_task", kwargs={"value": "ok"})

    assert calls == ["ok"]


def test_create_job_service_uses_inline_by_default() -> None:
    service = create_job_service(Settings())

    assert isinstance(service, InlineJobService)


def test_rq_backend_requires_redis_url() -> None:
    with pytest.raises(ValueError):
        Settings(jobs_backend="rq")


def test_rq_backend_can_use_redis_url_fallback() -> None:
    settings = Settings(jobs_backend="rq", redis_url="redis://localhost:6379/0")

    assert settings.resolved_jobs_redis_url == "redis://localhost:6379/0"


def test_job_numeric_settings_must_be_positive() -> None:
    with pytest.raises(ValueError):
        Settings(jobs_default_timeout_seconds=0)


def test_category_queue_settings_are_available() -> None:
    settings = Settings()

    assert settings.jobs_email_queue == "email"
    assert settings.jobs_files_queue == "files"
    assert settings.jobs_ai_queue == "ai"
    assert settings.jobs_reports_queue == "reports"
    assert settings.jobs_webhooks_queue == "webhooks"
    assert settings.jobs_notifications_queue == "notifications"


def test_registry_contains_initial_task_categories() -> None:
    expected = {
        "email.send_password_reset",
        "email.send_verification",
        "email.send_invite",
        "email.send_system_notification",
        "files.process_upload",
        "ai.run_task",
        "reports.generate",
        "webhooks.deliver",
        "notifications.send",
    }

    assert expected.issubset(TASK_REGISTRY)


def test_unimplemented_category_jobs_fail_explicitly() -> None:
    with pytest.raises(NotImplementedError):
        TASK_REGISTRY["files.process_upload"](storage_key="uploads/file.txt")


def test_rq_job_service_enqueues_registered_task(monkeypatch: pytest.MonkeyPatch) -> None:
    enqueued: list[dict[str, object]] = []

    class FakeRedis:
        @classmethod
        def from_url(cls, url: str) -> "FakeRedis":
            return cls()

    class FakeRedisModule(ModuleType):
        Redis = FakeRedis

    class FakeJob:
        id = "rq-job-id"

    class FakeQueue:
        def __init__(self, name: str, connection: object) -> None:
            self.name = name
            self.connection = connection

        def enqueue(self, task: object, **kwargs: object) -> FakeJob:
            enqueued.append({"queue": self.name, "task": task, **kwargs})
            return FakeJob()

        def enqueue_in(self, delay: object, task: object, **kwargs: object) -> FakeJob:
            enqueued.append({"queue": self.name, "delay": delay, "task": task, **kwargs})
            return FakeJob()

    class FakeRqModule(ModuleType):
        Queue = FakeQueue

    def task() -> None:
        return None

    monkeypatch.setitem(sys.modules, "redis", FakeRedisModule("redis"))
    monkeypatch.setitem(sys.modules, "rq", FakeRqModule("rq"))
    service = RqJobService(
        redis_url="redis://localhost:6379/0",
        default_queue="default",
        registry={"test.task": task},
        default_timeout_seconds=300,
        result_ttl_seconds=60,
        failure_ttl_seconds=120,
    )

    handle = service.enqueue("test.task", queue="email", kwargs={"value": "x"})

    assert handle.id == "rq-job-id"
    assert handle.queue == "email"
    assert enqueued[0]["queue"] == "email"
    assert enqueued[0]["task"] is run_registered_task
    assert enqueued[0]["args"] == ("test.task", (), {"value": "x"})
    assert enqueued[0]["job_timeout"] == 300


def test_rq_job_service_supports_delayed_enqueue(monkeypatch: pytest.MonkeyPatch) -> None:
    enqueued: list[dict[str, object]] = []

    class FakeRedis:
        @classmethod
        def from_url(cls, url: str) -> "FakeRedis":
            return cls()

    class FakeRedisModule(ModuleType):
        Redis = FakeRedis

    class FakeJob:
        id = "delayed-job-id"

    class FakeQueue:
        def __init__(self, name: str, connection: object) -> None:
            self.name = name

        def enqueue(self, task: object, **kwargs: object) -> FakeJob:
            return FakeJob()

        def enqueue_in(self, delay: object, task: object, **kwargs: object) -> FakeJob:
            enqueued.append({"delay": delay, "task": task, **kwargs})
            return FakeJob()

    class FakeRqModule(ModuleType):
        Queue = FakeQueue

    monkeypatch.setitem(sys.modules, "redis", FakeRedisModule("redis"))
    monkeypatch.setitem(sys.modules, "rq", FakeRqModule("rq"))
    service = RqJobService(
        redis_url="redis://localhost:6379/0",
        default_queue="default",
        registry={"test.task": lambda: None},
        default_timeout_seconds=300,
        result_ttl_seconds=60,
        failure_ttl_seconds=120,
    )

    handle = service.enqueue("test.task", delay_seconds=10)

    assert handle.id == "delayed-job-id"
    assert enqueued[0]["task"] is run_registered_task
