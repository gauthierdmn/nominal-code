# type: ignore
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.prompts import (
    TAG_BRANCH_NAME,
    TAG_FILE_PATH,
    TAG_UNTRUSTED_COMMENT,
    TAG_UNTRUSTED_DIFF,
    TAG_UNTRUSTED_REQUEST,
)
from nominal_code.agent.result import AgentResult
from nominal_code.config import CliAgentConfig, ReviewerConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.handlers.output import FALLBACK_MESSAGE
from nominal_code.handlers.review import (
    MAX_EXISTING_COMMENTS,
    REVIEWER_ALLOWED_TOOLS,
    ReviewResult,
    _build_reviewer_prompt,
    _format_existing_comments,
    review,
    run_and_post_review,
)
from nominal_code.models import (
    ChangedFile,
    EventType,
    FileStatus,
)
from nominal_code.platforms.base import CommentEvent, ExistingComment, PlatformName


def _make_config(allowed_users=None):
    config = MagicMock()
    config.allowed_users = frozenset(allowed_users or ["alice"])
    config.workspace = MagicMock()
    config.workspace.base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()
    config.prompts = MagicMock()
    config.prompts.coding_guidelines = "Use snake_case."
    config.prompts.language_guidelines = {"python": "Python style rules."}
    config.worker = None
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
    body="@claude-reviewer review this",
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
    platform.build_clone_url = MagicMock(
        return_value="https://ro-token@github.com/owner/repo.git",
    )

    return platform


class TestReviewerProcessComment:
    @pytest.mark.asyncio
    async def test_reviewer_calls_fetch_pr_diff(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-old\n+new",
                ),
            ],
        )
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                await run_and_post_review(
                    event=comment,
                    prompt="review this",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            platform.fetch_pr_diff.assert_called_once_with(
                repo_full_name="owner/repo",
                pr_number=42,
            )

    @pytest.mark.asyncio
    async def test_reviewer_uses_reviewer_system_prompt(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs

            assert "Review code." in call_kwargs["system_prompt"]
            assert call_kwargs["allowed_tools"] == REVIEWER_ALLOWED_TOOLS

    @pytest.mark.asyncio
    async def test_reviewer_uses_resolve_coding_guidelines(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                    await run_and_post_review(
                        event=comment,
                        prompt="review",
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

    @pytest.mark.asyncio
    async def test_reviewer_calls_submit_review(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch=(
                        "@@ -8,6 +8,7 @@\n context\n context"
                        "\n+new line\n context\n context\n context"
                    ),
                ),
            ],
        )
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Found issues",
                "comments": [
                    {"path": "src/main.py", "line": 10, "body": "Bug here"},
                ],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            platform.submit_review.assert_called_once()
            call_kwargs = platform.submit_review.call_args.kwargs

            assert call_kwargs["summary"] == "Found issues"
            assert len(call_kwargs["findings"]) == 1
            assert call_kwargs["findings"][0].file_path == "src/main.py"

    @pytest.mark.asyncio
    async def test_reviewer_repair_on_invalid_json(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        valid_json = json.dumps(
            {
                "summary": "Fixed output",
                "comments": [],
            }
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_cli_agent",
                new_callable=AsyncMock,
            ) as mock_tracking_run,
            patch(
                "nominal_code.handlers.output.invoke_agent",
                new_callable=AsyncMock,
            ) as mock_repair_run,
        ):
            mock_tracking_run.return_value = AgentResult(
                output="not valid json",
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                conversation_id="sess-1",
            )
            mock_repair_run.return_value = AgentResult(
                output=valid_json,
                is_error=False,
                num_turns=1,
                duration_ms=200,
            )

            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            assert mock_tracking_run.call_count == 1
            assert mock_repair_run.call_count == 1
            platform.submit_review.assert_not_called()
            platform.post_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_reviewer_fallback_after_exhausted_repair(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        bad_result = AgentResult(
            output="still not json",
            is_error=False,
            num_turns=1,
            duration_ms=500,
            conversation_id="sess-1",
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_cli_agent",
                new_callable=AsyncMock,
            ) as mock_tracking_run,
            patch(
                "nominal_code.handlers.output.invoke_agent",
                new_callable=AsyncMock,
            ) as mock_repair_run,
        ):
            mock_tracking_run.return_value = bad_result
            mock_repair_run.return_value = bad_result

            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            assert mock_tracking_run.call_count == 1
            assert mock_repair_run.call_count == 2
            platform.submit_review.assert_not_called()
            platform.post_reply.assert_called_once()

            reply_body = platform.post_reply.call_args.kwargs["reply"].body

            assert reply_body == FALLBACK_MESSAGE


class TestBuildReviewerPrompt:
    def test__build_reviewer_prompt_includes_changed_files(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1 +1 @@\n-old\n+new",
            ),
            ChangedFile(
                file_path="src/utils.py",
                status=FileStatus.ADDED,
                patch="@@ -0,0 +1 @@\n+line",
            ),
        ]
        result = _build_reviewer_prompt(
            event=comment, user_prompt="focus on security", changed_files=changed_files
        )

        assert "src/main.py" in result
        assert "modified" in result
        assert "src/utils.py" in result
        assert "added" in result
        assert "focus on security" in result
        assert "-old" in result
        assert "+new" in result
        assert f"<{TAG_FILE_PATH}>" in result
        assert f"<{TAG_UNTRUSTED_DIFF}>" in result
        assert f"<{TAG_UNTRUSTED_REQUEST}>" in result

    def test__build_reviewer_prompt_with_deps_path(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = _build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            deps_path=Path("/tmp/.deps"),
        )

        assert "Dependencies directory: /tmp/.deps" in result
        assert "git clone" in result
        assert "--depth=1" in result

    def test__build_reviewer_prompt_without_deps_path(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = _build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert "Dependencies directory" not in result

    def test__build_reviewer_prompt_no_patch(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="binary.png", status=FileStatus.ADDED, patch=""),
        ]
        result = _build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert "binary.png" in result
        assert "no patch available" in result


class TestBuildReviewerPromptWithExistingComments:
    def test__build_reviewer_prompt_includes_existing_comments(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        existing = [
            ExistingComment(
                author="alice",
                body="Bug on this line",
                file_path="a.py",
                line=10,
                created_at="2026-01-01T10:00:00Z",
            ),
        ]
        result = _build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            existing_comments=existing,
        )

        assert "Existing discussions" in result
        assert "@alice" in result
        assert f"`<{TAG_FILE_PATH}>a.py</{TAG_FILE_PATH}>:10`" in result
        assert "Bug on this line" in result
        assert f"<{TAG_UNTRUSTED_COMMENT}>" in result

    def test__build_reviewer_prompt_no_existing_comments_omits_section(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        result = _build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert "Existing discussions" not in result

    def test__build_reviewer_prompt_empty_existing_comments_omits_section(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        result = _build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            existing_comments=[],
        )

        assert "Existing discussions" not in result

    def test__build_reviewer_prompt_resolved_comment_tagged(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        existing = [
            ExistingComment(
                author="bob",
                body="Fixed now",
                is_resolved=True,
                created_at="2026-01-01T10:00:00Z",
            ),
        ]
        result = _build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            existing_comments=existing,
        )

        assert "(resolved)" in result

    def test__build_reviewer_prompt_top_level_comment_no_location(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        existing = [
            ExistingComment(
                author="alice",
                body="General comment",
                created_at="2026-01-01T10:00:00Z",
            ),
        ]
        result = _build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            existing_comments=existing,
        )

        assert "**@alice**\n" in result
        assert "General comment" in result


class TestBotCommentFiltering:
    @pytest.mark.asyncio
    async def test_reviewer_filters_bot_comments(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_comments = AsyncMock(
            return_value=[
                ExistingComment(
                    author="claude-reviewer",
                    body="Bot's own review",
                    created_at="2026-01-01T09:00:00Z",
                ),
                ExistingComment(
                    author="alice",
                    body="Human comment",
                    created_at="2026-01-01T10:00:00Z",
                ),
            ],
        )
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            call_kwargs = mock_run.call_args.kwargs
            prompt_text = call_kwargs["prompt"]

            assert "Human comment" in prompt_text
            assert "Bot's own review" not in prompt_text

    @pytest.mark.asyncio
    async def test_reviewer_caps_existing_comments(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_comments = AsyncMock(
            return_value=[
                ExistingComment(
                    author="alice",
                    body=f"Comment {idx}",
                    created_at=f"2026-01-01T{idx:02d}:00:00Z",
                )
                for idx in range(80)
            ],
        )
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            call_kwargs = mock_run.call_args.kwargs
            prompt_text = call_kwargs["prompt"]
            comment_count = prompt_text.count("**@alice**")

            assert comment_count == MAX_EXISTING_COMMENTS

    @pytest.mark.asyncio
    async def test_reviewer_fetches_diff_comments_and_workspace_in_parallel(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        call_order = []

        async def track_fetch_diff(repo_full_name, pr_number):
            call_order.append("fetch_pr_diff")
            return []

        async def track_fetch_comments(repo_full_name, pr_number):
            call_order.append("fetch_pr_comments")
            return []

        async def track_ensure_ready():
            call_order.append("ensure_ready")

        platform.fetch_pr_diff = AsyncMock(side_effect=track_fetch_diff)
        platform.fetch_pr_comments = AsyncMock(side_effect=track_fetch_comments)

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                conversation_id="sess-1",
            )

            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock(side_effect=track_ensure_ready)
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                with patch(
                    "nominal_code.handlers.review.asyncio.gather",
                    wraps=asyncio.gather,
                ) as mock_gather:
                    await run_and_post_review(
                        event=comment,
                        prompt="review",
                        config=config,
                        platform=platform,
                        conversation_store=conversation_store,
                    )

                    mock_gather.assert_called_once()
                    gather_args = mock_gather.call_args.args

                    assert len(gather_args) == 3

            platform.fetch_pr_diff.assert_called_once_with(
                repo_full_name="owner/repo",
                pr_number=42,
            )
            platform.fetch_pr_comments.assert_called_once_with(
                repo_full_name="owner/repo",
                pr_number=42,
            )
            mock_ws.ensure_ready.assert_called_once()

            expected = {"fetch_pr_diff", "fetch_pr_comments", "ensure_ready"}

            assert set(call_order) == expected


class TestReview:
    @pytest.mark.asyncio
    async def test_review_returns_result(self):
        config = _make_config()
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
                ),
            ],
        )
        comment = _make_comment()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [
                    {"path": "src/main.py", "line": 2, "body": "Nice addition"},
                ],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                result = await review(
                    event=comment,
                    prompt="review this",
                    config=config,
                    platform=platform,
                )

        assert isinstance(result, ReviewResult)
        assert result.agent_review is not None
        assert result.agent_review.summary == "Looks good"
        assert len(result.valid_findings) == 1
        assert result.valid_findings[0].file_path == "src/main.py"
        assert result.effective_summary == "Looks good"

    @pytest.mark.asyncio
    async def test_review_returns_none_result_on_bad_json(self):
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()

        bad_result = AgentResult(
            output="not json",
            is_error=False,
            num_turns=1,
            duration_ms=500,
            conversation_id="sess-1",
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_cli_agent",
                new_callable=AsyncMock,
                return_value=bad_result,
            ),
            patch(
                "nominal_code.handlers.output.invoke_agent",
                new_callable=AsyncMock,
                return_value=bad_result,
            ),
            patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class,
        ):
            mock_ws = MagicMock()
            mock_ws.ensure_ready = AsyncMock()
            mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
            mock_ws_class.return_value = mock_ws

            result = await review(
                event=comment,
                prompt="review",
                config=config,
                platform=platform,
            )

        assert result.agent_review is None
        assert result.raw_output == FALLBACK_MESSAGE

    @pytest.mark.asyncio
    async def test_review_without_conversation_store(self):
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()

        review_json = json.dumps(
            {
                "summary": "OK",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
                is_error=False,
                num_turns=1,
                duration_ms=500,
                conversation_id="sess-1",
            )

            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                result = await review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=None,
                )

        assert result.agent_review is not None
        assert result.agent_review.summary == "OK"

    @pytest.mark.asyncio
    async def test_run_and_post_review_still_works(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-old\n+new",
                ),
            ],
        )
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

                await run_and_post_review(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            platform.post_reply.assert_called_once()
            reply_body = platform.post_reply.call_args.kwargs["reply"].body

            assert reply_body == "Looks good"


class TestFormatExistingComments:
    def test_format_existing_comments_empty_list(self):

        result = _format_existing_comments(comments=[])

        assert "## Existing discussions" in result

    def test_format_existing_comments_includes_author(self):
        comments = [ExistingComment(author="alice", body="Looks good!", created_at="")]

        result = _format_existing_comments(comments=comments)

        assert "alice" in result
        assert "Looks good!" in result
        assert f"<{TAG_UNTRUSTED_COMMENT}>" in result

    def test_format_existing_comments_includes_file_path(self):
        comments = [
            ExistingComment(
                author="bob",
                body="Fix this.",
                file_path="src/main.py",
                line=10,
                created_at="",
            )
        ]

        result = _format_existing_comments(comments=comments)

        assert "src/main.py" in result
        assert "10" in result

    def test_format_existing_comments_marks_resolved(self):
        comments = [
            ExistingComment(
                author="alice",
                body="Already fixed.",
                is_resolved=True,
                created_at="",
            )
        ]

        result = _format_existing_comments(comments=comments)

        assert "resolved" in result

    def test_format_existing_comments_top_level_comment_no_file_shown(self):
        comments = [
            ExistingComment(author="alice", body="LGTM", file_path="", created_at="")
        ]

        result = _format_existing_comments(comments=comments)

        assert "alice" in result
        assert "LGTM" in result


class TestPromptBoundaryTags:
    def test__build_reviewer_prompt_wraps_diff_in_boundary_tags(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1 +1 @@\n-old\n+new",
            ),
        ]

        result = _build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert f"<{TAG_UNTRUSTED_DIFF}>" in result
        assert f"</{TAG_UNTRUSTED_DIFF}>" in result

    def test__build_reviewer_prompt_wraps_user_prompt_in_boundary_tags(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]

        result = _build_reviewer_prompt(
            event=comment, user_prompt="check security", changed_files=changed_files
        )

        assert f"<{TAG_UNTRUSTED_REQUEST}>" in result
        assert f"</{TAG_UNTRUSTED_REQUEST}>" in result
        assert "check security" in result

    def test__build_reviewer_prompt_wraps_branch_in_boundary_tags(self):
        comment = _make_comment(branch="feat/evil-branch")
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]

        result = _build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert f"<{TAG_BRANCH_NAME}>feat/evil-branch</{TAG_BRANCH_NAME}>" in result

    def test__build_reviewer_prompt_wraps_file_paths_in_boundary_tags(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]

        result = _build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert f"<{TAG_FILE_PATH}>src/main.py</{TAG_FILE_PATH}>" in result

    def test__format_existing_comments_wraps_body_in_boundary_tags(self):
        comments = [
            ExistingComment(author="alice", body="Nice work!", created_at=""),
        ]

        result = _format_existing_comments(comments=comments)

        assert f"<{TAG_UNTRUSTED_COMMENT}>" in result
        assert f"</{TAG_UNTRUSTED_COMMENT}>" in result
        assert "Nice work!" in result

    def test__format_existing_comments_wraps_file_path_in_boundary_tags(self):
        comments = [
            ExistingComment(
                author="bob",
                body="Fix this.",
                file_path="src/main.py",
                line=5,
                created_at="",
            ),
        ]

        result = _format_existing_comments(comments=comments)

        assert f"<{TAG_FILE_PATH}>src/main.py</{TAG_FILE_PATH}>" in result
