from __future__ import annotations

import logging
from datetime import timedelta
from typing import Protocol, runtime_checkable

from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.llm.messages import Message, TextBlock
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName

logger: logging.Logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS: int = 500_000


@runtime_checkable
class ConversationStore(Protocol):
    """
    Protocol for per-PR conversation state persistence.

    Implementations must support storing and retrieving conversation IDs
    (CLI session IDs or provider response IDs) and full message histories
    (API mode only), keyed by ``(platform, repo, pr_number, bot_type)``.
    An optional ``namespace`` parameter isolates keys across logical
    boundaries (e.g. tenants in multi-tenant deployments).
    """

    def get_conversation_id(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        namespace: str = "",
    ) -> str | None:
        """
        Look up the conversation ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            namespace (str): Logical namespace for key isolation.

        Returns:
            str | None: The stored conversation ID, or None if none exists.
        """

        ...

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
        Store a conversation ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            value (str): The conversation ID to store.
            namespace (str): Logical namespace for key isolation.
        """

        ...

    def get_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        namespace: str = "",
    ) -> list[Message] | None:
        """
        Look up stored messages for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            namespace (str): Logical namespace for key isolation.

        Returns:
            list[Message] | None: The stored messages, or None if none exist.
        """

        ...

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
        Store messages for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            value (list[Message]): The messages to store.
            namespace (str): Logical namespace for key isolation.
        """

        ...


def truncate_messages(
    messages: list[Message],
    max_chars: int = MAX_MESSAGE_CHARS,
) -> list[Message]:
    """
    Drop oldest user+assistant message pairs to stay within a character budget.

    Preserves the most recent turns. Each message's character cost is
    estimated from its text blocks.

    Args:
        messages (list[Message]): The full message history.
        max_chars (int): Maximum total characters allowed.

    Returns:
        list[Message]: The truncated message list.
    """

    if not messages:
        return messages

    total_chars: int = sum(_message_chars(msg) for msg in messages)

    if total_chars <= max_chars:
        return messages

    result: list[Message] = list(messages)

    while len(result) > 1 and total_chars > max_chars:
        removed: Message = result.pop(0)
        total_chars -= _message_chars(removed)

        if result and result[0].role == "assistant":
            removed_assistant: Message = result.pop(0)
            total_chars -= _message_chars(removed_assistant)

    return result


def _message_chars(message: Message) -> int:
    """
    Estimate the character count of a message from its content blocks.

    Args:
        message (Message): The message to measure.

    Returns:
        int: Approximate character count.
    """

    total: int = 0

    for block in message.content:
        if isinstance(block, TextBlock):
            total += len(block.text)
        else:
            total += 200

    return total


def build_conversation_store(
    redis_url: str | None = None,
    redis_key_ttl_seconds: int | None = None,
) -> ConversationStore:
    """
    Build a conversation store based on whether Redis is configured.

    Returns a ``RedisConversationStore`` when ``redis_url`` is provided.
    Returns a ``MemoryConversationStore`` when no URL is given. Raises
    when Redis is requested but cannot be created.

    Args:
        redis_url (str | None): Redis connection URL
            (e.g. ``redis://host:6379/0``). When ``None``, an in-memory
            store is returned.
        redis_key_ttl_seconds (int | None): TTL in seconds for Redis keys.
            Uses the Redis store default (7 days) when ``None``.

    Returns:
        ConversationStore: A Redis-backed or in-memory conversation store.

    Raises:
        ImportError: If the ``redis`` package is not installed.
        ValueError: If ``redis_url`` is malformed.
        redis.RedisError: If the Redis client cannot be created.
    """

    if not redis_url:
        logger.info("No redis_url configured, using in-memory conversation store")

        return MemoryConversationStore()

    import redis

    from nominal_code.conversation.redis import (
        DEFAULT_KEY_TTL,
        RedisConversationStore,
    )

    key_ttl: timedelta = (
        timedelta(seconds=redis_key_ttl_seconds)
        if redis_key_ttl_seconds is not None
        else DEFAULT_KEY_TTL
    )

    client: redis.Redis = redis.Redis.from_url(url=redis_url)
    store: RedisConversationStore = RedisConversationStore(
        client=client,
        key_ttl=key_ttl,
    )

    logger.info("Using Redis conversation store at %s", redis_url)

    return store
