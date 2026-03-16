from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchResult:
    """
    Framework-agnostic result from webhook dispatch functions.

    Replaces direct ``web.Response`` returns so that dispatch logic is
    decoupled from any specific HTTP framework.

    Attributes:
        status (str): Semantic status string (e.g. ``"accepted"``,
            ``"ignored"``, ``"no_mention"``, ``"unauthorized"``).
        http_status (int): Suggested HTTP status code for the caller
            to use when building the actual response.
    """

    status: str
    http_status: int = 200
