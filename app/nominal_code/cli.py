from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import sys

from nominal_code.config import Config
from nominal_code.handlers.reviewer import ExecuteReviewResult, execute_review
from nominal_code.main import setup_logging
from nominal_code.platforms.base import (
    CommentReply,
    PlatformName,
    ReviewComment,
    ReviewerPlatform,
)

PR_REF_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<repo>[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)#(?P<number>\d+)$",
)
CLI_AUTHOR_USERNAME: str = "cli"

logger: logging.Logger = logging.getLogger(__name__)


def parse_pr_ref(ref: str) -> tuple[str, int]:
    """
    Parse a PR reference like ``owner/repo#42`` into repo name and number.

    Args:
        ref (str): The PR reference string.

    Returns:
        tuple[str, int]: A pair of (repo_full_name, pr_number).

    Raises:
        ValueError: If the reference does not match the expected format.
    """

    match: re.Match[str] | None = PR_REF_PATTERN.match(ref)

    if not match:
        raise ValueError(
            f"Invalid PR reference: '{ref}'. Expected format: owner/repo#123",
        )

    return match.group("repo"), int(match.group("number"))


def build_cli_parser() -> argparse.ArgumentParser:
    """
    Build the argument parser for the CLI ``review`` subcommand.

    Returns:
        argparse.ArgumentParser: The configured parser.
    """

    parser: argparse.ArgumentParser = argparse.ArgumentParser(
        prog="nominal-code",
        description="AI-powered code review for GitHub and GitLab PRs.",
    )

    subparsers: argparse._SubParsersAction[argparse.ArgumentParser] = (
        parser.add_subparsers(dest="command")
    )

    review_parser: argparse.ArgumentParser = subparsers.add_parser(
        "review",
        help="Run a one-off code review on a pull request.",
    )

    review_parser.add_argument(
        "pr_ref",
        help="PR reference in owner/repo#number format (e.g. owner/repo#42).",
    )

    review_parser.add_argument(
        "--prompt",
        "-p",
        default="",
        help="Custom review instructions.",
    )

    review_parser.add_argument(
        "--platform",
        choices=[name.value for name in PlatformName],
        default=PlatformName.GITHUB.value,
        help="Platform type (default: github).",
    )

    review_parser.add_argument(
        "--model",
        default="",
        help="Agent model override.",
    )

    review_parser.add_argument(
        "--max-turns",
        type=int,
        default=0,
        help="Agent max turns.",
    )

    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to stdout without posting to the PR.",
    )

    return parser


def build_platform(platform_name: PlatformName) -> ReviewerPlatform:
    """
    Construct a platform instance for CLI use from environment tokens.

    No webhook secret is required since CLI mode does not receive webhooks.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        ReviewerPlatform: The constructed platform client.

    Raises:
        SystemExit: If the required token environment variable is not set.
    """

    if platform_name == PlatformName.GITHUB:
        token: str = os.environ.get("GITHUB_TOKEN", "")

        if not token:
            logger.error("GITHUB_TOKEN is required for GitHub reviews")
            sys.exit(1)

        from nominal_code.platforms.github import GitHubPlatform

        return GitHubPlatform(token=token)

    if platform_name == PlatformName.GITLAB:
        token = os.environ.get("GITLAB_TOKEN", "")

        if not token:
            logger.error("GITLAB_TOKEN is required for GitLab reviews")
            sys.exit(1)

        from nominal_code.platforms.gitlab import GitLabPlatform

        base_url: str = os.environ.get("GITLAB_BASE_URL", "https://gitlab.com")

        return GitLabPlatform(token=token, base_url=base_url)

    logger.error("Unsupported platform: %s", platform_name)
    sys.exit(1)


def print_review(result: ExecuteReviewResult) -> None:
    """
    Format and print review results to stdout in plain text.

    Args:
        result (ExecuteReviewResult): The review result to display.
    """

    if result.review_result is None:
        print("Review failed to produce structured output.\n")
        print("Raw output:")
        print(result.raw_output)

        return

    print(f"Summary: {result.effective_summary}\n")

    if result.valid_findings:
        print(f"Findings ({len(result.valid_findings)}):\n")

        for finding in result.valid_findings:
            print(f"  {finding.file_path}:{finding.line}")
            print(f"    {finding.body}\n")

    if result.rejected_findings:
        print(f"Rejected findings ({len(result.rejected_findings)}):\n")

        for finding in result.rejected_findings:
            print(f"  {finding.file_path}:{finding.line}")
            print(f"    {finding.body}\n")

    if not result.valid_findings and not result.rejected_findings:
        print("No issues found.")


async def run_review(args: argparse.Namespace) -> int:
    """
    Main CLI review flow: parse ref, build config and platform, run review.

    Args:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    try:
        repo_full_name, pr_number = parse_pr_ref(args.pr_ref)
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    config: Config = Config.for_cli(
        model=args.model,
        max_turns=args.max_turns,
    )

    platform_name: PlatformName = PlatformName(args.platform)
    platform: ReviewerPlatform = build_platform(platform_name)

    branch: str = await platform.fetch_pr_branch(
        ReviewComment(
            platform=platform_name,
            repo_full_name=repo_full_name,
            pr_number=pr_number,
            pr_branch="",
            comment_id=0,
            author_username="",
            body="",
            diff_hunk="",
            file_path="",
            clone_url="",
        ),
    )

    if not branch:
        logger.error(
            "Could not resolve branch for %s#%d",
            repo_full_name,
            pr_number,
        )

        return 1

    comment: ReviewComment = ReviewComment(
        platform=platform_name,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_branch=branch,
        comment_id=0,
        author_username=CLI_AUTHOR_USERNAME,
        body="",
        diff_hunk="",
        file_path="",
        clone_url="",
    )

    try:
        result: ExecuteReviewResult = await execute_review(
            comment=comment,
            prompt=args.prompt,
            config=config,
            platform=platform,
        )
    except RuntimeError:
        logger.exception("Failed to set up workspace")

        return 1
    except Exception:
        logger.exception("Error running review")

        return 1

    print_review(result)

    if not args.dry_run and result.review_result is not None:
        if result.valid_findings:
            await platform.submit_review(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                findings=result.valid_findings,
                summary=result.effective_summary,
                comment=comment,
            )
        else:
            await platform.post_reply(
                comment,
                CommentReply(body=result.effective_summary),
            )

        logger.info("Review posted to %s#%d", repo_full_name, pr_number)

    return 0


def cli_main() -> None:
    """
    Entry point for the ``review`` CLI subcommand.
    """

    setup_logging()

    parser: argparse.ArgumentParser = build_cli_parser()
    args: argparse.Namespace = parser.parse_args()

    if args.command != "review":
        parser.print_help()
        sys.exit(1)

    exit_code: int = asyncio.run(run_review(args))
    sys.exit(exit_code)
