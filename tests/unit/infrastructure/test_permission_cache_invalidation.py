from fnmatch import fnmatch

from app.infrastructure.redis import semantic_cache


async def test_content_invalidation_removes_exact_and_vector_entries_only_for_space(monkeypatch):
    class Redis:
        keys = {
            "cache:exact:tenant:a:1",
            "cache:vec:tenant:a:1",
            "cache:exact:tenant:b:1",
            "cache:vec:other:a:1",
        }
        indexes = {"idx:cache:tenant:a", "idx:cache:tenant:b"}

        async def execute_command(self, command, name, *args):
            assert command == "FT.DROPINDEX"
            self.indexes.discard(name)

        async def scan(self, cursor, match, count):
            return 0, [key for key in self.keys if fnmatch(key, match)]

        async def delete(self, *keys):
            self.keys.difference_update(keys)

    redis = Redis()
    monkeypatch.setattr(semantic_cache, "get_redis_binary", lambda: redis)
    await semantic_cache.SemanticCache().invalidate("tenant", "a")
    assert redis.keys == {"cache:exact:tenant:b:1", "cache:vec:other:a:1"}
    assert redis.indexes == {"idx:cache:tenant:b"}
