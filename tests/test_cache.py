from app.services.cache import InMemoryCache


def test_in_memory_cache_get_set_delete() -> None:
    cache = InMemoryCache()

    cache.set("key", "value")

    assert cache.get("key") == "value"
    cache.delete("key")
    assert cache.get("key") is None


def test_in_memory_cache_increment_sets_ttl() -> None:
    cache = InMemoryCache()

    assert cache.increment("counter", ttl_seconds=60) == 1
    assert cache.increment("counter", ttl_seconds=60) == 2
    assert cache.get("counter") == "2"
    assert cache.ttl("counter") > 0
