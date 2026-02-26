# type: ignore
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent_runner import AgentResult
from nominal_code.bot_type import BotType, ChangedFile, FileStatus, ReviewFinding
from nominal_code.config import ReviewerConfig
from nominal_code.handlers.reviewer import (
    MAX_EXISTING_COMMENTS,
    REVIEWER_ALLOWED_TOOLS,
    build_effective_summary,
    build_reviewer_prompt,
    filter_findings,
    parse_review_output,
)
from nominal_code.handlers.shared import handle_comment
from nominal_code.platforms.base import ExistingComment, PlatformName, ReviewComment
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
    return ReviewComment(
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
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review this",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

            platform.fetch_pr_diff.assert_called_once_with("owner/repo", 42)

    @pytest.mark.asyncio
    async def test_reviewer_uses_reviewer_system_prompt(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

            mock_run.assert_called_once()
            call_kwargs = mock_run.call_args.kwargs

            assert "Review code." in call_kwargs["system_prompt"]
            assert call_kwargs["permission_mode"] == "bypassPermissions"
            assert call_kwargs["allowed_tools"] == REVIEWER_ALLOWED_TOOLS

    @pytest.mark.asyncio
    async def test_reviewer_uses_resolve_coding_guidelines(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                with patch(
                    "nominal_code.handlers.reviewer.resolve_guidelines",
                    return_value="Repo guidelines override",
                ) as mock_resolve:
                    await handle_comment(
                        comment=comment,
                        prompt="review",
                        config=config,
                        platform=platform,
                        session_store=session_store,
                        session_queue=session_queue,
                        bot_type=BotType.REVIEWER,
                    )

                    await asyncio.sleep(0.1)

                    mock_resolve.assert_called_once_with(
                        "/tmp/workspaces/owner/repo/pr-42",
                        "Use snake_case.",
                        {"python": "Python style rules."},
                        [],
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
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Found issues",
                "comments": [
                    {"path": "src/main.py", "line": 10, "body": "Bug here"},
                ],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

            platform.submit_review.assert_called_once()
            call_kwargs = platform.submit_review.call_args.kwargs

            assert call_kwargs["summary"] == "Found issues"
            assert len(call_kwargs["findings"]) == 1
            assert call_kwargs["findings"][0].file_path == "src/main.py"

    @pytest.mark.asyncio
    async def test_reviewer_retry_on_invalid_json(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_store = SessionStore()
        session_queue = SessionQueue()

        valid_json = json.dumps(
            {
                "summary": "Fixed output",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.side_effect = [
                AgentResult(
                    output="not valid json",
                    is_error=False,
                    num_turns=1,
                    duration_ms=1000,
                    session_id="sess-1",
                ),
                AgentResult(
                    output=valid_json,
                    is_error=False,
                    num_turns=1,
                    duration_ms=500,
                    session_id="sess-1",
                ),
            ]

            with patch(
                "nominal_code.handlers.reviewer.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

            assert mock_run.call_count == 2
            platform.submit_review.assert_not_called()
            platform.post_reply.assert_called_once()

    @pytest.mark.asyncio
    async def test_reviewer_fallback_after_exhausted_retries(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_store = SessionStore()
        session_queue = SessionQueue()

        bad_result = AgentResult(
            output="still not json",
            is_error=False,
            num_turns=1,
            duration_ms=500,
            session_id="sess-1",
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = bad_result

            with patch(
                "nominal_code.handlers.reviewer.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock()
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

            assert mock_run.call_count == 3
            platform.submit_review.assert_not_called()
            platform.post_reply.assert_called_once()

            reply_body = platform.post_reply.call_args.args[1].body

            assert reply_body == "still not json"


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
        result = build_reviewer_prompt(comment, "focus on security", changed_files)

        assert "src/main.py" in result
        assert "modified" in result
        assert "src/utils.py" in result
        assert "added" in result
        assert "focus on security" in result
        assert "-old" in result
        assert "+new" in result

    def test_build_reviewer_prompt_with_deps_path(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(
            comment,
            "",
            changed_files,
            deps_path="/tmp/.deps",
        )

        assert "Dependencies directory: /tmp/.deps" in result
        assert "git clone" in result
        assert "--depth=1" in result

    def test_build_reviewer_prompt_without_deps_path(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(
                file_path="src/main.py",
                status=FileStatus.MODIFIED,
                patch="+new",
            ),
        ]
        result = build_reviewer_prompt(comment, "", changed_files)

        assert "Dependencies directory" not in result

    def test_build_reviewer_prompt_no_patch(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="binary.png", status=FileStatus.ADDED, patch=""),
        ]
        result = build_reviewer_prompt(comment, "", changed_files)

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
        result = parse_review_output(output)

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
        result = parse_review_output(output)

        assert result is not None
        assert result.summary == "No issues found"
        assert result.findings == []

    def test_parse_review_output_malformed_json(self):
        result = parse_review_output("not json at all")

        assert result is None

    def test_parse_review_output_missing_summary(self):
        output = json.dumps({"comments": []})
        result = parse_review_output(output)

        assert result is None

    def test_parse_review_output_invalid_comment_missing_path(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [{"line": 10, "body": "test"}],
            }
        )
        result = parse_review_output(output)

        assert result is None

    def test_parse_review_output_invalid_comment_bad_line(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [{"path": "a.py", "line": -1, "body": "test"}],
            }
        )
        result = parse_review_output(output)

        assert result is None

    def test_parse_review_output_strips_markdown_fences(self):
        output = '```json\n{"summary": "Good", "comments": []}\n```'
        result = parse_review_output(output)

        assert result is not None
        assert result.summary == "Good"

    def test_parse_review_output_not_a_dict(self):
        result = parse_review_output("[1, 2, 3]")

        assert result is None


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
            comment,
            "",
            changed_files,
            existing_comments=existing,
        )

        assert "Existing discussions" in result
        assert "@alice" in result
        assert "`a.py:10`" in result
        assert "> Bug on this line" in result

    def test_build_reviewer_prompt_no_existing_comments_omits_section(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        result = build_reviewer_prompt(comment, "", changed_files)

        assert "Existing discussions" not in result

    def test_build_reviewer_prompt_empty_existing_comments_omits_section(self):
        comment = _make_comment()
        changed_files = [
            ChangedFile(file_path="a.py", status=FileStatus.MODIFIED, patch="+new"),
        ]
        result = build_reviewer_prompt(
            comment,
            "",
            changed_files,
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
            comment,
            "",
            changed_files,
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
            comment,
            "",
            changed_files,
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
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

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
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
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
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                await handle_comment(
                    comment=comment,
                    prompt="review",
                    config=config,
                    platform=platform,
                    session_store=session_store,
                    session_queue=session_queue,
                    bot_type=BotType.REVIEWER,
                )

                await asyncio.sleep(0.1)

            call_kwargs = mock_run.call_args.kwargs
            prompt_text = call_kwargs["prompt"]
            comment_count = prompt_text.count("**@alice**")

            assert comment_count == MAX_EXISTING_COMMENTS

    @pytest.mark.asyncio
    async def test_reviewer_fetches_diff_comments_and_workspace_in_parallel(self):
        config = _make_config(allowed_users=["alice"])
        platform = _make_platform()
        comment = _make_comment(author="alice")
        session_store = SessionStore()
        session_queue = SessionQueue()

        review_json = json.dumps(
            {
                "summary": "Looks good",
                "comments": [],
            }
        )

        call_order = []

        async def track_fetch_diff(*args):
            call_order.append("fetch_pr_diff")
            return []

        async def track_fetch_comments(*args):
            call_order.append("fetch_pr_comments")
            return []

        async def track_ensure_ready():
            call_order.append("ensure_ready")

        platform.fetch_pr_diff = AsyncMock(side_effect=track_fetch_diff)
        platform.fetch_pr_comments = AsyncMock(side_effect=track_fetch_comments)

        with patch(
            "nominal_code.handlers.reviewer.run_agent",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = AgentResult(
                output=review_json,
                is_error=False,
                num_turns=1,
                duration_ms=1000,
                session_id="sess-1",
            )

            with patch(
                "nominal_code.handlers.reviewer.GitWorkspace",
            ) as mock_ws_class:
                mock_ws = MagicMock()
                mock_ws.ensure_ready = AsyncMock(side_effect=track_ensure_ready)
                mock_ws.repo_path = "/tmp/workspaces/owner/repo/pr-42"
                mock_ws_class.return_value = mock_ws

                with patch(
                    "nominal_code.handlers.reviewer.asyncio.gather",
                    wraps=asyncio.gather,
                ) as mock_gather:
                    await handle_comment(
                        comment=comment,
                        prompt="review",
                        config=config,
                        platform=platform,
                        session_store=session_store,
                        session_queue=session_queue,
                        bot_type=BotType.REVIEWER,
                    )

                    await asyncio.sleep(0.1)

                    mock_gather.assert_called_once()
                    gather_args = mock_gather.call_args.args

                    assert len(gather_args) == 3

            platform.fetch_pr_diff.assert_called_once_with("owner/repo", 42)
            platform.fetch_pr_comments.assert_called_once_with("owner/repo", 42)
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
        valid, rejected = filter_findings(findings, changed_files)

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
        valid, rejected = filter_findings(findings, changed_files)

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
        valid, rejected = filter_findings(findings, changed_files)

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
        valid, rejected = filter_findings(findings, changed_files)

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
        valid, rejected = filter_findings([], changed_files)

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
        valid, rejected = filter_findings(findings, changed_files)

        assert len(valid) == 2
        assert len(rejected) == 1
        assert rejected[0].body == "Between hunks"

    def test_filter_findings_deletion_lines_excluded(self):
        changed_files = [
            ChangedFile(
                file_path="a.py",
                status=FileStatus.MODIFIED,
                patch="@@ -1,3 +1,2 @@\n context\n-deleted\n context",
            ),
        ]
        findings = [
            ReviewFinding(file_path="a.py", line=1, body="Context line ok"),
            ReviewFinding(file_path="a.py", line=2, body="After deletion ok"),
        ]
        valid, rejected = filter_findings(findings, changed_files)

        assert len(valid) == 2
        assert len(rejected) == 0


class TestBuildEffectiveSummary:
    def test_build_effective_summary_no_rejected(self):
        result = build_effective_summary("All good", [])

        assert result == "All good"

    def test_build_effective_summary_with_rejected(self):
        rejected = [
            ReviewFinding(file_path="src/other.py", line=5, body="Missing update"),
            ReviewFinding(file_path="src/utils.py", line=20, body="Stale reference"),
        ]
        result = build_effective_summary("Found issues", rejected)

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
        result = build_effective_summary("Summary", rejected)

        assert "**a.py:1**" in result
        assert "Needs change" in result
