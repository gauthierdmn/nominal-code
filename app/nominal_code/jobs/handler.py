from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.conversation.base import ConversationStore
    from nominal_code.handlers.review import ReviewResult
    from nominal_code.platforms.base import (
        CommentEvent,
        Platform,
        PullRequestEvent,
        ReviewerPlatform,
    )


@runtime_checkable
class JobHandler(Protocol):
    """
    Protocol for handling review and worker job execution.

    Implementations control how review and worker jobs are processed.
    The default implementation delegates to the standard handler
    functions. Custom implementations can add preprocessing, context
    enrichment, or alternative execution strategies.
    """

    async def handle_review(
        self,
        event: PullRequestEvent,
        prompt: str,
        config: Config,
        platform: ReviewerPlatform,
        conversation_store: ConversationStore | None = None,
        namespace: str = "",
    ) -> ReviewResult:
        """
        Execute a code review and post results.

        Args:
            event (PullRequestEvent): The event that triggered the review.
            prompt (str): The extracted user prompt.
            config (Config): Application configuration.
            platform (ReviewerPlatform): Platform client with reviewer capabilities.
            conversation_store (ConversationStore | None): Conversation store for
                conversation continuity.
            namespace (str): Logical namespace for conversation key isolation.

        Returns:
            ReviewResult: The review result with findings and summary.
        """

        ...

    async def handle_worker(
        self,
        event: CommentEvent,
        prompt: str,
        config: Config,
        platform: Platform,
        conversation_store: ConversationStore | None = None,
        namespace: str = "",
    ) -> None:
        """
        Execute a worker job to review and fix code.

        Args:
            event (CommentEvent): The comment event that triggered the worker.
            prompt (str): The extracted user prompt.
            config (Config): Application configuration.
            platform (Platform): The platform client.
            conversation_store (ConversationStore | None): Conversation store for
                conversation continuity.
            namespace (str): Logical namespace for conversation key isolation.
        """

        ...


class DefaultJobHandler:
    """
    Default job handler that delegates to the existing handler functions.

    Wraps ``run_and_post_review`` and ``review_and_fix`` to preserve the
    current behavior when no custom handler is provided.
    """

    async def handle_review(
        self,
        event: PullRequestEvent,
        prompt: str,
        config: Config,
        platform: ReviewerPlatform,
        conversation_store: ConversationStore | None = None,
        namespace: str = "",
    ) -> ReviewResult:
        """
        Delegate to ``run_and_post_review``.

        Args:
            event (PullRequestEvent): The event that triggered the review.
            prompt (str): The extracted user prompt.
            config (Config): Application configuration.
            platform (ReviewerPlatform): Platform client with reviewer capabilities.
            conversation_store (ConversationStore | None): Conversation store for
                conversation continuity.
            namespace (str): Logical namespace for conversation key isolation.

        Returns:
            ReviewResult: The review result with findings and summary.
        """

        from nominal_code.handlers.review import run_and_post_review

        return await run_and_post_review(
            event=event,
            prompt=prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
            namespace=namespace,
        )

    async def handle_worker(
        self,
        event: CommentEvent,
        prompt: str,
        config: Config,
        platform: Platform,
        conversation_store: ConversationStore | None = None,
        namespace: str = "",
    ) -> None:
        """
        Delegate to ``review_and_fix``.

        Args:
            event (CommentEvent): The comment event that triggered the worker.
            prompt (str): The extracted user prompt.
            config (Config): Application configuration.
            platform (Platform): The platform client.
            conversation_store (ConversationStore | None): Conversation store for
                conversation continuity.
            namespace (str): Logical namespace for conversation key isolation.
        """

        from nominal_code.handlers.worker import review_and_fix

        await review_and_fix(
            event=event,
            prompt=prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
            namespace=namespace,
        )
