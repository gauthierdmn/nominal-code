from __future__ import annotations

import logging
from pathlib import Path

from nominal_code.config.agent import (
    EXPLORER_DEFAULT_MAX_TURNS,
    REVIEWER_DEFAULT_MAX_TURNS,
    AgentConfig,
    AgentRoleConfig,
    ApiAgentConfig,
    CliAgentConfig,
)
from nominal_code.config.env import load_app_settings
from nominal_code.config.kubernetes import KubernetesConfig
from nominal_code.config.models import (
    AgentRoleSettings,
    AppSettings,
    GitHubSettings,
    GitLabSettings,
)
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
from nominal_code.llm.registry import PROVIDERS
from nominal_code.models import EventType, ProviderName
from nominal_code.prompts import load_bundled_language_guidelines, load_prompt

logger: logging.Logger = logging.getLogger(__name__)

REVIEWER_BUNDLED_PROMPT: str = "reviewer_prompt.md"
EXPLORER_BUNDLED_PROMPT: str = "explore/explorer.md"


def load_config(
    *,
    require_webhook: bool = False,
    default_provider: str | None = None,
    model: str | None = None,
    provider: ProviderName | None = None,
    guidelines_path: Path | None = None,
) -> Config:
    """
    Load application configuration from YAML and environment variables.

    Supports all operating modes via keyword arguments:

    - **Webhook mode**: pass ``require_webhook=True`` to enforce
      ``REVIEWER_BOT_USERNAME`` and ``ALLOWED_USERS``.
    - **CLI mode**: pass ``model`` and/or ``provider`` to override
      settings. Agent type is determined by ``provider``.
    - **CI / job mode**: pass ``default_provider`` to force API agent
      mode with a fallback provider when ``AGENT_PROVIDER`` is unset.

    Args:
        require_webhook (bool): When True, require ``REVIEWER_BOT_USERNAME``
            and ``ALLOWED_USERS``, and populate the ``webhook`` field.
        default_provider (str | None): Fallback provider name for API agent
            mode. When set, the agent is always ``ApiAgentConfig``.
        model (str | None): Agent model override. None to use settings.
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
        provider=provider,
    )

    workspace_base_dir: Path = (
        Path(settings.workspace.base_dir)
        if settings.workspace.base_dir
        else WorkspaceConfig().base_dir
    )

    coding_guidelines: str = resolve_prompt_override(
        inline=settings.prompts.coding_guidelines,
        file_path=settings.prompts.coding_guidelines_file,
        default="",
    )

    if guidelines_path is not None:
        custom_coding: str = load_file_content(guidelines_path)

        if custom_coding:
            coding_guidelines = custom_coding

    if settings.prompts.language_guidelines_dir:
        language_guidelines: dict[str, str] = load_language_guidelines(
            Path(settings.prompts.language_guidelines_dir),
        )
    else:
        language_guidelines = load_bundled_language_guidelines()

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
        dry_run=settings.dry_run,
        ignore_existing_comments=settings.ignore_existing_comments,
    )


def resolve_prompt_override(inline: str, file_path: str, default: str) -> str:
    """
    Resolve a prompt override from explicit inline + file inputs.

    The caller passes two separate values (inline content, file path)
    plus a default. Precedence, from highest to lowest:

    1. ``file_path`` — read the file; raise ``ValueError`` if the path
       does not point to a readable file.
    2. ``inline`` — used verbatim.
    3. ``default`` — used when neither override is set.

    When both ``inline`` and ``file_path`` are supplied, a warning is
    logged and ``file_path`` wins.

    No ``Path(value).is_file()`` probe is performed on inline content —
    the caller declares the mode by which argument they populate.

    Args:
        inline (str): Inline prompt content override. Empty means unset.
        file_path (str): Path to a file whose contents are the override.
            Empty means unset.
        default (str): Fallback value when both overrides are empty.
            Callers pass the pre-loaded bundled default here when one
            exists.

    Returns:
        str: The resolved prompt content.

    Raises:
        ValueError: If ``file_path`` is set but does not point to a
            readable file.
    """

    if inline and file_path:
        logger.warning(
            "Prompt override has both inline and _FILE set; using the file",
        )

    if file_path:
        path: Path = Path(file_path)

        if not path.is_file():
            raise ValueError(
                f"Prompt override file does not exist or is not readable: {file_path}",
            )

        return load_file_content(path)

    if inline:
        return inline

    return default


def _build_reviewer(
    settings: AppSettings,
    require_webhook: bool,
) -> ReviewerConfig:
    """
    Build the reviewer bot identity configuration.

    Only bot-identity concerns (username, suggestions) live on
    ``ReviewerConfig``. The reviewer's system prompt is resolved on the
    agent-runtime side via ``_build_agent``.

    Args:
        settings (AppSettings): The application settings.
        require_webhook (bool): Whether webhook-mode validation applies.

    Returns:
        ReviewerConfig: The reviewer identity config.

    Raises:
        ValueError: If ``require_webhook`` is True and no bot username is set.
    """

    suggestions_prompt: str = ""

    if settings.reviewer.inline_suggestions:
        suggestions_prompt = load_prompt("reviewer_suggestions.md")

    if require_webhook:
        if not settings.reviewer.bot_username:
            raise ValueError("REVIEWER_BOT_USERNAME must be set")

        return ReviewerConfig(
            bot_username=settings.reviewer.bot_username,
            suggestions_prompt=suggestions_prompt,
        )

    return ReviewerConfig(
        bot_username="",
        suggestions_prompt=suggestions_prompt,
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
    provider: ProviderName | None,
) -> AgentConfig:
    """
    Build the agent configuration from settings and overrides.

    When ``default_provider`` is set, always returns ``ApiAgentConfig``
    with per-role runtime configs (reviewer + explorer). When no
    provider is configured anywhere, returns ``CliAgentConfig`` with the
    reviewer's system prompt populated.

    Args:
        settings (AppSettings): The application settings.
        default_provider (str | None): Fallback provider for API mode.
        model (str | None): Reviewer model override. None uses settings.
        provider (ProviderName | None): Reviewer provider override.
            None uses settings.

    Returns:
        AgentConfig: The resolved agent configuration.

    Raises:
        ValueError: If a provider name is not recognised.
    """

    effective_model: str | None = (
        model if model is not None else settings.agent.reviewer.model
    )

    if default_provider is not None:
        return _build_api_agent(
            settings=settings,
            reviewer_provider_name=_resolve_reviewer_provider_name(
                settings=settings,
                override=provider,
                default_provider=default_provider,
            ),
            reviewer_model=effective_model,
        )

    provider_name: ProviderName | None = provider

    if provider_name is None and settings.agent.reviewer.provider:
        provider_name = _parse_provider_name(
            settings.agent.reviewer.provider,
            source="AGENT_PROVIDER",
        )

    if provider_name is None:
        return CliAgentConfig(
            model=effective_model,
            cli_path=settings.agent.cli_path,
            system_prompt=resolve_prompt_override(
                inline=settings.agent.reviewer.system_prompt,
                file_path=settings.agent.reviewer.system_prompt_file,
                default=load_prompt(REVIEWER_BUNDLED_PROMPT),
            ),
        )

    return _build_api_agent(
        settings=settings,
        reviewer_provider_name=provider_name,
        reviewer_model=effective_model,
    )


def _build_api_agent(
    settings: AppSettings,
    reviewer_provider_name: ProviderName,
    reviewer_model: str | None,
) -> ApiAgentConfig:
    """
    Build an API agent configuration with per-role runtime config.

    Each role (reviewer, explorer) is built by taking the provider's
    ``PROVIDERS`` template and ``model_copy``-ing per-role overrides
    (model, system prompt, max turns). The explorer inherits the
    reviewer's provider/model when its settings are empty.

    Args:
        settings (AppSettings): The application settings.
        reviewer_provider_name (ProviderName): Resolved reviewer provider.
        reviewer_model (str | None): Reviewer model override.

    Returns:
        ApiAgentConfig: The resolved API agent configuration.

    Raises:
        ValueError: If the explorer's provider name is not recognised.
    """

    reviewer_role: AgentRoleConfig = _build_role_config(
        role_settings=settings.agent.reviewer,
        provider_name=reviewer_provider_name,
        model_override=reviewer_model,
        bundled_prompt=load_prompt(REVIEWER_BUNDLED_PROMPT),
        default_max_turns=REVIEWER_DEFAULT_MAX_TURNS,
    )

    explorer_provider_name: ProviderName = reviewer_provider_name

    if settings.agent.explorer.provider:
        explorer_provider_name = _parse_provider_name(
            settings.agent.explorer.provider,
            source="AGENT_EXPLORER_PROVIDER",
        )

    explorer_model: str | None = settings.agent.explorer.model or reviewer_role.model

    explorer_role: AgentRoleConfig = _build_role_config(
        role_settings=settings.agent.explorer,
        provider_name=explorer_provider_name,
        model_override=explorer_model,
        bundled_prompt=load_prompt(EXPLORER_BUNDLED_PROMPT),
        default_max_turns=EXPLORER_DEFAULT_MAX_TURNS,
    )

    return ApiAgentConfig(reviewer=reviewer_role, explorer=explorer_role)


def _build_role_config(
    role_settings: AgentRoleSettings,
    provider_name: ProviderName,
    model_override: str | None,
    bundled_prompt: str,
    default_max_turns: int,
) -> AgentRoleConfig:
    """
    Build one role's ``AgentRoleConfig`` by layering overrides on the provider template.

    Starts from ``PROVIDERS[provider_name]`` (the catalog template) and
    ``model_copy``-ies in per-role overrides: model, system prompt,
    max turns.

    Args:
        role_settings (AgentRoleSettings): Raw settings for this role.
        provider_name (ProviderName): Resolved LLM provider.
        model_override (str | None): Effective model to use; when None,
            keeps the provider template's default.
        bundled_prompt (str): Pre-loaded bundled prompt content used as
            the fallback when no override is configured.
        default_max_turns (int): Per-role default max turns (8 for
            reviewer, 32 for explorer).

    Returns:
        AgentRoleConfig: The fully-resolved role config.
    """

    template: AgentRoleConfig = PROVIDERS[provider_name]
    updates: dict[str, object] = {}

    if model_override:
        updates["model"] = model_override

    updates["system_prompt"] = resolve_prompt_override(
        inline=role_settings.system_prompt,
        file_path=role_settings.system_prompt_file,
        default=bundled_prompt,
    )

    updates["max_turns"] = (
        role_settings.max_turns
        if role_settings.max_turns is not None
        else default_max_turns
    )

    return template.model_copy(update=updates)


def _resolve_reviewer_provider_name(
    settings: AppSettings,
    override: ProviderName | None,
    default_provider: str,
) -> ProviderName:
    """
    Resolve the reviewer provider name in API-forced mode.

    Priority: explicit ``override`` > settings > ``default_provider``.

    Args:
        settings (AppSettings): The application settings.
        override (ProviderName | None): Explicit override (from kwargs).
        default_provider (str): Fallback provider name string.

    Returns:
        ProviderName: The resolved provider.

    Raises:
        ValueError: If the resolved name is not recognised.
    """

    if override is not None:
        return override

    if settings.agent.reviewer.provider:
        return _parse_provider_name(
            settings.agent.reviewer.provider,
            source="AGENT_PROVIDER",
        )

    return _parse_provider_name(default_provider, source="default_provider")


def _parse_provider_name(value: str, *, source: str) -> ProviderName:
    """
    Parse a string into a ``ProviderName``, raising a clear error otherwise.

    Args:
        value (str): The raw provider name.
        source (str): Diagnostic label identifying the source of ``value``.

    Returns:
        ProviderName: The parsed enum value.

    Raises:
        ValueError: When ``value`` is not a known provider.
    """

    try:
        return ProviderName(value)
    except ValueError:
        available: str = ", ".join(p.value for p in ProviderName)

        raise ValueError(
            f"Unknown {source}: {value!r}. Available: {available}",
        ) from None


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
