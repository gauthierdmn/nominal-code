# type: ignore
import argparse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.bot_type import AgentReview, ReviewFinding
from nominal_code.cli import (
    build_cli_parser,
    parse_pr_ref,
    print_review,
    run_review,
)
from nominal_code.handlers.reviewer import ReviewResult


class TestParsePrRef:
    def test_parse_pr_ref_valid(self):
        repo, number = parse_pr_ref("owner/repo#42")

        assert repo == "owner/repo"
        assert number == 42

    def test_parse_pr_ref_large_number(self):
        repo, number = parse_pr_ref("org/project#99999")

        assert repo == "org/project"
        assert number == 99999

    def test_parse_pr_ref_with_dots_and_dashes(self):
        repo, number = parse_pr_ref("my-org/my.repo-name#1")

        assert repo == "my-org/my.repo-name"
        assert number == 1

    def test_parse_pr_ref_missing_hash(self):
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("owner/repo42")

    def test_parse_pr_ref_missing_number(self):
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("owner/repo#")

    def test_parse_pr_ref_missing_repo(self):
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("#42")

    def test_parse_pr_ref_no_slash(self):
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("repo#42")

    def test_parse_pr_ref_empty(self):
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("")

    def test_parse_pr_ref_extra_hash(self):
        with pytest.raises(ValueError, match="Invalid PR reference"):
            parse_pr_ref("owner/repo#42#extra")


class TestBuildCliParser:
    def test_parser_review_subcommand(self):
        parser = build_cli_parser()
        args = parser.parse_args(["review", "owner/repo#42"])

        assert args.command == "review"
        assert args.pr_ref == "owner/repo#42"
        assert args.prompt == ""
        assert args.platform == "github"
        assert args.model == ""
        assert args.max_turns == 0
        assert args.dry_run is False

    def test_parser_review_all_options(self):
        parser = build_cli_parser()
        args = parser.parse_args(
            [
                "review",
                "org/project#10",
                "--prompt",
                "focus on security",
                "--platform",
                "gitlab",
                "--model",
                "claude-sonnet-4-6",
                "--max-turns",
                "5",
                "--dry-run",
            ]
        )

        assert args.pr_ref == "org/project#10"
        assert args.prompt == "focus on security"
        assert args.platform == "gitlab"
        assert args.model == "claude-sonnet-4-6"
        assert args.max_turns == 5
        assert args.dry_run is True

    def test_parser_prompt_short_flag(self):
        parser = build_cli_parser()
        args = parser.parse_args(["review", "o/r#1", "-p", "check types"])

        assert args.prompt == "check types"


class TestPrintReview:
    def test_print_review_with_findings(self, capsys):
        result = ReviewResult(
            agent_review=AgentReview(
                summary="Found issues",
                findings=[
                    ReviewFinding(file_path="a.py", line=10, body="Bug here"),
                ],
            ),
            valid_findings=[
                ReviewFinding(file_path="a.py", line=10, body="Bug here"),
            ],
            rejected_findings=[],
            effective_summary="Found issues",
            raw_output="{}",
        )

        print_review(result)
        captured = capsys.readouterr()

        assert "Found issues" in captured.out
        assert "a.py:10" in captured.out
        assert "Bug here" in captured.out

    def test_print_review_no_findings(self, capsys):
        result = ReviewResult(
            agent_review=AgentReview(summary="All good"),
            valid_findings=[],
            rejected_findings=[],
            effective_summary="All good",
            raw_output="{}",
        )

        print_review(result)
        captured = capsys.readouterr()

        assert "All good" in captured.out
        assert "No issues found" in captured.out

    def test_print_review_failed_parse(self, capsys):
        result = ReviewResult(
            agent_review=None,
            valid_findings=[],
            rejected_findings=[],
            effective_summary="",
            raw_output="broken output",
        )

        print_review(result)
        captured = capsys.readouterr()

        assert "failed to produce structured output" in captured.out.lower()
        assert "broken output" in captured.out

    def test_print_review_with_rejected_findings(self, capsys):
        result = ReviewResult(
            agent_review=AgentReview(
                summary="Review",
                findings=[
                    ReviewFinding(file_path="a.py", line=10, body="Valid"),
                    ReviewFinding(file_path="b.py", line=99, body="Outside diff"),
                ],
            ),
            valid_findings=[
                ReviewFinding(file_path="a.py", line=10, body="Valid"),
            ],
            rejected_findings=[
                ReviewFinding(file_path="b.py", line=99, body="Outside diff"),
            ],
            effective_summary="Review",
            raw_output="{}",
        )

        print_review(result)
        captured = capsys.readouterr()

        assert "Findings (1)" in captured.out
        assert "Rejected findings (1)" in captured.out
        assert "b.py:99" in captured.out


class TestRunReview:
    @pytest.mark.asyncio
    async def test_run_review_invalid_ref(self):
        args = argparse.Namespace(
            pr_ref="bad-ref",
            platform="github",
            model="",
            max_turns=0,
            prompt="",
            dry_run=True,
        )
        exit_code = await run_review(args)

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_review_dry_run_does_not_post(self):
        args = argparse.Namespace(
            pr_ref="owner/repo#42",
            platform="github",
            model="",
            max_turns=0,
            prompt="review please",
            dry_run=True,
        )

        mock_platform = MagicMock()
        mock_platform.fetch_pr_branch = AsyncMock(return_value="feature-branch")
        mock_platform.submit_review = AsyncMock()
        mock_platform.post_reply = AsyncMock()

        review_result = ReviewResult(
            agent_review=AgentReview(summary="Looks good"),
            valid_findings=[],
            rejected_findings=[],
            effective_summary="Looks good",
            raw_output="{}",
        )

        with patch(
            "nominal_code.cli.build_platform",
            return_value=mock_platform,
        ):
            with patch(
                "nominal_code.cli.review",
                new_callable=AsyncMock,
                return_value=review_result,
            ):
                exit_code = await run_review(args)

        assert exit_code == 0
        mock_platform.submit_review.assert_not_called()
        mock_platform.post_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_run_review_posts_when_not_dry_run(self):
        args = argparse.Namespace(
            pr_ref="owner/repo#42",
            platform="github",
            model="",
            max_turns=0,
            prompt="",
            dry_run=False,
        )

        mock_platform = MagicMock()
        mock_platform.fetch_pr_branch = AsyncMock(return_value="main")
        mock_platform.submit_review = AsyncMock()
        mock_platform.post_reply = AsyncMock()

        review_result = ReviewResult(
            agent_review=AgentReview(
                summary="Issues found",
                findings=[
                    ReviewFinding(file_path="a.py", line=1, body="Fix this"),
                ],
            ),
            valid_findings=[
                ReviewFinding(file_path="a.py", line=1, body="Fix this"),
            ],
            rejected_findings=[],
            effective_summary="Issues found",
            raw_output="{}",
        )

        with patch(
            "nominal_code.cli.build_platform",
            return_value=mock_platform,
        ):
            with patch(
                "nominal_code.cli.review",
                new_callable=AsyncMock,
                return_value=review_result,
            ):
                exit_code = await run_review(args)

        assert exit_code == 0
        mock_platform.submit_review.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_review_branch_resolution_failure(self):
        args = argparse.Namespace(
            pr_ref="owner/repo#42",
            platform="github",
            model="",
            max_turns=0,
            prompt="",
            dry_run=True,
        )

        mock_platform = MagicMock()
        mock_platform.fetch_pr_branch = AsyncMock(return_value="")

        with patch(
            "nominal_code.cli.build_platform",
            return_value=mock_platform,
        ):
            exit_code = await run_review(args)

        assert exit_code == 1

    @pytest.mark.asyncio
    async def test_run_review_posts_comment_when_no_findings(self):
        args = argparse.Namespace(
            pr_ref="owner/repo#42",
            platform="github",
            model="",
            max_turns=0,
            prompt="",
            dry_run=False,
        )

        mock_platform = MagicMock()
        mock_platform.fetch_pr_branch = AsyncMock(return_value="main")
        mock_platform.submit_review = AsyncMock()
        mock_platform.post_reply = AsyncMock()

        review_result = ReviewResult(
            agent_review=AgentReview(summary="All clear"),
            valid_findings=[],
            rejected_findings=[],
            effective_summary="All clear",
            raw_output="{}",
        )

        with patch(
            "nominal_code.cli.build_platform",
            return_value=mock_platform,
        ):
            with patch(
                "nominal_code.cli.review",
                new_callable=AsyncMock,
                return_value=review_result,
            ):
                exit_code = await run_review(args)

        assert exit_code == 0
        mock_platform.submit_review.assert_not_called()
        mock_platform.post_reply.assert_called_once()
