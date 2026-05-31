import time
from typing import Protocol


class CacheService(Protocol):
    def get(self, key: str) -> str | None: ...

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None: ...

    def delete(self, key: str) -> None: ...

    def increment(self, key: str, *, ttl_seconds: int | None = None) -> int: ...

    def ttl(self, key: str) -> int: ...


class InMemoryCache:
    def __init__(self) -> None:
        self._values: dict[str, tuple[str, float | None]] = {}

    def get(self, key: str) -> str | None:
        item = self._values.get(key)
        if item is None:
            return None
        value, expires_at = item
        if expires_at is not None and expires_at <= time.monotonic():
            self.delete(key)
            return None
        return value

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        expires_at = time.monotonic() + ttl_seconds if ttl_seconds is not None else None
        self._values[key] = (value, expires_at)

    def delete(self, key: str) -> None:
        self._values.pop(key, None)

    def increment(self, key: str, *, ttl_seconds: int | None = None) -> int:
        value = self.get(key)
        count = int(value) + 1 if value is not None else 1
        existing_ttl = self.ttl(key)
        ttl = ttl_seconds if value is None else existing_ttl if existing_ttl > 0 else None
        self.set(key, str(count), ttl_seconds=ttl)
        return count

    def ttl(self, key: str) -> int:
        item = self._values.get(key)
        if item is None:
            return -2
        _, expires_at = item
        if expires_at is None:
            return -1
        remaining = int(expires_at - time.monotonic())
        if remaining < 0:
            self.delete(key)
            return -2
        return max(1, remaining)
