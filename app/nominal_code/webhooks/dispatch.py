from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from nominal_code.models import BotType
from nominal_code.platforms.base import CommentEvent, LifecycleEvent

if TYPE_CHECKING:
    from nominal_code.agent.session import SessionQueue
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform

EYES_REACTION: str = "eyes"

logger: logging.Logger = logging.getLogger(__name__)


async def enqueue_job(
    event: CommentEvent | LifecycleEvent,
    bot_type: BotType,
    config: Config,
    platform: Platform,
    session_queue: SessionQueue,
    job: Callable[[], Awaitable[None]],
) -> None:
    """
    Pre-flight checks and enqueue a caller-provided job closure.

    For comment events: validates the author against allowed users, logs
    the event, and posts an eyes reaction.
    For lifecycle events: logs with event type/title/author and skips auth and reaction.

    Args:
        event (CommentEvent | LifecycleEvent): The parsed event.
        bot_type (BotType): Which bot personality to use.
        config (Config): Application configuration.
        platform (Platform): The platform client for API calls.
        session_queue (SessionQueue): Per-PR job queue.
        job (Callable[[], Awaitable[None]]): The async job to enqueue.
    """

    if isinstance(event, CommentEvent):
        if event.author_username not in config.allowed_users:
            logger.warning(
                "Ignoring comment from unauthorized user: %s",
                event.author_username,
            )

            return

        logger.info(
            "Processing %s comment from %s on %s#%d: %s",
            bot_type.value,
            event.author_username,
            event.repo_full_name,
            event.pr_number,
            event.body[:100],
        )

        await platform.post_reaction(event, EYES_REACTION)
    else:
        logger.info(
            "Auto-trigger %s reviewer on %s#%d (title=%s, author=%s)",
            event.event_type,
            event.repo_full_name,
            event.pr_number,
            event.pr_title[:80],
            event.pr_author,
        )

    await session_queue.enqueue(
        event.platform,
        event.repo_full_name,
        event.pr_number,
        bot_type.value,
        job,
    )
