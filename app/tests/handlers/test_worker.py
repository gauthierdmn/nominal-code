# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.prompts import (
    TAG_BRANCH_NAME,
    TAG_FILE_PATH,
    TAG_UNTRUSTED_HUNK,
    TAG_UNTRUSTED_REQUEST,
)
from nominal_code.agent.result import AgentResult
from nominal_code.config import CliAgentConfig, WorkerConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.handlers.worker import _build_prompt, review_and_fix
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_config(allowed_users=None):
    config = MagicMock()
    config.allowed_users = frozenset(allowed_users or ["alice"])
    config.workspace = MagicMock()
    config.workspace.base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()
    config.prompts = MagicMock()
    config.prompts.coding_guidelines = "Use snake_case."
    config.prompts.language_guidelines = {"python": "Python style rules."}
    config.worker = WorkerConfig(
        bot_username="claude-worker",
        system_prompt="Be concise.",
    )
    config.reviewer = None

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

    return platform


class TestWorkerProcessComment:
    @pytest.mark.asyncio
    async def test_worker_passes_system_prompt_to_run_agent(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output="Done!",
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                conversation_id="sess-1",
            )

            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                await review_and_fix(
                    event=comment,
                    prompt="fix this",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs

            assert "Be concise." in call_kwargs["system_prompt"]
            assert "Use snake_case." in call_kwargs["system_prompt"]

    @pytest.mark.asyncio
    async def test_worker_uses_resolve_coding_guidelines(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output="Done!",
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                conversation_id="sess-1",
            )

            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                with patch(
                    "nominal_code.agent.prompts.resolve_guidelines",
                    return_value="Repo guidelines override",
                ) as mock_resolve:
                    await review_and_fix(
                        event=comment,
                        prompt="fix this",
                        config=config,
                        platform=platform,
                        conversation_store=conversation_store,
                    )

                    mock_resolve.assert_called_once_with(
                        repo_path=Path("/tmp/workspaces/owner/repo/pr-42"),
                        default_guidelines="Use snake_case.",
                        language_guidelines={"python": "Python style rules."},
                        file_paths=[],
                    )

                call_kwargs = mock_run.call_args.kwargs

                assert "Repo guidelines override" in call_kwargs["system_prompt"]


class TestBuildPrompt:
    def test__build_prompt_basic(self):
        comment = _make_comment()
        result = _build_prompt(event=comment, user_prompt="fix the bug")

        assert "fix the bug" in result
        assert "owner/repo" in result
        assert "#42" in result
        assert f"<{TAG_UNTRUSTED_REQUEST}>" in result
        assert f"<{TAG_BRANCH_NAME}>" in result

    def test__build_prompt_with_deps_path(self):
        comment = _make_comment()
        result = _build_prompt(
            event=comment, user_prompt="fix the bug", deps_path=Path("/tmp/.deps")
        )

        assert "Dependencies directory: /tmp/.deps" in result
        assert "git clone" in result

    def test__build_prompt_without_deps_path(self):
        comment = _make_comment()
        result = _build_prompt(event=comment, user_prompt="fix the bug")

        assert "Dependencies directory" not in result

    def test__build_prompt_with_file_and_diff(self):
        comment = _make_comment(
            file_path="src/main.py",
            diff_hunk="@@ -1,3 +1,5 @@",
        )
        result = _build_prompt(event=comment, user_prompt="refactor this")

        assert "src/main.py" in result
        assert "@@ -1,3 +1,5 @@" in result
        assert "refactor this" in result
        assert f"<{TAG_FILE_PATH}>" in result
        assert f"<{TAG_UNTRUSTED_HUNK}>" in result


class TestPromptBoundaryTags:
    def test__build_prompt_wraps_diff_hunk_in_boundary_tags(self):
        comment = _make_comment(
            file_path="src/main.py",
            diff_hunk="@@ -1,3 +1,5 @@\n context",
        )

        result = _build_prompt(event=comment, user_prompt="fix this")

        assert f"<{TAG_UNTRUSTED_HUNK}>" in result
        assert f"</{TAG_UNTRUSTED_HUNK}>" in result

    def test__build_prompt_wraps_user_request_in_boundary_tags(self):
        comment = _make_comment()

        result = _build_prompt(event=comment, user_prompt="do something")

        assert f"<{TAG_UNTRUSTED_REQUEST}>" in result
        assert f"</{TAG_UNTRUSTED_REQUEST}>" in result
        assert "do something" in result

    def test__build_prompt_wraps_branch_in_boundary_tags(self):
        comment = _make_comment(branch="feat/test")

        result = _build_prompt(event=comment, user_prompt="fix")

        assert f"<{TAG_BRANCH_NAME}>feat/test</{TAG_BRANCH_NAME}>" in result

    def test__build_prompt_wraps_file_path_in_boundary_tags(self):
        comment = _make_comment(file_path="src/app.py")

        result = _build_prompt(event=comment, user_prompt="fix")

        assert f"<{TAG_FILE_PATH}>src/app.py</{TAG_FILE_PATH}>" in result
