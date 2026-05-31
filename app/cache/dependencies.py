from app.cache.redis import RedisCache
from app.core.config import Settings
from app.services.cache import CacheService, InMemoryCache


def create_cache(settings: Settings) -> CacheService:
    if settings.cache_backend == "memory":
        return InMemoryCache()
    if settings.cache_backend == "redis" and settings.redis_url:
        return RedisCache(settings.redis_url)
    raise ValueError("Unsupported cache backend.")
