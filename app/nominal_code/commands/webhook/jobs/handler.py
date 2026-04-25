from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from nominal_code.review.reviewer import ReviewScope, run_and_post_review

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
        scope: ReviewScope = ReviewScope.PR,
        workspace_path: str | None = None,
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
            scope (ReviewScope): Whether this is a PR diff review or a
                whole-repository codebase review.
            workspace_path (str): Pre-existing workspace path. Required when
                ``scope`` is ``ReviewScope.CODEBASE``.

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
        scope: ReviewScope = ReviewScope.PR,
        workspace_path: str | None = None,
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
            scope (ReviewScope): Whether this is a PR diff review or a
                whole-repository codebase review.
            workspace_path (str): Pre-existing workspace path. Required when
                ``scope`` is ``ReviewScope.CODEBASE``.

        Returns:
            ReviewResult: The review result with findings and summary.
        """

        return await run_and_post_review(
            event=event,
            prompt=prompt,
            config=config,
            platform=platform,
            conversation_store=conversation_store,
            namespace=namespace,
            context=context,
            scope=scope,
            workspace_path=workspace_path,
        )
