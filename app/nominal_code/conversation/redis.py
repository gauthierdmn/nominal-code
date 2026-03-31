from __future__ import annotations

import dataclasses
import json
import logging
from datetime import timedelta
from enum import StrEnum
from typing import Any

import redis

from nominal_code.config.settings import DEFAULT_REDIS_KEY_TTL_SECONDS
from nominal_code.llm.messages import (
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_KEY_TTL: timedelta = timedelta(seconds=DEFAULT_REDIS_KEY_TTL_SECONDS)


class _KeyPrefix(StrEnum):
    """
    Redis key prefixes for conversation data.
    """

    CONVERSATION = "conv"
    MESSAGES = "msgs"


class RedisConversationStore:
    """
    Redis-backed store for per-PR conversation state.

    Persists conversation IDs and message histories in Redis so that
    short-lived K8s pods can share conversation context across jobs.

    Attributes:
        _client (redis.Redis): The Redis client.
        _key_ttl (timedelta): Time to live (TTL) for keys.
    """

    def __init__(
        self,
        client: redis.Redis,
        key_ttl: timedelta = DEFAULT_KEY_TTL,
    ) -> None:
        """
        Initialize the Redis conversation store.

        Args:
            client (redis.Redis): A sync Redis client.
            key_ttl (timedelta): TTL for keys.
        """

        self._client = client
        self._key_ttl = key_ttl

    def get_conversation_id(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        namespace: str = "",
    ) -> str | None:
        """
        Look up the conversation ID for a PR/MR thread from Redis.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            namespace (str): Logical namespace for key isolation.

        Returns:
            str | None: The stored conversation ID, or None if not found
                or on Redis error.
        """

        key: str = _build_key(
            prefix=_KeyPrefix.CONVERSATION,
            platform=platform,
            repo=repo,
            pr_number=pr_number,
            bot_type=bot_type,
            namespace=namespace,
        )

        try:
            stored: bytes | None = self._client.get(key)  # type: ignore[assignment]
        except redis.RedisError:
            logger.warning("Redis GET failed for key %s", key, exc_info=True)

            return None

        if stored is None:
            return None

        return stored.decode("utf-8")

    def set_conversation_id(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        value: str,
        namespace: str = "",
    ) -> None:
        """
        Store a conversation ID for a PR/MR thread in Redis.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            value (str): The conversation ID to store.
            namespace (str): Logical namespace for key isolation.
        """

        key: str = _build_key(
            prefix=_KeyPrefix.CONVERSATION,
            platform=platform,
            repo=repo,
            pr_number=pr_number,
            bot_type=bot_type,
            namespace=namespace,
        )

        try:
            self._client.set(
                key,
                value.encode("utf-8"),
                ex=self._key_ttl,
            )
        except redis.RedisError:
            logger.warning("Redis SET failed for key %s", key, exc_info=True)

    def get_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        namespace: str = "",
    ) -> list[Message] | None:
        """
        Look up stored messages for a PR/MR thread from Redis.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            namespace (str): Logical namespace for key isolation.

        Returns:
            list[Message] | None: The stored messages, or None if not found
                or on Redis error.
        """

        key: str = _build_key(
            prefix=_KeyPrefix.MESSAGES,
            platform=platform,
            repo=repo,
            pr_number=pr_number,
            bot_type=bot_type,
            namespace=namespace,
        )

        try:
            stored: bytes | None = self._client.get(key)  # type: ignore[assignment]
        except redis.RedisError:
            logger.warning("Redis GET failed for key %s", key, exc_info=True)

            return None

        if stored is None:
            return None

        try:
            return _deserialize_messages(stored.decode("utf-8"))
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("Failed to deserialize messages from %s: %s", key, exc)

            return None

    def set_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        value: list[Message],
        namespace: str = "",
    ) -> None:
        """
        Store messages for a PR/MR thread in Redis.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            value (list[Message]): The messages to store.
            namespace (str): Logical namespace for key isolation.
        """

        key: str = _build_key(
            prefix=_KeyPrefix.MESSAGES,
            platform=platform,
            repo=repo,
            pr_number=pr_number,
            bot_type=bot_type,
            namespace=namespace,
        )

        try:
            serialized: str = _serialize_messages(value)
            self._client.set(
                key,
                serialized.encode("utf-8"),
                ex=self._key_ttl,
            )
        except redis.RedisError:
            logger.warning("Redis SET failed for key %s", key, exc_info=True)


def _build_key(
    prefix: _KeyPrefix,
    platform: PlatformName,
    repo: str,
    pr_number: int,
    bot_type: BotType,
    namespace: str = "",
) -> str:
    """
    Build a Redis key for conversation data.

    Args:
        prefix (_KeyPrefix): Key prefix for the data type.
        platform (PlatformName): The platform name.
        repo (str): The full repository name.
        pr_number (int): The pull/merge request number.
        bot_type (BotType): The type of bot.
        namespace (str): Logical namespace inserted after ``nc:``.

    Returns:
        str: The Redis key.
    """

    if namespace:
        return (
            f"nc:{namespace}:{prefix}:"
            f"{platform.value}:{repo}:{pr_number}:{bot_type.value}"
        )

    return f"nc:{prefix}:{platform.value}:{repo}:{pr_number}:{bot_type.value}"


def _serialize_messages(messages: list[Message]) -> str:
    """
    Serialize a list of messages to JSON.

    Each content block is annotated with a ``"type"`` discriminator
    for unambiguous deserialization.

    Args:
        messages (list[Message]): The messages to serialize.

    Returns:
        str: JSON-encoded message list.
    """

    serialized: list[dict[str, Any]] = []

    for message in messages:
        blocks: list[dict[str, Any]] = []

        for block in message.content:
            block_dict: dict[str, Any] = dataclasses.asdict(block)

            if isinstance(block, TextBlock):
                block_dict["type"] = "text"
            elif isinstance(block, ToolUseBlock):
                block_dict["type"] = "tool_use"
            elif isinstance(block, ToolResultBlock):
                block_dict["type"] = "tool_result"

            blocks.append(block_dict)

        serialized.append({"role": message.role, "content": blocks})

    return json.dumps(serialized)


def _deserialize_messages(data: str) -> list[Message]:
    """
    Deserialize a JSON string into a list of messages.

    Uses the ``"type"`` discriminator on each content block to reconstruct
    the correct dataclass type.

    Args:
        data (str): JSON-encoded message list.

    Returns:
        list[Message]: The deserialized messages.

    Raises:
        json.JSONDecodeError: If the JSON is malformed.
        KeyError: If a required field is missing.
        TypeError: If a field has an unexpected type.
    """

    parsed_messages: list[dict[str, Any]] = json.loads(data)
    messages: list[Message] = []

    for msg_data in parsed_messages:
        blocks: list[TextBlock | ToolUseBlock | ToolResultBlock] = []

        for block_data in msg_data["content"]:
            block_type: str = block_data["type"]

            if block_type == "text":
                blocks.append(TextBlock(text=block_data["text"]))
            elif block_type == "tool_use":
                blocks.append(
                    ToolUseBlock(
                        id=block_data["id"],
                        name=block_data["name"],
                        input=block_data["input"],
                    ),
                )
            elif block_type == "tool_result":
                blocks.append(
                    ToolResultBlock(
                        tool_use_id=block_data["tool_use_id"],
                        content=block_data["content"],
                        is_error=block_data.get("is_error", False),
                    ),
                )

        messages.append(Message(role=msg_data["role"], content=blocks))

    return messages
