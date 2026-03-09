from __future__ import annotations

import argparse
import asyncio
import logging
import re
import sys

from environs import Env

from nominal_code.config import Config
from nominal_code.handlers.review import ReviewResult, review
from nominal_code.main import setup_logging
from nominal_code.models import EventType, ProviderName
from nominal_code.platforms.base import (
    CommentReply,
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)

PR_REF_PATTERN: re.Pattern[str] = re.compile(
    r"^(?P<repo>[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+)#(?P<number>\d+)$",
)
CLI_AUTHOR_USERNAME: str = "cli"

logger: logging.Logger = logging.getLogger(__name__)
env: Env = Env()


def cli_main() -> None:
    """
    Entry point for the ``review`` CLI subcommand.
    """

    setup_logging()

    parser: argparse.ArgumentParser = _build_cli_parser()
    args: argparse.Namespace = parser.parse_args()

    if args.command != "review":
        parser.print_help()
        sys.exit(1)

    exit_code: int = asyncio.run(_run_review(args))
    sys.exit(exit_code)


def _parse_pr_ref(ref: str) -> tuple[str, int]:
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


def _build_cli_parser() -> argparse.ArgumentParser:
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
        "--provider",
        default="",
        help="LLM provider (e.g. openai, anthropic) for API runner.",
    )

    review_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to stdout without posting to the PR.",
    )

    return parser


def _build_platform(platform_name: PlatformName) -> ReviewerPlatform:
    """
    Construct a platform instance for CLI use from environment tokens.

    For GitHub, supports both PAT and App authentication. App mode requires
    ``GITHUB_APP_ID``, a private key, and ``GITHUB_INSTALLATION_ID``.
    No webhook secret is required since CLI mode does not receive webhooks.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        ReviewerPlatform: The constructed platform client.

    Raises:
        SystemExit: If the required token environment variable is not set.
    """

    if platform_name == PlatformName.GITHUB:
        from nominal_code.platforms.github import (
            GitHubAppAuth,
            GitHubAuth,
            GitHubPatAuth,
            GitHubPlatform,
            load_private_key,
        )

        app_id: str = env.str("GITHUB_APP_ID", "")
        private_key: str = load_private_key()

        if app_id and private_key:
            installation_id: int = env.int("GITHUB_INSTALLATION_ID", 0)

            if not installation_id:
                logger.error(
                    "GITHUB_INSTALLATION_ID is required for CLI mode "
                    "with GitHub App auth",
                )
                sys.exit(1)

            auth: GitHubAuth = GitHubAppAuth(
                app_id=app_id,
                private_key=private_key,
                installation_id=installation_id,
            )

            return GitHubPlatform(auth=auth)

        token: str = env.str("GITHUB_TOKEN", "")

        if not token:
            logger.error("GITHUB_TOKEN is required for GitHub reviews")
            sys.exit(1)

        auth = GitHubPatAuth(token=token)

        return GitHubPlatform(auth=auth)

    if platform_name == PlatformName.GITLAB:
        token = env.str("GITLAB_TOKEN", "")

        if not token:
            logger.error("GITLAB_TOKEN is required for GitLab reviews")
            sys.exit(1)

        from nominal_code.platforms.gitlab import GitLabPlatform
        from nominal_code.platforms.gitlab.platform import GITLAB_API_BASE

        base_url: str = env.str("GITLAB_API_BASE", GITLAB_API_BASE)

        return GitLabPlatform(token=token, base_url=base_url)

    logger.error("Unsupported platform: %s", platform_name)
    sys.exit(1)


def _print_review(result: ReviewResult) -> None:
    """
    Format and print review results to stdout in plain text.

    Args:
        result (ReviewResult): The review result to display.
    """

    if result.agent_review is None:
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


async def _run_review(args: argparse.Namespace) -> int:
    """
    Main CLI review flow: parse ref, build config and platform, run review.

    Args:
        args (argparse.Namespace): Parsed CLI arguments.

    Returns:
        int: Exit code (0 on success, 1 on failure).
    """

    try:
        repo_full_name, pr_number = _parse_pr_ref(args.pr_ref)
    except ValueError as exc:
        logger.error("%s", exc)

        return 1

    provider: ProviderName | None = (
        ProviderName(args.provider) if args.provider else None
    )

    config: Config = Config.for_cli(
        model=args.model,
        max_turns=args.max_turns,
        provider=provider,
    )

    platform_name: PlatformName = PlatformName(args.platform)
    platform: ReviewerPlatform = _build_platform(platform_name=platform_name)

    await platform.ensure_auth()

    branch: str = await platform.fetch_pr_branch(
        repo_full_name=repo_full_name,
        pr_number=pr_number,
    )

    if not branch:
        logger.error(
            "Could not resolve branch for %s#%d",
            repo_full_name,
            pr_number,
        )

        return 1

    event: PullRequestEvent = PullRequestEvent(
        platform=platform_name,
        repo_full_name=repo_full_name,
        pr_number=pr_number,
        pr_branch=branch,
        clone_url=platform.build_clone_url(repo_full_name),
        event_type=EventType.PR_OPENED,
    )

    try:
        result: ReviewResult = await review(
            event=event,
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

    _print_review(result)

    if not args.dry_run and result.agent_review is not None:
        if result.valid_findings:
            await platform.submit_review(
                repo_full_name=repo_full_name,
                pr_number=pr_number,
                findings=result.valid_findings,
                summary=result.effective_summary,
                event=event,
            )
        else:
            await platform.post_reply(
                event=event,
                reply=CommentReply(body=result.effective_summary),
            )

        logger.info("Review posted to %s#%d", repo_full_name, pr_number)

    return 0
