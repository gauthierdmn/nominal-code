from __future__ import annotations

from nominal_code.llm.messages import Message
from nominal_code.models import PRKey
from nominal_code.platforms.base import PlatformName


class MemoryConversationStore:
    """
    In-memory store for per-PR conversation state.

    Holds two parallel dicts keyed by
    ``(platform, repo, pr_number, namespace)``:
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
        namespace: str = "",
    ) -> str | None:
        """
        Look up the conversation ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            namespace (str): Logical namespace for key isolation.

        Returns:
            str | None: The stored conversation ID, or None if none exists.
        """

        return self._conversation_ids.get(
            (platform.value, repo, pr_number, namespace),
        )

    def set_conversation_id(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        value: str,
        namespace: str = "",
    ) -> None:
        """
        Store a conversation ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            value (str): The conversation ID to store.
            namespace (str): Logical namespace for key isolation.
        """

        self._conversation_ids[(platform.value, repo, pr_number, namespace)] = value

    def get_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        namespace: str = "",
    ) -> list[Message] | None:
        """
        Look up stored messages for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            namespace (str): Logical namespace for key isolation.

        Returns:
            list[Message] | None: The stored messages, or None if none exist.
        """

        return self._messages.get(
            (platform.value, repo, pr_number, namespace),
        )

    def set_messages(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        value: list[Message],
        namespace: str = "",
    ) -> None:
        """
        Store messages for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            value (list[Message]): The messages to store.
            namespace (str): Logical namespace for key isolation.
        """

        self._messages[(platform.value, repo, pr_number, namespace)] = value
