from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class DispatchStatus(Enum):
    """
    Semantic status for webhook dispatch results.

    Attributes:
        ACCEPTED: The event was accepted and a job was enqueued.
        IGNORED: The event was silently skipped (wrong type, missing config).
        NO_MENTION: No bot mention was found in the comment body.
        UNAUTHORIZED: The comment author is not authorized.
    """

    ACCEPTED = "accepted"
    IGNORED = "ignored"
    NO_MENTION = "no_mention"
    UNAUTHORIZED = "unauthorized"


@dataclass(frozen=True)
class DispatchResult:
    """
    Framework-agnostic result from webhook dispatch functions.

    Replaces direct ``web.Response`` returns so that dispatch logic is
    decoupled from any specific HTTP framework.

    Attributes:
        status (DispatchStatus): Semantic status of the dispatch.
        http_status (int): Suggested HTTP status code for the caller
            to use when building the actual response.
    """

    status: DispatchStatus
    http_status: int = 200
