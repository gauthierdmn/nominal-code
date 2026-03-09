from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from nominal_code.llm.messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName

pytest.importorskip("redis")

import redis

from nominal_code.conversation.redis import (
    RedisConversationStore,
    _build_key,
    _deserialize_messages,
    _serialize_messages,
)


class TestBuildKey:
    def test_build_key_messages(self):
        result = _build_key(
            "msgs", PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert result == "nc:msgs:github:owner/repo:42:worker"

    def test_build_key_conversation(self):
        result = _build_key(
            "conv", PlatformName.GITLAB, "group/project", 7, BotType.REVIEWER
        )

        assert result == "nc:conv:gitlab:group/project:7:reviewer"


class TestSerializationRoundTrip:
    def test_text_block_round_trip(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hello")]),
            Message(role="assistant", content=[TextBlock(text="world")]),
        ]
        serialized = _serialize_messages(messages)
        result = _deserialize_messages(serialized)

        assert len(result) == 2
        assert result[0].role == "user"
        assert result[0].content[0].text == "hello"
        assert result[1].role == "assistant"
        assert result[1].content[0].text == "world"

    def test_tool_use_block_round_trip(self):
        messages = [
            Message(
                role="assistant",
                content=[
                    ToolUseBlock(
                        id="call_1",
                        name="Read",
                        input={"file_path": "/foo/bar.py", "offset": 10},
                    ),
                ],
            ),
        ]
        serialized = _serialize_messages(messages)
        result = _deserialize_messages(serialized)

        block = result[0].content[0]

        assert isinstance(block, ToolUseBlock)
        assert block.id == "call_1"
        assert block.name == "Read"
        assert block.input == {"file_path": "/foo/bar.py", "offset": 10}

    def test_tool_result_block_round_trip(self):
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call_1",
                        content="file contents here",
                        is_error=False,
                    ),
                ],
            ),
        ]
        serialized = _serialize_messages(messages)
        result = _deserialize_messages(serialized)

        block = result[0].content[0]

        assert isinstance(block, ToolResultBlock)
        assert block.tool_use_id == "call_1"
        assert block.content == "file contents here"
        assert block.is_error is False

    def test_tool_result_error_round_trip(self):
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call_2",
                        content="command failed",
                        is_error=True,
                    ),
                ],
            ),
        ]
        serialized = _serialize_messages(messages)
        result = _deserialize_messages(serialized)

        block = result[0].content[0]

        assert isinstance(block, ToolResultBlock)
        assert block.is_error is True

    def test_mixed_content_blocks_round_trip(self):
        messages = [
            Message(
                role="assistant",
                content=[
                    TextBlock(text="I'll read the file"),
                    ToolUseBlock(
                        id="call_3",
                        name="Glob",
                        input={"pattern": "*.py"},
                    ),
                ],
            ),
            Message(
                role="user",
                content=[
                    ToolResultBlock(
                        tool_use_id="call_3",
                        content="main.py\nconfig.py",
                    ),
                ],
            ),
        ]
        serialized = _serialize_messages(messages)
        result = _deserialize_messages(serialized)

        assert len(result) == 2
        assert len(result[0].content) == 2
        assert isinstance(result[0].content[0], TextBlock)
        assert isinstance(result[0].content[1], ToolUseBlock)
        assert isinstance(result[1].content[0], ToolResultBlock)

    def test_empty_messages_round_trip(self):
        serialized = _serialize_messages([])
        result = _deserialize_messages(serialized)

        assert result == []


class TestRedisConversationStoreGetConversationId:
    def test_get_returns_none_when_not_found(self):
        client = MagicMock(spec=redis.Redis)
        client.get.return_value = None
        store = RedisConversationStore(client)

        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_get_returns_value(self):
        client = MagicMock(spec=redis.Redis)
        client.get.return_value = b"conv-123"
        store = RedisConversationStore(client)

        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER
        )

        assert result == "conv-123"

    def test_get_returns_none_on_redis_error(self):
        client = MagicMock(spec=redis.Redis)
        client.get.side_effect = redis.RedisError("connection lost")
        store = RedisConversationStore(client)

        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None


class TestRedisConversationStoreSetConversationId:
    def test_set_calls_redis_with_ttl(self):
        client = MagicMock(spec=redis.Redis)
        store = RedisConversationStore(client, key_ttl=timedelta(hours=1))

        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER, "conv-abc"
        )

        client.set.assert_called_once_with(
            "nc:conv:github:owner/repo:42:reviewer",
            b"conv-abc",
            ex=timedelta(hours=1),
        )

    def test_set_silently_catches_redis_error(self):
        client = MagicMock(spec=redis.Redis)
        client.set.side_effect = redis.RedisError("connection lost")
        store = RedisConversationStore(client)

        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "val"
        )


class TestRedisConversationStoreGetMessages:
    def test_get_returns_none_when_not_found(self):
        client = MagicMock(spec=redis.Redis)
        client.get.return_value = None
        store = RedisConversationStore(client)

        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_get_deserializes_messages(self):
        messages = [Message(role="user", content=[TextBlock(text="hi")])]
        serialized = _serialize_messages(messages)
        client = MagicMock(spec=redis.Redis)
        client.get.return_value = serialized.encode("utf-8")
        store = RedisConversationStore(client)

        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER
        )

        assert result is not None
        assert len(result) == 1
        assert result[0].content[0].text == "hi"

    def test_get_returns_none_on_redis_error(self):
        client = MagicMock(spec=redis.Redis)
        client.get.side_effect = redis.RedisError("connection lost")
        store = RedisConversationStore(client)

        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_get_returns_none_on_corrupt_data(self):
        client = MagicMock(spec=redis.Redis)
        client.get.return_value = b"not valid json"
        store = RedisConversationStore(client)

        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None


class TestRedisConversationStoreSetMessages:
    def test_set_calls_redis_with_ttl(self):
        client = MagicMock(spec=redis.Redis)
        store = RedisConversationStore(client, key_ttl=timedelta(hours=2))
        messages = [Message(role="user", content=[TextBlock(text="hello")])]

        store.set_messages(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, messages
        )

        client.set.assert_called_once()
        call_args = client.set.call_args

        assert call_args[0][0] == "nc:msgs:github:owner/repo:42:worker"
        assert call_args[1]["ex"] == timedelta(hours=2)

    def test_set_silently_catches_redis_error(self):
        client = MagicMock(spec=redis.Redis)
        client.set.side_effect = redis.RedisError("connection lost")
        store = RedisConversationStore(client)
        messages = [Message(role="user", content=[TextBlock(text="hi")])]

        store.set_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, messages
        )
