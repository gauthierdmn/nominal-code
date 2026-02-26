# type: ignore
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent_runner import AgentResult
from nominal_code.bot_type import BotType, EventType
from nominal_code.config import ReviewerConfig, WorkerConfig
from nominal_code.handlers.common import (
    build_system_prompt,
    detect_languages,
    handle_auto_trigger,
    handle_comment,
    load_repo_guidelines,
    load_repo_language_guidelines,
    resolve_guidelines,
)
from nominal_code.platforms.base import PlatformName, PullRequestEvent
from nominal_code.session import SessionQueue, SessionStore


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
    return PullRequestEvent(
        platform=platform,
        repo_full_name=repo,
        pr_number=pr_number,
        pr_branch=branch,
        comment_id=100,
        author_username=author,
        body=body,
        diff_hunk=diff_hunk,
        file_path=file_path,
        clone_url="https://token@github.com/owner/repo.git",
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


class TestHandleComment:
    @pytest.mark.asyncio
    async def test_handle_comment_unauthorized_user(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="eve")
        session_store = SessionStore()
        session_queue = SessionQueue()

        await handle_comment(
            event=comment,
            prompt="fix this",
            config=config,
            platform=platform,
            session_store=session_store,
            session_queue=session_queue,
            bot_type=BotType.WORKER,
        )

        platform.post_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_comment_worker_authorized_user_enqueues_job(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_store = SessionStore()
        session_queue = SessionQueue()

        with patch(
            "nominal_code.handlers.worker.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output="Done!",
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                session_id="sess-1",
            )

            with patch(
                "nominal_code.handlers.worker.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    event=comment,
                    prompt="fix this",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.WORKER,
                )

                await asyncio.sleep(0.1)

            platform.post_reaction.assert_called_once()
            platform.post_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_comment_branch_resolution_when_missing(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_branch = AsyncMock(return_value="resolved-branch")
        comment = _make_comment(author="alice", branch="")
        session_store = SessionStore()
        session_queue = SessionQueue()

        with patch(
            "nominal_code.handlers.worker.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output="Done!",
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                session_id="sess-1",
            )

            with patch(
                "nominal_code.handlers.worker.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    event=comment,
                    prompt="fix this",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.WORKER,
                )

                await asyncio.sleep(0.1)

            platform.fetch_pr_branch.assert_called_once_with(comment)
            platform.post_reply.assert_called_once()


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


class TestHandleAutoTrigger:
    @pytest.mark.asyncio
    async def test_handle_auto_trigger_enqueues_reviewer_job(self):
        config = _make_config()
        platform = _make_platform()
        session_store = SessionStore()
        session_queue = SessionQueue()

        event = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            comment_id=0,
            author_username="",
            body="",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.PR_OPENED,
            pr_title="Add new feature",
            pr_author="alice",
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output='{"summary": "Looks good", "comments": []}',
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                session_id="sess-1",
            )

            with patch(
                "nominal_code.handlers.reviewer.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-1"
                mock_ws_class.return_value = mock_ws

                await handle_auto_trigger(
                    event=event,
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                )

                await asyncio.sleep(0.1)

            mock_run.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_auto_trigger_skips_when_no_reviewer(self):
        config = _make_config()
        config.reviewer = None
        platform = _make_platform()
        session_store = SessionStore()
        session_queue = SessionQueue()

        event = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            comment_id=0,
            author_username="",
            body="",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.PR_OPENED,
            pr_title="Add feature",
            pr_author="alice",
        )

        await handle_auto_trigger(
            event=event,
            config=config,
            platform=platform,
            session_store=session_store,
            session_queue=session_queue,
        )

        platform.post_reaction.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_auto_trigger_does_not_check_allowed_users(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        session_store = SessionStore()
        session_queue = SessionQueue()

        event = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            comment_id=0,
            author_username="",
            body="",
            diff_hunk="",
            file_path="",
            clone_url="",
            event_type=EventType.PR_OPENED,
            pr_title="Add feature",
            pr_author="eve",
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output='{"summary": "OK", "comments": []}',
                is_error=False,
                num_turns=1,
                duration_ms=500,
                session_id="sess-1",
            )

            with patch(
                "nominal_code.handlers.reviewer.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-1"
                mock_ws_class.return_value = mock_ws

                await handle_auto_trigger(
                    event=event,
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                )

                await asyncio.sleep(0.1)

            mock_run.assert_called_once()
            platform.post_reaction.assert_not_called()
