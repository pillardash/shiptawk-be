from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Protocol

from app.core.config import Settings


@dataclass(frozen=True)
class StoredObject:
    key: str
    content_type: str | None = None
    content_length: int | None = None
    etag: str | None = None


class StorageService(Protocol):
    def put(self, key: str, content: bytes, *, content_type: str | None = None) -> StoredObject: ...

    def get(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...

    def exists(self, key: str) -> bool: ...

    def url(self, key: str) -> str | None: ...


def validate_storage_key(key: str) -> str:
    if not key or key.startswith(("/", "\\")) or "\\" in key:
        raise ValueError("Storage key must be a relative POSIX path.")
    raw_parts = key.split("/")
    path = PurePosixPath(key)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_parts):
        raise ValueError("Storage key must not contain empty, current, or parent path parts.")
    if any(ord(char) < 32 for char in key):
        raise ValueError("Storage key must not contain control characters.")
    return key


def create_storage_service(settings: Settings) -> StorageService:
    if settings.storage_provider == "local":
        from app.services.storage.local import LocalStorageService

        return LocalStorageService(root_path=settings.resolved_storage_local_path)

    if settings.storage_provider == "s3":
        from app.services.storage.s3 import S3StorageService

        if (
            settings.storage_bucket is None
            or settings.storage_access_key is None
            or settings.storage_secret_key is None
        ):
            raise ValueError("S3 storage requires bucket, access key, and secret key.")
        return S3StorageService(
            bucket=settings.storage_bucket,
            region=settings.storage_region,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key,
            endpoint_url=settings.storage_endpoint,
            public_base_url=settings.storage_public_base_url,
            presigned_url_expire_seconds=settings.storage_presigned_url_expire_seconds,
        )

    raise ValueError("Unsupported storage provider.")
