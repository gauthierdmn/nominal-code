# type: ignore
import pytest

from nominal_code.platforms.gitlab.auth import GitLabPatAuth


class TestGitLabPatAuth:
    def test_get_api_token(self):
        auth = GitLabPatAuth(token="glpat-test")

        assert auth.get_api_token() == "glpat-test"

    def test_get_clone_token_fallback(self):
        auth = GitLabPatAuth(token="glpat-test")

        assert auth.get_clone_token() == "glpat-test"

    def test_get_clone_token_explicit(self):
        auth = GitLabPatAuth(token="glpat-test", reviewer_token="glpat-readonly")

        assert auth.get_clone_token() == "glpat-readonly"

    @pytest.mark.asyncio
    async def test_ensure_auth_noop(self):
        auth = GitLabPatAuth(token="glpat-test")

        await auth.ensure_auth(account_id=123)

        assert auth.get_api_token() == "glpat-test"
