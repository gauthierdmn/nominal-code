# type: ignore
from unittest.mock import MagicMock, patch

import pytest

from nominal_code.conversation.base import ConversationStore, build_conversation_store
from nominal_code.conversation.memory import MemoryConversationStore

pytest.importorskip("redis")

import redis

from nominal_code.conversation.redis import RedisConversationStore


class TestBuildConversationStoreMemory:
    def test_build_conversation_store_returns_memory_when_no_url(self):
        store = build_conversation_store()

        assert isinstance(store, MemoryConversationStore)

    def test_build_conversation_store_returns_memory_when_empty_url(self):
        store = build_conversation_store(redis_url="")

        assert isinstance(store, MemoryConversationStore)

    def test_build_conversation_store_memory_satisfies_protocol(self):
        store = build_conversation_store()

        assert isinstance(store, ConversationStore)


class TestBuildConversationStoreRedis:
    @patch("redis.Redis.from_url")
    def test_build_conversation_store_returns_redis_when_url_provided(
        self,
        mock_from_url,
    ):
        mock_from_url.return_value = MagicMock()

        store = build_conversation_store(redis_url="redis://localhost:6379/0")

        assert isinstance(store, RedisConversationStore)

    @patch("redis.Redis.from_url")
    def test_build_conversation_store_redis_satisfies_protocol(self, mock_from_url):
        mock_from_url.return_value = MagicMock()

        store = build_conversation_store(redis_url="redis://localhost:6379/0")

        assert isinstance(store, ConversationStore)

    @patch("redis.Redis.from_url")
    def test_build_conversation_store_accepts_custom_ttl(self, mock_from_url):
        mock_from_url.return_value = MagicMock()

        store = build_conversation_store(
            redis_url="redis://localhost:6379/0",
            redis_key_ttl_seconds=3600,
        )

        assert isinstance(store, RedisConversationStore)

    @patch("redis.Redis.from_url")
    def test_build_conversation_store_uses_default_ttl_when_zero(self, mock_from_url):
        mock_from_url.return_value = MagicMock()

        store = build_conversation_store(
            redis_url="redis://localhost:6379/0",
            redis_key_ttl_seconds=0,
        )

        assert isinstance(store, RedisConversationStore)


class TestBuildConversationStoreRedisFailure:
    @patch(
        "redis.Redis.from_url",
        side_effect=redis.RedisError("connection refused"),
    )
    def test_build_conversation_store_raises_on_redis_error(self, mock_from_url):
        with pytest.raises(redis.RedisError):
            build_conversation_store(redis_url="redis://bad-host:6379/0")

    def test_build_conversation_store_raises_on_malformed_url(self):
        with pytest.raises((ValueError, redis.RedisError)):
            build_conversation_store(redis_url="not-a-url")
