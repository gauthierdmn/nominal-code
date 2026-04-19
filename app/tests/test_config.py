# type: ignore
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nominal_code.config import ApiAgentConfig, CliAgentConfig, Config, load_config
from nominal_code.config.settings import (
    load_file_content,
    load_language_guidelines,
    parse_reviewer_triggers,
    parse_title_tags,
)
from nominal_code.models import EventType, ProviderName


@pytest.fixture
def _reviewer_only_env():
    env = {
        "REVIEWER_BOT_USERNAME": "claude-reviewer",
        "ALLOWED_USERS": "alice,bob",
    }

    with patch.dict(os.environ, env, clear=True):
        yield


@pytest.fixture
def _full_env(tmp_path):
    reviewer_prompt_file = tmp_path / "custom_reviewer_prompt.md"
    reviewer_prompt_file.write_text("Review carefully.", encoding="utf-8")

    guidelines_file = tmp_path / "custom_guidelines.md"
    guidelines_file.write_text("Use snake_case.", encoding="utf-8")

    lang_dir = tmp_path / "languages"
    lang_dir.mkdir()

    env = {
        "REVIEWER_BOT_USERNAME": "claude-reviewer",
        "WEBHOOK_HOST": "127.0.0.1",
        "WEBHOOK_PORT": "9090",
        "ALLOWED_USERS": "alice, bob, charlie",
        "WORKSPACE_BASE_DIR": "/tmp/workspaces",
        "AGENT_MAX_TURNS": "10",
        "AGENT_MODEL": "claude-sonnet-4-20250514",
        "AGENT_CLI_PATH": "/usr/local/bin/claude",
        "REVIEWER_SYSTEM_PROMPT": str(reviewer_prompt_file),
        "CODING_GUIDELINES": str(guidelines_file),
        "LANGUAGE_GUIDELINES_DIR": str(lang_dir),
    }

    with patch.dict(os.environ, env, clear=True):
        yield


class TestFromEnv:
    def test_from_env_reviewer_only(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.reviewer.bot_username == "claude-reviewer"

    def test_from_env_neither_bot_raises(self):
        env = {
            "ALLOWED_USERS": "alice",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="REVIEWER_BOT_USERNAME"):
                Config.from_env(require_webhook=True)

    def test_from_env_shared_defaults(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.host == "0.0.0.0"
        assert config.webhook.port == 8080
        assert config.webhook.filtering.allowed_users == frozenset({"alice", "bob"})
        assert config.agent.model is None
        assert config.agent.cli_path is None

    def test_from_env_full_config(self, _full_env):
        config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.reviewer.bot_username == "claude-reviewer"
        assert config.agent.system_prompt == "Review carefully."
        assert config.webhook is not None
        assert config.webhook.host == "127.0.0.1"
        assert config.webhook.port == 9090
        assert config.webhook.filtering.allowed_users == frozenset(
            {"alice", "bob", "charlie"},
        )
        assert config.workspace.base_dir == Path("/tmp/workspaces")
        assert config.agent.model == "claude-sonnet-4-20250514"
        assert config.agent.cli_path == "/usr/local/bin/claude"
        assert config.prompts.coding_guidelines == "Use snake_case."
        assert config.prompts.language_guidelines == {}

    def test_from_env_reviewer_system_prompt_from_file(
        self,
        tmp_path,
        _reviewer_only_env,
    ):
        prompt_file = tmp_path / "reviewer.md"
        prompt_file.write_text("Review code.\n", encoding="utf-8")

        with patch.dict(os.environ, {"REVIEWER_SYSTEM_PROMPT": str(prompt_file)}):
            config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.agent.system_prompt == "Review code."

    def test_from_env_reviewer_system_prompt_inline_content(
        self,
        _reviewer_only_env,
    ):
        inline_prompt: str = "Review the changes.\nFocus on security."

        with patch.dict(
            os.environ,
            {"REVIEWER_SYSTEM_PROMPT": inline_prompt},
        ):
            config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.agent.system_prompt == inline_prompt

    def test_from_env_explorer_system_prompt_from_file(
        self,
        tmp_path,
        _reviewer_only_env,
    ):
        prompt_file = tmp_path / "explorer.md"
        prompt_file.write_text("You are an explorer.\n", encoding="utf-8")

        with patch.dict(
            os.environ,
            {
                "AGENT_PROVIDER": "anthropic",
                "ANTHROPIC_API_KEY": "test",
                "EXPLORER_SYSTEM_PROMPT": str(prompt_file),
            },
        ):
            config = Config.from_env(require_webhook=True)

        assert config.agent.explorer.system_prompt == "You are an explorer."

    def test_from_env_explorer_system_prompt_inline_content(
        self,
        _reviewer_only_env,
    ):
        inline_prompt: str = "You are an explorer.\nAnswer concisely."

        with patch.dict(
            os.environ,
            {
                "AGENT_PROVIDER": "anthropic",
                "ANTHROPIC_API_KEY": "test",
                "EXPLORER_SYSTEM_PROMPT": inline_prompt,
            },
        ):
            config = Config.from_env(require_webhook=True)

        assert config.agent.explorer.system_prompt == inline_prompt

    def test_from_env_coding_guidelines_from_file(self, tmp_path, _reviewer_only_env):
        guidelines_file = tmp_path / "guidelines.md"
        guidelines_file.write_text("Use snake_case.\n", encoding="utf-8")

        with patch.dict(
            os.environ,
            {"CODING_GUIDELINES": str(guidelines_file)},
        ):
            config = Config.from_env(require_webhook=True)

        assert config.prompts.coding_guidelines == "Use snake_case."

    def test_from_env_coding_guidelines_inline_content(
        self,
        _reviewer_only_env,
    ):
        inline_guidelines: str = "Use snake_case.\nPrefer f-strings."

        with patch.dict(
            os.environ,
            {"CODING_GUIDELINES": inline_guidelines},
        ):
            config = Config.from_env(require_webhook=True)

        assert config.prompts.coding_guidelines == inline_guidelines

    def test_from_env_coding_guidelines_defaults_empty(
        self,
        _reviewer_only_env,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        config = Config.from_env(require_webhook=True)

        assert config.prompts.coding_guidelines == ""

    def test_from_env_missing_allowed_users_raises(self):
        env = {
            "REVIEWER_BOT_USERNAME": "claude-reviewer",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="ALLOWED_USERS"):
                Config.from_env(require_webhook=True)

    def test_from_env_empty_allowed_users_raises(self):
        env = {
            "REVIEWER_BOT_USERNAME": "claude-reviewer",
            "ALLOWED_USERS": "  , , ",
        }

        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="at least one username"):
                Config.from_env(require_webhook=True)

    def test_from_env_reviewer_triggers_parsed(self, _reviewer_only_env):
        with patch.dict(os.environ, {"REVIEWER_TRIGGERS": "pr_opened,pr_push"}):
            config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.routing.reviewer_triggers == frozenset(
            {EventType.PR_OPENED, EventType.PR_PUSH},
        )

    def test_from_env_reviewer_triggers_empty(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.routing.reviewer_triggers == frozenset()

    def test_from_env_allowed_repos_parsed(self, _reviewer_only_env):
        with patch.dict(
            os.environ,
            {"ALLOWED_REPOS": "owner/repo-a, owner/repo-b"},
        ):
            config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.filtering.allowed_repos == frozenset(
            {"owner/repo-a", "owner/repo-b"},
        )

    def test_from_env_allowed_repos_default_empty(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.filtering.allowed_repos == frozenset()

    def test_from_env_default_agent_is_cli(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert isinstance(config.agent, CliAgentConfig)

    def test_from_env_agent_provider_creates_api_config(self, _reviewer_only_env):
        with patch.dict(os.environ, {"AGENT_PROVIDER": "openai"}):
            config = Config.from_env(require_webhook=True)

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.reviewer.name == ProviderName.OPENAI

    def test_from_env_agent_provider_with_model_override(self, _reviewer_only_env):
        with patch.dict(
            os.environ,
            {"AGENT_PROVIDER": "anthropic", "AGENT_MODEL": "claude-opus-4-6"},
        ):
            config = Config.from_env(require_webhook=True)

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.reviewer.model == "claude-opus-4-6"

    def test_from_env_unknown_agent_provider_raises(self, _reviewer_only_env):
        with patch.dict(os.environ, {"AGENT_PROVIDER": "unknown"}):
            with pytest.raises(ValueError, match="Unknown AGENT_PROVIDER"):
                Config.from_env(require_webhook=True)

    def test_from_env_inline_suggestions_enabled_by_default(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.reviewer.suggestions_prompt != ""

    def test_from_env_inline_suggestions_disabled(self, _reviewer_only_env):
        with patch.dict(os.environ, {"INLINE_SUGGESTIONS": "false"}):
            config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.reviewer.suggestions_prompt == ""

    def test_from_env_inline_suggestions_explicit_true(self, _reviewer_only_env):
        with patch.dict(os.environ, {"INLINE_SUGGESTIONS": "true"}):
            config = Config.from_env(require_webhook=True)

        assert config.reviewer is not None
        assert config.reviewer.suggestions_prompt != ""


class TestParseReviewerTriggers:
    def testparse_reviewer_triggers_empty_string(self):
        result = parse_reviewer_triggers("")

        assert result == frozenset()

    def testparse_reviewer_triggers_whitespace_only(self):
        result = parse_reviewer_triggers("   ")

        assert result == frozenset()

    def testparse_reviewer_triggers_single_value(self):
        result = parse_reviewer_triggers("pr_opened")

        assert result == frozenset({EventType.PR_OPENED})

    def testparse_reviewer_triggers_multiple_values(self):
        result = parse_reviewer_triggers("pr_opened,pr_push,pr_reopened")

        assert result == frozenset(
            {EventType.PR_OPENED, EventType.PR_PUSH, EventType.PR_REOPENED},
        )

    def testparse_reviewer_triggers_with_whitespace(self):
        result = parse_reviewer_triggers(" pr_opened , pr_push ")

        assert result == frozenset({EventType.PR_OPENED, EventType.PR_PUSH})

    def testparse_reviewer_triggers_invalid_value_skipped(self):
        result = parse_reviewer_triggers("pr_opened,invalid_event,pr_push")

        assert result == frozenset({EventType.PR_OPENED, EventType.PR_PUSH})

    def testparse_reviewer_triggers_all_invalid(self):
        result = parse_reviewer_triggers("foo,bar")

        assert result == frozenset()

    def testparse_reviewer_triggers_trailing_comma(self):
        result = parse_reviewer_triggers("pr_opened,")

        assert result == frozenset({EventType.PR_OPENED})


class TestLoadFileContent:
    def testload_file_content_reads_existing_file(self, tmp_path):
        target = tmp_path / "prompt.md"
        target.write_text("  System prompt here  \n", encoding="utf-8")

        result = load_file_content(target)

        assert result == "System prompt here"

    def testload_file_content_returns_empty_for_missing_file(self, tmp_path):
        result = load_file_content(tmp_path / "nonexistent.md")

        assert result == ""

    def testload_file_content_strips_whitespace(self, tmp_path):
        target = tmp_path / "file.md"
        target.write_text("\n\nContent\n\n", encoding="utf-8")

        result = load_file_content(target)

        assert result == "Content"

    def testload_file_content_empty_file_returns_empty(self, tmp_path):
        target = tmp_path / "empty.md"
        target.write_text("", encoding="utf-8")

        result = load_file_content(target)

        assert result == ""


class TestLoadLanguageGuidelines:
    def testload_language_guidelines_reads_md_files(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "python.md").write_text("Python rules.", encoding="utf-8")
        (lang_dir / "go.md").write_text("Go rules.", encoding="utf-8")

        result = load_language_guidelines(lang_dir)

        assert result["python"] == "Python rules."
        assert result["go"] == "Go rules."

    def testload_language_guidelines_returns_empty_dict_for_missing_dir(self, tmp_path):
        result = load_language_guidelines(tmp_path / "nonexistent")

        assert result == {}

    def testload_language_guidelines_skips_empty_files(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "python.md").write_text("", encoding="utf-8")
        (lang_dir / "go.md").write_text("  \n  ", encoding="utf-8")

        result = load_language_guidelines(lang_dir)

        assert result == {}

    def testload_language_guidelines_ignores_non_md_files(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "python.txt").write_text("Should be ignored.", encoding="utf-8")
        (lang_dir / "python.md").write_text("Python rules.", encoding="utf-8")

        result = load_language_guidelines(lang_dir)

        assert list(result.keys()) == ["python"]

    def testload_language_guidelines_uses_stem_as_key(self, tmp_path):
        lang_dir = tmp_path / "languages"
        lang_dir.mkdir()
        (lang_dir / "typescript.md").write_text("TS rules.", encoding="utf-8")

        result = load_language_guidelines(lang_dir)

        assert "typescript" in result


class TestConfigForCli:
    def test_config_for_cli_creates_valid_config(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config()

        assert config.reviewer is not None
        assert config.reviewer.bot_username == ""

    def test_config_for_cli_applies_model_override(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config(model="claude-opus-4-6")

        assert config.agent.model == "claude-opus-4-6"

    def test_config_for_cli_no_webhook_settings_required(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config()

        assert config.webhook is None

    def test_config_for_cli_title_tags_default_empty(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config()

        assert config.webhook is None

    def test_config_for_cli_allowed_repos_default_empty(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config()

        assert config.webhook is None

    def test_config_for_cli_default_agent_is_cli(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config()

        assert isinstance(config.agent, CliAgentConfig)

    def test_config_for_cli_provider_creates_api_config(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config(provider=ProviderName.OPENAI)

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.reviewer.name == ProviderName.OPENAI
        assert config.agent.reviewer.model == "gpt-4.1"

    def test_config_for_cli_provider_with_model_override(self, tmp_path):
        with patch.dict(os.environ, {"WORKSPACE_BASE_DIR": str(tmp_path)}, clear=True):
            config = load_config(provider=ProviderName.OPENAI, model="gpt-4o")

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.reviewer.model == "gpt-4o"

    def test_config_for_cli_provider_from_env(self, tmp_path):
        with patch.dict(
            os.environ,
            {"WORKSPACE_BASE_DIR": str(tmp_path), "AGENT_PROVIDER": "anthropic"},
            clear=True,
        ):
            config = load_config()

        assert isinstance(config.agent, ApiAgentConfig)
        assert config.agent.reviewer.name == ProviderName.ANTHROPIC


class TestParseTitleTags:
    def testparse_title_tags_empty_string(self):
        result = parse_title_tags("")

        assert result == frozenset()

    def testparse_title_tags_whitespace_only(self):
        result = parse_title_tags("   ")

        assert result == frozenset()

    def testparse_title_tags_single_value(self):
        result = parse_title_tags("nominalbot")

        assert result == frozenset({"nominalbot"})

    def testparse_title_tags_multiple_values(self):
        result = parse_title_tags("alpha,beta,gamma")

        assert result == frozenset({"alpha", "beta", "gamma"})

    def testparse_title_tags_strips_whitespace(self):
        result = parse_title_tags(" alpha , beta ")

        assert result == frozenset({"alpha", "beta"})

    def testparse_title_tags_lowercases(self):
        result = parse_title_tags("NominalBot,CI")

        assert result == frozenset({"nominalbot", "ci"})

    def testparse_title_tags_trailing_comma(self):
        result = parse_title_tags("alpha,")

        assert result == frozenset({"alpha"})

    def testparse_title_tags_empty_segments_skipped(self):
        result = parse_title_tags("alpha,,beta,")

        assert result == frozenset({"alpha", "beta"})


class TestFromEnvTitleTags:
    def test_from_env_title_tags_parsed(self, _reviewer_only_env):
        with patch.dict(
            os.environ,
            {
                "PR_TITLE_INCLUDE_TAGS": "nominalbot,ci",
                "PR_TITLE_EXCLUDE_TAGS": "skip",
            },
        ):
            config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.filtering.pr_title_include_tags == frozenset(
            {"nominalbot", "ci"},
        )
        assert config.webhook.filtering.pr_title_exclude_tags == frozenset({"skip"})

    def test_from_env_title_tags_default_empty(self, _reviewer_only_env):
        config = Config.from_env(require_webhook=True)

        assert config.webhook is not None
        assert config.webhook.filtering.pr_title_include_tags == frozenset()
        assert config.webhook.filtering.pr_title_exclude_tags == frozenset()
