# type: ignore
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.platforms.github import (
    NO_INSTALLATION,
    CachedToken,
    GitHubAppAuth,
    GitHubPatAuth,
)


class TestGitHubPatAuth:
    def test_get_api_token_returns_stored_token(self):
        auth = GitHubPatAuth(token="ghp_abc123")

        assert auth.get_api_token() == "ghp_abc123"

    def test_get_api_token_ignores_account_id(self):
        auth = GitHubPatAuth(token="ghp_abc123")

        assert auth.get_api_token(12345) == "ghp_abc123"

    @pytest.mark.asyncio
    async def test_ensure_auth_is_noop(self):
        auth = GitHubPatAuth(token="ghp_abc123")

        await auth.ensure_auth(12345)

        assert auth.get_api_token() == "ghp_abc123"


class TestGitHubAppAuth:
    def test_get_api_token_raises_before_refresh(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")

        with pytest.raises(RuntimeError, match="not yet available"):
            auth.get_api_token(100)

    @pytest.mark.asyncio
    async def test_ensure_auth_raises_when_account_id_zero(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")

        with pytest.raises(RuntimeError, match="non-zero account_id"):
            await auth.ensure_auth(NO_INSTALLATION)

    @pytest.mark.asyncio
    async def test_ensure_auth_refreshes_for_nonzero_account(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"token": "ghs_new_token"}

        jwt_patch = patch.object(
            auth,
            "_generate_jwt",
            return_value="fake-jwt",
        )
        client_patch = patch(
            "nominal_code.platforms.github.auth.httpx.AsyncClient",
        )

        with jwt_patch, client_patch as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await auth.ensure_auth(100)

        assert auth.get_api_token(100) == "ghs_new_token"

    @pytest.mark.asyncio
    async def test_refresh_token_fetches_token(self):
        auth = GitHubAppAuth(
            app_id="12345",
            private_key="fake-key",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"token": "ghs_new_token"}

        jwt_patch = patch.object(
            auth,
            "_generate_jwt",
            return_value="fake-jwt",
        )
        client_patch = patch(
            "nominal_code.platforms.github.auth.httpx.AsyncClient",
        )

        with jwt_patch, client_patch as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await auth._refresh_token(100)

        assert auth.get_api_token(100) == "ghs_new_token"

    @pytest.mark.asyncio
    async def test_two_installations_get_independent_tokens(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")

        mock_response_a = MagicMock()
        mock_response_a.raise_for_status = MagicMock()
        mock_response_a.json.return_value = {"token": "ghs_token_a"}

        mock_response_b = MagicMock()
        mock_response_b.raise_for_status = MagicMock()
        mock_response_b.json.return_value = {"token": "ghs_token_b"}

        jwt_patch = patch.object(
            auth,
            "_generate_jwt",
            return_value="fake-jwt",
        )
        client_patch = patch(
            "nominal_code.platforms.github.auth.httpx.AsyncClient",
        )

        with jwt_patch, client_patch as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(side_effect=[mock_response_a, mock_response_b])
            mock_client_cls.return_value = mock_client

            await auth._refresh_token(100)
            await auth._refresh_token(200)

        assert auth.get_api_token(100) == "ghs_token_a"
        assert auth.get_api_token(200) == "ghs_token_b"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_api_call(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        auth._token_cache[100] = CachedToken(
            token="ghs_cached",
            expires_at=time.monotonic() + 3600,
        )

        with patch.object(auth, "_generate_jwt") as mock_jwt:
            await auth._refresh_token(100)

            mock_jwt.assert_not_called()

        assert auth.get_api_token(100) == "ghs_cached"

    @pytest.mark.asyncio
    async def test_cache_expiry_triggers_refresh(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        auth._token_cache[100] = CachedToken(
            token="ghs_old",
            expires_at=time.monotonic() + 100,
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {"token": "ghs_refreshed"}

        jwt_patch = patch.object(
            auth,
            "_generate_jwt",
            return_value="fake-jwt",
        )
        client_patch = patch(
            "nominal_code.platforms.github.auth.httpx.AsyncClient",
        )

        with jwt_patch, client_patch as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client

            await auth._refresh_token(100)

        assert auth.get_api_token(100) == "ghs_refreshed"

    def test_prune_expired_entries_removes_expired(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        auth._token_cache[100] = CachedToken(
            token="ghs_expired",
            expires_at=time.monotonic() - 10,
        )
        auth._token_cache[200] = CachedToken(
            token="ghs_valid",
            expires_at=time.monotonic() + 3600,
        )

        auth._prune_expired_entries()

        assert 100 not in auth._token_cache
        assert 200 in auth._token_cache


class TestGitHubPatAuthInit:
    def test_pat_auth_init_stores_token(self):
        auth = GitHubPatAuth(token="ghp_mytoken")

        assert auth.token == "ghp_mytoken"


class TestGitHubAppAuthInit:
    def test_app_auth_init_stores_app_id(self):
        auth = GitHubAppAuth(app_id="99999", private_key="pem-key")

        assert auth.app_id == "99999"

    def test_app_auth_init_stores_private_key(self):
        auth = GitHubAppAuth(app_id="12345", private_key="my-pem")

        assert auth.private_key == "my-pem"

    def test_app_auth_init_token_cache_is_empty(self):
        auth = GitHubAppAuth(app_id="12345", private_key="pem")

        assert auth._token_cache == {}
