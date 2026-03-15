# type: ignore

from nominal_code.platforms.github.auth import GitHubPatAuth
from nominal_code.platforms.github.platform import GitHubPlatform


class TestGitHubApiBase:
    def test_default_base_url(self):
        platform = GitHubPlatform(auth=GitHubPatAuth(token="test-token"))

        assert "api.github.com" in str(platform._client.base_url)

    def test_base_url_override_via_parameter(self):
        platform = GitHubPlatform(
            auth=GitHubPatAuth(token="test-token"),
            base_url="https://github.example.com/api/v3",
        )

        assert "github.example.com" in str(platform._client.base_url)

    def test_parameter_overrides_env_var(self):
        platform = GitHubPlatform(
            auth=GitHubPatAuth(token="test-token"),
            base_url="https://custom.example.com",
        )

        assert "custom.example.com" in str(platform._client.base_url)

    def test_empty_base_url_falls_back_to_module_default(self):
        platform = GitHubPlatform(
            auth=GitHubPatAuth(token="test-token"),
            base_url="",
        )

        assert "api.github.com" in str(platform._client.base_url)
