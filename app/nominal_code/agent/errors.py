from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from nominal_code.platforms.base import CommentReply, PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.platforms.base import Platform

logger: logging.Logger = logging.getLogger(__name__)


@asynccontextmanager
async def handle_agent_errors(
    event: PullRequestEvent,
    platform: Platform,
    agent_label: str,
) -> AsyncIterator[None]:
    """
    Context manager that catches workspace and agent errors and posts replies.

    Catches ``RuntimeError`` (workspace setup failures) and generic
    ``Exception`` (agent runtime errors), logs them, and posts a user-facing
    error message to the platform.

    Args:
        event (PullRequestEvent): The event to reply to on error.
        platform (Platform): The platform client for posting replies.
        agent_label (str): Label for log messages (e.g. ``worker``, ``reviewer``).

    Yields:
        None: Control to the caller's body block.
    """

    try:
        yield
    except RuntimeError:
        logger.exception("Failed to set up workspace")

        try:
            await platform.post_reply(
                event,
                CommentReply(body="Failed to set up the git workspace."),
            )
        except Exception:
            logger.exception("Failed to post workspace error reply")

    except Exception:
        logger.exception("Error running agent (%s)", agent_label)

        try:
            await platform.post_reply(
                event,
                CommentReply(
                    body="An unexpected error occurred while running the agent.",
                ),
            )
        except Exception:
            logger.exception("Failed to post agent error reply")
