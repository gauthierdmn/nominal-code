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
from nominal_code.models import (
    ChangedFile,
    ErrorType,
    EventType,
    FileStatus,
    InvocationError,
)
from nominal_code.platforms.base import CommentEvent, ExistingComment, PlatformName
from nominal_code.review.output import FALLBACK_MESSAGE
from nominal_code.review.prompts import (
    build_codebase_reviewer_prompt,
    build_reviewer_prompt,
    format_existing_comments,
)
from nominal_code.review.reviewer import (
    MAX_EXISTING_COMMENTS,
    ReviewResult,
    ReviewScope,
    _prepare_review_context,
    review,
    run_and_post_review,
)


def _make_config(allowed_users=None):
    config = MagicMock()
    config.allowed_users = frozenset(allowed_users or ["alice"])
    config.workspace = MagicMock()
    config.workspace.base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig(system_prompt="Review code.")
    config.prompts = MagicMock()
    config.prompts.coding_guidelines = "Use snake_case."
    config.prompts.language_guidelines = {"python": "Python style rules."}
    config.worker = None
    config.dry_run = False
    config.ignore_existing_comments = False
    config.reviewer = ReviewerConfig(
        bot_username="claude-reviewer",
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
    from nominal_code.platforms.base import PullRequestMetadata

    platform = MagicMock()
    platform.name = "github"
    platform.post_reaction = AsyncMock()
    platform.post_reply = AsyncMock()
    platform.fetch_pr_branch = AsyncMock(return_value="")
    platform.fetch_pr_diff = AsyncMock(
        return_value=[
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1 +1 @@\n-old\n+new",
            ),
        ],
    )
    platform.fetch_pr_comments = AsyncMock(return_value=[])
    platform.fetch_pr_metadata = AsyncMock(return_value=PullRequestMetadata())
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
    async def test_reviewer_skips_fetch_pr_comments_when_configured_to_ignore(self):
        config = _make_config(allowed_users=["alice"])
        config.ignore_existing_comments = True
        platform = _make_platform()
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        review_json = json.dumps({"summary": "Looks good", "comments": []})

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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

        platform.fetch_pr_comments.assert_not_called()

    @pytest.mark.asyncio
    async def test_reviewer_raises_when_fetch_pr_diff_returns_empty(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(return_value=[])
        comment = _make_comment(author="alice")
        conversation_store = MemoryConversationStore()

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            with patch(
                "nominal_code.workspace.setup.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = Path("/tmp/workspaces/owner/repo/pr-42")
                mock_ws_class.return_value = mock_ws

                with pytest.raises(ValueError, match="no changed files"):
                    await run_and_post_review(
                        event=comment,
                        prompt="review",
                        config=config,
                        platform=platform,
                        conversation_store=conversation_store,
                    )

            # LLM must never be called when the diff is empty.
            mock_run.assert_not_called()

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
            assert "Read" in call_kwargs["allowed_tools"]
            assert "Glob" in call_kwargs["allowed_tools"]
            assert "Grep" in call_kwargs["allowed_tools"]

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
                    "nominal_code.review.reviewer.resolve_guidelines",
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
                        file_paths=[Path("src/main.py")],
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
                "nominal_code.review.output.invoke_agent",
                new_callable=AsyncMock,
            ) as mock_repair_run,
        ):
            mock_tracking_run.return_value = AgentResult(
                output="not valid json",
                num_turns=1,
                duration_ms=1000,
                conversation_id="sess-1",
            )
            mock_repair_run.return_value = AgentResult(
                output=valid_json,
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
                "nominal_code.review.output.invoke_agent",
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
    def test_build_reviewer_prompt_includes_changed_files(self):
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
        result = build_reviewer_prompt(
            event=comment, user_prompt="focus on security", changed_files=changed_files
        )

        assert "src/main.py" in result
        assert "modified" in result
        assert "src/utils.py" in result
        assert "added" in result
        assert "focus on security" in result
        assert "-1:old" in result
        assert "+1:new" in result
        assert f"<{TAG_FILE_PATH}>" in result
        assert f"<{TAG_UNTRUSTED_DIFF}>" in result
        assert f"<{TAG_UNTRUSTED_REQUEST}>" in result

    def test_build_reviewer_prompt_without_context(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert "Callers" not in result

    def test_build_reviewer_prompt_no_patch(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="binary.png", status=FileStatus.ADDED, patch=""),
        ]
        result = build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert "binary.png" in result
        assert "no patch available" in result

    def test_build_reviewer_prompt_includes_context(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            context="## Exploration\n\nFound 3 callers of changed function.",
        )

        assert "## Exploration" in result
        assert "Found 3 callers" in result

    def test_build_reviewer_prompt_empty_context_omitted(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            context="",
        )

        assert "Exploration" not in result

    def test_build_reviewer_prompt_context_before_review_instruction(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            context="CONTEXT_MARKER",
        )

        context_pos = result.index("CONTEXT_MARKER")
        instruction_pos = result.index("Review the above changes")
        assert context_pos < instruction_pos

    def test_build_reviewer_prompt_inline_suggestions_appended(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            inline_suggestions=True,
        )

        assert "suggestion" in result
        assert "replacement code" in result

    def test_build_reviewer_prompt_no_inline_suggestions(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            inline_suggestions=False,
        )

        assert "replacement code" not in result

    def test_build_reviewer_prompt_includes_base_branch(self):
        comment = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="feature",
            base_branch="main",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=1,
            author_username="alice",
            body="review",
        )
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
        )

        assert "-> main" in result

    def test_build_reviewer_prompt_omits_base_branch_when_empty(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
        )

        assert "Base branch" not in result


class TestBuildReviewerPromptWithExistingComments:
    def test_build_reviewer_prompt_includes_existing_comments(self):
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
        result = build_reviewer_prompt(
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

    def test_build_reviewer_prompt_no_existing_comments_omits_section(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        result = build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert "Existing discussions" not in result

    def test_build_reviewer_prompt_empty_existing_comments_omits_section(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            existing_comments=[],
        )

        assert "Existing discussions" not in result

    def test_build_reviewer_prompt_resolved_comment_tagged(self):
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
        result = build_reviewer_prompt(
            event=comment,
            user_prompt="",
            changed_files=changed_files,
            existing_comments=existing,
        )

        assert "(resolved)" in result

    def test_build_reviewer_prompt_top_level_comment_no_location(self):
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
        result = build_reviewer_prompt(
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
            return [
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-old\n+new",
                ),
            ]

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
                    "nominal_code.review.reviewer.asyncio.gather",
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

                    assert len(gather_args) == 4

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
                "nominal_code.review.output.invoke_agent",
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
        # Agent returned successfully but its output couldn't be
        # parsed → PARSE_ERROR (no underlying provider/runtime cause).
        assert result.error.type == ErrorType.PARSE_ERROR
        assert "could not be parsed" in result.error.message.lower()

    @pytest.mark.asyncio
    async def test_review_propagates_provider_error_from_agent(self):
        """Provider failure flows: ``raw_output`` stays as the generic
        fallback comment (so the PR doesn't see "API error: 503"), but
        ``error_type``/``error_message`` carry the underlying cause for
        callers to consume internally."""
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()

        provider_failure = AgentResult(
            output="API error: 503 UNAVAILABLE",
            num_turns=2,
            duration_ms=500,
            conversation_id="sess-1",
            error=InvocationError(
                type=ErrorType.PROVIDER_ERROR,
                message="503 UNAVAILABLE",
            ),
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_cli_agent",
                new_callable=AsyncMock,
                return_value=provider_failure,
            ),
            patch(
                "nominal_code.review.output.invoke_agent",
                new_callable=AsyncMock,
                return_value=provider_failure,
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

        # User-facing comment is the generic fallback, NOT the API error.
        assert result.raw_output == FALLBACK_MESSAGE
        assert "API error" not in result.raw_output
        # Internal-only structured fields carry the underlying cause.
        assert result.error.type == ErrorType.PROVIDER_ERROR
        assert result.error.message == "503 UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_review_propagates_runtime_error_from_agent(self):
        """Runtime failures (bugs in tool dispatch, etc.) get a distinct
        classification so they can be alerted on separately from
        provider flakes."""
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()

        runtime_failure = AgentResult(
            output="Unexpected error: KeyError: 'foo'",
            num_turns=0,
            duration_ms=100,
            conversation_id="sess-1",
            error=InvocationError(
                type=ErrorType.RUNTIME_ERROR,
                message="KeyError: 'foo'",
            ),
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_cli_agent",
                new_callable=AsyncMock,
                return_value=runtime_failure,
            ),
            patch(
                "nominal_code.review.output.invoke_agent",
                new_callable=AsyncMock,
                return_value=runtime_failure,
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

        assert result.raw_output == FALLBACK_MESSAGE
        assert result.error.type == ErrorType.RUNTIME_ERROR
        assert result.error.message == "KeyError: 'foo'"

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
    def testformat_existing_comments_empty_list(self):

        result = format_existing_comments(comments=[])

        assert "## Existing discussions" in result

    def testformat_existing_comments_includes_author(self):
        comments = [ExistingComment(author="alice", body="Looks good!", created_at="")]

        result = format_existing_comments(comments=comments)

        assert "alice" in result
        assert "Looks good!" in result
        assert f"<{TAG_UNTRUSTED_COMMENT}>" in result

    def testformat_existing_comments_includes_file_path(self):
        comments = [
            ExistingComment(
                author="bob",
                body="Fix this.",
                file_path="src/main.py",
                line=10,
                created_at="",
            )
        ]

        result = format_existing_comments(comments=comments)

        assert "src/main.py" in result
        assert "10" in result

    def testformat_existing_comments_marks_resolved(self):
        comments = [
            ExistingComment(
                author="alice",
                body="Already fixed.",
                is_resolved=True,
                created_at="",
            )
        ]

        result = format_existing_comments(comments=comments)

        assert "resolved" in result

    def testformat_existing_comments_top_level_comment_no_file_shown(self):
        comments = [
            ExistingComment(author="alice", body="LGTM", file_path="", created_at="")
        ]

        result = format_existing_comments(comments=comments)

        assert "alice" in result
        assert "LGTM" in result


class TestPromptBoundaryTags:
    def test_build_reviewer_prompt_wraps_diff_in_boundary_tags(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1 +1 @@\n-old\n+new",
            ),
        ]

        result = build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert f"<{TAG_UNTRUSTED_DIFF}>" in result
        assert f"</{TAG_UNTRUSTED_DIFF}>" in result

    def test_build_reviewer_prompt_wraps_user_prompt_in_boundary_tags(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]

        result = build_reviewer_prompt(
            event=comment, user_prompt="check security", changed_files=changed_files
        )

        assert f"<{TAG_UNTRUSTED_REQUEST}>" in result
        assert f"</{TAG_UNTRUSTED_REQUEST}>" in result
        assert "check security" in result

    def test_build_reviewer_prompt_wraps_branch_in_boundary_tags(self):
        comment = _make_comment(branch="feat/evil-branch")
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]

        result = build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert f"<{TAG_BRANCH_NAME}>feat/evil-branch</{TAG_BRANCH_NAME}>" in result

    def test_build_reviewer_prompt_wraps_file_paths_in_boundary_tags(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]

        result = build_reviewer_prompt(
            event=comment, user_prompt="", changed_files=changed_files
        )

        assert f"<{TAG_FILE_PATH}>src/main.py</{TAG_FILE_PATH}>" in result

    def test_format_existing_comments_wraps_body_in_boundary_tags(self):
        comments = [
            ExistingComment(author="alice", body="Nice work!", created_at=""),
        ]

        result = format_existing_comments(comments=comments)

        assert f"<{TAG_UNTRUSTED_COMMENT}>" in result
        assert f"</{TAG_UNTRUSTED_COMMENT}>" in result
        assert "Nice work!" in result

    def test_format_existing_comments_wraps_file_path_in_boundary_tags(self):
        comments = [
            ExistingComment(
                author="bob",
                body="Fix this.",
                file_path="src/main.py",
                line=5,
                created_at="",
            ),
        ]

        result = format_existing_comments(comments=comments)

        assert f"<{TAG_FILE_PATH}>src/main.py</{TAG_FILE_PATH}>" in result


class TestAgenticReviewer:
    @pytest.mark.asyncio
    async def test_review_uses_agentic_flow_for_api_agent(self):
        from nominal_code.config import AgentRoleConfig, ApiAgentConfig
        from nominal_code.models import ProviderName

        config = _make_config()
        config.agent = ApiAgentConfig(
            reviewer=AgentRoleConfig(
                name=ProviderName.GOOGLE,
                model="gemini-2.5-pro",
                system_prompt="Review code.",
            ),
            explorer=AgentRoleConfig(
                name=ProviderName.GOOGLE,
                model="gemini-2.5-flash",
                system_prompt="Explore code.",
            ),
        )

        mock_agent_result = MagicMock()
        mock_agent_result.output = '{"summary": "LGTM", "findings": []}'
        mock_agent_result.cost = None
        mock_agent_result.num_turns = 3
        mock_agent_result.duration_ms = 5000
        mock_agent_result.messages = ()
        mock_agent_result.is_error = False
        mock_agent_result.conversation_id = None
        mock_agent_result.exhausted_without_review = False

        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock()

        event = _make_comment()
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="a.py",
                    status=FileStatus.MODIFIED,
                    patch="+new",
                ),
            ],
        )

        with (
            patch(
                "nominal_code.review.reviewer.create_provider",
                return_value=mock_provider,
            ),
            patch(
                "nominal_code.review.reviewer.invoke_agent",
                new_callable=AsyncMock,
                return_value=mock_agent_result,
            ),
        ):
            result = await review(
                event=event,
                prompt="",
                config=config,
                platform=platform,
                workspace_path="/tmp/repo",
            )

        assert result.agent_review is not None
        assert result.agent_review.summary == "LGTM"
        assert result.num_turns == 3

    @pytest.mark.asyncio
    async def test_fallback_review_on_exhausted_turns(self):
        from nominal_code.config import AgentRoleConfig, ApiAgentConfig
        from nominal_code.models import ProviderName

        config = _make_config()
        config.agent = ApiAgentConfig(
            reviewer=AgentRoleConfig(
                name=ProviderName.GOOGLE,
                model="gemini-2.5-pro",
                system_prompt="Review code.",
            ),
            explorer=AgentRoleConfig(
                name=ProviderName.GOOGLE,
                model="gemini-2.5-flash",
                system_prompt="Explore code.",
            ),
        )

        exhausted_result = MagicMock()
        exhausted_result.output = "Max turns reached."
        exhausted_result.cost = None
        exhausted_result.num_turns = 8
        exhausted_result.duration_ms = 10000
        exhausted_result.messages = ()
        exhausted_result.is_error = False
        exhausted_result.conversation_id = None
        exhausted_result.exhausted_without_review = True

        fallback_result = MagicMock()
        fallback_result.output = '{"summary": "Fallback review", "findings": []}'
        fallback_result.cost = None
        fallback_result.num_turns = 1
        fallback_result.duration_ms = 2000
        fallback_result.messages = ()
        fallback_result.is_error = False
        fallback_result.conversation_id = None
        fallback_result.exhausted_without_review = False

        mock_provider = AsyncMock()
        mock_provider.close = AsyncMock()

        event = _make_comment()
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="a.py",
                    status=FileStatus.MODIFIED,
                    patch="+new",
                ),
            ],
        )

        with (
            patch(
                "nominal_code.review.reviewer.create_provider",
                return_value=mock_provider,
            ),
            patch(
                "nominal_code.review.reviewer.invoke_agent",
                new_callable=AsyncMock,
                side_effect=[exhausted_result, fallback_result],
            ) as mock_invoke,
        ):
            result = await review(
                event=event,
                prompt="",
                config=config,
                platform=platform,
                workspace_path="/tmp/repo",
            )

        assert mock_invoke.call_count == 2
        assert result.agent_review is not None
        assert result.agent_review.summary == "Fallback review"


class TestCodebaseScopeReview:
    @pytest.mark.asyncio
    async def test_codebase_scope_requires_workspace_path(self):
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()

        with pytest.raises(ValueError, match="workspace_path is required"):
            await review(
                event=comment,
                prompt="",
                config=config,
                platform=platform,
                scope=ReviewScope.CODEBASE,
            )

    @pytest.mark.asyncio
    async def test_codebase_scope_skips_platform_api_calls(self):
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()
        review_json = json.dumps({"summary": "All good", "comments": []})

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
                num_turns=1,
                duration_ms=500,
                conversation_id="sess-audit",
            )

            await review(
                event=comment,
                prompt="",
                config=config,
                platform=platform,
                workspace_path="/tmp/repo",
                scope=ReviewScope.CODEBASE,
            )

        platform.fetch_pr_diff.assert_not_called()
        platform.fetch_pr_comments.assert_not_called()
        platform.fetch_pr_metadata.assert_not_called()

    @pytest.mark.asyncio
    async def test_codebase_scope_all_findings_valid(self):
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment()

        review_json = json.dumps(
            {
                "summary": "Found issues",
                "comments": [
                    {
                        "path": "untracked/new_file.py",
                        "line": 10,
                        "body": "Missing error handling",
                        "side": "RIGHT",
                    }
                ],
            }
        )

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
                num_turns=1,
                duration_ms=500,
                conversation_id="sess-audit",
            )

            result = await review(
                event=comment,
                prompt="",
                config=config,
                platform=platform,
                workspace_path="/tmp/repo",
                scope=ReviewScope.CODEBASE,
            )

        assert len(result.valid_findings) == 1
        assert result.valid_findings[0].file_path == "untracked/new_file.py"
        assert result.rejected_findings == []

    @pytest.mark.asyncio
    async def test_codebase_scope_prompt_header(self):
        config = _make_config()
        platform = _make_platform()
        comment = _make_comment(repo="owner/myrepo", branch="main")
        review_json = json.dumps({"summary": "Done", "comments": []})

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
                num_turns=1,
                duration_ms=500,
                conversation_id="sess-audit",
            )

            await review(
                event=comment,
                prompt="",
                config=config,
                platform=platform,
                workspace_path="/tmp/repo",
                scope=ReviewScope.CODEBASE,
            )

        captured_prompt = mock_run.call_args[1]["prompt"]
        assert captured_prompt.startswith("## Codebase review: owner/myrepo")
        assert "Pull request" not in captured_prompt
        assert "## Changed files" not in captured_prompt

    def test_build_codebase_reviewer_prompt_structure(self):
        from nominal_code.models import EventType
        from nominal_code.platforms.base import CommentEvent, PlatformName

        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="acme/backend",
            pr_number=0,
            pr_branch="main",
            clone_url="",
            event_type=EventType.PR_OPENED,
            comment_id=0,
            author_username="",
            body="",
        )

        prompt = build_codebase_reviewer_prompt(event=event, user_prompt="")

        assert prompt.startswith("## Codebase review: acme/backend")
        assert "main" in prompt
        assert "Pull request" not in prompt
        assert "## Changed files" not in prompt

    def test_build_codebase_reviewer_prompt_with_user_instructions(self):
        from nominal_code.models import EventType
        from nominal_code.platforms.base import CommentEvent, PlatformName

        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="acme/backend",
            pr_number=0,
            pr_branch="main",
            clone_url="",
            event_type=EventType.PR_OPENED,
            comment_id=0,
            author_username="",
            body="",
        )

        prompt = build_codebase_reviewer_prompt(
            event=event,
            user_prompt="Focus on error handling",
        )

        assert "Focus on error handling" in prompt
        assert "Additional instructions" in prompt


def _make_lifecycle_event():
    from nominal_code.platforms.base import LifecycleEvent

    return LifecycleEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feat/x",
        event_type=EventType.PR_OPENED,
        clone_url="https://token@github.com/owner/repo.git",
        base_branch="main",
        pr_author="alice",
    )


class TestPrepareReviewContextIgnorePatterns:
    @pytest.mark.asyncio
    async def test_files_matching_patterns_are_excluded(self, tmp_path):
        config = _make_config(allowed_users=["alice"])
        config.reviewer = ReviewerConfig(
            bot_username="claude-reviewer",
            ignore_patterns=frozenset({"*.lock", "vendor/**"}),
        )
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="Cargo.lock",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
                ChangedFile(
                    file_path="vendor/foo.go",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
            ],
        )
        event = _make_lifecycle_event()

        review_context = await _prepare_review_context(
            event=event,
            config=config,
            platform=platform,
            workspace_path=str(tmp_path),
            bot_username="claude-reviewer",
        )

        kept_paths = [changed.file_path for changed in review_context.changed_files]
        assert kept_paths == ["src/main.py"]

    @pytest.mark.asyncio
    async def test_empty_patterns_keeps_all_files(self, tmp_path):
        config = _make_config(allowed_users=["alice"])
        config.reviewer = ReviewerConfig(
            bot_username="claude-reviewer",
            ignore_patterns=frozenset(),
        )
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="Cargo.lock",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
            ],
        )
        event = _make_lifecycle_event()

        review_context = await _prepare_review_context(
            event=event,
            config=config,
            platform=platform,
            workspace_path=str(tmp_path),
            bot_username="claude-reviewer",
        )

        kept_paths = [changed.file_path for changed in review_context.changed_files]
        assert kept_paths == ["Cargo.lock", "src/main.py"]

    @pytest.mark.asyncio
    async def test_excluded_files_logged_at_warning(self, tmp_path, caplog):
        import logging

        config = _make_config(allowed_users=["alice"])
        config.reviewer = ReviewerConfig(
            bot_username="claude-reviewer",
            ignore_patterns=frozenset({"*.lock"}),
        )
        platform = _make_platform()
        platform.fetch_pr_diff = AsyncMock(
            return_value=[
                ChangedFile(
                    file_path="Cargo.lock",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
                ChangedFile(
                    file_path="src/main.py",
                    status=FileStatus.MODIFIED,
                    patch="@@ -1 +1 @@\n-x\n+y\n",
                ),
            ],
        )
        event = _make_lifecycle_event()

        with caplog.at_level(logging.WARNING, logger="nominal_code.review.reviewer"):
            await _prepare_review_context(
                event=event,
                config=config,
                platform=platform,
                workspace_path=str(tmp_path),
                bot_username="claude-reviewer",
            )

        assert any(
            "Excluded 1 of 2 files by ignore_patterns" in record.message
            and "owner/repo#42" in record.message
            for record in caplog.records
        )
