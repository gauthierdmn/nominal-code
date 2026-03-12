from __future__ import annotations

import logging

from environs import Env

from nominal_code.platforms.base import PlatformAuth

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)


class GitLabPatAuth(PlatformAuth):
    """
    Personal Access Token authentication for GitLab.

    Stores static tokens that never expire or refresh.

    Attributes:
        _token (str): The primary GitLab PAT.
        _reviewer_token (str): Optional read-only token for reviewer clones.
    """

    def __init__(self, token: str, reviewer_token: str = "") -> None:
        """
        Initialize PAT-based authentication.

        Args:
            token (str): The primary GitLab personal access token.
            reviewer_token (str): Optional read-only token for reviewer clones.
        """

        self._token: str = token
        self._reviewer_token: str = reviewer_token

    def get_api_token(self, account_id: int = 0) -> str:
        """
        Return the static PAT token.

        Args:
            account_id (int): Ignored for PAT auth.

        Returns:
            str: The configured personal access token.
        """

        return self._token

    def get_clone_token(self, account_id: int = 0) -> str:
        """
        Return the reviewer token, falling back to the main PAT.

        Args:
            account_id (int): Ignored for PAT auth.

        Returns:
            str: The reviewer token or main token.
        """

        return self._reviewer_token or self._token

    async def ensure_auth(self, account_id: int = 0) -> None:
        """
        No-op for PAT authentication.

        Args:
            account_id (int): Ignored for PAT auth.
        """
