from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
import jwt
from environs import Env

logger: logging.Logger = logging.getLogger(__name__)

JWT_EXPIRY_SECONDS: int = 600
TOKEN_REFRESH_MARGIN_SECONDS: int = 300


class GitHubAuth(ABC):
    """
    Abstract base for GitHub authentication strategies.

    Subclasses provide either static PAT tokens or dynamic App installation
    tokens. The platform delegates all token access through this interface.
    """

    @abstractmethod
    def get_token(self) -> str:
        """
        Return the current API token.

        Returns:
            str: A valid GitHub API token.
        """

    @abstractmethod
    def get_reviewer_token(self) -> str:
        """
        Return a read-only token for reviewer clone URLs.

        Falls back to the main token when no separate reviewer token exists.

        Returns:
            str: A valid GitHub API token.
        """

    @abstractmethod
    async def refresh_if_needed(self) -> None:
        """
        Refresh the token if it is expired or about to expire.

        No-op for static token strategies.
        """

    @abstractmethod
    def set_installation_id(self, installation_id: int) -> None:
        """
        Set the target GitHub App installation ID.

        No-op for PAT-based authentication.

        Args:
            installation_id (int): The GitHub App installation ID.
        """


class GitHubPatAuth(GitHubAuth):
    """
    Personal Access Token authentication for GitHub.

    Stores static tokens that never expire or refresh.

    Attributes:
        token (str): The primary GitHub PAT.
        reviewer_token (str): Optional read-only token for reviewer clones.
    """

    def __init__(self, token: str, reviewer_token: str = "") -> None:
        """
        Initialize PAT-based authentication.

        Args:
            token (str): The primary GitHub personal access token.
            reviewer_token (str): Optional read-only token for reviewer clones.
        """

        self.token: str = token
        self.reviewer_token: str = reviewer_token

    def get_token(self) -> str:
        """
        Return the static PAT token.

        Returns:
            str: The configured personal access token.
        """

        return self.token

    def get_reviewer_token(self) -> str:
        """
        Return the reviewer token, falling back to the main PAT.

        Returns:
            str: The reviewer token or main token.
        """

        return self.reviewer_token or self.token

    async def refresh_if_needed(self) -> None:
        """
        No-op for PAT authentication.
        """

    def set_installation_id(self, installation_id: int) -> None:
        """
        No-op for PAT authentication.

        Args:
            installation_id (int): Ignored.
        """


class GitHubAppAuth(GitHubAuth):
    """
    GitHub App authentication via JWT and installation access tokens.

    Generates RS256 JWTs to authenticate as the App, then exchanges them
    for short-lived installation access tokens. Tokens are cached and
    refreshed transparently when they approach expiry.

    Attributes:
        app_id (str): The GitHub App's numeric ID.
        private_key (str): The PEM-encoded RSA private key.
        installation_id (int): The target installation ID.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
        installation_id: int = 0,
    ) -> None:
        """
        Initialize GitHub App authentication.

        Args:
            app_id (str): The GitHub App's numeric ID.
            private_key (str): PEM-encoded RSA private key for JWT signing.
            installation_id (int): The target installation ID (can be set later
                via webhook payload).
        """

        self.app_id: str = app_id
        self.private_key: str = private_key
        self.installation_id: int = installation_id
        self._cached_token: str = ""
        self._token_expires_at: float = 0.0

    def get_token(self) -> str:
        """
        Return the cached installation access token.

        Returns:
            str: The current installation access token.

        Raises:
            RuntimeError: If no token has been obtained yet.
        """

        if not self._cached_token:
            raise RuntimeError(
                "GitHub App token not yet available. Call refresh_if_needed() first."
            )

        return self._cached_token

    def get_reviewer_token(self) -> str:
        """
        Return the installation access token for reviewer operations.

        GitHub App permissions handle scoping, so this delegates to get_token().

        Returns:
            str: The current installation access token.
        """

        return self.get_token()

    async def refresh_if_needed(self) -> None:
        """
        Refresh the installation access token if missing or expiring soon.

        Generates a JWT, exchanges it for an installation token via the
        GitHub API, and caches the result with its expiry timestamp.

        Raises:
            RuntimeError: If the installation ID is not set.
        """

        if self._cached_token and not self._is_token_expiring():
            return

        if not self.installation_id:
            raise RuntimeError(
                "GitHub App installation ID is not set. "
                "Set GITHUB_INSTALLATION_ID or wait for a webhook payload."
            )

        jwt_token: str = self._generate_jwt()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response: httpx.Response = await client.post(
                    f"https://api.github.com/app/installations/{self.installation_id}/access_tokens",
                    headers={
                        "Authorization": f"Bearer {jwt_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                response.raise_for_status()

            data: dict[str, str] = response.json()
            self._cached_token = data["token"]
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Failed to fetch GitHub App installation token "
                f"for installation {self.installation_id}"
            ) from exc
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                "GitHub App token response is malformed or missing 'token' field"
            ) from exc

        self._token_expires_at = time.monotonic() + 3600

        logger.info(
            "Refreshed GitHub App token for installation %d",
            self.installation_id,
        )

    def set_installation_id(self, installation_id: int) -> None:
        """
        Set the target installation ID, invalidating the cache if changed.

        Args:
            installation_id (int): The GitHub App installation ID.
        """

        if installation_id == self.installation_id:
            return

        self.installation_id = installation_id
        self._cached_token = ""
        self._token_expires_at = 0.0

        logger.info(
            "Updated GitHub App installation ID to %d",
            installation_id,
        )

    def _generate_jwt(self) -> str:
        """
        Generate an RS256-signed JWT for GitHub App authentication.

        Returns:
            str: The encoded JWT string.
        """

        now: int = int(time.time())
        payload: dict[str, int | str] = {
            "iat": now - 60,
            "exp": now + JWT_EXPIRY_SECONDS,
            "iss": self.app_id,
        }

        return jwt.encode(payload, self.private_key, algorithm="RS256")

    def _is_token_expiring(self) -> bool:
        """
        Check whether the cached token is expiring within the refresh margin.

        Returns:
            bool: True if the token needs refreshing.
        """

        return time.monotonic() >= self._token_expires_at - TOKEN_REFRESH_MARGIN_SECONDS


def load_private_key() -> str:
    """
    Load the GitHub App private key from environment variables.

    Checks ``GITHUB_APP_PRIVATE_KEY`` for an inline PEM string first,
    then ``GITHUB_APP_PRIVATE_KEY_PATH`` for a file path.

    Returns:
        str: The PEM private key contents, or empty string if not configured.
    """

    _env: Env = Env()
    inline_key: str = _env.str("GITHUB_APP_PRIVATE_KEY", "")

    if inline_key:
        return inline_key

    key_path: str = _env.str("GITHUB_APP_PRIVATE_KEY_PATH", "")

    if key_path:
        try:
            return Path(key_path).read_text()
        except OSError:
            logger.exception(
                "Failed to read GitHub App private key from %s",
                key_path,
            )

            return ""

    return ""
