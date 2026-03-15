from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class KubernetesConfig(BaseModel):
    """
    Configuration for the Kubernetes job runner.

    Attributes:
        image (str): Docker image for review pods.
        namespace (str): Kubernetes namespace for review Jobs.
        service_account (str): ServiceAccount name for review pods.
        image_pull_policy (str): Image pull policy override.
        backoff_limit (int): Job retry count (0 = no retries).
        active_deadline_seconds (int): Per-job timeout in seconds.
        ttl_after_finished (int): Seconds before completed Jobs are cleaned up.
        env_from_secrets (tuple[str, ...]): K8s Secret names to mount as env.
        resource_requests_cpu (str): CPU request (e.g. ``"500m"``).
        resource_requests_memory (str): Memory request (e.g. ``"512Mi"``).
        resource_limits_cpu (str): CPU limit.
        resource_limits_memory (str): Memory limit.
        pod_security_enabled (bool): Enable pod security hardening
            (``securityContext``, ``automountServiceAccountToken``).
        run_as_user (int): UID to run the container as when security is enabled.
        read_only_root_filesystem (bool): Mount the root filesystem as
            read-only when security is enabled.
        automount_service_account_token (bool): Whether to mount the service
            account token in job pods.
    """

    model_config = ConfigDict(frozen=True)

    image: str
    namespace: str = "default"
    service_account: str = ""
    image_pull_policy: str = ""
    backoff_limit: int = 0
    active_deadline_seconds: int = 600
    ttl_after_finished: int = 3600
    env_from_secrets: tuple[str, ...] = ()
    resource_requests_cpu: str = ""
    resource_requests_memory: str = ""
    resource_limits_cpu: str = ""
    resource_limits_memory: str = ""
    pod_security_enabled: bool = True
    run_as_user: int = 1000
    read_only_root_filesystem: bool = True
    automount_service_account_token: bool = False
