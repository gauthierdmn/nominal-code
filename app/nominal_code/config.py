from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from environs import Env

from nominal_code.models import EventType

logger: logging.Logger = logging.getLogger(__name__)
env: Env = Env()

DEFAULT_REVIEWER_PROMPT_PATH: str = "prompts/reviewer_prompt.md"
DEFAULT_WORKER_PROMPT_PATH: str = "prompts/system_prompt.md"
DEFAULT_CODING_GUIDELINES_PATH: str = "prompts/coding_guidelines.md"
DEFAULT_LANGUAGE_GUIDELINES_DIR: str = "prompts/languages"
DEFAULT_WEBHOOK_HOST: str = "0.0.0.0"
DEFAULT_WEBHOOK_PORT: int = 8080
DEFAULT_CLEANUP_INTERVAL_HOURS: int = 6


@dataclass(frozen=True)
class WorkerConfig:
    """
    Worker bot configuration.

    Attributes:
        bot_username (str): The @mention name for the worker bot.
        system_prompt (str): System prompt text for worker bot invocations.
    """

    bot_username: str
    system_prompt: str


@dataclass(frozen=True)
class ReviewerConfig:
    """
    Reviewer bot configuration.

    Attributes:
        bot_username (str): The @mention name for the reviewer bot.
        system_prompt (str): System prompt text for reviewer bot invocations.
    """

    bot_username: str
    system_prompt: str


@dataclass(frozen=True)
class Config:
    """
    Application configuration loaded from environment variables.

    Attributes:
        worker (WorkerConfig | None): Worker bot config, or None if disabled.
        reviewer (ReviewerConfig | None): Reviewer bot config, or None if disabled.
        webhook_host (str): Host to bind the webhook server.
        webhook_port (int): Port to bind the webhook server.
        allowed_users (frozenset[str]): Usernames permitted to trigger the bots.
        workspace_base_dir (str): Directory for cloning repositories.
        agent_max_turns (int): Maximum agentic turns (0 for unlimited).
        agent_model (str): Optional model override.
        agent_cli_path (str): Path to the agent CLI binary.
        coding_guidelines (str): Coding guidelines text appended to the
            system prompt.
        language_guidelines (dict[str, str]): Language-specific guidelines
            keyed by language name (e.g. ``python``), loaded from
            ``prompts/languages/``.
        cleanup_interval_hours (int): Hours between workspace cleanup runs
            (0 disables).
        reviewer_triggers (frozenset[EventType]): PR lifecycle event types
            that auto-trigger the reviewer bot. Empty means disabled.
    """

    worker: WorkerConfig | None
    reviewer: ReviewerConfig | None
    webhook_host: str
    webhook_port: int
    allowed_users: frozenset[str]
    workspace_base_dir: str
    agent_max_turns: int
    agent_model: str
    agent_cli_path: str
    coding_guidelines: str
    language_guidelines: dict[str, str]
    cleanup_interval_hours: int
    reviewer_triggers: frozenset[EventType] = frozenset()

    @classmethod
    def for_cli(
        cls,
        model: str = "",
        max_turns: int = 0,
    ) -> Config:
        """
        Build a Config for CLI mode without requiring webhook-only settings.

        Reviewer is always enabled with the default system prompt. Settings
        like ``ALLOWED_USERS`` and bot usernames are not required.

        Args:
            model (str): Optional agent model override.
            max_turns (int): Optional agent max turns override.

        Returns:
            Config: A configuration suitable for one-off CLI reviews.
        """

        reviewer_system_prompt: str = _load_file_content(
            env.str("REVIEWER_SYSTEM_PROMPT", DEFAULT_REVIEWER_PROMPT_PATH),
        )

        workspace_base_dir: str = env.str(
            "WORKSPACE_BASE_DIR",
            os.path.join(tempfile.gettempdir(), "nominal-code"),
        )

        coding_guidelines: str = _load_file_content(
            env.str("CODING_GUIDELINES", DEFAULT_CODING_GUIDELINES_PATH),
        )
        language_guidelines: dict[str, str] = _load_language_guidelines(
            env.str("LANGUAGE_GUIDELINES_DIR", DEFAULT_LANGUAGE_GUIDELINES_DIR),
        )

        return cls(
            worker=None,
            reviewer=ReviewerConfig(
                bot_username="",
                system_prompt=reviewer_system_prompt,
            ),
            webhook_host="",
            webhook_port=0,
            allowed_users=frozenset(),
            workspace_base_dir=workspace_base_dir,
            agent_max_turns=max_turns or env.int("AGENT_MAX_TURNS", 0),
            agent_model=model or env.str("AGENT_MODEL", ""),
            agent_cli_path=env.str("AGENT_CLI_PATH", ""),
            coding_guidelines=coding_guidelines,
            language_guidelines=language_guidelines,
            cleanup_interval_hours=0,
        )

    @classmethod
    def from_env(cls) -> Config:
        """
        Build a Config by reading environment variables.

        At least one of WORKER_BOT_USERNAME or REVIEWER_BOT_USERNAME must be set.

        Returns:
            Config: A fully populated configuration instance.

        Raises:
            ValueError: If ALLOWED_USERS is empty, no bot is configured, or an
                environment variable has an invalid value.
        """

        worker_bot_username: str = env.str("WORKER_BOT_USERNAME", "")
        worker: WorkerConfig | None = None

        if worker_bot_username:
            worker_system_prompt: str = _load_file_content(
                env.str("WORKER_SYSTEM_PROMPT", DEFAULT_WORKER_PROMPT_PATH),
            )
            worker = WorkerConfig(
                bot_username=worker_bot_username,
                system_prompt=worker_system_prompt,
            )

        reviewer_bot_username: str = env.str("REVIEWER_BOT_USERNAME", "")
        reviewer: ReviewerConfig | None = None

        if reviewer_bot_username:
            reviewer_system_prompt: str = _load_file_content(
                env.str("REVIEWER_SYSTEM_PROMPT", DEFAULT_REVIEWER_PROMPT_PATH),
            )
            reviewer = ReviewerConfig(
                bot_username=reviewer_bot_username,
                system_prompt=reviewer_system_prompt,
            )

        if worker is None and reviewer is None:
            raise ValueError(
                "At least one of WORKER_BOT_USERNAME or REVIEWER_BOT_USERNAME "
                "must be set",
            )

        webhook_host: str = env.str("WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST)
        webhook_port: int = env.int("WEBHOOK_PORT", DEFAULT_WEBHOOK_PORT)

        try:
            users_raw: str = env.str("ALLOWED_USERS")
        except Exception as exc:
            raise ValueError(
                "Required environment variable 'ALLOWED_USERS' is not set"
            ) from exc

        allowed_users: frozenset[str] = frozenset(
            user.strip() for user in users_raw.split(",") if user.strip()
        )

        if not allowed_users:
            raise ValueError("ALLOWED_USERS must contain at least one username")

        workspace_base_dir: str = env.str(
            "WORKSPACE_BASE_DIR",
            os.path.join(tempfile.gettempdir(), "nominal-code"),
        )

        agent_max_turns: int = env.int("AGENT_MAX_TURNS", 0)
        agent_model: str = env.str("AGENT_MODEL", "")
        agent_cli_path: str = env.str("AGENT_CLI_PATH", "")
        coding_guidelines: str = _load_file_content(
            env.str("CODING_GUIDELINES", DEFAULT_CODING_GUIDELINES_PATH),
        )
        language_guidelines: dict[str, str] = _load_language_guidelines(
            env.str("LANGUAGE_GUIDELINES_DIR", DEFAULT_LANGUAGE_GUIDELINES_DIR),
        )

        cleanup_interval_hours: int = env.int(
            "CLEANUP_INTERVAL_HOURS",
            DEFAULT_CLEANUP_INTERVAL_HOURS,
        )

        reviewer_triggers: frozenset[EventType] = _parse_reviewer_triggers(
            env.str("REVIEWER_TRIGGERS", ""),
        )

        return cls(
            worker=worker,
            reviewer=reviewer,
            webhook_host=webhook_host,
            webhook_port=webhook_port,
            allowed_users=allowed_users,
            workspace_base_dir=workspace_base_dir,
            agent_max_turns=agent_max_turns,
            agent_model=agent_model,
            agent_cli_path=agent_cli_path,
            coding_guidelines=coding_guidelines,
            language_guidelines=language_guidelines,
            cleanup_interval_hours=cleanup_interval_hours,
            reviewer_triggers=reviewer_triggers,
        )


def _parse_reviewer_triggers(raw: str) -> frozenset[EventType]:
    """
    Parse a comma-separated string of event type names into a frozenset.

    Invalid names are logged as warnings and skipped.

    Args:
        raw (str): Comma-separated event type names (e.g. ``pr_opened,pr_push``).

    Returns:
        frozenset[EventType]: The parsed event types.
    """

    if not raw.strip():
        return frozenset()

    triggers: set[EventType] = set()

    for token in raw.split(","):
        name: str = token.strip()

        if not name:
            continue

        try:
            triggers.add(EventType(name))
        except ValueError:
            logger.warning("Ignoring unknown REVIEWER_TRIGGERS value: %s", name)

    return frozenset(triggers)


def _load_file_content(file_path: str) -> str:
    """
    Read text content from a file path.

    Returns an empty string if the file does not exist, allowing the bot
    to run without the file when the default path is absent.

    Args:
        file_path (str): Path to the file.

    Returns:
        str: The file contents, or empty string if the file is missing.
    """

    path: Path = Path(file_path)

    if not path.is_file():
        return ""

    return path.read_text(encoding="utf-8").strip()


def _load_language_guidelines(directory: str) -> dict[str, str]:
    """
    Load all language guideline files from a directory.

    Each ``.md`` file in the directory becomes an entry keyed by its stem
    (e.g. ``python.md`` → ``"python"``). Missing or non-directory paths
    are silently ignored.

    Args:
        directory (str): Path to the language guidelines directory.

    Returns:
        dict[str, str]: Language name to guideline content mapping.
    """

    dir_path: Path = Path(directory)

    if not dir_path.is_dir():
        return {}

    guidelines: dict[str, str] = {}

    for file_path in sorted(dir_path.glob("*.md")):
        content: str = file_path.read_text(encoding="utf-8").strip()

        if content:
            guidelines[file_path.stem] = content

    return guidelines
