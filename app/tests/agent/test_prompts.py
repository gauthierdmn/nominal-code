# type: ignore
from nominal_code.agent.prompts import (
    build_system_prompt,
    detect_languages,
    load_repo_guidelines,
    load_repo_language_guidelines,
    resolve_guidelines,
)


class TestBuildSystemPrompt:
    def test_build_system_prompt_combines_both(self):
        result = build_system_prompt("Be concise.", "Use snake_case.")

        assert result == "Be concise.\n\nUse snake_case."

    def test_build_system_prompt_prompt_only(self):
        result = build_system_prompt("Be concise.", "")

        assert result == "Be concise."

    def test_build_system_prompt_guidelines_only(self):
        result = build_system_prompt("", "Use snake_case.")

        assert result == "Use snake_case."

    def test_build_system_prompt_both_empty(self):
        result = build_system_prompt("", "")

        assert result == ""


class TestLoadRepoGuidelines:
    def test_load_repo_guidelines_reads_file(self, tmp_path):
        nominal_dir = tmp_path / ".nominal"
        nominal_dir.mkdir()
        guidelines_file = nominal_dir / "guidelines.md"
        guidelines_file.write_text("  Repo-specific rules  \n")

        result = load_repo_guidelines(str(tmp_path))

        assert result == "Repo-specific rules"

    def test_load_repo_guidelines_missing_file(self, tmp_path):
        result = load_repo_guidelines(str(tmp_path))

        assert result == ""


class TestDetectLanguages:
    def test_detect_languages_python_files(self):
        result = detect_languages(["src/main.py", "src/utils.pyi"])

        assert result == ["python"]

    def test_detect_languages_unknown_extensions_ignored(self):
        result = detect_languages(["README.md", "Makefile", "data.csv"])

        assert result == []

    def test_detect_languages_empty_list(self):
        result = detect_languages([])

        assert result == []

    def test_detect_languages_mixed_known_and_unknown(self):
        result = detect_languages(["app.py", "style.css", "index.html"])

        assert result == ["python"]

    def test_detect_languages_deduplicates(self):
        result = detect_languages(["a.py", "b.py", "c.pyi"])

        assert result == ["python"]


class TestLoadRepoLanguageGuidelines:
    def test_load_repo_language_guidelines_reads_file(self, tmp_path):
        lang_dir = tmp_path / ".nominal" / "languages"
        lang_dir.mkdir(parents=True)
        python_file = lang_dir / "python.md"
        python_file.write_text("  Repo Python rules  \n")

        result = load_repo_language_guidelines(str(tmp_path), "python")

        assert result == "Repo Python rules"

    def test_load_repo_language_guidelines_missing_file(self, tmp_path):
        result = load_repo_language_guidelines(str(tmp_path), "python")

        assert result == ""


class TestResolveGuidelines:
    def test_resolve_guidelines_general_only_no_language_files(self, tmp_path):
        result = resolve_guidelines(
            str(tmp_path),
            "Default rules",
            {},
            ["README.md"],
        )

        assert result == "Default rules"

    def test_resolve_guidelines_repo_general_overrides_default(self, tmp_path):
        nominal_dir = tmp_path / ".nominal"
        nominal_dir.mkdir()
        (nominal_dir / "guidelines.md").write_text("Repo rules")

        result = resolve_guidelines(
            str(tmp_path),
            "Default rules",
            {},
            [],
        )

        assert result == "Repo rules"

    def test_resolve_guidelines_appends_builtin_language(self, tmp_path):
        result = resolve_guidelines(
            str(tmp_path),
            "General rules",
            {"python": "Python rules"},
            ["main.py"],
        )

        assert result == "General rules\n\nPython rules"

    def test_resolve_guidelines_repo_language_overrides_builtin(self, tmp_path):
        lang_dir = tmp_path / ".nominal" / "languages"
        lang_dir.mkdir(parents=True)
        (lang_dir / "python.md").write_text("Repo Python rules")

        result = resolve_guidelines(
            str(tmp_path),
            "General rules",
            {"python": "Built-in Python rules"},
            ["main.py"],
        )

        assert result == "General rules\n\nRepo Python rules"

    def test_resolve_guidelines_no_language_match_skips_language(self, tmp_path):
        result = resolve_guidelines(
            str(tmp_path),
            "General rules",
            {"python": "Python rules"},
            ["style.css"],
        )

        assert result == "General rules"

    def test_resolve_guidelines_empty_when_nothing_found(self, tmp_path):
        result = resolve_guidelines(str(tmp_path), "", {}, [])

        assert result == ""
