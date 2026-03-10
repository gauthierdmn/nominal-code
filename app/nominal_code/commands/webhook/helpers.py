from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from nominal_code.models import BotType
from nominal_code.platforms.base import CommentEvent, LifecycleEvent

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform

EYES_REACTION: str = "eyes"

logger: logging.Logger = logging.getLogger(__name__)


def extract_mention(text: str, bot_username: str) -> str | None:
    """
    Detect an @mention of the bot and extract the prompt that follows it.

    The match is case-insensitive and works with or without a leading ``@``.
    Returns None if the bot is not mentioned.

    Args:
        text (str): The comment body to search.
        bot_username (str): The bot's username (without ``@`` prefix).

    Returns:
        str | None: The extracted prompt after the mention, or None if
            the bot was not mentioned.
    """

    pattern: str = rf"@{re.escape(bot_username)}\b"
    match_: re.Match[str] | None = re.search(
        pattern=pattern,
        string=text,
        flags=re.IGNORECASE,
    )

    if match_ is None:
        return None

    prompt: str = text[match_.end() :].strip()

    return prompt if prompt else None


async def acknowledge_event(
    event: CommentEvent | LifecycleEvent,
    bot_type: BotType,
    config: Config,
    platform: Platform,
) -> bool:
    """
    Authorize and acknowledge an event before dispatching a job.

    For comment events: validates the author against allowed users, logs
    the event, posts an eyes reaction on the comment and PR.
    For lifecycle events: logs with event type/title/author, posts a PR
    reaction, and skips auth and comment reaction.

    Args:
        event (CommentEvent | LifecycleEvent): The parsed event.
        bot_type (BotType): Which bot personality to use.
        config (Config): Application configuration.
        platform (Platform): The platform client for API calls.

    Returns:
        bool: True if the job should proceed, False if it should be skipped.
    """

    if isinstance(event, CommentEvent):
        if event.author_username not in config.allowed_users:
            logger.warning(
                "Ignoring comment from unauthorized user: %s",
                event.author_username,
            )

            return False

        logger.info(
            "Processing %s comment from %s on %s#%d: %s",
            bot_type.value,
            event.author_username,
            event.repo_full_name,
            event.pr_number,
            event.body[:100],
        )

        await platform.ensure_auth()
        await platform.post_reaction(event=event, reaction=EYES_REACTION)

    else:
        logger.info(
            "Auto-trigger %s reviewer on %s#%d (title=%s, author=%s)",
            event.event_type,
            event.repo_full_name,
            event.pr_number,
            event.pr_title[:80],
            event.pr_author,
        )

        await platform.ensure_auth()

    await platform.post_pr_reaction(
        repo_full_name=event.repo_full_name,
        pr_number=event.pr_number,
        reaction=EYES_REACTION,
    )

    return True
