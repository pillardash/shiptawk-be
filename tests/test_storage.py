import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.core.config import Settings
from app.services.storage.base import create_storage_service, validate_storage_key
from app.services.storage.local import LocalStorageService
from app.services.storage.s3 import S3StorageService


def test_local_storage_put_get_exists_delete(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    storage = LocalStorageService(root_path=str(root))

    stored = storage.put("avatars/user.txt", b"content", content_type="text/plain")

    assert stored.key == "avatars/user.txt"
    assert stored.content_type == "text/plain"
    assert stored.content_length == 7
    assert stored.etag is not None
    assert storage.exists("avatars/user.txt") is True
    assert storage.get("avatars/user.txt") == b"content"
    assert storage.url("avatars/user.txt") is None

    storage.delete("avatars/user.txt")

    assert storage.exists("avatars/user.txt") is False


def test_local_storage_rejects_path_traversal(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    storage = LocalStorageService(root_path=str(root))

    with pytest.raises(ValueError):
        storage.put("../secret.txt", b"secret")

    with pytest.raises(ValueError):
        storage.put("/secret.txt", b"secret")

    with pytest.raises(ValueError):
        storage.put("nested/../secret.txt", b"secret")

    with pytest.raises(ValueError):
        storage.put("nested\\secret.txt", b"secret")


def test_storage_key_validation_rejects_unsafe_keys() -> None:
    assert validate_storage_key("reports/2026/export.csv") == "reports/2026/export.csv"

    for key in ["", "/absolute", "../secret", "nested/../secret", "nested//secret", "a\x00b"]:
        with pytest.raises(ValueError):
            validate_storage_key(key)


def test_storage_factory_uses_local_by_default(tmp_path: Path) -> None:
    root = tmp_path / "storage"
    settings = Settings(storage_local_path=str(root))

    storage = create_storage_service(settings)

    assert isinstance(storage, LocalStorageService)


def test_production_local_storage_requires_explicit_path() -> None:
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            allowed_hosts=["api.example.com"],
            cors_origins=["https://app.example.com"],
            jwt_secret_key="changed",
            storage_provider="local",
        )


def test_s3_storage_requires_bucket_and_credentials() -> None:
    with pytest.raises(ValueError):
        Settings(storage_provider="s3")


def test_s3_storage_can_be_created_with_s3_compatible_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeBody:
        def read(self) -> bytes:
            return b"content"

    class FakeClient:
        def __init__(self) -> None:
            self.objects: dict[str, bytes] = {}

        def put_object(self, **kwargs: Any) -> dict[str, str]:
            self.objects[str(kwargs["Key"])] = bytes(kwargs["Body"])
            return {"ETag": '"etag-value"'}

        def get_object(self, **kwargs: Any) -> dict[str, FakeBody]:
            return {"Body": FakeBody()}

        def delete_object(self, **kwargs: Any) -> None:
            self.objects.pop(str(kwargs["Key"]), None)

        def head_object(self, **kwargs: Any) -> None:
            return None

        def generate_presigned_url(
            self,
            client_method: str,
            Params: dict[str, str],
            ExpiresIn: int,
        ) -> str:
            return f"https://storage.example.com/{Params['Key']}?expires={ExpiresIn}"

    fake_client = FakeClient()

    class FakeBoto3(ModuleType):
        def client(self, *args: Any, **kwargs: Any) -> FakeClient:
            return fake_client

    class FakeBotocoreExceptions(ModuleType):
        class ClientError(Exception):
            def __init__(self) -> None:
                self.response = {"Error": {"Code": "404"}}

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3("boto3"))
    monkeypatch.setitem(
        sys.modules,
        "botocore.exceptions",
        FakeBotocoreExceptions("botocore.exceptions"),
    )
    storage = S3StorageService(
        bucket="bucket",
        region="us-east-1",
        access_key="access",
        secret_key="secret",
        endpoint_url="https://r2.example.com",
        presigned_url_expire_seconds=60,
    )

    stored = storage.put("exports/report.pdf", b"content", content_type="application/pdf")

    assert stored.key == "exports/report.pdf"
    assert stored.content_length == 7
    assert stored.etag == "etag-value"
    assert storage.get("exports/report.pdf") == b"content"
    assert storage.exists("exports/report.pdf") is True
    assert (
        storage.url("exports/report.pdf")
        == "https://storage.example.com/exports/report.pdf?expires=60"
    )


def test_s3_public_url_does_not_expose_local_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBoto3(ModuleType):
        def client(self, *args: Any, **kwargs: Any) -> object:
            return object()

    class FakeBotocoreExceptions(ModuleType):
        class ClientError(Exception):
            def __init__(self) -> None:
                self.response = {"Error": {"Code": "404"}}

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3("boto3"))
    monkeypatch.setitem(
        sys.modules,
        "botocore.exceptions",
        FakeBotocoreExceptions("botocore.exceptions"),
    )
    storage = S3StorageService(
        bucket="bucket",
        region="us-east-1",
        access_key="access",
        secret_key="secret",
        public_base_url="https://cdn.example.com/files",
    )

    assert (
        storage.url("reports/May 2026.pdf")
        == "https://cdn.example.com/files/reports/May%202026.pdf"
    )


def test_s3_storage_rejects_unsafe_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeBoto3(ModuleType):
        def client(self, *args: Any, **kwargs: Any) -> object:
            return object()

    class FakeBotocoreExceptions(ModuleType):
        class ClientError(Exception):
            def __init__(self) -> None:
                self.response = {"Error": {"Code": "404"}}

    monkeypatch.setitem(sys.modules, "boto3", FakeBoto3("boto3"))
    monkeypatch.setitem(
        sys.modules,
        "botocore.exceptions",
        FakeBotocoreExceptions("botocore.exceptions"),
    )
    storage = S3StorageService(
        bucket="bucket",
        region="us-east-1",
        access_key="access",
        secret_key="secret",
    )

    with pytest.raises(ValueError):
        storage.url("../secret.txt")
