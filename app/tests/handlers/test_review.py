# type: ignore
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.router import AgentResult
from nominal_code.config import CliAgentConfig, ReviewerConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.handlers.review import (
    FALLBACK_MESSAGE,
    MAX_EXISTING_COMMENTS,
    REVIEWER_ALLOWED_TOOLS,
    ReviewResult,
    _build_diff_index,
    _build_effective_summary,
    _build_fallback_comment,
    _build_reviewer_prompt,
    _extract_json_substring,
    _filter_findings,
    _format_existing_comments,
    _parse_diff_lines,
    _parse_finding,
    _repair_review_output,
    parse_review_output,
    review,
    review_and_post,
)
from nominal_code.models import (
    ChangedFile,
    DiffSide,
    EventType,
    FileStatus,
    ReviewFinding,
)
from nominal_code.platforms.base import CommentEvent, ExistingComment, PlatformName


def _make_config(allowed_users=None):
    config = MagicMock()
    config.allowed_users = frozenset(allowed_users or ["alice"])
    config.workspace_base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()
    config.coding_guidelines = "Use snake_case."
    config.language_guidelines = {"python": "Python style rules."}
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
    platform.build_reviewer_clone_url = MagicMock(
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
            "nominal_code.agent.cli.runner.run",
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

                await review_and_post(
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
            "nominal_code.agent.cli.runner.run",
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

                await review_and_post(
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
            "nominal_code.agent.cli.runner.run",
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
                    await review_and_post(
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
            "nominal_code.agent.cli.runner.run",
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

                await review_and_post(
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
                "nominal_code.agent.cli.runner.run",
                new_callable=AsyncMock,
            ) as mock_tracking_run,
            patch(
                "nominal_code.handlers.review.run",
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

                await review_and_post(
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
                "nominal_code.agent.cli.runner.run",
                new_callable=AsyncMock,
            ) as mock_tracking_run,
            patch(
                "nominal_code.handlers.review.run",
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

                await review_and_post(
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


class TestParseReviewOutput:
    def test_parse_review_output_valid_json(self):
        output = json.dumps(
            {
                "summary": "Looks good overall",
                "comments": [
                    {"path": "src/main.py", "line": 10, "body": "Bug here"},
                    {"path": "src/utils.py", "line": 5, "body": "Perf issue"},
                ],
            }
        )
        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "Looks good overall"
        assert len(result.findings) == 2
        assert result.findings[0].file_path == "src/main.py"
        assert result.findings[0].line == 10
        assert result.findings[1].body == "Perf issue"

    def test_parse_review_output_valid_json_empty_comments(self):
        output = json.dumps(
            {
                "summary": "No issues found",
                "comments": [],
            }
        )
        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "No issues found"
        assert result.findings == []

    def test_parse_review_output_malformed_json(self):
        result = parse_review_output(output="not json at all")

        assert result is None

    def test_parse_review_output_missing_summary(self):
        output = json.dumps({"comments": []})
        result = parse_review_output(output=output)

        assert result is None

    def test_parse_review_output_invalid_comment_missing_path(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [{"line": 10, "body": "test"}],
            }
        )
        result = parse_review_output(output=output)

        assert result is None

    def test_parse_review_output_invalid_comment_bad_line(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [{"path": "a.py", "line": -1, "body": "test"}],
            }
        )
        result = parse_review_output(output=output)

        assert result is None

    def test_parse_review_output_strips_markdown_fences(self):
        output = '```json\n{"summary": "Good", "comments": []}\n```'
        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "Good"

    def test_parse_review_output_not_a_dict(self):
        result = parse_review_output(output="[1, 2, 3]")

        assert result is None

    def test_parse_review_output_repairs_unescaped_quotes(self):
        broken = (
            '{"summary": "ok", "comments": '
            '[{"path": "f.py", "line": 1, "body": "use "foo" here"}]}'
        )

        result = parse_review_output(output=broken)

        assert result is not None
        assert result.summary == "ok"
        assert result.findings[0].body == 'use "foo" here'

    def test_parse_review_output_extracts_json_from_prose(self):
        output = 'Here is my review:\n{"summary": "Looks good", "comments": []}\nDone!'

        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "Looks good"

    def test_parse_review_output_repairs_trailing_comma(self):
        broken = (
            '{"summary": "ok", "comments": '
            '[{"path": "a.py", "line": 1, "body": "fix",}],}'
        )

        result = parse_review_output(output=broken)

        assert result is not None
        assert result.summary == "ok"

    def test_parse_review_output_empty_string(self):
        result = parse_review_output(output="")

        assert result is None

    def test_parse_review_output_repairs_suggestion_with_quotes(self):
        broken = (
            '{"summary": "Fix SQL", "comments": [{"path": "db.py", "line": 10, '
            '"body": "SQL injection", '
            '"suggestion": "query = "SELECT * FROM users WHERE id = ?""}]}'
        )

        result = parse_review_output(output=broken)

        assert result is not None
        assert result.findings[0].suggestion is not None
        assert "SELECT" in result.findings[0].suggestion

    def test_parse_review_output_left_side_finding(self):
        output = json.dumps(
            {
                "summary": "Found deletion issue",
                "comments": [
                    {
                        "path": "src/main.py",
                        "line": 5,
                        "body": "Removed code had a bug",
                        "side": "LEFT",
                    },
                ],
            }
        )
        result = parse_review_output(output=output)

        assert result is not None
        assert result.findings[0].side == DiffSide.LEFT

    def test_parse_review_output_invalid_side_returns_none(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [
                    {"path": "a.py", "line": 1, "body": "test", "side": "INVALID"},
                ],
            }
        )
        result = parse_review_output(output=output)

        assert result is None


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
        assert "`a.py:10`" in result
        assert "> Bug on this line" in result

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
        assert "> General comment" in result


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
            "nominal_code.agent.cli.runner.run",
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

                await review_and_post(
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
            "nominal_code.agent.cli.runner.run",
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

                await review_and_post(
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
            "nominal_code.agent.cli.runner.run",
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
                    await review_and_post(
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


class TestFilterFindings:
    def test_filter_findings_keeps_valid_in_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/main.py", line=2, body="Issue here"),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert len(rejected) == 0

    def test_filter_findings_rejects_line_outside_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/main.py", line=100, body="Not in diff"),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1

    def test_filter_findings_rejects_file_not_in_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/other.py", line=5, body="Not in PR"),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1

    def test_filter_findings_splits_mixed(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="src/main.py", line=1, body="Valid"),
            ReviewFinding(file_path="src/main.py", line=999, body="Invalid line"),
            ReviewFinding(file_path="src/other.py", line=5, body="Invalid file"),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert valid[0].body == "Valid"
        assert len(rejected) == 2

    def test_filter_findings_empty_findings(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1 +1 @@\n+new",
            ),
        ]
        valid, rejected = _filter_findings(findings=[], changed_files=changed_files)

        assert valid == []
        assert rejected == []

    def test_filter_findings_multiple_hunks(self):
        patch = (
            "@@ -1,3 +1,3 @@\n-old\n+new\n context\n context\n"
            "@@ -20,3 +20,4 @@\n context\n+added\n context\n context"
        )
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch=patch),
        ]
        findings = [
            ReviewFinding(file_path="a.py", line=1, body="In first hunk"),
            ReviewFinding(file_path="a.py", line=21, body="In second hunk"),
            ReviewFinding(file_path="a.py", line=10, body="Between hunks"),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 2
        assert len(rejected) == 1
        assert rejected[0].body == "Between hunks"

    def test_filter_findings_deletion_lines_on_left_side(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,2 @@\n context\n-deleted\n context",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="a.py",
                line=2,
                body="Deleted line comment",
                side=DiffSide.LEFT,
            ),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert len(rejected) == 0

    def test_filter_findings_rejects_left_finding_on_right_only_file(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.ADDED,
                patch="@@ -0,0 +1,3 @@\n+line one\n+line two\n+line three",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="a.py",
                line=1,
                body="No left side here",
                side=DiffSide.LEFT,
            ),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1

    def test_filter_findings_multiline_suggestion_fully_in_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,5 +1,5 @@\n context\n+line2\n+line3\n+line4\n context",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="src/main.py",
                line=4,
                body="Simplify",
                suggestion="simplified()",
                start_line=2,
            ),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 1
        assert len(rejected) == 0

    def test_filter_findings_multiline_suggestion_partially_outside_diff(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -10,3 +10,4 @@\n context\n+added\n context\n context",
            ),
        ]
        findings = [
            ReviewFinding(
                file_path="src/main.py",
                line=12,
                body="Simplify",
                suggestion="simplified()",
                start_line=8,
            ),
        ]
        valid, rejected = _filter_findings(
            findings=findings, changed_files=changed_files
        )

        assert len(valid) == 0
        assert len(rejected) == 1


class TestBuildEffectiveSummary:
    def test_build_effective_summary_no_rejected(self):
        result = _build_effective_summary(summary="All good", rejected_findings=[])

        assert result == "All good"

    def test_build_effective_summary_with_rejected(self):
        rejected = [
            ReviewFinding(file_path="src/other.py", line=5, body="Missing update"),
            ReviewFinding(file_path="src/utils.py", line=20, body="Stale reference"),
        ]
        result = _build_effective_summary(
            summary="Found issues", rejected_findings=rejected
        )

        assert result.startswith("Found issues")
        assert "Additional notes" in result
        assert "not in diff" in result
        assert "**src/other.py:5**" in result
        assert "Missing update" in result
        assert "**src/utils.py:20**" in result
        assert "Stale reference" in result

    def test_build_effective_summary_single_rejected(self):
        rejected = [
            ReviewFinding(file_path="a.py", line=1, body="Needs change"),
        ]
        result = _build_effective_summary(summary="Summary", rejected_findings=rejected)

        assert "**a.py:1**" in result
        assert "Needs change" in result


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
            "nominal_code.agent.cli.runner.run",
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
                "nominal_code.agent.cli.runner.run",
                new_callable=AsyncMock,
                return_value=bad_result,
            ),
            patch(
                "nominal_code.handlers.review.run",
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
            "nominal_code.agent.cli.runner.run",
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
    async def test_review_and_post_still_works(self):
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
            "nominal_code.agent.cli.runner.run",
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

                await review_and_post(
                    event=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    conversation_store=conversation_store,
                )

            platform.post_reply.assert_called_once()
            reply_body = platform.post_reply.call_args.kwargs["reply"].body

            assert reply_body == "Looks good"


class TestParseFinding:
    def test_parse_finding_valid(self):
        item = {"path": "src/main.py", "line": 10, "body": "Fix this"}

        result = _parse_finding(item=item)

        assert result.file_path == "src/main.py"
        assert result.line == 10
        assert result.body == "Fix this"

    def test_parse_finding_defaults_side_to_right(self):
        item = {"path": "src/main.py", "line": 5, "body": "Note"}

        result = _parse_finding(item=item)

        assert result.side == DiffSide.RIGHT

    def test_parse_finding_explicit_left_side(self):
        item = {"path": "src/main.py", "line": 5, "body": "Note", "side": "LEFT"}

        result = _parse_finding(item=item)

        assert result.side == DiffSide.LEFT

    def test_parse_finding_non_dict_raises(self):
        with pytest.raises(ValueError, match="not a dict"):
            _parse_finding(item="not a dict")

    def test_parse_finding_missing_path_raises(self):
        with pytest.raises(ValueError, match="invalid path"):
            _parse_finding(item={"line": 5, "body": "text"})

    def test_parse_finding_empty_path_raises(self):
        with pytest.raises(ValueError, match="invalid path"):
            _parse_finding(item={"path": "", "line": 5, "body": "text"})

    def test_parse_finding_non_string_path_raises(self):
        with pytest.raises(ValueError, match="invalid path"):
            _parse_finding(item={"path": 123, "line": 5, "body": "text"})

    def test_parse_finding_boolean_line_raises(self):
        with pytest.raises(ValueError, match="invalid line"):
            _parse_finding(item={"path": "src/main.py", "line": True, "body": "text"})

    def test_parse_finding_line_zero_raises(self):
        with pytest.raises(ValueError, match="invalid line"):
            _parse_finding(item={"path": "src/main.py", "line": 0, "body": "text"})

    def test_parse_finding_negative_line_raises(self):
        with pytest.raises(ValueError, match="invalid line"):
            _parse_finding(item={"path": "src/main.py", "line": -1, "body": "text"})

    def test_parse_finding_missing_body_raises(self):
        with pytest.raises(ValueError, match="invalid body"):
            _parse_finding(item={"path": "src/main.py", "line": 5})

    def test_parse_finding_empty_body_raises(self):
        with pytest.raises(ValueError, match="invalid body"):
            _parse_finding(item={"path": "src/main.py", "line": 5, "body": ""})

    def test_parse_finding_invalid_side_raises(self):
        with pytest.raises(ValueError, match="invalid side"):
            _parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 5,
                    "body": "text",
                    "side": "MIDDLE",
                }
            )

    def test_parse_finding_with_suggestion(self):
        item = {
            "path": "src/main.py",
            "line": 10,
            "body": "Use snake_case",
            "suggestion": "user_count = len(users)",
        }

        result = _parse_finding(item=item)

        assert result.suggestion == "user_count = len(users)"
        assert result.start_line is None

    def test_parse_finding_with_multiline_suggestion(self):
        item = {
            "path": "src/main.py",
            "line": 20,
            "body": "Simplify this",
            "suggestion": "if items:\n    process(items)",
            "start_line": 18,
        }

        result = _parse_finding(item=item)

        assert result.suggestion == "if items:\n    process(items)"
        assert result.start_line == 18

    def test_parse_finding_suggestion_empty_string_raises(self):
        with pytest.raises(ValueError, match="invalid suggestion"):
            _parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 10,
                    "body": "Fix",
                    "suggestion": "",
                }
            )

    def test_parse_finding_boolean_start_line_raises(self):
        with pytest.raises(ValueError, match="invalid start_line"):
            _parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 10,
                    "body": "Fix",
                    "suggestion": "new code",
                    "start_line": True,
                }
            )

    def test_parse_finding_suggestion_start_line_greater_than_line_raises(self):
        with pytest.raises(ValueError, match="start_line must be <= line"):
            _parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 5,
                    "body": "Fix",
                    "suggestion": "new code",
                    "start_line": 10,
                }
            )

    def test_parse_finding_start_line_without_suggestion(self):
        item = {
            "path": "src/main.py",
            "line": 24,
            "body": "Hardcoded credentials",
            "start_line": 20,
        }

        result = _parse_finding(item=item)

        assert result.start_line == 20
        assert result.suggestion is None

    def test_parse_finding_suggestion_on_left_side_raises(self):
        with pytest.raises(ValueError, match="suggestion not allowed on LEFT side"):
            _parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 5,
                    "body": "Fix",
                    "side": "LEFT",
                    "suggestion": "new code",
                }
            )


class TestParseReviewOutputWithSuggestions:
    def test_parse_review_output_with_suggestions(self):
        output = json.dumps(
            {
                "summary": "Found issues",
                "comments": [
                    {
                        "path": "src/main.py",
                        "line": 10,
                        "body": "Use snake_case",
                        "suggestion": "user_count = len(users)",
                    },
                    {
                        "path": "src/main.py",
                        "line": 20,
                        "body": "Simplify",
                        "suggestion": "if items:\n    process(items)",
                        "start_line": 18,
                    },
                ],
            }
        )

        result = parse_review_output(output=output)

        assert result is not None
        assert len(result.findings) == 2
        assert result.findings[0].suggestion == "user_count = len(users)"
        assert result.findings[0].start_line is None
        assert result.findings[1].suggestion == "if items:\n    process(items)"
        assert result.findings[1].start_line == 18


class TestExtractJsonSubstring:
    def test_extracts_json_from_prose(self):
        text = 'Here is the JSON: {"summary": "ok", "comments": []} done.'

        result = _extract_json_substring(text=text)

        assert result == '{"summary": "ok", "comments": []}'

    def test_returns_original_when_no_braces(self):
        result = _extract_json_substring(text="no json here")

        assert result == "no json here"

    def test_returns_original_when_only_open_brace(self):
        result = _extract_json_substring(text="just { open")

        assert result == "just { open"

    def test_handles_nested_braces(self):
        text = '{"outer": {"inner": 1}}'

        result = _extract_json_substring(text=text)

        assert result == '{"outer": {"inner": 1}}'

    def test_strips_markdown_around_json(self):
        text = 'Sure, here is the review:\n```json\n{"summary": "ok"}\n```'

        result = _extract_json_substring(text=text)

        assert result == '{"summary": "ok"}'

    def test_returns_original_for_empty_string(self):
        result = _extract_json_substring(text="")

        assert result == ""

    def test_returns_original_when_closing_before_opening(self):
        result = _extract_json_substring(text="} before {")

        assert result == "} before {"


class TestRepairReviewOutput:
    @pytest.mark.asyncio
    async def test_repair_succeeds_on_first_llm_attempt(self):
        config = _make_config()
        valid_json = json.dumps({"summary": "Fixed", "comments": []})

        with patch(
            "nominal_code.handlers.review.run",
            new_callable=AsyncMock,
            return_value=AgentResult(
                output=valid_json,
                is_error=False,
                num_turns=1,
                duration_ms=100,
            ),
        ) as mock_run:
            result = await _repair_review_output(
                broken_output="bad json", config=config, cwd=Path("/tmp")
            )

        assert result is not None
        assert result.summary == "Fixed"
        assert mock_run.call_count == 1

    @pytest.mark.asyncio
    async def test_repair_succeeds_on_second_llm_attempt(self):
        config = _make_config()
        valid_json = json.dumps({"summary": "Fixed", "comments": []})

        with patch(
            "nominal_code.handlers.review.run",
            new_callable=AsyncMock,
            side_effect=[
                AgentResult(
                    output="still broken",
                    is_error=False,
                    num_turns=1,
                    duration_ms=100,
                ),
                AgentResult(
                    output=valid_json,
                    is_error=False,
                    num_turns=1,
                    duration_ms=100,
                ),
            ],
        ) as mock_run:
            result = await _repair_review_output(
                broken_output="bad json", config=config, cwd=Path("/tmp")
            )

        assert result is not None
        assert result.summary == "Fixed"
        assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_repair_extracts_json_before_sending_to_llm(self):
        config = _make_config()
        valid_json = json.dumps({"summary": "Fixed", "comments": []})
        wrapped = 'Here is the review:\n{"summary": "broken}\nDone.'

        with patch(
            "nominal_code.handlers.review.run",
            new_callable=AsyncMock,
            return_value=AgentResult(
                output=valid_json,
                is_error=False,
                num_turns=1,
                duration_ms=100,
            ),
        ) as mock_run:
            await _repair_review_output(
                broken_output=wrapped, config=config, cwd=Path("/tmp")
            )

        prompt_sent = mock_run.call_args.kwargs["prompt"]

        assert "Here is the review" not in prompt_sent
        assert "Done." not in prompt_sent

    @pytest.mark.asyncio
    async def test_repair_returns_none_when_all_strategies_fail(self):
        config = _make_config()

        with patch(
            "nominal_code.handlers.review.run",
            new_callable=AsyncMock,
            return_value=AgentResult(
                output="gibberish",
                is_error=False,
                num_turns=1,
                duration_ms=100,
            ),
        ):
            result = await _repair_review_output(
                broken_output="total nonsense", config=config, cwd=Path("/tmp")
            )

        assert result is None


class TestBuildFallbackComment:
    def test_extracts_summary_from_broken_json(self):
        broken = '{"summary": "This PR has issues", "comments": [bad stuff}'

        result = _build_fallback_comment(raw_output=broken)

        assert "This PR has issues" in result
        assert "unable to produce inline review comments" in result

    def test_handles_escaped_quotes_in_summary(self):
        broken = '{"summary": "Found \\"critical\\" bugs", "comments": []bad'

        result = _build_fallback_comment(raw_output=broken)

        assert 'Found "critical" bugs' in result

    def test_summary_variant_includes_contact_admin(self):
        broken = '{"summary": "Has bugs", "comments": [bad}'

        result = _build_fallback_comment(raw_output=broken)

        assert "contact your administrator" in result

    def test_returns_generic_message_when_no_summary(self):
        result = _build_fallback_comment(raw_output="total nonsense")

        assert result == FALLBACK_MESSAGE

    def test_returns_generic_message_for_empty_output(self):
        result = _build_fallback_comment(raw_output="")

        assert result == FALLBACK_MESSAGE


class TestParseDiffLines:
    def test_parse_diff_lines_addition_lines_in_right(self):
        patch_text = "@@ -0,0 +1,3 @@\n+line one\n+line two\n+line three\n"

        result = _parse_diff_lines(patch=patch_text)

        assert 1 in result[DiffSide.RIGHT]
        assert 2 in result[DiffSide.RIGHT]
        assert 3 in result[DiffSide.RIGHT]

    def test_parse_diff_lines_deletion_lines_in_left(self):
        patch_text = "@@ -1,2 +1,0 @@\n-removed line 1\n-removed line 2\n"

        result = _parse_diff_lines(patch=patch_text)

        assert 1 in result[DiffSide.LEFT]
        assert 2 in result[DiffSide.LEFT]

    def test_parse_diff_lines_context_lines_in_both(self):
        patch_text = "@@ -5,3 +5,3 @@\n context one\n context two\n"

        result = _parse_diff_lines(patch=patch_text)

        assert 5 in result[DiffSide.LEFT]
        assert 5 in result[DiffSide.RIGHT]

    def test_parse_diff_lines_empty_patch_returns_empty_sets(self):
        result = _parse_diff_lines(patch="")

        assert result[DiffSide.LEFT] == set()
        assert result[DiffSide.RIGHT] == set()

    def test_parse_diff_lines_returns_both_sides(self):
        patch_text = "@@ -1,1 +1,1 @@\n-old\n+new\n"

        result = _parse_diff_lines(patch=patch_text)

        assert DiffSide.LEFT in result
        assert DiffSide.RIGHT in result


class TestBuildDiffIndex:
    def test_build_diff_index_includes_files_with_patches(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,1 +1,1 @@\n-old\n+new\n",
            )
        ]

        result = _build_diff_index(changed_files=changed_files)

        assert "src/main.py" in result

    def test_build_diff_index_excludes_files_without_patch(self):
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.ADDED,
                patch="",
            )
        ]

        result = _build_diff_index(changed_files=changed_files)

        assert "src/main.py" not in result

    def test_build_diff_index_empty_list_returns_empty_dict(self):
        result = _build_diff_index(changed_files=[])

        assert result == {}

    def test_build_diff_index_maps_file_to_side_sets(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,1 +1,1 @@\n+new line\n",
            )
        ]

        result = _build_diff_index(changed_files=changed_files)

        assert DiffSide.LEFT in result["a.py"]
        assert DiffSide.RIGHT in result["a.py"]


class TestFormatExistingComments:
    def test_format_existing_comments_empty_list(self):

        result = _format_existing_comments(comments=[])

        assert "## Existing discussions" in result

    def test_format_existing_comments_includes_author(self):
        from nominal_code.platforms.base import ExistingComment

        comments = [ExistingComment(author="alice", body="Looks good!", created_at="")]

        result = _format_existing_comments(comments=comments)

        assert "alice" in result
        assert "Looks good!" in result

    def test_format_existing_comments_includes_file_path(self):
        from nominal_code.platforms.base import ExistingComment

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
        from nominal_code.platforms.base import ExistingComment

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
        from nominal_code.platforms.base import ExistingComment

        comments = [
            ExistingComment(author="alice", body="LGTM", file_path="", created_at="")
        ]

        result = _format_existing_comments(comments=comments)

        assert "alice" in result
        assert "LGTM" in result
