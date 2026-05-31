import hashlib
from pathlib import Path

from app.services.storage.base import StoredObject, validate_storage_key


class LocalStorageService:
    def __init__(self, *, root_path: str) -> None:
        self.root_path = Path(root_path).resolve()
        self.root_path.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, content: bytes, *, content_type: str | None = None) -> StoredObject:
        key = validate_storage_key(key)
        path = self._path_for_key(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredObject(
            key=key,
            content_type=content_type,
            content_length=len(content),
            etag=hashlib.sha256(content).hexdigest(),
        )

    def get(self, key: str) -> bytes:
        key = validate_storage_key(key)
        return self._path_for_key(key).read_bytes()

    def delete(self, key: str) -> None:
        key = validate_storage_key(key)
        self._path_for_key(key).unlink(missing_ok=True)

    def exists(self, key: str) -> bool:
        key = validate_storage_key(key)
        return self._path_for_key(key).is_file()

    def url(self, key: str) -> str | None:
        key = validate_storage_key(key)
        self._path_for_key(key)
        return None

    def _path_for_key(self, key: str) -> Path:
        path = (self.root_path / key).resolve()
        if path == self.root_path or self.root_path not in path.parents:
            raise ValueError("Storage key escapes the configured storage root.")
        return path
