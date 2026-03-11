from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from nominal_code.config.agent import (
    AgentConfig,
    ApiAgentConfig,
    ProviderConfig,
    resolve_agent_config,
)
from nominal_code.config.kubernetes import KubernetesConfig
from nominal_code.config.models import AppSettings
from nominal_code.config.settings import (
    Config,
    ReviewerConfig,
    WorkerConfig,
    load_file_content,
    load_language_guidelines,
    parse_reviewer_triggers,
    parse_title_tags,
)
from nominal_code.models import EventType, ProviderName

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_BASE_DIR: Path = Path(tempfile.gettempdir()) / "nominal-code"


def load_config(config_path: str = "") -> Config:
    """
    Load a full Config for webhook server mode.

    Reads from YAML file and environment variable overrides. At least one of
    worker or reviewer bot must be configured. ``ALLOWED_USERS`` is required.

    Args:
        config_path (str): Optional path to YAML config file. When empty,
            uses ``CONFIG_PATH`` env var or ``config.yaml`` in CWD.

    Returns:
        Config: A fully populated configuration instance.

    Raises:
        ValueError: If no bot is configured, ALLOWED_USERS is empty, or an
            environment variable has an invalid value.
    """

    settings: AppSettings = AppSettings.from_env()

    worker: WorkerConfig | None = None

    if settings.worker.bot_username:
        worker_system_prompt: str = load_file_content(
            Path(settings.worker.system_prompt_path),
        )
        worker = WorkerConfig(
            bot_username=settings.worker.bot_username,
            system_prompt=worker_system_prompt,
        )

    reviewer: ReviewerConfig | None = None

    if settings.reviewer.bot_username:
        reviewer_system_prompt: str = load_file_content(
            Path(settings.reviewer.system_prompt_path),
        )
        reviewer = ReviewerConfig(
            bot_username=settings.reviewer.bot_username,
            system_prompt=reviewer_system_prompt,
        )

    if worker is None and reviewer is None:
        raise ValueError(
            "At least one of WORKER_BOT_USERNAME or REVIEWER_BOT_USERNAME must be set",
        )

    allowed_users: frozenset[str] = frozenset(settings.access.allowed_users)

    if not allowed_users:
        raise ValueError("ALLOWED_USERS must contain at least one username")

    workspace_base_dir: Path = (
        Path(settings.workspace.base_dir)
        if settings.workspace.base_dir
        else DEFAULT_WORKSPACE_BASE_DIR
    )

    coding_guidelines: str = load_file_content(
        Path(settings.prompts.coding_guidelines_path),
    )
    language_guidelines: dict[str, str] = load_language_guidelines(
        Path(settings.prompts.language_guidelines_dir),
    )

    reviewer_triggers: frozenset[EventType] = parse_reviewer_triggers(
        ",".join(settings.reviewer.triggers),
    )

    allowed_repos: frozenset[str] = frozenset(settings.access.allowed_repos)

    pr_title_include_tags: frozenset[str] = parse_title_tags(
        ",".join(settings.access.pr_title_include_tags),
    )
    pr_title_exclude_tags: frozenset[str] = parse_title_tags(
        ",".join(settings.access.pr_title_exclude_tags),
    )

    provider_name: ProviderName | None = None

    if settings.agent.provider:
        try:
            provider_name = ProviderName(settings.agent.provider)
        except ValueError:
            available: str = ", ".join(p.value for p in ProviderName)

            raise ValueError(
                f"Unknown AGENT_PROVIDER: {settings.agent.provider!r}. "
                f"Available: {available}",
            ) from None

    agent_config: AgentConfig = resolve_agent_config(
        provider_name=provider_name,
        model=settings.agent.model,
        max_turns=settings.agent.max_turns,
        cli_path=settings.agent.cli_path,
    )

    kubernetes_config: KubernetesConfig | None = _resolve_kubernetes(settings)

    return Config(
        worker=worker,
        reviewer=reviewer,
        webhook_host=settings.webhook.host,
        webhook_port=settings.webhook.port,
        allowed_users=allowed_users,
        workspace_base_dir=workspace_base_dir,
        agent=agent_config,
        coding_guidelines=coding_guidelines,
        language_guidelines=language_guidelines,
        cleanup_interval_hours=settings.workspace.cleanup_interval_hours,
        reviewer_triggers=reviewer_triggers,
        allowed_repos=allowed_repos,
        pr_title_include_tags=pr_title_include_tags,
        pr_title_exclude_tags=pr_title_exclude_tags,
        kubernetes=kubernetes_config,
        redis_url=settings.redis.url,
        redis_key_ttl_seconds=settings.redis.key_ttl_seconds,
    )


def load_config_for_cli(
    model: str = "",
    max_turns: int = 0,
    provider: ProviderName | None = None,
) -> Config:
    """
    Build a Config for CLI mode without requiring webhook-only settings.

    Reviewer is always enabled with the default system prompt. Settings
    like ``ALLOWED_USERS`` and bot usernames are not required.

    Args:
        model (str): Optional agent model override.
        max_turns (int): Optional agent max turns override.
        provider (ProviderName | None): Optional LLM provider.

    Returns:
        Config: A configuration suitable for one-off CLI reviews.
    """

    settings: AppSettings = AppSettings.from_env()

    reviewer_system_prompt: str = load_file_content(
        Path(settings.reviewer.system_prompt_path),
    )

    workspace_base_dir: Path = (
        Path(settings.workspace.base_dir)
        if settings.workspace.base_dir
        else DEFAULT_WORKSPACE_BASE_DIR
    )

    coding_guidelines: str = load_file_content(
        Path(settings.prompts.coding_guidelines_path),
    )
    language_guidelines: dict[str, str] = load_language_guidelines(
        Path(settings.prompts.language_guidelines_dir),
    )

    provider_name: ProviderName | None = provider

    if not provider_name and settings.agent.provider:
        provider_name = ProviderName(settings.agent.provider)

    effective_model: str = model or settings.agent.model
    effective_max_turns: int = max_turns or settings.agent.max_turns

    agent_config: AgentConfig = resolve_agent_config(
        provider_name=provider_name,
        model=effective_model,
        max_turns=effective_max_turns,
        cli_path=settings.agent.cli_path,
    )

    return Config(
        worker=None,
        reviewer=ReviewerConfig(
            bot_username="",
            system_prompt=reviewer_system_prompt,
        ),
        webhook_host="",
        webhook_port=0,
        allowed_users=frozenset(),
        workspace_base_dir=workspace_base_dir,
        agent=agent_config,
        coding_guidelines=coding_guidelines,
        language_guidelines=language_guidelines,
        cleanup_interval_hours=0,
        redis_url="",
        redis_key_ttl_seconds=86400,
    )


def load_config_for_ci(
    provider: ProviderConfig,
    model: str = "",
    max_turns: int = 0,
    guidelines_path: Path = Path(),
) -> Config:
    """
    Build a Config for CI mode (GitHub Actions / GitLab CI).

    Calls the LLM provider API directly and optionally accepts a custom
    coding guidelines path.

    Args:
        provider (ProviderConfig): The resolved provider configuration.
        model (str): Optional model override.
        max_turns (int): Optional agent max turns override.
        guidelines_path (Path): Optional path to a coding guidelines file.

    Returns:
        Config: A configuration suitable for CI-triggered reviews.
    """

    settings: AppSettings = AppSettings.from_env()

    model_override: str = model or settings.agent.model

    if model_override:
        provider = provider.model_copy(update={"model": model_override})

    reviewer_system_prompt: str = load_file_content(
        Path(settings.reviewer.system_prompt_path),
    )

    workspace_base_dir: Path = (
        Path(settings.workspace.base_dir)
        if settings.workspace.base_dir
        else DEFAULT_WORKSPACE_BASE_DIR
    )

    coding_guidelines: str = load_file_content(
        Path(settings.prompts.coding_guidelines_path),
    )

    if guidelines_path != Path():
        custom_coding: str = load_file_content(guidelines_path)

        if custom_coding:
            coding_guidelines = custom_coding

    language_guidelines: dict[str, str] = load_language_guidelines(
        Path(settings.prompts.language_guidelines_dir),
    )

    effective_max_turns: int = max_turns or settings.agent.max_turns

    return Config(
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
            max_turns=effective_max_turns,
        ),
        coding_guidelines=coding_guidelines,
        language_guidelines=language_guidelines,
        cleanup_interval_hours=0,
        redis_url=settings.redis.url,
        redis_key_ttl_seconds=settings.redis.key_ttl_seconds,
    )


def _resolve_kubernetes(settings: AppSettings) -> KubernetesConfig | None:
    """
    Resolve KubernetesConfig from settings when a K8s image is configured.

    Returns ``None`` when ``kubernetes.image`` is empty.

    Args:
        settings (AppSettings): The application settings.

    Returns:
        KubernetesConfig | None: The resolved config, or ``None`` when disabled.
    """

    if not settings.kubernetes.image:
        return None

    env_from_secrets: tuple[str, ...] = tuple(settings.kubernetes.env_from_secrets)

    return KubernetesConfig(
        image=settings.kubernetes.image,
        namespace=settings.kubernetes.namespace,
        service_account=settings.kubernetes.service_account,
        image_pull_policy=settings.kubernetes.image_pull_policy,
        backoff_limit=settings.kubernetes.backoff_limit,
        active_deadline_seconds=settings.kubernetes.active_deadline_seconds,
        ttl_after_finished=settings.kubernetes.ttl_after_finished,
        env_from_secrets=env_from_secrets,
        resource_requests_cpu=settings.kubernetes.resources.requests.cpu,
        resource_requests_memory=settings.kubernetes.resources.requests.memory,
        resource_limits_cpu=settings.kubernetes.resources.limits.cpu,
        resource_limits_memory=settings.kubernetes.resources.limits.memory,
    )
