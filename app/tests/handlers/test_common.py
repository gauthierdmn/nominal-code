# type: ignore
from unittest.mock import AsyncMock, MagicMock

import pytest

from nominal_code.bot_type import BotType, EventType
from nominal_code.config import ReviewerConfig, WorkerConfig
from nominal_code.handlers.common import (
    build_system_prompt,
    detect_languages,
    enqueue_job,
    load_repo_guidelines,
    load_repo_language_guidelines,
    resolve_guidelines,
)
from nominal_code.platforms.base import CommentEvent, LifecycleEvent, PlatformName
from nominal_code.session import SessionQueue


def _make_config(allowed_users=None):
    config = MagicMock()
    config.allowed_users = frozenset(allowed_users or ["alice"])
    config.workspace_base_dir = "/tmp/workspaces"
    config.agent_model = ""
    config.agent_max_turns = 0
    config.agent_cli_path = ""
    config.coding_guidelines = "Use snake_case."
    config.language_guidelines = {"python": "Python style rules."}
    config.worker = WorkerConfig(
        bot_username="claude-worker",
        system_prompt="Be concise.",
    )
    config.reviewer = ReviewerConfig(
        bot_username="claude-reviewer",
        system_prompt="Review code.",
    )

    return config


def _make_comment(
    author="alice",
    platform=PlatformName.GITHUB,
    repo="owner/repo",
    pr_number=42,
    branch="feature",
    body="@claude-worker fix this",
    diff_hunk="",
    file_path="",
):
    return CommentEvent(
        platform=platform,
        repo_full_name=repo,
        pr_number=pr_number,
        pr_branch=branch,
        clone_url="https://token@github.com/owner/repo.git",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username=author,
        body=body,
        diff_hunk=diff_hunk,
        file_path=file_path,
    )


def _make_platform():
    platform = MagicMock()
    platform.post_reaction = AsyncMock()
    platform.post_reply = AsyncMock()
    platform.fetch_pr_branch = AsyncMock(return_value="")
    platform.fetch_pr_diff = AsyncMock(return_value=[])
    platform.fetch_pr_comments = AsyncMock(return_value=[])
    platform.submit_review = AsyncMock()
    platform.build_reviewer_clone_url = MagicMock(
        return_value="https://ro-token@github.com/owner/repo.git",
    )

    return platform


class TestEnqueueJob:
    @pytest.mark.asyncio
    async def test_enqueue_job_unauthorized_user(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="eve")
        session_queue = SessionQueue()
        mock_job = AsyncMock()

        await enqueue_job(
            event=comment,
            bot_type=BotType.WORKER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=mock_job,
        )

        platform.post_reaction.assert_not_called()
        mock_job.assert_not_called()

    @pytest.mark.asyncio
    async def test_enqueue_job_authorized_user_posts_reaction_and_enqueues(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_queue = SessionQueue()
        mock_job = AsyncMock()

        await enqueue_job(
            event=comment,
            bot_type=BotType.WORKER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=mock_job,
        )

        platform.post_reaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_enqueue_job_auto_trigger_skips_auth_and_reaction(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        session_queue = SessionQueue()
        mock_job = AsyncMock()

        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            clone_url="",
            event_type=EventType.PR_OPENED,
            pr_title="Add feature",
            pr_author="eve",
        )

        await enqueue_job(
            event=event,
            bot_type=BotType.REVIEWER,
            config=config,
            platform=platform,
            session_queue=session_queue,
            job=mock_job,
        )

        platform.post_reaction.assert_not_called()


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
