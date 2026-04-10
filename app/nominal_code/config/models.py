from __future__ import annotations

from pydantic import BaseModel, Field

from nominal_code.config.settings import (
    DEFAULT_GITHUB_API_BASE,
    DEFAULT_REDIS_KEY_TTL_SECONDS,
)


class GitHubSettings(BaseModel):
    """
    GitHub platform settings.

    Attributes:
        token (str): Personal access token.
        app_id (str): GitHub App numeric ID.
        private_key (str): Inline PEM-encoded RSA private key.
        private_key_path (str): File path to PEM-encoded RSA private key.
        installation_id (int): Fixed installation ID for CLI/CI modes.
        webhook_secret (str): Webhook HMAC verification secret.
        api_base (str): GitHub API base URL.
    """

    token: str | None = None
    app_id: str | None = None
    private_key: str | None = None
    private_key_path: str | None = None
    installation_id: int = 0
    webhook_secret: str | None = None
    api_base: str = DEFAULT_GITHUB_API_BASE


class GitLabSettings(BaseModel):
    """
    GitLab platform settings.

    Attributes:
        token (str): Personal access token.
        webhook_secret (str): Webhook verification secret.
        api_base (str): GitLab instance base URL.
        ci_server_url (str): GitLab CI predefined variable for self-hosted instances.
    """

    token: str | None = None
    webhook_secret: str | None = None
    api_base: str | None = None
    ci_server_url: str | None = None


class WebhookSettings(BaseModel):
    """
    Webhook server settings.

    Attributes:
        host (str): Host to bind the webhook server.
        port (int): Port to bind the webhook server.
    """

    host: str = "0.0.0.0"
    port: int = 8080


class ReviewerSettings(BaseModel):
    """
    Reviewer bot settings.

    Attributes:
        bot_username (str): The @mention name for the reviewer bot.
        system_prompt_path (str): Path to the reviewer prompt file.
        triggers (list[str]): PR lifecycle events that auto-trigger the reviewer.
        inline_suggestions (bool): Whether to enable one-click-apply code
            suggestions in review comments.
    """

    bot_username: str | None = None
    system_prompt_path: str = ""
    triggers: list[str] = Field(default_factory=list)
    inline_suggestions: bool = True


class AgentSettings(BaseModel):
    """
    Agent runner settings.

    Attributes:
        provider (str): LLM provider name (empty for CLI mode).
        model (str): Model name override.
        cli_path (str): Path to the Claude Code CLI binary.
    """

    provider: str | None = None
    model: str | None = None
    cli_path: str | None = None


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

    base_dir: str | None = None


class PromptsSettings(BaseModel):
    """
    Prompt file settings.

    Attributes:
        coding_guidelines_path (str): Path to coding guidelines file.
        language_guidelines_dir (str): Path to language guidelines directory.
    """

    coding_guidelines_path: str = ""
    language_guidelines_dir: str = ""


class RedisSettings(BaseModel):
    """
    Redis connection settings.

    Attributes:
        url (str): Redis connection URL.
        key_ttl_seconds (int): TTL for Redis keys in seconds.
    """

    url: str | None = None
    key_ttl_seconds: int = DEFAULT_REDIS_KEY_TTL_SECONDS


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

    image: str | None = None
    namespace: str = "default"
    service_account: str | None = None
    image_pull_policy: str | None = None
    backoff_limit: int = 0
    active_deadline_seconds: int = 600
    ttl_after_finished: int = 3600
    env_from_secrets: list[str] = Field(default_factory=list)
    resources: KubernetesResourcesSettings = Field(
        default_factory=KubernetesResourcesSettings,
    )


class AppSettings(BaseModel):
    """
    Top-level application settings loaded from YAML and environment variables.

    Priority order (last wins): model defaults, YAML file, environment variables.

    Attributes:
        github (GitHubSettings): GitHub platform settings.
        gitlab (GitLabSettings): GitLab platform settings.
        webhook (WebhookSettings): Webhook server settings.
        reviewer (ReviewerSettings): Reviewer bot settings.
        agent (AgentSettings): Agent runner settings.
        access (AccessSettings): Access control settings.
        workspace (WorkspaceSettings): Workspace management settings.
        prompts (PromptsSettings): Prompt file settings.
        redis (RedisSettings): Redis connection settings.
        kubernetes (KubernetesSettings): Kubernetes job runner settings.
    """

    github: GitHubSettings = Field(default_factory=GitHubSettings)
    gitlab: GitLabSettings = Field(default_factory=GitLabSettings)
    webhook: WebhookSettings = Field(default_factory=WebhookSettings)
    reviewer: ReviewerSettings = Field(default_factory=ReviewerSettings)
    agent: AgentSettings = Field(default_factory=AgentSettings)
    access: AccessSettings = Field(default_factory=AccessSettings)
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
    prompts: PromptsSettings = Field(default_factory=PromptsSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    kubernetes: KubernetesSettings = Field(default_factory=KubernetesSettings)
