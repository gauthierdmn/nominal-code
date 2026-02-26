from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING

from nominal_code.bot_type import BotType
from nominal_code.platforms.base import CommentReply, PullRequestEvent

if TYPE_CHECKING:
    from nominal_code.config import Config
    from nominal_code.platforms.base import Platform
    from nominal_code.session import SessionQueue

NOMINAL_CONFIG_DIR: str = ".nominal"
REPO_GUIDELINES_PATH: str = os.path.join(NOMINAL_CONFIG_DIR, "guidelines.md")
EYES_REACTION: str = "eyes"
EXTENSION_TO_LANGUAGE: dict[str, str] = {
    ".py": "python",
    ".pyi": "python",
}

logger: logging.Logger = logging.getLogger(__name__)


async def enqueue_job(
    event: PullRequestEvent,
    bot_type: BotType,
    config: Config,
    platform: Platform,
    session_queue: SessionQueue,
    job: Callable[[], Awaitable[None]],
) -> None:
    """
    Pre-flight checks and enqueue a caller-provided job closure.

    For comment-triggered events (``author_username`` is set): validates
    the author against allowed users, logs the event, and posts an eyes
    reaction. For auto-trigger events (``author_username`` is empty):
    logs with event type/title/author and skips auth and reaction.

    Args:
        event (PullRequestEvent): The parsed event.
        bot_type (BotType): Which bot personality to use.
        config (Config): Application configuration.
        platform (Platform): The platform client for API calls.
        session_queue (SessionQueue): Per-PR job queue.
        job (Callable[[], Awaitable[None]]): The async job to enqueue.
    """

    if event.author_username:
        if event.author_username not in config.allowed_users:
            logger.warning(
                "Ignoring comment from unauthorized user: %s",
                event.author_username,
            )

            return

        logger.info(
            "Processing %s comment from %s on %s#%d: %s",
            bot_type.value,
            event.author_username,
            event.repo_full_name,
            event.pr_number,
            event.body[:100],
        )

        await platform.post_reaction(event, EYES_REACTION)
    else:
        logger.info(
            "Auto-trigger %s reviewer on %s#%d (title=%s, author=%s)",
            event.event_type,
            event.repo_full_name,
            event.pr_number,
            event.pr_title[:80],
            event.pr_author,
        )

    await session_queue.enqueue(
        event.platform,
        event.repo_full_name,
        event.pr_number,
        bot_type.value,
        job,
    )


async def resolve_branch(
    event: PullRequestEvent,
    platform: Platform,
) -> PullRequestEvent | None:
    """
    Return event with resolved branch, or None on failure.

    If the event already has a branch, returns it unchanged. Otherwise
    fetches the branch from the platform. Returns None if the branch
    cannot be determined.

    Args:
        event (PullRequestEvent): The event to resolve.
        platform (Platform): The platform client for API calls.

    Returns:
        PullRequestEvent | None: Event with branch set, or None on failure.
    """

    if event.pr_branch:
        return event

    branch: str = await platform.fetch_pr_branch(event)

    if branch:
        return replace(event, pr_branch=branch)

    logger.error(
        "Cannot determine branch for %s#%d",
        event.repo_full_name,
        event.pr_number,
    )

    await platform.post_reply(
        event,
        CommentReply(body="Unable to determine the PR branch."),
    )

    return None


def load_repo_guidelines(repo_path: str) -> str:
    """
    Load repo-level coding guidelines from the repository root.

    Looks for a `.nominal/guidelines.md` file in the given repo path.
    Returns its contents if found, otherwise returns an empty string.

    Args:
        repo_path (str): Absolute path to the repository root.

    Returns:
        str: The guidelines content, or empty string if not found.
    """

    full_path: str = os.path.join(repo_path, REPO_GUIDELINES_PATH)

    if not os.path.isfile(full_path):
        return ""

    with open(full_path, encoding="utf-8") as guidelines_file:
        return guidelines_file.read().strip()


def detect_languages(file_paths: list[str]) -> list[str]:
    """
    Detect programming languages from file paths using their extensions.

    Returns a sorted, deduplicated list of language names.

    Args:
        file_paths (list[str]): File paths to inspect.

    Returns:
        list[str]: Detected language names, sorted alphabetically.
    """

    languages: set[str] = set()

    for file_path in file_paths:
        extension: str = os.path.splitext(file_path)[1].lower()
        language: str | None = EXTENSION_TO_LANGUAGE.get(extension)

        if language:
            languages.add(language)

    return sorted(languages)


def load_repo_language_guidelines(repo_path: str, language: str) -> str:
    """
    Load a language-specific guideline file from the repository's ``.nominal/`` dir.

    Looks for ``.nominal/{language}.md`` in the given repo path.

    Args:
        repo_path (str): Absolute path to the repository root.
        language (str): Language name (e.g. ``python``).

    Returns:
        str: The guideline content, or empty string if not found.
    """

    full_path: str = os.path.join(
        repo_path,
        NOMINAL_CONFIG_DIR,
        "languages",
        f"{language}.md",
    )

    if not os.path.isfile(full_path):
        return ""

    with open(full_path, encoding="utf-8") as guidelines_file:
        return guidelines_file.read().strip()


def resolve_guidelines(
    repo_path: str,
    default_guidelines: str,
    language_guidelines: dict[str, str],
    file_paths: list[str],
) -> str:
    """
    Compose effective guidelines from general and language-specific sources.

    Resolution order for general guidelines: ``.nominal/guidelines.md`` in the
    repository overrides the default. For each detected language: ``.nominal/{lang}.md``
    overrides the built-in ``prompts/languages/{lang}.md``.

    Args:
        repo_path (str): Absolute path to the repository root.
        default_guidelines (str): Fallback general guidelines from config.
        language_guidelines (dict[str, str]): Built-in language guidelines
            keyed by language name.
        file_paths (list[str]): File paths used to detect relevant languages.

    Returns:
        str: The composed guidelines string.
    """

    parts: list[str] = []

    repo_general: str = load_repo_guidelines(repo_path)
    general: str = repo_general if repo_general else default_guidelines

    if general:
        parts.append(general)

    for language in detect_languages(file_paths):
        repo_lang: str = load_repo_language_guidelines(repo_path, language)
        lang_content: str = (
            repo_lang
            if repo_lang
            else language_guidelines.get(
                language,
                "",
            )
        )

        if lang_content:
            parts.append(lang_content)

    return "\n\n".join(parts)


def build_system_prompt(system_prompt: str, guidelines: str) -> str:
    """
    Combine the system prompt and guidelines into a single string.

    Either part may be empty; empty parts are skipped.

    Args:
        system_prompt (str): The base system prompt text.
        guidelines (str): The composed guidelines text.

    Returns:
        str: The combined system prompt, or empty string if both are empty.
    """

    parts: list[str] = [part for part in (system_prompt, guidelines) if part]

    return "\n\n".join(parts)
