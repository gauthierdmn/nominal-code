# type: ignore
import os
from unittest.mock import patch

import pytest

from nominal_code.config import Config


@pytest.fixture
def _worker_only_env():
    env = {
        "WORKER_BOT_USERNAME": "claude-worker",
        "ALLOWED_USERS": "alice,bob",
    }

    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture
def _reviewer_only_env():
    env = {
        "REVIEWER_BOT_USERNAME": "claude-reviewer",
        "ALLOWED_USERS": "alice,bob",
    }

    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture
def _both_bots_env():
    env = {
        "WORKER_BOT_USERNAME": "claude-worker",
        "REVIEWER_BOT_USERNAME": "claude-reviewer",
        "ALLOWED_USERS": "alice,bob",
    }

    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture
def _full_env(tmp_path):
    worker_prompt_file = tmp_path / "custom_prompt.md"
    worker_prompt_file.write_text("You are a custom bot.", encoding="utf-8")

    reviewer_prompt_file = tmp_path / "custom_reviewer_prompt.md"
    reviewer_prompt_file.write_text("Review carefully.", encoding="utf-8")

    guidelines_file = tmp_path / "custom_guidelines.md"
    guidelines_file.write_text("Use snake_case.", encoding="utf-8")

    lang_dir = tmp_path / "languages"
    lang_dir.mkdir()

    env = {
        "WORKER_BOT_USERNAME": "claude-worker",
        "REVIEWER_BOT_USERNAME": "claude-reviewer",
        "WEBHOOK_HOST": "127.0.0.1",
        "WEBHOOK_PORT": "9090",
        "ALLOWED_USERS": "alice, bob, charlie",
        "WORKSPACE_BASE_DIR": "/tmp/workspaces",
        "AGENT_MAX_TURNS": "10",
        "AGENT_MODEL": "claude-sonnet-4-20250514",
        "AGENT_CLI_PATH": "/usr/local/bin/claude",
        "WORKER_SYSTEM_PROMPT": str(worker_prompt_file),
        "REVIEWER_SYSTEM_PROMPT": str(reviewer_prompt_file),
        "CODING_GUIDELINES": str(guidelines_file),
        "LANGUAGE_GUIDELINES_DIR": str(lang_dir),
        "CLEANUP_INTERVAL_HOURS": "12",
    }

    with patch.dict(os.environ, env, clear=True):
        yield


class TestFromEnv:
    def test_from_env_worker_only(self, _worker_only_env):
        config = Config.from_env()

        assert config.worker is not None
        assert config.worker.bot_username == "claude-worker"
        assert config.reviewer is None

    def test_from_env_reviewer_only(self, _reviewer_only_env):
        config = Config.from_env()

        assert config.reviewer is not None
        assert config.reviewer.bot_username == "claude-reviewer"
        assert config.worker is None

    def test_from_env_both_bots(self, _both_bots_env):
        config = Config.from_env()

        assert config.worker is not None
        assert config.worker.bot_username == "claude-worker"
        assert config.reviewer is not None
        assert config.reviewer.bot_username == "claude-reviewer"

    def test_from_env_neither_bot_raises(self):
        env = {
            "ALLOWED_USERS": "alice",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="At least one"):
                Config.from_env()

    def test_from_env_shared_defaults(self, _both_bots_env):
        config = Config.from_env()

        assert config.webhook_host == "0.0.0.0"
        assert config.webhook_port == 8080
        assert config.allowed_users == frozenset({"alice", "bob"})
        assert config.agent_max_turns == 0
        assert config.agent_model == ""
        assert config.agent_cli_path == ""
        assert config.cleanup_interval_hours == 6

    def test_from_env_full_config(self, _full_env):
        config = Config.from_env()

        assert config.worker is not None
        assert config.worker.bot_username == "claude-worker"
        assert config.worker.system_prompt == "You are a custom bot."
        assert config.reviewer is not None
        assert config.reviewer.bot_username == "claude-reviewer"
        assert config.reviewer.system_prompt == "Review carefully."
        assert config.webhook_host == "127.0.0.1"
        assert config.webhook_port == 9090
        assert config.allowed_users == frozenset({"alice", "bob", "charlie"})
        assert config.workspace_base_dir == "/tmp/workspaces"
        assert config.agent_max_turns == 10
        assert config.agent_model == "claude-sonnet-4-20250514"
        assert config.agent_cli_path == "/usr/local/bin/claude"
        assert config.coding_guidelines == "Use snake_case."
        assert config.language_guidelines == {}
        assert config.cleanup_interval_hours == 12

    def test_from_env_worker_system_prompt_from_file(self, tmp_path, _worker_only_env):
        prompt_file = tmp_path / "prompt.md"
        prompt_file.write_text("Be helpful.\n", encoding="utf-8")

        with patch.dict(os.environ, {"WORKER_SYSTEM_PROMPT": str(prompt_file)}):
            config = Config.from_env()

        assert config.worker is not None
        assert config.worker.system_prompt == "Be helpful."

    def test_from_env_worker_system_prompt_missing_file_returns_empty(
        self,
        _worker_only_env,
    ):
        with patch.dict(
            os.environ,
            {"WORKER_SYSTEM_PROMPT": "/nonexistent/prompt.md"},
        ):
            config = Config.from_env()

        assert config.worker is not None
        assert config.worker.system_prompt == ""

    def test_from_env_worker_system_prompt_defaults_empty(
        self,
        _worker_only_env,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        config = Config.from_env()

        assert config.worker is not None
        assert config.worker.system_prompt == ""

    def test_from_env_reviewer_system_prompt_from_file(
        self,
        tmp_path,
        _reviewer_only_env,
    ):
        prompt_file = tmp_path / "reviewer.md"
        prompt_file.write_text("Review code.\n", encoding="utf-8")

        with patch.dict(os.environ, {"REVIEWER_SYSTEM_PROMPT": str(prompt_file)}):
            config = Config.from_env()

        assert config.reviewer is not None
        assert config.reviewer.system_prompt == "Review code."

    def test_from_env_reviewer_system_prompt_missing_file_returns_empty(
        self,
        _reviewer_only_env,
    ):
        with patch.dict(
            os.environ,
            {"REVIEWER_SYSTEM_PROMPT": "/nonexistent/reviewer.md"},
        ):
            config = Config.from_env()

        assert config.reviewer is not None
        assert config.reviewer.system_prompt == ""

    def test_from_env_coding_guidelines_from_file(self, tmp_path, _both_bots_env):
        guidelines_file = tmp_path / "guidelines.md"
        guidelines_file.write_text("Use snake_case.\n", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"CODING_GUIDELINES": str(guidelines_file)},
        ):
            config = Config.from_env()

        assert config.coding_guidelines == "Use snake_case."

    def test_from_env_coding_guidelines_missing_file_returns_empty(
        self,
        _both_bots_env,
    ):
        with patch.dict(
            os.environ,
            {"CODING_GUIDELINES": "/nonexistent/guidelines.md"},
        ):
            config = Config.from_env()

        assert config.coding_guidelines == ""

    def test_from_env_coding_guidelines_defaults_empty(
        self,
        _both_bots_env,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        config = Config.from_env()

        assert config.coding_guidelines == ""

    def test_from_env_missing_allowed_users_raises(self):
        env = {
            "WORKER_BOT_USERNAME": "claude-worker",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(OSError, match="ALLOWED_USERS"):
                Config.from_env()

    def test_from_env_empty_allowed_users_raises(self):
        env = {
            "WORKER_BOT_USERNAME": "claude-worker",
            "ALLOWED_USERS": "  , , ",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="at least one username"):
                Config.from_env()
