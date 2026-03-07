from __future__ import annotations

import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from environs import Env, EnvError

from nominal_code.models import EventType, ProviderName

logger: logging.Logger = logging.getLogger(__name__)
env: Env = Env()

DEFAULT_REVIEWER_PROMPT_PATH: Path = Path("prompts/reviewer_prompt.md")
DEFAULT_WORKER_PROMPT_PATH: Path = Path("prompts/system_prompt.md")
DEFAULT_CODING_GUIDELINES_PATH: Path = Path("prompts/coding_guidelines.md")
DEFAULT_LANGUAGE_GUIDELINES_DIR: Path = Path("prompts/languages")
DEFAULT_WEBHOOK_HOST: str = "0.0.0.0"
DEFAULT_WEBHOOK_PORT: int = 8080
DEFAULT_CLEANUP_INTERVAL_HOURS: int = 6


@dataclass(frozen=True)
class ProviderConfig:
    """
    LLM provider configuration.

    Attributes:
        name (ProviderName): Provider identifier.
        model (str): Model name (e.g. ``"claude-sonnet-4-20250514"``).
        base_url (str | None): Base URL for OpenAI-compatible providers.
            ``None`` for native providers and OpenAI itself (uses SDK default).
    """

    name: ProviderName
    model: str
    base_url: str | None = None

    @property
    def api_key_env(self) -> str:
        """
        Environment variable name for the provider's API key.

        Derived from the provider name: ``{NAME}_API_KEY``
        (e.g. ``"ANTHROPIC_API_KEY"``).

        Returns:
            str: The environment variable name.
        """

        return f"{self.name.upper()}_API_KEY"


@dataclass(frozen=True)
class CliAgentConfig:
    """
    Agent configuration for CLI and webhook modes.

    Uses the Claude Code CLI subprocess. Supports session resumption
    and Claude Pro/Max subscriptions.

    Attributes:
        model (str): Optional model override (empty string uses CLI default).
        max_turns (int): Maximum agentic turns (0 for unlimited).
        cli_path (str): Path to the Claude Code CLI binary (empty to use
            bundled).
    """

    model: str = ""
    max_turns: int = 0
    cli_path: str = ""


@dataclass(frozen=True)
class ApiAgentConfig:
    """
    Agent configuration for CI mode.

    Calls the LLM provider API directly. Requires a provider API key.
    Stateless (no session continuity).

    The effective model is always ``provider.model``. To override the
    default, pass a ``ProviderConfig`` with the desired model set (see
    ``Config.for_ci``).

    Attributes:
        provider (ProviderConfig): The LLM provider configuration. Determines
            which SDK, API key, and model to use.
        max_turns (int): Maximum agentic turns (0 for unlimited).
    """

    provider: ProviderConfig
    max_turns: int = 0


AgentConfig = CliAgentConfig | ApiAgentConfig


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
        workspace_base_dir (Path): Directory for cloning repositories.
        agent (AgentConfig): Agent runner configuration. Either a
            ``CliAgentConfig`` (CLI/webhook mode) or ``ApiAgentConfig``
            (CI mode with direct API calls).
        coding_guidelines (str): Coding guidelines text appended to the
            system prompt.
        language_guidelines (dict[str, str]): Language-specific guidelines
            keyed by language name (e.g. ``python``), loaded from
            ``prompts/languages/``.
        cleanup_interval_hours (int): Hours between workspace cleanup runs
            (0 disables).
        reviewer_triggers (frozenset[EventType]): PR lifecycle event types
            that auto-trigger the reviewer bot. Empty means disabled.
        allowed_repos (frozenset[str]): Repository full names (e.g.
            ``owner/repo``) to process. When empty, all repos are accepted.
        pr_title_include_tags (frozenset[str]): Allowlist of tags. When set,
            only events whose PR title contains ``[tag]`` for at least one
            tag are processed. Empty means disabled.
        pr_title_exclude_tags (frozenset[str]): Blocklist of tags. Events
            whose PR title contains ``[tag]`` for any tag in this set are
            skipped. Empty means disabled.
    """

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
            env.path("REVIEWER_SYSTEM_PROMPT", DEFAULT_REVIEWER_PROMPT_PATH),
        )

        workspace_base_dir: Path = env.path(
            "WORKSPACE_BASE_DIR",
            Path(tempfile.gettempdir()) / "nominal-code",
        )

        coding_guidelines: str = _load_file_content(
            env.path("CODING_GUIDELINES", DEFAULT_CODING_GUIDELINES_PATH),
        )
        language_guidelines: dict[str, str] = _load_language_guidelines(
            env.path("LANGUAGE_GUIDELINES_DIR", DEFAULT_LANGUAGE_GUIDELINES_DIR),
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
            agent=CliAgentConfig(
                model=model or env.str("AGENT_MODEL", ""),
                max_turns=max_turns or env.int("AGENT_MAX_TURNS", 0),
                cli_path=env.str("AGENT_CLI_PATH", ""),
            ),
            coding_guidelines=coding_guidelines,
            language_guidelines=language_guidelines,
            cleanup_interval_hours=0,
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
        Build a Config for CI mode (GitHub Actions / GitLab CI).

        Similar to ``for_cli`` but calls the LLM provider API directly
        and optionally accepts a custom coding guidelines path.

        Args:
            provider (ProviderConfig): The resolved provider configuration
                (from ``PROVIDERS`` registry). When ``model`` is given,
                a copy with the overridden model is stored.
            model (str): Optional model override. When set, replaces the
                provider's default model.
            max_turns (int): Optional agent max turns override.
            guidelines_path (Path): Optional path to a coding guidelines file.

        Returns:
            Config: A configuration suitable for CI-triggered reviews.
        """

        model_override: str = model or env.str("AGENT_MODEL", "")

        if model_override:
            provider = ProviderConfig(
                name=provider.name,
                model=model_override,
                base_url=provider.base_url,
            )

        reviewer_system_prompt: str = _load_file_content(
            env.path("REVIEWER_SYSTEM_PROMPT", DEFAULT_REVIEWER_PROMPT_PATH),
        )

        workspace_base_dir: Path = env.path(
            "WORKSPACE_BASE_DIR",
            Path(tempfile.gettempdir()) / "nominal-code",
        )

        coding_guidelines: str = _load_file_content(
            env.path("CODING_GUIDELINES", DEFAULT_CODING_GUIDELINES_PATH),
        )

        if guidelines_path != Path():
            custom_coding: str = _load_file_content(guidelines_path)

            if custom_coding:
                coding_guidelines = custom_coding

        language_guidelines: dict[str, str] = _load_language_guidelines(
            env.path("LANGUAGE_GUIDELINES_DIR", DEFAULT_LANGUAGE_GUIDELINES_DIR),
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
            agent=ApiAgentConfig(
                provider=provider,
                max_turns=max_turns or env.int("AGENT_MAX_TURNS", 0),
            ),
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
                env.path("WORKER_SYSTEM_PROMPT", DEFAULT_WORKER_PROMPT_PATH),
            )
            worker = WorkerConfig(
                bot_username=worker_bot_username,
                system_prompt=worker_system_prompt,
            )

        reviewer_bot_username: str = env.str("REVIEWER_BOT_USERNAME", "")
        reviewer: ReviewerConfig | None = None

        if reviewer_bot_username:
            reviewer_system_prompt: str = _load_file_content(
                env.path("REVIEWER_SYSTEM_PROMPT", DEFAULT_REVIEWER_PROMPT_PATH),
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
        except EnvError as exc:
            raise ValueError(
                "Required environment variable 'ALLOWED_USERS' is not set"
            ) from exc

        allowed_users: frozenset[str] = frozenset(
            user.strip() for user in users_raw.split(",") if user.strip()
        )

        if not allowed_users:
            raise ValueError("ALLOWED_USERS must contain at least one username")

        workspace_base_dir: Path = env.path(
            "WORKSPACE_BASE_DIR",
            Path(tempfile.gettempdir()) / "nominal-code",
        )

        coding_guidelines: str = _load_file_content(
            env.path("CODING_GUIDELINES", DEFAULT_CODING_GUIDELINES_PATH),
        )
        language_guidelines: dict[str, str] = _load_language_guidelines(
            env.path("LANGUAGE_GUIDELINES_DIR", DEFAULT_LANGUAGE_GUIDELINES_DIR),
        )

        cleanup_interval_hours: int = env.int(
            "CLEANUP_INTERVAL_HOURS",
            DEFAULT_CLEANUP_INTERVAL_HOURS,
        )

        reviewer_triggers: frozenset[EventType] = _parse_reviewer_triggers(
            env.str("REVIEWER_TRIGGERS", ""),
        )

        allowed_repos: frozenset[str] = frozenset(
            repo.strip()
            for repo in env.str("ALLOWED_REPOS", "").split(",")
            if repo.strip()
        )

        pr_title_include_tags: frozenset[str] = _parse_title_tags(
            env.str("PR_TITLE_INCLUDE_TAGS", ""),
        )
        pr_title_exclude_tags: frozenset[str] = _parse_title_tags(
            env.str("PR_TITLE_EXCLUDE_TAGS", ""),
        )

        return cls(
            worker=worker,
            reviewer=reviewer,
            webhook_host=webhook_host,
            webhook_port=webhook_port,
            allowed_users=allowed_users,
            workspace_base_dir=workspace_base_dir,
            agent=CliAgentConfig(
                model=env.str("AGENT_MODEL", ""),
                max_turns=env.int("AGENT_MAX_TURNS", 0),
                cli_path=env.str("AGENT_CLI_PATH", ""),
            ),
            coding_guidelines=coding_guidelines,
            language_guidelines=language_guidelines,
            cleanup_interval_hours=cleanup_interval_hours,
            reviewer_triggers=reviewer_triggers,
            allowed_repos=allowed_repos,
            pr_title_include_tags=pr_title_include_tags,
            pr_title_exclude_tags=pr_title_exclude_tags,
        )


def _parse_title_tags(raw: str) -> frozenset[str]:
    """
    Parse a comma-separated string of tag names into a lowercased frozenset.

    Strips whitespace and lowercases each tag.

    Args:
        raw (str): Comma-separated tag names (e.g. ``"nominalbot, CI"``).

    Returns:
        frozenset[str]: The parsed tags, lowercased.
    """

    if not raw.strip():
        return frozenset()

    return frozenset(tag.strip().lower() for tag in raw.split(",") if tag.strip())


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


def _load_file_content(file_path: Path) -> str:
    """
    Read text content from a file path.

    Returns an empty string if the file does not exist, allowing the bot
    to run without the file when the default path is absent.

    Args:
        file_path (Path): Path to the file.

    Returns:
        str: The file contents, or empty string if the file is missing.
    """

    if not file_path.is_file():
        return ""

    return file_path.read_text(encoding="utf-8").strip()


def _load_language_guidelines(directory: Path) -> dict[str, str]:
    """
    Load all language guideline files from a directory.

    Each ``.md`` file in the directory becomes an entry keyed by its stem
    (e.g. ``python.md`` → ``"python"``). Missing or non-directory paths
    are silently ignored.

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
