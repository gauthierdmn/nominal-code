# type: ignore
from pathlib import Path
from unittest.mock import MagicMock

from nominal_code.agent.prompts import (
    TAG_REPO_GUIDELINES,
    _detect_languages,
    _load_repo_guidelines,
    _load_repo_language_guidelines,
    resolve_guidelines,
    wrap_tag,
)


class TestLoadRepoGuidelines:
    def test__load_repo_guidelines_reads_file(self, tmp_path):
        nominal_dir = tmp_path / ".nominal"
        nominal_dir.mkdir()
        guidelines_file = nominal_dir / "guidelines.md"
        guidelines_file.write_text("  Repo-specific rules  \n")

        result = _load_repo_guidelines(tmp_path)

        assert result == "Repo-specific rules"

    def test__load_repo_guidelines_missing_file(self, tmp_path):
        result = _load_repo_guidelines(tmp_path)

        assert result == ""


class TestDetectLanguages:
    def test__detect_languages_python_files(self):
        result = _detect_languages([Path("src/main.py"), Path("src/utils.pyi")])

        assert result == {"python"}

    def test__detect_languages_unknown_extensions_ignored(self):
        result = _detect_languages(
            [Path("README.md"), Path("Makefile"), Path("data.csv")]
        )

        assert result == set()

    def test__detect_languages_empty_list(self):
        result = _detect_languages([])

        assert result == set()

    def test__detect_languages_mixed_known_and_unknown(self):
        result = _detect_languages(
            [Path("app.py"), Path("style.css"), Path("index.html")]
        )

        assert result == {"python"}

    def test__detect_languages_deduplicates(self):
        result = _detect_languages([Path("a.py"), Path("b.py"), Path("c.pyi")])

        assert result == {"python"}


class TestLoadRepoLanguageGuidelines:
    def test__load_repo_language_guidelines_reads_file(self, tmp_path):
        lang_dir = tmp_path / ".nominal" / "languages"
        lang_dir.mkdir(parents=True)
        python_file = lang_dir / "python.md"
        python_file.write_text("  Repo Python rules  \n")

        result = _load_repo_language_guidelines(tmp_path, "python")

        assert result == "Repo Python rules"

    def test__load_repo_language_guidelines_missing_file(self, tmp_path):
        result = _load_repo_language_guidelines(tmp_path, "python")

        assert result == ""


class TestResolveGuidelines:
    def test_resolve_guidelines_general_only_no_language_files(self, tmp_path):
        result = resolve_guidelines(
            tmp_path,
            "Default rules",
            {},
            [Path("README.md")],
        )

        assert result == "Default rules"

    def test_resolve_guidelines_repo_general_overrides_default(self, tmp_path):
        nominal_dir = tmp_path / ".nominal"
        nominal_dir.mkdir()
        (nominal_dir / "guidelines.md").write_text("Repo rules")

        result = resolve_guidelines(
            tmp_path,
            "Default rules",
            {},
            [],
        )

        assert result == "Repo rules"

    def test_resolve_guidelines_appends_builtin_language(self, tmp_path):
        result = resolve_guidelines(
            tmp_path,
            "General rules",
            {"python": "Python rules"},
            [Path("main.py")],
        )

        assert result == "General rules\n\nPython rules"

    def test_resolve_guidelines_repo_language_overrides_builtin(self, tmp_path):
        lang_dir = tmp_path / ".nominal" / "languages"
        lang_dir.mkdir(parents=True)
        (lang_dir / "python.md").write_text("Repo Python rules")

        result = resolve_guidelines(
            tmp_path,
            "General rules",
            {"python": "Built-in Python rules"},
            [Path("main.py")],
        )

        assert result == "General rules\n\nRepo Python rules"

    def test_resolve_guidelines_no_language_match_skips_language(self, tmp_path):
        result = resolve_guidelines(
            tmp_path,
            "General rules",
            {"python": "Python rules"},
            [Path("style.css")],
        )

        assert result == "General rules"

    def test_resolve_guidelines_empty_when_nothing_found(self, tmp_path):
        result = resolve_guidelines(tmp_path, "", {}, [])

        assert result == ""


class TestResolveSystemPrompt:
    def test_resolve_system_prompt_joins_prompt_and_guidelines(self, tmp_path):
        from nominal_code.agent.prompts import resolve_system_prompt

        workspace = MagicMock()
        workspace.repo_path = tmp_path
        config = MagicMock()
        config.prompts.coding_guidelines = "Use snake_case."
        config.prompts.language_guidelines = {}

        result = resolve_system_prompt(workspace, config, "You are a bot.", [])

        assert result.startswith("You are a bot.")
        assert "Use snake_case." in result
        assert f"<{TAG_REPO_GUIDELINES}>" in result

    def test_resolve_system_prompt_includes_language_guidelines(self, tmp_path):
        from nominal_code.agent.prompts import resolve_system_prompt

        workspace = MagicMock()
        workspace.repo_path = tmp_path
        config = MagicMock()
        config.prompts.coding_guidelines = ""
        config.prompts.language_guidelines = {"python": "Python rules."}

        result = resolve_system_prompt(
            workspace,
            config,
            "Base prompt.",
            [Path("app.py")],
        )

        assert "Python rules." in result

    def test_resolve_system_prompt_empty_guidelines_still_has_prompt(self, tmp_path):
        from nominal_code.agent.prompts import resolve_system_prompt

        workspace = MagicMock()
        workspace.repo_path = tmp_path
        config = MagicMock()
        config.prompts.coding_guidelines = ""
        config.prompts.language_guidelines = {}

        result = resolve_system_prompt(workspace, config, "Only base.", [])

        assert "Only base." in result
        assert f"<{TAG_REPO_GUIDELINES}>" not in result

    def test_resolve_system_prompt_separator_between_prompt_and_guidelines(
        self, tmp_path
    ):
        from nominal_code.agent.prompts import resolve_system_prompt

        workspace = MagicMock()
        workspace.repo_path = tmp_path
        config = MagicMock()
        config.prompts.coding_guidelines = "Guidelines text."
        config.prompts.language_guidelines = {}

        result = resolve_system_prompt(workspace, config, "Bot prompt.", [])

        assert "\n\n" in result

    def test_resolve_system_prompt_wraps_guidelines_in_boundary_tags(self, tmp_path):
        from nominal_code.agent.prompts import resolve_system_prompt

        workspace = MagicMock()
        workspace.repo_path = tmp_path
        config = MagicMock()
        config.prompts.coding_guidelines = "Follow PEP 8."
        config.prompts.language_guidelines = {}

        result = resolve_system_prompt(workspace, config, "Base prompt.", [])

        assert f"<{TAG_REPO_GUIDELINES}>" in result
        assert f"</{TAG_REPO_GUIDELINES}>" in result
        assert "Follow PEP 8." in result

    def test_resolve_system_prompt_empty_guidelines_no_boundary_tags(self, tmp_path):
        from nominal_code.agent.prompts import resolve_system_prompt

        workspace = MagicMock()
        workspace.repo_path = tmp_path
        config = MagicMock()
        config.prompts.coding_guidelines = ""
        config.prompts.language_guidelines = {}

        result = resolve_system_prompt(workspace, config, "Base prompt.", [])

        assert f"<{TAG_REPO_GUIDELINES}>" not in result
        assert result == "Base prompt."


class TestWrapTag:
    def test_wrap_tag_produces_correct_xml(self):
        result = wrap_tag("foo", "bar")

        assert result == "<foo>\nbar\n</foo>"
