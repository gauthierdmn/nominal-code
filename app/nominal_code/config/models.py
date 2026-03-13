from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from environs import Env
from pydantic import BaseModel, Field

_env: Env = Env()


class WebhookSettings(BaseModel):
    """
    Webhook server settings.

    Attributes:
        host (str): Host to bind the webhook server.
        port (int): Port to bind the webhook server.
    """

    host: str = "0.0.0.0"
    port: int = 8080


class WorkerSettings(BaseModel):
    """
    Worker bot settings.

    Attributes:
        bot_username (str): The @mention name for the worker bot.
        system_prompt_path (str): Path to the system prompt file.
    """

    bot_username: str = ""
    system_prompt_path: str = "prompts/system_prompt.md"


class ReviewerSettings(BaseModel):
    """
    Reviewer bot settings.

    Attributes:
        bot_username (str): The @mention name for the reviewer bot.
        system_prompt_path (str): Path to the reviewer prompt file.
        triggers (list[str]): PR lifecycle events that auto-trigger the reviewer.
    """

    bot_username: str = ""
    system_prompt_path: str = "prompts/reviewer_prompt.md"
    triggers: list[str] = Field(default_factory=list)


class AgentSettings(BaseModel):
    """
    Agent runner settings.

    Attributes:
        provider (str): LLM provider name (empty for CLI mode).
        model (str): Model name override.
        max_turns (int): Maximum agentic turns (0 for unlimited).
        cli_path (str): Path to the Claude Code CLI binary.
    """

    provider: str = ""
    model: str = ""
    max_turns: int = 0
    cli_path: str = ""


class AccessSettings(BaseModel):
    """
    Access control settings.

    Attributes:
        allowed_users (list[str]): Usernames permitted to trigger the bots.
        allowed_repos (list[str]): Repository full names to process.
        pr_title_include_tags (list[str]): Allowlist of PR title tags.
        pr_title_exclude_tags (list[str]): Blocklist of PR title tags.
    """

    allowed_users: list[str] = Field(default_factory=list)
    allowed_repos: list[str] = Field(default_factory=list)
    pr_title_include_tags: list[str] = Field(default_factory=list)
    pr_title_exclude_tags: list[str] = Field(default_factory=list)


class WorkspaceSettings(BaseModel):
    """
    Workspace management settings.

    Attributes:
        base_dir (str): Directory for cloning repositories.
    """

    base_dir: str = ""


class PromptsSettings(BaseModel):
    """
    Prompt file settings.

    Attributes:
        coding_guidelines_path (str): Path to coding guidelines file.
        language_guidelines_dir (str): Path to language guidelines directory.
    """

    coding_guidelines_path: str = "prompts/coding_guidelines.md"
    language_guidelines_dir: str = "prompts/languages"


class RedisSettings(BaseModel):
    """
    Redis connection settings.

    Attributes:
        url (str): Redis connection URL.
        key_ttl_seconds (int): TTL for Redis keys in seconds.
    """

    url: str = ""
    key_ttl_seconds: int = 86400


class KubernetesResourceSettings(BaseModel):
    """
    Kubernetes resource requests or limits.

    Attributes:
        cpu (str): CPU request/limit (e.g. ``"500m"``).
        memory (str): Memory request/limit (e.g. ``"512Mi"``).
    """

    cpu: str = ""
    memory: str = ""


class KubernetesResourcesSettings(BaseModel):
    """
    Kubernetes resource specifications.

    Attributes:
        requests (KubernetesResourceSettings): Resource requests.
        limits (KubernetesResourceSettings): Resource limits.
    """

    requests: KubernetesResourceSettings = Field(
        default_factory=KubernetesResourceSettings,
    )
    limits: KubernetesResourceSettings = Field(
        default_factory=KubernetesResourceSettings,
    )


class KubernetesSettings(BaseModel):
    """
    Kubernetes job runner settings.

    Attributes:
        image (str): Docker image for review pods.
        namespace (str): Kubernetes namespace for review Jobs.
        service_account (str): ServiceAccount name for review pods.
        image_pull_policy (str): Image pull policy override.
        backoff_limit (int): Job retry count (0 = no retries).
        active_deadline_seconds (int): Per-job timeout in seconds.
        ttl_after_finished (int): Seconds before completed Jobs are cleaned up.
        env_from_secrets (list[str]): K8s Secret names to mount as env.
        resources (KubernetesResourcesSettings): Resource requests and limits.
    """

    image: str = ""
    namespace: str = "default"
    service_account: str = ""
    image_pull_policy: str = ""
    backoff_limit: int = 0
    active_deadline_seconds: int = 600
    ttl_after_finished: int = 3600
    env_from_secrets: list[str] = Field(default_factory=list)
    resources: KubernetesResourcesSettings = Field(
        default_factory=KubernetesResourcesSettings,
    )


def _yaml_settings_source() -> dict[str, Any]:
    """
    Load settings from a YAML config file.

    The file path is determined by the ``CONFIG_PATH`` environment variable,
    or ``config.yaml`` in the current working directory if it exists.

    Returns:
        dict[str, Any]: Parsed YAML data, or empty dict if no file found.
    """

    config_path: str = _env.str("CONFIG_PATH", "")

    if not config_path:
        default_path: Path = Path("config.yaml")

        if default_path.is_file():
            config_path = str(default_path)

    if not config_path:
        return {}

    path: Path = Path(config_path)

    if not path.is_file():
        return {}

    with path.open(encoding="utf-8") as file_handle:
        data: Any = yaml.safe_load(file_handle)

    if not isinstance(data, dict):
        return {}

    return data


class AppSettings(BaseModel):
    """
    Top-level application settings loaded from YAML and environment variables.

    Priority order (last wins): model defaults, YAML file, environment variables.

    Attributes:
        webhook (WebhookSettings): Webhook server settings.
        worker (WorkerSettings): Worker bot settings.
        reviewer (ReviewerSettings): Reviewer bot settings.
        agent (AgentSettings): Agent runner settings.
        access (AccessSettings): Access control settings.
        workspace (WorkspaceSettings): Workspace management settings.
        prompts (PromptsSettings): Prompt file settings.
        redis (RedisSettings): Redis connection settings.
        kubernetes (KubernetesSettings): Kubernetes job runner settings.
    """

    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    worker: WorkerSettings = Field(default_factory=WorkerSettings)
    reviewer: ReviewerSettings = Field(default_factory=ReviewerSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    access: AccessSettings = Field(default_factory=AccessSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    prompts: PromptsSettings = Field(default_factory=PromptsSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    kubernetes: KubernetesSettings = Field(default_factory=KubernetesSettings)

    @classmethod
    def from_env(cls) -> AppSettings:
        """
        Load settings from YAML file and environment variable overrides.

        Supports legacy flat environment variable names by reading them
        explicitly and merging into the nested structure. Priority order
        (last wins): model defaults, YAML file, environment variables.

        Returns:
            AppSettings: The resolved settings.
        """

        yaml_data: dict[str, Any] = _yaml_settings_source()
        env_overrides: dict[str, Any] = _collect_env_overrides()
        merged: dict[str, Any] = _deep_merge(yaml_data, env_overrides)

        return cls(**merged)


def _collect_env_overrides() -> dict[str, Any]:
    """
    Collect legacy flat environment variables into nested settings structure.

    Maps legacy env var names to their corresponding nested paths in the
    settings model. Only includes values that are actually set in the
    environment.

    Returns:
        dict[str, Any]: Nested dictionary of environment variable overrides.
    """

    result: dict[str, Any] = {}

    _ENV_MAP: list[tuple[str, list[str]]] = [
        ("WEBHOOK_HOST", ["webhook", "host"]),
        ("WEBHOOK_PORT", ["webhook", "port"]),
        ("WORKER_BOT_USERNAME", ["worker", "bot_username"]),
        ("WORKER_SYSTEM_PROMPT", ["worker", "system_prompt_path"]),
        ("REVIEWER_BOT_USERNAME", ["reviewer", "bot_username"]),
        ("REVIEWER_SYSTEM_PROMPT", ["reviewer", "system_prompt_path"]),
        ("REVIEWER_TRIGGERS", ["reviewer", "triggers"]),
        ("AGENT_PROVIDER", ["agent", "provider"]),
        ("AGENT_MODEL", ["agent", "model"]),
        ("AGENT_MAX_TURNS", ["agent", "max_turns"]),
        ("AGENT_CLI_PATH", ["agent", "cli_path"]),
        ("ALLOWED_USERS", ["access", "allowed_users"]),
        ("ALLOWED_REPOS", ["access", "allowed_repos"]),
        ("PR_TITLE_INCLUDE_TAGS", ["access", "pr_title_include_tags"]),
        ("PR_TITLE_EXCLUDE_TAGS", ["access", "pr_title_exclude_tags"]),
        ("WORKSPACE_BASE_DIR", ["workspace", "base_dir"]),
        ("CODING_GUIDELINES", ["prompts", "coding_guidelines_path"]),
        ("LANGUAGE_GUIDELINES_DIR", ["prompts", "language_guidelines_dir"]),
        ("REDIS_URL", ["redis", "url"]),
        ("REDIS_KEY_TTL_SECONDS", ["redis", "key_ttl_seconds"]),
        ("K8S_IMAGE", ["kubernetes", "image"]),
        ("K8S_NAMESPACE", ["kubernetes", "namespace"]),
        ("K8S_SERVICE_ACCOUNT", ["kubernetes", "service_account"]),
        ("K8S_IMAGE_PULL_POLICY", ["kubernetes", "image_pull_policy"]),
        ("K8S_BACKOFF_LIMIT", ["kubernetes", "backoff_limit"]),
        ("K8S_ACTIVE_DEADLINE_SECONDS", ["kubernetes", "active_deadline_seconds"]),
        ("K8S_TTL_AFTER_FINISHED", ["kubernetes", "ttl_after_finished"]),
        ("K8S_ENV_FROM_SECRETS", ["kubernetes", "env_from_secrets"]),
        ("K8S_RESOURCE_REQUESTS_CPU", ["kubernetes", "resources", "requests", "cpu"]),
        (
            "K8S_RESOURCE_REQUESTS_MEMORY",
            ["kubernetes", "resources", "requests", "memory"],
        ),
        ("K8S_RESOURCE_LIMITS_CPU", ["kubernetes", "resources", "limits", "cpu"]),
        (
            "K8S_RESOURCE_LIMITS_MEMORY",
            ["kubernetes", "resources", "limits", "memory"],
        ),
    ]

    COMMA_LIST_KEYS: set[str] = {
        "ALLOWED_USERS",
        "ALLOWED_REPOS",
        "PR_TITLE_INCLUDE_TAGS",
        "PR_TITLE_EXCLUDE_TAGS",
        "REVIEWER_TRIGGERS",
        "K8S_ENV_FROM_SECRETS",
    }

    INT_KEYS: set[str] = {
        "WEBHOOK_PORT",
        "AGENT_MAX_TURNS",
        "REDIS_KEY_TTL_SECONDS",
        "K8S_BACKOFF_LIMIT",
        "K8S_ACTIVE_DEADLINE_SECONDS",
        "K8S_TTL_AFTER_FINISHED",
    }

    for env_name, path in _ENV_MAP:
        raw: str | None = os.environ.get(env_name)

        if raw is None:
            continue

        value: str | int | list[str]

        if env_name in COMMA_LIST_KEYS:
            value = [item.strip() for item in raw.split(",") if item.strip()]
        elif env_name in INT_KEYS:
            value = int(raw)
        else:
            value = raw

        _set_nested(result, path, value)

    return result


def _set_nested(data: dict[str, Any], path: list[str], value: Any) -> None:
    """
    Set a value in a nested dictionary using a list of keys as the path.

    Creates intermediate dictionaries as needed.

    Args:
        data (dict[str, Any]): The target dictionary.
        path (list[str]): The key path (e.g. ``["kubernetes", "namespace"]``).
        value (Any): The value to set.
    """

    for key in path[:-1]:
        data = data.setdefault(key, {})

    data[path[-1]] = value


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep-merge two dictionaries, with override taking precedence.

    Args:
        base (dict[str, Any]): The base dictionary.
        override (dict[str, Any]): The override dictionary.

    Returns:
        dict[str, Any]: The merged dictionary.
    """

    result: dict[str, Any] = dict(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value

    return result
