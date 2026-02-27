# type: ignore
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.platforms.github import (
    GitHubAppAuth,
    GitHubPatAuth,
    load_private_key,
)


class TestGitHubPatAuth:
    def test_get_token_returns_stored_token(self):
        auth = GitHubPatAuth(token="ghp_abc123")

        assert auth.get_token() == "ghp_abc123"

    def test_get_reviewer_token_returns_reviewer_when_set(self):
        auth = GitHubPatAuth(token="ghp_main", reviewer_token="ghp_readonly")

        assert auth.get_reviewer_token() == "ghp_readonly"

    def test_get_reviewer_token_falls_back_to_main(self):
        auth = GitHubPatAuth(token="ghp_main")

        assert auth.get_reviewer_token() == "ghp_main"

    @pytest.mark.asyncio
    async def test_refresh_if_needed_is_noop(self):
        auth = GitHubPatAuth(token="ghp_abc123")

        await auth.refresh_if_needed()

        assert auth.get_token() == "ghp_abc123"

    def test_set_installation_id_is_noop(self):
        auth = GitHubPatAuth(token="ghp_abc123")
        auth.set_installation_id(12345)

        assert auth.get_token() == "ghp_abc123"


class TestGitHubAppAuth:
    def test_get_token_raises_before_refresh(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")

        with pytest.raises(RuntimeError, match="not yet available"):
            auth.get_token()

    def test_get_reviewer_token_delegates_to_get_token(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        auth._cached_token = "ghs_install_token"

        assert auth.get_reviewer_token() == "ghs_install_token"

    def test_set_installation_id_invalidates_cache(self):
        auth = GitHubAppAuth(
            app_id="12345",
            private_key="fake-key",
            installation_id=100,
        )
        auth._cached_token = "ghs_old_token"
        auth._token_expires_at = time.monotonic() + 3600

        auth.set_installation_id(200)

        assert auth.installation_id == 200
        assert auth._cached_token == ""

    def test_set_installation_id_same_value_preserves_cache(self):
        auth = GitHubAppAuth(
            app_id="12345",
            private_key="fake-key",
            installation_id=100,
        )
        auth._cached_token = "ghs_token"

        auth.set_installation_id(100)

        assert auth._cached_token == "ghs_token"

    @pytest.mark.asyncio
    async def test_refresh_if_needed_raises_without_installation_id(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")

        with pytest.raises(RuntimeError, match="installation ID is not set"):
            await auth.refresh_if_needed()

    @pytest.mark.asyncio
    async def test_refresh_if_needed_fetches_token(self):
        auth = GitHubAppAuth(
            app_id="12345",
            private_key="fake-key",
            installation_id=100,
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

            await auth.refresh_if_needed()

        assert auth.get_token() == "ghs_new_token"

    @pytest.mark.asyncio
    async def test_refresh_if_needed_skips_when_token_valid(self):
        auth = GitHubAppAuth(
            app_id="12345",
            private_key="fake-key",
            installation_id=100,
        )
        auth._cached_token = "ghs_valid"
        auth._token_expires_at = time.monotonic() + 3600

        with patch.object(auth, "_generate_jwt") as mock_jwt:
            await auth.refresh_if_needed()

            mock_jwt.assert_not_called()

        assert auth.get_token() == "ghs_valid"

    def test_is_token_expiring_true_when_within_margin(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        auth._token_expires_at = time.monotonic() + 100

        assert auth._is_token_expiring() is True

    def test_is_token_expiring_false_when_fresh(self):
        auth = GitHubAppAuth(app_id="12345", private_key="fake-key")
        auth._token_expires_at = time.monotonic() + 3600

        assert auth._is_token_expiring() is False


class TestLoadPrivateKey:
    def test_load_private_key_inline(self):
        pem_key = "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"
        env = {"GITHUB_APP_PRIVATE_KEY": pem_key}

        with patch.dict("os.environ", env, clear=True):
            result = load_private_key()

        assert "BEGIN RSA PRIVATE KEY" in result

    def test_load_private_key_from_file(self, tmp_path):
        key_file = tmp_path / "private-key.pem"
        pem_content = (
            "-----BEGIN RSA PRIVATE KEY-----\nfile-key\n-----END RSA PRIVATE KEY-----"
        )
        key_file.write_text(pem_content)
        env = {"GITHUB_APP_PRIVATE_KEY_PATH": str(key_file)}

        with patch.dict("os.environ", env, clear=True):
            result = load_private_key()

        assert "file-key" in result

    def test_load_private_key_prefers_inline_over_file(self, tmp_path):
        key_file = tmp_path / "private-key.pem"
        key_file.write_text("file-content")
        env = {
            "GITHUB_APP_PRIVATE_KEY": "inline-content",
            "GITHUB_APP_PRIVATE_KEY_PATH": str(key_file),
        }

        with patch.dict("os.environ", env, clear=True):
            result = load_private_key()

        assert result == "inline-content"

    def test_load_private_key_returns_empty_when_unset(self):
        with patch.dict("os.environ", {}, clear=True):
            result = load_private_key()

        assert result == ""
