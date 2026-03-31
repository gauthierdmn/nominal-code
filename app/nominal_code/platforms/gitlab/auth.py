from __future__ import annotations

import logging

from nominal_code.platforms.base import PlatformAuth

logger: logging.Logger = logging.getLogger(__name__)


class GitLabPatAuth(PlatformAuth):
    """
    Personal Access Token authentication for GitLab.

    Stores static tokens that never expire or refresh.

    Attributes:
        _token (str): The primary GitLab PAT.
    """

    def __init__(self, token: str) -> None:
        """
        Initialize PAT-based authentication.

        Args:
            token (str): The primary GitLab personal access token.
        """

        self._token: str = token

    def get_api_token(self, account_id: int = 0) -> str:
        """
        Return the static PAT token.

        Args:
            account_id (int): Ignored for PAT auth.

        Returns:
            str: The configured personal access token.
        """

        return self._token

    async def ensure_auth(self, account_id: int = 0) -> None:
        """
        No-op for PAT authentication.

        Args:
            account_id (int): Ignored for PAT auth.
        """
