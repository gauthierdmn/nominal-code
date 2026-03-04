# type: ignore
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nominal_code.config import (
    Config,
    _load_file_content,
    _load_language_guidelines,
    _parse_reviewer_triggers,
    _parse_title_tags,
)
from nominal_code.models import EventType


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
        assert config.agent.max_turns == 0
        assert config.agent.model == ""
        assert config.agent.cli_path == ""
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
        assert config.workspace_base_dir == Path("/tmp/workspaces")
        assert config.agent.max_turns == 10
        assert config.agent.model == "claude-sonnet-4-20250514"
        assert config.agent.cli_path == "/usr/local/bin/claude"
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
            with pytest.raises(ValueError, match="ALLOWED_USERS"):
                Config.from_env()

    def test_from_env_empty_allowed_users_raises(self):
        env = {
            "WORKER_BOT_USERNAME": "claude-worker",
            "ALLOWED_USERS": "  , , ",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="at least one username"):
                Config.from_env()

    def test_from_env_reviewer_triggers_parsed(self, _both_bots_env):
        with patch.dict(os.environ, {"REVIEWER_TRIGGERS": "pr_opened,pr_push"}):
            config = Config.from_env()

        assert config.reviewer_triggers == frozenset(
            {EventType.PR_OPENED, EventType.PR_PUSH},
        )

    def test_from_env_reviewer_triggers_empty(self, _both_bots_env):
        config = Config.from_env()

        assert config.reviewer_triggers == frozenset()

    def test_from_env_allowed_repos_parsed(self, _both_bots_env):
        with patch.dict(
            os.environ,
            {"ALLOWED_REPOS": "owner/repo-a, owner/repo-b"},
        ):
            config = Config.from_env()

        assert config.allowed_repos == frozenset({"owner/repo-a", "owner/repo-b"})

    def test_from_env_allowed_repos_default_empty(self, _both_bots_env):
        config = Config.from_env()

        assert config.allowed_repos == frozenset()


class TestParseReviewerTriggers:
    def test_parse_reviewer_triggers_empty_string(self):
        result = _parse_reviewer_triggers("")

        assert result == frozenset()

    def test_parse_reviewer_triggers_whitespace_only(self):
        result = _parse_reviewer_triggers("   ")

        assert result == frozenset()

    def test_parse_reviewer_triggers_single_value(self):
        result = _parse_reviewer_triggers("pr_opened")

        assert result == frozenset({EventType.PR_OPENED})

    def test_parse_reviewer_triggers_multiple_values(self):
        result = _parse_reviewer_triggers("pr_opened,pr_push,pr_reopened")

        assert result == frozenset(
            {EventType.PR_OPENED, EventType.PR_PUSH, EventType.PR_REOPENED},
        )

    def test_parse_reviewer_triggers_with_whitespace(self):
        result = _parse_reviewer_triggers(" pr_opened , pr_push ")

        assert result == frozenset({EventType.PR_OPENED, EventType.PR_PUSH})

    def test_parse_reviewer_triggers_invalid_value_skipped(self):
        result = _parse_reviewer_triggers("pr_opened,invalid_event,pr_push")

        assert result == frozenset({EventType.PR_OPENED, EventType.PR_PUSH})

    def test_parse_reviewer_triggers_all_invalid(self):
        result = _parse_reviewer_triggers("foo,bar")

        assert result == frozenset()

    def test_parse_reviewer_triggers_trailing_comma(self):
        result = _parse_reviewer_triggers("pr_opened,")

        assert result == frozenset({EventType.PR_OPENED})


class TestLoadFileContent:
    def test_load_file_content_reads_existing_file(self, tmp_path):
        target = tmp_path / "prompt.md"
        target.write_text("  System prompt here  \n", encoding="utf-8")

        result = _load_file_content(target)

        assert result == "System prompt here"

    def test_load_file_content_returns_empty_for_missing_file(self, tmp_path):
        result = _load_file_content(tmp_path / "nonexistent.md")

        assert result == ""

    def test_load_file_content_strips_whitespace(self, tmp_path):
        target = tmp_path / "file.md"
        target.write_text("\n\nContent\n\n", encoding="utf-8")

        result = _load_file_content(target)

        assert result == "Content"

    def test_load_file_content_empty_file_returns_empty(self, tmp_path):
        target = tmp_path / "empty.md"
        target.write_text("", encoding="utf-8")

        result = _load_file_content(target)

        assert result == ""


class TestLoadLanguageGuidelines:
    def test_load_language_guidelines_reads_md_files(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "python.md").write_text("Python rules.", encoding="utf-8")
        (lang_dir / "go.md").write_text("Go rules.", encoding="utf-8")

        result = _load_language_guidelines(lang_dir)

        assert result["python"] == "Python rules."
        assert result["go"] == "Go rules."

    def test_load_language_guidelines_returns_empty_dict_for_missing_dir(
        self, tmp_path
    ):
        result = _load_language_guidelines(tmp_path / "nonexistent")

        assert result == {}

    def test_load_language_guidelines_skips_empty_files(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "python.md").write_text("", encoding="utf-8")
        (lang_dir / "go.md").write_text("  \n  ", encoding="utf-8")

        result = _load_language_guidelines(lang_dir)

        assert result == {}

    def test_load_language_guidelines_ignores_non_md_files(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "python.txt").write_text("Should be ignored.", encoding="utf-8")
        (lang_dir / "python.md").write_text("Python rules.", encoding="utf-8")

        result = _load_language_guidelines(lang_dir)

        assert list(result.keys()) == ["python"]

    def test_load_language_guidelines_uses_stem_as_key(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "typescript.md").write_text("TS rules.", encoding="utf-8")

        result = _load_language_guidelines(lang_dir)

        assert "typescript" in result


class TestConfigForCli:
    def test_config_for_cli_creates_valid_config(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli()

        assert config.worker is None
        assert config.reviewer is not None
        assert config.reviewer.bot_username == ""

    def test_config_for_cli_applies_model_override(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli(model="claude-opus-4-6")

        assert config.agent.model == "claude-opus-4-6"

    def test_config_for_cli_applies_max_turns_override(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli(max_turns=5)

        assert config.agent.max_turns == 5

    def test_config_for_cli_no_webhook_settings_required(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli()

        assert config.webhook_host == ""
        assert config.webhook_port == 0
        assert config.allowed_users == frozenset()

    def test_config_for_cli_cleanup_interval_is_zero(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli()

        assert config.cleanup_interval_hours == 0

    def test_config_for_cli_title_tags_default_empty(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli()

        assert config.pr_title_include_tags == frozenset()
        assert config.pr_title_exclude_tags == frozenset()

    def test_config_for_cli_allowed_repos_default_empty(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = Config.for_cli()

        assert config.allowed_repos == frozenset()


class TestParseTitleTags:
    def test_parse_title_tags_empty_string(self):
        result = _parse_title_tags("")

        assert result == frozenset()

    def test_parse_title_tags_whitespace_only(self):
        result = _parse_title_tags("   ")

        assert result == frozenset()

    def test_parse_title_tags_single_value(self):
        result = _parse_title_tags("nominalbot")

        assert result == frozenset({"nominalbot"})

    def test_parse_title_tags_multiple_values(self):
        result = _parse_title_tags("alpha,beta,gamma")

        assert result == frozenset({"alpha", "beta", "gamma"})

    def test_parse_title_tags_strips_whitespace(self):
        result = _parse_title_tags(" alpha , beta ")

        assert result == frozenset({"alpha", "beta"})

    def test_parse_title_tags_lowercases(self):
        result = _parse_title_tags("NominalBot,CI")

        assert result == frozenset({"nominalbot", "ci"})

    def test_parse_title_tags_trailing_comma(self):
        result = _parse_title_tags("alpha,")

        assert result == frozenset({"alpha"})

    def test_parse_title_tags_empty_segments_skipped(self):
        result = _parse_title_tags("alpha,,beta,")

        assert result == frozenset({"alpha", "beta"})


class TestFromEnvTitleTags:
    def test_from_env_title_tags_parsed(self, _both_bots_env):
        with patch.dict(
            os.environ,
            {
                "PR_TITLE_INCLUDE_TAGS": "nominalbot,ci",
                "PR_TITLE_EXCLUDE_TAGS": "skip",
            },
        ):
            config = Config.from_env()

        assert config.pr_title_include_tags == frozenset({"nominalbot", "ci"})
        assert config.pr_title_exclude_tags == frozenset({"skip"})

    def test_from_env_title_tags_default_empty(self, _both_bots_env):
        config = Config.from_env()

        assert config.pr_title_include_tags == frozenset()
        assert config.pr_title_exclude_tags == frozenset()
