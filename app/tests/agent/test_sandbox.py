# type: ignore
import os
from unittest.mock import patch

from nominal_code.agent.sandbox import (
    REDACTED,
    SAFE_ENV_VARS,
    build_sanitized_env,
    sanitize_output,
)


class TestBuildSanitizedEnv:
    def test_keeps_safe_vars(self):
        env = {"PATH": "/usr/bin", "HOME": "/home/user", "GITLAB_TOKEN": "secret"}

        with patch.dict(os.environ, env, clear=True):
            result = build_sanitized_env()

        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/home/user"
        assert "GITLAB_TOKEN" not in result

    def test_strips_secrets(self):
        env = {
            "PATH": "/usr/bin",
            "GITLAB_TOKEN": "glpat-xxxx",
            "REDIS_URL": "redis://localhost:6379",
            "ANTHROPIC_API_KEY": "sk-ant-xxx",
            "ENCRYPTION_KEY": "fernet-key",
            "ADMIN_API_TOKEN": "admin-secret",
        }

        with patch.dict(os.environ, env, clear=True):
            result = build_sanitized_env()

        assert "GITLAB_TOKEN" not in result
        assert "REDIS_URL" not in result
        assert "ANTHROPIC_API_KEY" not in result
        assert "ENCRYPTION_KEY" not in result
        assert "ADMIN_API_TOKEN" not in result

    def test_extra_safe_vars(self):
        env = {"PATH": "/usr/bin", "MY_CUSTOM": "value"}

        with patch.dict(os.environ, env, clear=True):
            result = build_sanitized_env(extra_safe_vars=["MY_CUSTOM"])

        assert result["MY_CUSTOM"] == "value"

    def test_empty_env(self):
        with patch.dict(os.environ, {}, clear=True):
            result = build_sanitized_env()

        assert result == {}

    def test_all_safe_vars_preserved(self):
        env = {var: f"value_{var}" for var in SAFE_ENV_VARS}

        with patch.dict(os.environ, env, clear=True):
            result = build_sanitized_env()

        for var in SAFE_ENV_VARS:
            assert result[var] == f"value_{var}"


class TestSanitizeOutput:
    def test_redacts_gitlab_pat(self):
        text = "Token: glpat-ABCDEFGHIJKLMNOPQRST12345"
        result = sanitize_output(text)

        assert "glpat-" not in result
        assert REDACTED in result

    def test_redacts_github_pat(self):
        text = "Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        result = sanitize_output(text)

        assert "ghp_" not in result
        assert REDACTED in result

    def test_redacts_github_app_token(self):
        text = "Token: ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        result = sanitize_output(text)

        assert "ghs_" not in result
        assert REDACTED in result

    def test_redacts_openai_key(self):
        text = "Key: sk-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
        result = sanitize_output(text)

        assert "sk-" not in result
        assert REDACTED in result

    def test_redacts_google_api_key(self):
        text = "Key: AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZabcdefg"
        result = sanitize_output(text)

        assert "AIza" not in result
        assert REDACTED in result

    def test_redacts_private_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpA..."
        result = sanitize_output(text)

        assert "PRIVATE KEY" not in result
        assert REDACTED in result

    def test_redacts_bearer_token(self):
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc"
        result = sanitize_output(text)

        assert "eyJhbGci" not in result
        assert REDACTED in result

    def test_preserves_normal_text(self):
        text = "This is a normal code review comment about function naming."
        result = sanitize_output(text)

        assert result == text

    def test_multiple_secrets_redacted(self):
        text = (
            "Found glpat-ABCDEFGHIJKLMNOPabcde"
            " and sk-ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef in code"
        )
        result = sanitize_output(text)

        assert "glpat-" not in result
        assert "sk-" not in result
        assert result.count(REDACTED) == 2

    def test_empty_string(self):
        assert sanitize_output("") == ""
