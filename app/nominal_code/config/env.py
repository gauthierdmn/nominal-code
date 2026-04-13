from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from environs import Env

from nominal_code.config.models import AppSettings

_env: Env = Env()

ENV_MAP: list[tuple[str, list[str]]] = [
    ("GITHUB_TOKEN", ["github", "token"]),
    ("GITHUB_APP_ID", ["github", "app_id"]),
    ("GITHUB_APP_PRIVATE_KEY", ["github", "private_key"]),
    ("GITHUB_APP_PRIVATE_KEY_PATH", ["github", "private_key_path"]),
    ("GITHUB_INSTALLATION_ID", ["github", "installation_id"]),
    ("GITHUB_WEBHOOK_SECRET", ["github", "webhook_secret"]),
    ("GITHUB_API_BASE", ["github", "api_base"]),
    ("GITLAB_TOKEN", ["gitlab", "token"]),
    ("GITLAB_WEBHOOK_SECRET", ["gitlab", "webhook_secret"]),
    ("GITLAB_API_BASE", ["gitlab", "api_base"]),
    ("CI_SERVER_URL", ["gitlab", "ci_server_url"]),
    ("WEBHOOK_HOST", ["webhook", "host"]),
    ("WEBHOOK_PORT", ["webhook", "port"]),
    ("REVIEWER_BOT_USERNAME", ["reviewer", "bot_username"]),
    ("REVIEWER_SYSTEM_PROMPT", ["reviewer", "system_prompt_path"]),
    ("REVIEWER_TRIGGERS", ["reviewer", "triggers"]),
    ("INLINE_SUGGESTIONS", ["reviewer", "inline_suggestions"]),
    ("AGENT_PROVIDER", ["agent", "reviewer", "provider"]),
    ("AGENT_MODEL", ["agent", "reviewer", "model"]),
    ("AGENT_CLI_PATH", ["agent", "cli_path"]),
    ("AGENT_EXPLORER_PROVIDER", ["agent", "explorer", "provider"]),
    ("AGENT_EXPLORER_MODEL", ["agent", "explorer", "model"]),
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

COMMA_LIST_KEYS: frozenset[str] = frozenset(
    {
        "ALLOWED_USERS",
        "ALLOWED_REPOS",
        "PR_TITLE_INCLUDE_TAGS",
        "PR_TITLE_EXCLUDE_TAGS",
        "REVIEWER_TRIGGERS",
        "K8S_ENV_FROM_SECRETS",
    }
)

INT_KEYS: frozenset[str] = frozenset(
    {
        "WEBHOOK_PORT",
        "REDIS_KEY_TTL_SECONDS",
        "GITHUB_INSTALLATION_ID",
        "K8S_BACKOFF_LIMIT",
        "K8S_ACTIVE_DEADLINE_SECONDS",
        "K8S_TTL_AFTER_FINISHED",
    }
)

BOOL_KEYS: frozenset[str] = frozenset(
    {
        "INLINE_SUGGESTIONS",
    }
)


def load_app_settings() -> AppSettings:
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

    return AppSettings(**merged)


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

    for env_name, path in ENV_MAP:
        raw: str | None = os.environ.get(env_name)

        if raw is None:
            continue

        value: str | int | bool | list[str]

        if env_name in COMMA_LIST_KEYS:
            value = [item.strip() for item in raw.split(",") if item.strip()]
        elif env_name in INT_KEYS:
            value = int(raw)
        elif env_name in BOOL_KEYS:
            value = raw.lower() in ("true", "1", "yes")
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
