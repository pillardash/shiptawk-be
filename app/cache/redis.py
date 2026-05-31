import redis

from app.services.cache import CacheService


class RedisCache(CacheService):
    def __init__(self, redis_url: str) -> None:
        self.client = redis.Redis.from_url(redis_url, decode_responses=True)

    def get(self, key: str) -> str | None:
        value = self.client.get(key)
        return value if isinstance(value, str) else None

    def set(self, key: str, value: str, *, ttl_seconds: int | None = None) -> None:
        self.client.set(key, value, ex=ttl_seconds)

    def delete(self, key: str) -> None:
        self.client.delete(key)

    def increment(self, key: str, *, ttl_seconds: int | None = None) -> int:
        if ttl_seconds is None:
            return int(self.client.incr(key))
        return int(
            self.client.eval(
                """
                local count = redis.call('INCR', KEYS[1])
                if count == 1 then
                    redis.call('EXPIRE', KEYS[1], ARGV[1])
                end
                return count
                """,
                1,
                key,
                ttl_seconds,
            )
        )

    def ttl(self, key: str) -> int:
        return int(self.client.ttl(key))
