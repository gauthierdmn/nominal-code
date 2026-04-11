from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.platforms.base import (
        Platform,
        PullRequestEvent,
    )
    from nominal_code.review.reviewer import ReviewResult


class JobHandler(Protocol):
    """
    Protocol for handling review job execution.

    Implementations control how review jobs are processed.
    The default implementation delegates to the standard handler
    function. Custom implementations can add preprocessing, context
    enrichment, or alternative execution strategies.
    """

    async def handle_review(
        self,
        event: PullRequestEvent,
        prompt: str,
        config: Config,
        platform: Platform,
        conversation_store: ConversationStore | None = None,
        namespace: str = "",
        context: str = "",
    ) -> ReviewResult:
        """
        Execute a code review and post results.

        Args:
            event (PullRequestEvent): The event that triggered the review.
            prompt (str): The extracted user prompt.
            config (Config): Application configuration.
            platform (Platform): Platform client with reviewer capabilities.
            conversation_store (ConversationStore | None): Conversation store for
                conversation continuity.
            namespace (str): Logical namespace for conversation key isolation.
            context (str): Pre-review context to include in the user message.

        Returns:
            ReviewResult: The review result with findings and summary.
        """

        ...


class DefaultJobHandler:
    """
    Default job handler that delegates to ``run_and_post_review``.
    """

    async def handle_review(
        self,
        event: PullRequestEvent,
        prompt: str,
        config: Config,
        platform: Platform,
        conversation_store: ConversationStore | None = None,
        namespace: str = "",
        context: str = "",
    ) -> ReviewResult:
        """
        Delegate to ``run_and_post_review``.

        Args:
            event (PullRequestEvent): The event that triggered the review.
            prompt (str): The extracted user prompt.
            config (Config): Application configuration.
            platform (Platform): Platform client with reviewer capabilities.
            conversation_store (ConversationStore | None): Conversation store for
                conversation continuity.
            namespace (str): Logical namespace for conversation key isolation.
            context (str): Pre-review context to include in the user message.

        Returns:
            ReviewResult: The review result with findings and summary.
        """

        from nominal_code.review.reviewer import run_and_post_review

        return await run_and_post_review(
            event=event,
            prompt=prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
            namespace=namespace,
            context=context,
        )
