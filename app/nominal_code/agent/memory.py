from __future__ import annotations

from nominal_code.agent.providers.types import Message, TextBlock
from nominal_code.models import BotType, PRKey
from nominal_code.platforms.base import PlatformName

MAX_MESSAGE_CHARS: int = 500_000


class ConversationStore:
    """
    In-memory store for per-PR conversation state.

    Holds two parallel dicts keyed by ``(platform, repo, pr_number, bot_type)``:
    lightweight conversation IDs (CLI session IDs or provider response IDs)
    and full message histories (API mode only).
    """

    def __init__(self) -> None:
        """
        Initialize an empty conversation store.
        """

        self._conversation_ids: dict[PRKey, str] = {}
        self._messages: dict[PRKey, list[Message]] = {}

    def get_conversation_id(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
    ) -> str | None:
        """
        Look up the conversation ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.

        Returns:
            str | None: The stored conversation ID, or None if none exists.
        """

        return self._conversation_ids.get(
            (platform.value, repo, pr_number, bot_type.value),
        )

    def set_conversation_id(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        value: str,
    ) -> None:
        """
        Store a conversation ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            value (str): The conversation ID to store.
        """

        self._conversation_ids[(platform.value, repo, pr_number, bot_type.value)] = (
            value
        )

    def get_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
    ) -> list[Message] | None:
        """
        Look up stored messages for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.

        Returns:
            list[Message] | None: The stored messages, or None if none exist.
        """

        return self._messages.get(
            (platform.value, repo, pr_number, bot_type.value),
        )

    def set_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        value: list[Message],
    ) -> None:
        """
        Store messages for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            value (list[Message]): The messages to store.
        """

        self._messages[(platform.value, repo, pr_number, bot_type.value)] = value


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
