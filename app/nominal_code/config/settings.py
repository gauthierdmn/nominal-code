from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from nominal_code.config.agent import AgentConfig
from nominal_code.config.kubernetes import KubernetesConfig
from nominal_code.config.policies import FilteringPolicy, RoutingPolicy
from nominal_code.models import EventType

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_GITHUB_API_BASE: str = "https://api.github.com"
DEFAULT_GITLAB_API_BASE: str = "https://gitlab.com"
DEFAULT_REVIEWER_PROMPT_PATH: Path = Path("prompts/reviewer_prompt.md")
DEFAULT_CODING_GUIDELINES_PATH: Path = Path("prompts/coding_guidelines.md")
DEFAULT_LANGUAGE_GUIDELINES_DIR: Path = Path("prompts/languages")
DEFAULT_REDIS_KEY_TTL_SECONDS: int = 86400


class GitHubConfig(BaseModel):
    """
    GitHub platform configuration.

    Attributes:
        token (str): Personal access token.
        app_id (str): GitHub App numeric ID.
        private_key (str): Resolved PEM-encoded RSA private key.
        installation_id (int): Fixed installation ID for CLI/CI modes.
        webhook_secret (str): Webhook HMAC verification secret.
        api_base (str): GitHub API base URL.
    """

    model_config = ConfigDict(frozen=True)

    token: str | None = None
    app_id: str | None = None
    private_key: str | None = None
    installation_id: int = 0
    webhook_secret: str | None = None
    api_base: str = DEFAULT_GITHUB_API_BASE


class GitLabConfig(BaseModel):
    """
    GitLab platform configuration.

    Attributes:
        token (str): Personal access token.
        webhook_secret (str): Webhook verification secret.
        api_base (str): Resolved GitLab instance base URL.
    """

    model_config = ConfigDict(frozen=True)

    token: str | None = None
    webhook_secret: str | None = None
    api_base: str = DEFAULT_GITLAB_API_BASE


SUGGESTIONS_PROMPT_PATH: str = "prompts/reviewer_suggestions.md"


class ReviewerConfig(BaseModel):
    """
    Reviewer bot configuration.

    Attributes:
        bot_username (str): The @mention name for the reviewer bot.
        system_prompt (str): System prompt text for reviewer bot invocations.
        suggestions_prompt (str): Prompt section appended to the system prompt
            when non-empty, enabling one-click-apply code suggestions.
    """

    model_config = ConfigDict(frozen=True)

    bot_username: str
    system_prompt: str
    suggestions_prompt: str = ""


class PromptsConfig(BaseModel):
    """
    Prompt and guideline configuration.

    Attributes:
        coding_guidelines (str): Coding guidelines text appended to the
            system prompt.
        language_guidelines (dict[str, str]): Language-specific guidelines
            keyed by language name.
    """

    model_config = ConfigDict(frozen=True)

    coding_guidelines: str = ""
    language_guidelines: dict[str, str] = {}


class WorkspaceConfig(BaseModel):
    """
    Workspace directory configuration.

    Attributes:
        base_dir (Path): Directory for cloning repositories.
    """

    model_config = ConfigDict(frozen=True)

    base_dir: Path = Path(tempfile.gettempdir()) / "nominal-code"


class RedisConfig(BaseModel):
    """
    Redis connection configuration.

    Attributes:
        url (str): Redis connection URL.
        key_ttl_seconds (int): TTL for Redis keys in seconds.
    """

    model_config = ConfigDict(frozen=True)

    url: str
    key_ttl_seconds: int = DEFAULT_REDIS_KEY_TTL_SECONDS


class WebhookConfig(BaseModel):
    """
    Webhook server configuration.

    Bundles everything the webhook server needs: bind address, filtering,
    routing, optional Kubernetes runner, and optional Redis.

    Attributes:
        host (str): Host to bind the webhook server.
        port (int): Port to bind the webhook server.
        filtering (FilteringPolicy): Event filtering rules (repos, users,
            title tags).
        routing (RoutingPolicy): Event routing rules (reviewer triggers,
            bot usernames).
        kubernetes (KubernetesConfig | None): K8s job runner config.
        redis (RedisConfig | None): Redis connection config.
    """

    model_config = ConfigDict(frozen=True)

    host: str = "0.0.0.0"
    port: int = 8080
    filtering: FilteringPolicy = FilteringPolicy()
    routing: RoutingPolicy = RoutingPolicy()
    kubernetes: KubernetesConfig | None = None
    redis: RedisConfig | None = None


class Config(BaseModel):
    """
    Application configuration loaded from YAML and/or environment variables.

    Attributes:
        github (GitHubConfig): GitHub platform configuration.
        gitlab (GitLabConfig): GitLab platform configuration.
        reviewer (ReviewerConfig | None): Reviewer bot config, or None if disabled.
        agent (AgentConfig): Agent runner configuration.
        workspace (WorkspaceConfig): Workspace directory configuration.
        prompts (PromptsConfig): Prompt and guideline configuration.
        webhook (WebhookConfig | None): Webhook server configuration, or
            None in CLI/CI modes.
    """

    model_config = ConfigDict(frozen=True)

    github: GitHubConfig = GitHubConfig()
    gitlab: GitLabConfig = GitLabConfig()
    reviewer: ReviewerConfig | None = None
    agent: AgentConfig
    workspace: WorkspaceConfig = WorkspaceConfig()
    prompts: PromptsConfig = PromptsConfig()
    webhook: WebhookConfig | None = None

    @classmethod
    def from_env(cls, **kwargs: object) -> Config:
        """
        Build a Config by reading YAML and environment variables.

        Accepts the same keyword arguments as ``load_config()``.

        Returns:
            Config: The resolved configuration instance.
        """

        from nominal_code.config.loader import load_config

        return load_config(**kwargs)  # type: ignore[arg-type]


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
