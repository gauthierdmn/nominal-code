from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nominal_code.config.agent import AgentConfig, ProviderConfig
from nominal_code.config.kubernetes import KubernetesConfig
from nominal_code.models import EventType, ProviderName

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_REVIEWER_PROMPT_PATH: Path = Path("prompts/reviewer_prompt.md")
DEFAULT_WORKER_PROMPT_PATH: Path = Path("prompts/system_prompt.md")
DEFAULT_CODING_GUIDELINES_PATH: Path = Path("prompts/coding_guidelines.md")
DEFAULT_LANGUAGE_GUIDELINES_DIR: Path = Path("prompts/languages")
DEFAULT_WEBHOOK_HOST: str = "0.0.0.0"
DEFAULT_WEBHOOK_PORT: int = 8080
DEFAULT_CLEANUP_INTERVAL_HOURS: int = 6
DEFAULT_WORKSPACE_BASE_DIR: Path = Path(tempfile.gettempdir()) / "nominal-code"


class WorkerConfig(BaseModel):
    """
    Worker bot configuration.

    Attributes:
        bot_username (str): The @mention name for the worker bot.
        system_prompt (str): System prompt text for worker bot invocations.
    """

    model_config = ConfigDict(frozen=True)

    bot_username: str
    system_prompt: str


class ReviewerConfig(BaseModel):
    """
    Reviewer bot configuration.

    Attributes:
        bot_username (str): The @mention name for the reviewer bot.
        system_prompt (str): System prompt text for reviewer bot invocations.
    """

    model_config = ConfigDict(frozen=True)

    bot_username: str
    system_prompt: str


class Config(BaseModel):
    """
    Application configuration loaded from YAML and/or environment variables.

    Attributes:
        worker (WorkerConfig | None): Worker bot config, or None if disabled.
        reviewer (ReviewerConfig | None): Reviewer bot config, or None if disabled.
        webhook_host (str): Host to bind the webhook server.
        webhook_port (int): Port to bind the webhook server.
        allowed_users (frozenset[str]): Usernames permitted to trigger the bots.
        workspace_base_dir (Path): Directory for cloning repositories.
        agent (AgentConfig): Agent runner configuration.
        coding_guidelines (str): Coding guidelines text appended to the
            system prompt.
        language_guidelines (dict[str, str]): Language-specific guidelines
            keyed by language name.
        cleanup_interval_hours (int): Hours between workspace cleanup runs.
        reviewer_triggers (frozenset[EventType]): PR lifecycle event types
            that auto-trigger the reviewer bot.
        allowed_repos (frozenset[str]): Repository full names to process.
        pr_title_include_tags (frozenset[str]): Allowlist of tags.
        pr_title_exclude_tags (frozenset[str]): Blocklist of tags.
        kubernetes (KubernetesConfig | None): K8s job runner config.
        redis_url (str): Redis connection URL.
        redis_key_ttl_seconds (int): TTL for Redis keys in seconds.
    """

    model_config = ConfigDict(frozen=True)

    worker: WorkerConfig | None
    reviewer: ReviewerConfig | None
    webhook_host: str
    webhook_port: int
    allowed_users: frozenset[str]
    workspace_base_dir: Path
    agent: AgentConfig
    coding_guidelines: str
    language_guidelines: dict[str, str]
    cleanup_interval_hours: int
    reviewer_triggers: frozenset[EventType] = frozenset()
    allowed_repos: frozenset[str] = frozenset()
    pr_title_include_tags: frozenset[str] = frozenset()
    pr_title_exclude_tags: frozenset[str] = frozenset()
    kubernetes: KubernetesConfig | None = None
    redis_url: str = ""
    redis_key_ttl_seconds: int = 86400

    @classmethod
    def from_env(cls) -> Config:
        """
        Build a Config by reading YAML and environment variables.

        Backward-compatible wrapper around ``load_config()``.

        Returns:
            Config: A fully populated configuration instance.

        Raises:
            ValueError: If ALLOWED_USERS is empty, no bot is configured, or an
                environment variable has an invalid value.
        """

        from nominal_code.config.loader import load_config

        return load_config()

    @classmethod
    def for_cli(
        cls,
        model: str = "",
        max_turns: int = 0,
        provider: ProviderName | None = None,
    ) -> Config:
        """
        Build a Config for CLI mode.

        Backward-compatible wrapper around ``load_config_for_cli()``.

        Args:
            model (str): Optional agent model override.
            max_turns (int): Optional agent max turns override.
            provider (ProviderName | None): Optional LLM provider.

        Returns:
            Config: A configuration suitable for one-off CLI reviews.
        """

        from nominal_code.config.loader import load_config_for_cli

        return load_config_for_cli(
            model=model,
            max_turns=max_turns,
            provider=provider,
        )

    @classmethod
    def for_ci(
        cls,
        provider: ProviderConfig,
        model: str = "",
        max_turns: int = 0,
        guidelines_path: Path = Path(),
    ) -> Config:
        """
        Build a Config for CI mode.

        Backward-compatible wrapper around ``load_config_for_ci()``.

        Args:
            provider (ProviderConfig): The resolved provider configuration.
            model (str): Optional model override.
            max_turns (int): Optional agent max turns override.
            guidelines_path (Path): Optional path to a coding guidelines file.

        Returns:
            Config: A configuration suitable for CI-triggered reviews.
        """

        from nominal_code.config.loader import load_config_for_ci

        return load_config_for_ci(
            provider=provider,
            model=model,
            max_turns=max_turns,
            guidelines_path=guidelines_path,
        )


def parse_title_tags(tags: str) -> frozenset[str]:
    """
    Parse a comma-separated string of tag names into a lowercased frozenset.

    Strips whitespace and lowercases each tag.

    Args:
        tags (str): Comma-separated tag names (e.g. ``"nominalbot, CI"``).

    Returns:
        frozenset[str]: The parsed tags, lowercased.
    """

    if not tags.strip():
        return frozenset()

    return frozenset(tag.strip().lower() for tag in tags.split(",") if tag.strip())


def parse_reviewer_triggers(events: str) -> frozenset[EventType]:
    """
    Parse a comma-separated string of event type names into a frozenset.

    Invalid names are logged as warnings and skipped.

    Args:
        events (str): Comma-separated event type names (e.g. ``pr_opened,pr_push``).

    Returns:
        frozenset[EventType]: The parsed event types.
    """

    if not events.strip():
        return frozenset()

    triggers: set[EventType] = set()

    for token in events.split(","):
        name: str = token.strip()

        if not name:
            continue

        try:
            triggers.add(EventType(name))
        except ValueError:
            logger.warning("Ignoring unknown REVIEWER_TRIGGERS value: %s", name)

    return frozenset(triggers)


def load_file_content(file_path: Path) -> str:
    """
    Read text content from a file path.

    Returns an empty string if the file does not exist.

    Args:
        file_path (Path): Path to the file.

    Returns:
        str: The file contents, or empty string if the file is missing.
    """

    if not file_path.is_file():
        return ""

    return file_path.read_text(encoding="utf-8").strip()


def load_language_guidelines(directory: Path) -> dict[str, str]:
    """
    Load all language guideline files from a directory.

    Each ``.md`` file in the directory becomes an entry keyed by its stem.

    Args:
        directory (Path): Path to the language guidelines directory.

    Returns:
        dict[str, str]: Language name to guideline content mapping.
    """

    if not directory.is_dir():
        return {}

    guidelines: dict[str, str] = {}

    for file_path in sorted(directory.glob("*.md")):
        content: str = file_path.read_text(encoding="utf-8").strip()

        if content:
            guidelines[file_path.stem] = content

    return guidelines
