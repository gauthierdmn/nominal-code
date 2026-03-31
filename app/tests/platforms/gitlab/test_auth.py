# type: ignore
import pytest

from nominal_code.platforms.gitlab.auth import GitLabPatAuth


class TestGitLabPatAuth:
    def test_get_api_token(self):
        auth = GitLabPatAuth(token="glpat-test")

        assert auth.get_api_token() == "glpat-test"

    @pytest.mark.asyncio
    async def test_ensure_auth_noop(self):
        auth = GitLabPatAuth(token="glpat-test")

        await auth.ensure_auth(account_id=123)

        assert auth.get_api_token() == "glpat-test"
