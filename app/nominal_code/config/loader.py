from __future__ import annotations

import logging
from pathlib import Path

from nominal_code.config.agent import (
    AgentConfig,
    ApiAgentConfig,
    ProviderConfig,
    resolve_agent_config,
)
from nominal_code.config.env import load_app_settings
from nominal_code.config.kubernetes import KubernetesConfig
from nominal_code.config.models import AppSettings, GitHubSettings, GitLabSettings
from nominal_code.config.policies import FilteringPolicy, RoutingPolicy
from nominal_code.config.settings import (
    DEFAULT_GITLAB_API_BASE,
    Config,
    GitHubConfig,
    GitLabConfig,
    PromptsConfig,
    RedisConfig,
    ReviewerConfig,
    WebhookConfig,
    WorkspaceConfig,
    load_file_content,
    load_language_guidelines,
    parse_reviewer_triggers,
    parse_title_tags,
)
from nominal_code.models import EventType, ProviderName

logger: logging.Logger = logging.getLogger(__name__)


def load_config(
    *,
    require_webhook: bool = False,
    default_provider: str | None = None,
    model: str | None = None,
    max_turns: int | None = None,
    provider: ProviderName | None = None,
    guidelines_path: Path | None = None,
) -> Config:
    """
    Load application configuration from YAML and environment variables.

    Supports all operating modes via keyword arguments:

    - **Webhook mode**: pass ``require_webhook=True`` to enforce
      ``REVIEWER_BOT_USERNAME`` and ``ALLOWED_USERS``.
    - **CLI mode**: pass ``model``, ``max_turns``, and/or ``provider``
      to override settings. Agent type is determined by ``provider``.
    - **CI / job mode**: pass ``default_provider`` to force API agent
      mode with a fallback provider when ``AGENT_PROVIDER`` is unset.

    Args:
        require_webhook (bool): When True, require ``REVIEWER_BOT_USERNAME``
            and ``ALLOWED_USERS``, and populate the ``webhook`` field.
        default_provider (str | None): Fallback provider name for API agent
            mode. When set, the agent is always ``ApiAgentConfig``.
        model (str | None): Agent model override. None to use settings.
        max_turns (int | None): Agent max turns override. None to use settings.
        provider (ProviderName | None): LLM provider override. None to use
            settings.
        guidelines_path (Path | None): Custom coding guidelines file that
            overrides the default.

    Returns:
        Config: The resolved application configuration.

    Raises:
        ValueError: If required settings are missing or invalid.
    """

    settings: AppSettings = load_app_settings()

    reviewer: ReviewerConfig | None = _build_reviewer(
        settings=settings,
        require_webhook=require_webhook,
    )
    webhook: WebhookConfig | None = (
        _build_webhook(settings) if require_webhook else None
    )

    agent_config: AgentConfig = _build_agent(
        settings=settings,
        default_provider=default_provider,
        model=model,
        max_turns=max_turns,
        provider=provider,
    )

    workspace_base_dir: Path = (
        Path(settings.workspace.base_dir)
        if settings.workspace.base_dir
        else WorkspaceConfig().base_dir
    )

    coding_guidelines: str = load_file_content(
        Path(settings.prompts.coding_guidelines_path),
    )

    if guidelines_path is not None:
        custom_coding: str = load_file_content(guidelines_path)

        if custom_coding:
            coding_guidelines = custom_coding

    language_guidelines: dict[str, str] = load_language_guidelines(
        Path(settings.prompts.language_guidelines_dir),
    )

    return Config(
        github=_build_github_config(settings.github),
        gitlab=_build_gitlab_config(settings.gitlab),
        reviewer=reviewer,
        agent=agent_config,
        workspace=WorkspaceConfig(base_dir=workspace_base_dir),
        prompts=PromptsConfig(
            coding_guidelines=coding_guidelines,
            language_guidelines=language_guidelines,
        ),
        webhook=webhook,
    )


def _build_reviewer(
    settings: AppSettings,
    require_webhook: bool,
) -> ReviewerConfig | None:
    """
    Build the reviewer configuration from settings.

    In webhook mode, ``REVIEWER_BOT_USERNAME`` is required. In CLI/CI
    modes, the reviewer is always enabled with an empty bot username.

    Args:
        settings (AppSettings): The application settings.
        require_webhook (bool): Whether webhook-mode validation applies.

    Returns:
        ReviewerConfig | None: The reviewer config.

    Raises:
        ValueError: If ``require_webhook`` is True and no bot username is set.
    """

    reviewer_system_prompt: str = load_file_content(
        Path(settings.reviewer.system_prompt_path),
    )

    if require_webhook:
        if not settings.reviewer.bot_username:
            raise ValueError("REVIEWER_BOT_USERNAME must be set")

        return ReviewerConfig(
            bot_username=settings.reviewer.bot_username,
            system_prompt=reviewer_system_prompt,
        )

    return ReviewerConfig(
        bot_username="",
        system_prompt=reviewer_system_prompt,
    )


def _build_webhook(settings: AppSettings) -> WebhookConfig:
    """
    Build the webhook configuration from settings.

    Validates that ``ALLOWED_USERS`` is set and resolves filtering,
    routing, Kubernetes, and Redis sub-configs.

    Args:
        settings (AppSettings): The application settings.

    Returns:
        WebhookConfig: The webhook configuration.

    Raises:
        ValueError: If ``ALLOWED_USERS`` is empty.
    """

    allowed_users: frozenset[str] = frozenset(settings.access.allowed_users)

    if not allowed_users:
        raise ValueError("ALLOWED_USERS must contain at least one username")

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

    filtering: FilteringPolicy = FilteringPolicy(
        allowed_users=allowed_users,
        allowed_repos=allowed_repos,
        pr_title_include_tags=pr_title_include_tags,
        pr_title_exclude_tags=pr_title_exclude_tags,
    )

    routing: RoutingPolicy = RoutingPolicy(
        reviewer_triggers=reviewer_triggers,
        reviewer_bot_username=settings.reviewer.bot_username,
    )

    kubernetes_config: KubernetesConfig | None = _resolve_kubernetes(settings)

    redis_config: RedisConfig | None = None

    if settings.redis.url:
        redis_config = RedisConfig(
            url=settings.redis.url,
            key_ttl_seconds=settings.redis.key_ttl_seconds,
        )

    return WebhookConfig(
        host=settings.webhook.host,
        port=settings.webhook.port,
        filtering=filtering,
        routing=routing,
        kubernetes=kubernetes_config,
        redis=redis_config,
    )


def _build_agent(
    settings: AppSettings,
    default_provider: str | None,
    model: str | None,
    max_turns: int | None,
    provider: ProviderName | None,
) -> AgentConfig:
    """
    Build the agent configuration from settings and overrides.

    When ``default_provider`` is set, forces ``ApiAgentConfig`` with
    that provider as fallback. Otherwise, uses ``resolve_agent_config``
    which returns ``CliAgentConfig`` when no provider is specified.

    Args:
        settings (AppSettings): The application settings.
        default_provider (str | None): Fallback provider for API mode.
        model (str | None): Model override. None to use settings.
        max_turns (int | None): Max turns override. None to use settings.
        provider (ProviderName | None): Provider override. None to use settings.

    Returns:
        AgentConfig: The resolved agent configuration.

    Raises:
        ValueError: If the provider name is not recognised.
    """

    effective_model: str | None = model if model is not None else settings.agent.model
    effective_max_turns: int = (
        max_turns if max_turns is not None else settings.agent.max_turns
    )

    if default_provider is not None:
        return _build_api_agent(
            settings=settings,
            default_provider=default_provider,
            model=effective_model,
            max_turns=effective_max_turns,
        )

    provider_name: ProviderName | None = provider

    if provider_name is None and settings.agent.provider:
        try:
            provider_name = ProviderName(settings.agent.provider)
        except ValueError:
            available: str = ", ".join(p.value for p in ProviderName)

            raise ValueError(
                f"Unknown AGENT_PROVIDER: {settings.agent.provider!r}. "
                f"Available: {available}",
            ) from None

    return resolve_agent_config(
        provider_name=provider_name,
        model=effective_model,
        max_turns=effective_max_turns,
        cli_path=settings.agent.cli_path,
    )


def _build_api_agent(
    settings: AppSettings,
    default_provider: str,
    model: str | None,
    max_turns: int,
) -> ApiAgentConfig:
    """
    Build an API agent configuration with provider fallback.

    Args:
        settings (AppSettings): The application settings.
        default_provider (str): Fallback provider name.
        model (str | None): Model override.
        max_turns (int): Max turns.

    Returns:
        ApiAgentConfig: The resolved API agent configuration.

    Raises:
        ValueError: If the provider name is not recognised.
    """

    from nominal_code.llm.registry import PROVIDERS

    provider_name_str: str = settings.agent.provider or default_provider

    try:
        provider_name: ProviderName = ProviderName(provider_name_str)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown AGENT_PROVIDER: {provider_name_str!r}. Available: {available}",
        ) from None

    provider_config: ProviderConfig = PROVIDERS[provider_name]

    if model:
        provider_config = provider_config.model_copy(
            update={"model": model},
        )

    return ApiAgentConfig(
        provider=provider_config,
        max_turns=max_turns,
    )


def _resolve_kubernetes(
    settings: AppSettings,
) -> KubernetesConfig | None:
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


def _build_github_config(settings: GitHubSettings) -> GitHubConfig:
    """
    Build a GitHubConfig from mutable settings.

    Resolves the private key from inline value or file path.

    Args:
        settings (GitHubSettings): The mutable GitHub settings.

    Returns:
        GitHubConfig: The frozen GitHub configuration.
    """

    private_key: str | None = settings.private_key

    if not private_key and settings.private_key_path:
        try:
            private_key = (
                Path(settings.private_key_path)
                .read_text(
                    encoding="utf-8",
                )
                .strip()
            )
        except OSError:
            logger.warning(
                "Could not read private key from %s",
                settings.private_key_path,
            )

    return GitHubConfig(
        token=settings.token,
        app_id=settings.app_id,
        private_key=private_key,
        installation_id=settings.installation_id,
        webhook_secret=settings.webhook_secret,
        api_base=settings.api_base,
    )


def _build_gitlab_config(settings: GitLabSettings) -> GitLabConfig:
    """
    Build a GitLabConfig from mutable settings.

    Resolves the API base URL from ``api_base``, ``ci_server_url``,
    or the default ``DEFAULT_GITLAB_API_BASE``.

    Args:
        settings (GitLabSettings): The mutable GitLab settings.

    Returns:
        GitLabConfig: The frozen GitLab configuration.
    """

    api_base: str = (
        settings.api_base or settings.ci_server_url or DEFAULT_GITLAB_API_BASE
    )

    return GitLabConfig(
        token=settings.token,
        webhook_secret=settings.webhook_secret,
        api_base=api_base,
    )
