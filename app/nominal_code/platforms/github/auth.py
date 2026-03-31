from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx
import jwt

from nominal_code.platforms.base import PlatformAuth

logger: logging.Logger = logging.getLogger(__name__)

JWT_EXPIRY_SECONDS: int = 600
TOKEN_REFRESH_MARGIN_SECONDS: int = 300
NO_INSTALLATION: int = 0


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


class GitHubPatAuth(PlatformAuth):
    """
    Personal Access Token authentication for GitHub.

    Stores static tokens that never expire or refresh.

    Attributes:
        token (str): The primary GitHub PAT.
    """

    def __init__(self, token: str) -> None:
        """
        Initialize PAT-based authentication.

        Args:
            token (str): The primary GitHub personal access token.
        """

        self.token: str = token

    def get_api_token(self, account_id: int = 0) -> str:
        """
        Return the static PAT token, ignoring the account ID.

        Args:
            account_id (int): Ignored for PAT auth.

        Returns:
            str: The configured personal access token.
        """

        return self.token

    async def ensure_auth(self, account_id: int = 0) -> None:
        """
        No-op for PAT authentication.

        Args:
            account_id (int): Ignored for PAT auth.
        """


class GitHubAppAuth(PlatformAuth):
    """
    GitHub App authentication via JWT and installation access tokens.

    Generates RS256 JWTs to authenticate as the App, then exchanges them
    for short-lived installation access tokens. Tokens are cached per
    installation and refreshed transparently when they approach expiry.

    This class is a pure per-installation token cache. The platform layer
    owns mode selection (webhook vs CLI/CI) and resolves which installation
    ID to use.

    Attributes:
        app_id (str): The GitHub App's numeric ID.
        private_key (str): The PEM-encoded RSA private key.
    """

    def __init__(
        self,
        app_id: str,
        private_key: str,
    ) -> None:
        """
        Initialize GitHub App authentication.

        Args:
            app_id (str): The GitHub App's numeric ID.
            private_key (str): PEM-encoded RSA private key for JWT signing.
        """

        self.app_id: str = app_id
        self.private_key: str = private_key
        self._token_cache: dict[int, CachedToken] = {}

    def get_api_token(self, account_id: int = 0) -> str:
        """
        Return the cached token for a specific installation.

        Args:
            account_id (int): The GitHub App installation ID.

        Returns:
            str: The cached installation access token.

        Raises:
            RuntimeError: If no token is cached for the installation.
        """

        cached: CachedToken | None = self._token_cache.get(account_id)

        if cached is None:
            raise RuntimeError(
                "GitHub App token not yet available. Call ensure_auth() first."
            )

        return cached.token

    async def ensure_auth(self, account_id: int = 0) -> None:
        """
        Ensure a valid token for the given account.

        Args:
            account_id (int): The GitHub App installation ID.

        Raises:
            RuntimeError: If ``account_id`` is ``NO_INSTALLATION``.
        """

        if account_id == NO_INSTALLATION:
            raise RuntimeError(
                "GitHubAppAuth.ensure_auth() requires a non-zero account_id. "
                "The platform must resolve an installation ID before calling this."
            )

        await self._refresh_token(account_id)

    async def _refresh_token(self, installation_id: int) -> None:
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
