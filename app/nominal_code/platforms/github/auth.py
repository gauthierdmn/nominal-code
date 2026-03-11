from __future__ import annotations

import logging
import time
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import httpx
import jwt
from environs import Env

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)

JWT_EXPIRY_SECONDS: int = 600
TOKEN_REFRESH_MARGIN_SECONDS: int = 300


@dataclass(frozen=True)
class CachedToken:
    """
    An installation access token with its monotonic expiry timestamp.

    Attributes:
        token (str): The GitHub installation access token.
        expires_at (float): Monotonic clock timestamp when the token expires.
    """

    token: str
    expires_at: float


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
    def get_token_for_installation(self, installation_id: int) -> str:
        """
        Return the cached token for a specific installation.

        Args:
            installation_id (int): The GitHub App installation ID.

        Returns:
            str: A valid GitHub API token.

        Raises:
            RuntimeError: If no token is cached for the installation.
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
    async def refresh_token_for_installation(self, installation_id: int) -> None:
        """
        Ensure a valid token is cached for a specific installation.

        Checks the per-installation cache and refreshes only if the token
        is missing or about to expire. Also prunes expired entries from
        the cache.

        Args:
            installation_id (int): The GitHub App installation ID.
        """

    @abstractmethod
    def set_installation_id(self, installation_id: int) -> None:
        """
        Set the target GitHub App installation ID.

        .. deprecated::
            Use :meth:`refresh_token_for_installation` and
            :meth:`get_token_for_installation` instead. This method updates
            the default installation ID for backward compatibility with CLI
            and CI modes.

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

    def get_token_for_installation(self, installation_id: int) -> str:
        """
        Return the static PAT token, ignoring the installation ID.

        Args:
            installation_id (int): Ignored for PAT auth.

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

    async def refresh_token_for_installation(self, installation_id: int) -> None:
        """
        No-op for PAT authentication.

        Args:
            installation_id (int): Ignored.
        """

    def set_installation_id(self, installation_id: int) -> None:
        """
        No-op for PAT authentication.

        .. deprecated::
            Use :meth:`refresh_token_for_installation` instead.

        Args:
            installation_id (int): Ignored.
        """


class GitHubAppAuth(GitHubAuth):
    """
    GitHub App authentication via JWT and installation access tokens.

    Generates RS256 JWTs to authenticate as the App, then exchanges them
    for short-lived installation access tokens. Tokens are cached per
    installation and refreshed transparently when they approach expiry.

    Attributes:
        app_id (str): The GitHub App's numeric ID.
        private_key (str): The PEM-encoded RSA private key.
        _default_installation_id (int): The default installation ID for
            CLI/CI backward compatibility.
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
            installation_id (int): The default installation ID (can be set
                later via webhook payload or environment variable).
        """

        self.app_id: str = app_id
        self.private_key: str = private_key
        self._default_installation_id: int = installation_id
        self._token_cache: dict[int, CachedToken] = {}

    @property
    def installation_id(self) -> int:
        """
        The default installation ID for backward compatibility.

        Returns:
            int: The default installation ID.
        """

        return self._default_installation_id

    @installation_id.setter
    def installation_id(self, value: int) -> None:
        """
        Set the default installation ID.

        Args:
            value (int): The new default installation ID.
        """

        self._default_installation_id = value

    def get_token(self) -> str:
        """
        Return the cached token for the default installation.

        Returns:
            str: The current installation access token.

        Raises:
            RuntimeError: If no token has been obtained yet.
        """

        return self.get_token_for_installation(self._default_installation_id)

    def get_token_for_installation(self, installation_id: int) -> str:
        """
        Return the cached token for a specific installation.

        Args:
            installation_id (int): The GitHub App installation ID.

        Returns:
            str: The cached installation access token.

        Raises:
            RuntimeError: If no token is cached for the installation.
        """

        cached: CachedToken | None = self._token_cache.get(installation_id)

        if cached is None:
            raise RuntimeError(
                "GitHub App token not yet available. "
                "Call refresh_token_for_installation() first."
            )

        return cached.token

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
        Refresh the default installation's token if missing or expiring soon.

        Delegates to :meth:`refresh_token_for_installation` using the
        default installation ID.

        Raises:
            RuntimeError: If the default installation ID is not set.
        """

        await self.refresh_token_for_installation(self._default_installation_id)

    async def refresh_token_for_installation(self, installation_id: int) -> None:
        """
        Ensure a valid token is cached for a specific installation.

        Checks the per-installation cache and refreshes only if the token
        is missing or about to expire. Also prunes expired entries from
        the cache.

        Args:
            installation_id (int): The GitHub App installation ID.

        Raises:
            RuntimeError: If the installation ID is zero (not set).
        """

        self._prune_expired_entries()

        cached: CachedToken | None = self._token_cache.get(installation_id)

        if cached is not None and not self._is_entry_expiring(cached):
            return

        if not installation_id:
            raise RuntimeError(
                "GitHub App installation ID is not set. "
                "Set GITHUB_INSTALLATION_ID or wait for a webhook payload."
            )

        jwt_token: str = self._generate_jwt()

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response: httpx.Response = await client.post(
                    f"https://api.github.com/app/installations/{installation_id}/access_tokens",
                    headers={
                        "Authorization": f"Bearer {jwt_token}",
                        "Accept": "application/vnd.github.v3+json",
                    },
                )
                response.raise_for_status()

            data: dict[str, str] = response.json()
            token: str = data["token"]
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"Failed to fetch GitHub App installation token "
                f"for installation {installation_id}"
            ) from exc
        except (KeyError, ValueError) as exc:
            raise RuntimeError(
                "GitHub App token response is malformed or missing 'token' field"
            ) from exc

        expires_at: float = time.monotonic() + 3600

        self._token_cache[installation_id] = CachedToken(
            token=token,
            expires_at=expires_at,
        )

        logger.info(
            "Refreshed GitHub App token for installation %d",
            installation_id,
        )

    def set_installation_id(self, installation_id: int) -> None:
        """
        Set the default installation ID.

        .. deprecated::
            Use :meth:`refresh_token_for_installation` and
            :meth:`get_token_for_installation` instead.

        Args:
            installation_id (int): The GitHub App installation ID.
        """

        warnings.warn(
            "set_installation_id() is deprecated. "
            "Use refresh_token_for_installation() and "
            "get_token_for_installation() instead.",
            DeprecationWarning,
            stacklevel=2,
        )

        self._default_installation_id = installation_id

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

    def _is_entry_expiring(self, entry: CachedToken) -> bool:
        """
        Check whether a cached token is expiring within the refresh margin.

        Args:
            entry (CachedToken): The cached token entry to check.

        Returns:
            bool: True if the token needs refreshing.
        """

        return time.monotonic() >= entry.expires_at - TOKEN_REFRESH_MARGIN_SECONDS

    def _is_token_expiring(self) -> bool:
        """
        Check whether the default installation's token is expiring.

        Returns:
            bool: True if the token needs refreshing.
        """

        cached: CachedToken | None = self._token_cache.get(
            self._default_installation_id,
        )

        if cached is None:
            return True

        return self._is_entry_expiring(cached)

    def _prune_expired_entries(self) -> None:
        """
        Remove fully expired entries from the token cache.
        """

        now: float = time.monotonic()
        expired_keys: list[int] = [
            key for key, entry in self._token_cache.items() if now >= entry.expires_at
        ]

        for key in expired_keys:
            del self._token_cache[key]


def load_private_key() -> str:
    """
    Load the GitHub App private key from environment variables.

    Checks ``GITHUB_APP_PRIVATE_KEY`` for an inline PEM string first,
    then ``GITHUB_APP_PRIVATE_KEY_PATH`` for a file path.

    Returns:
        str: The PEM private key contents, or empty string if not configured.
    """

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
