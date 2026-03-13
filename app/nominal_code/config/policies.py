from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from nominal_code.models import EventType


class FilteringPolicy(BaseModel):
    """
    Controls which webhook events are processed.

    Governs repository filtering, user authorization, and PR title tag
    matching. Applied before any dispatch decision is made.

    Attributes:
        allowed_users (frozenset[str]): Usernames permitted to trigger the
            bots via @mentions.
        allowed_repos (frozenset[str]): Repository full names to process.
            Empty means all repositories are allowed.
        pr_title_include_tags (frozenset[str]): Allowlist of ``[tag]``
            patterns in PR titles. Empty means no include filter.
        pr_title_exclude_tags (frozenset[str]): Blocklist of ``[tag]``
            patterns in PR titles. Takes priority over include tags.
    """

    model_config = ConfigDict(frozen=True)

    allowed_users: frozenset[str] = frozenset()
    allowed_repos: frozenset[str] = frozenset()
    pr_title_include_tags: frozenset[str] = frozenset()
    pr_title_exclude_tags: frozenset[str] = frozenset()


class RoutingPolicy(BaseModel):
    """
    Controls how webhook events are dispatched to bots.

    Determines which lifecycle events auto-trigger the reviewer and
    which bot usernames are used for @mention matching.

    Attributes:
        reviewer_triggers (frozenset[EventType]): PR lifecycle event types
            that auto-trigger the reviewer bot.
        worker_bot_username (str): The @mention name for the worker bot.
        reviewer_bot_username (str): The @mention name for the reviewer bot.
    """

    model_config = ConfigDict(frozen=True)

    reviewer_triggers: frozenset[EventType] = frozenset()
    worker_bot_username: str = ""
    reviewer_bot_username: str = ""
