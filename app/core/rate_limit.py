from abc import ABC, abstractmethod
from dataclasses import dataclass

from app.services.cache import CacheService


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    retry_after_seconds: int = 0


class RateLimitStore(ABC):
    @abstractmethod
    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        raise NotImplementedError

    @abstractmethod
    def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        raise NotImplementedError


class CacheRateLimitStore(RateLimitStore):
    def __init__(self, cache: CacheService) -> None:
        self.cache = cache

    def check(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        value = self.cache.get(key)
        count = int(value) if value is not None else 0
        if count >= limit:
            return RateLimitResult(
                allowed=False,
                retry_after_seconds=max(1, self.cache.ttl(key)),
            )
        return RateLimitResult(allowed=True)

    def hit(self, key: str, *, limit: int, window_seconds: int) -> RateLimitResult:
        count = self.cache.increment(key, ttl_seconds=window_seconds)
        if count >= limit:
            return RateLimitResult(
                allowed=count == limit,
                retry_after_seconds=max(1, self.cache.ttl(key)),
            )
        return RateLimitResult(allowed=True)


def create_rate_limit_store(cache: CacheService) -> RateLimitStore:
    return CacheRateLimitStore(cache)
