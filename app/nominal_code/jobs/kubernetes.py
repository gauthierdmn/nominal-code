from __future__ import annotations

import logging
import os
import re
import time
from typing import Any

import httpx

from nominal_code.config import KubernetesConfig
from nominal_code.jobs.payload import JobPayload

logger: logging.Logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_TOKEN_PATH: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_ACCOUNT_CA_PATH: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
API_SERVER_URL: str = "https://kubernetes.default.svc"
JOB_NAME_MAX_LENGTH: int = 63
SLUG_PATTERN: re.Pattern[str] = re.compile(r"[^a-z0-9]")


class KubernetesRunner:
    """
    Dispatches review jobs by creating Kubernetes ``batch/v1`` Jobs.

    Attributes:
        _config (KubernetesConfig): Kubernetes-specific configuration.
    """

    def __init__(self, config: KubernetesConfig) -> None:
        """
        Initialize the Kubernetes runner.

        Args:
            config (KubernetesConfig): Kubernetes-specific configuration.
        """

        self._config = config

    async def run(self, job: JobPayload) -> None:
        """
        Create a Kubernetes Job for the given review.

        Builds the Job spec with the serialized payload as an env var,
        references configured Secrets, and POSTs to the Kubernetes API.

        Args:
            job (JobPayload): The review job to dispatch.
        """

        job_name: str = _build_job_name(
            platform=job.platform,
            repo_full_name=job.repo_full_name,
            pr_number=job.pr_number,
        )

        job_spec: dict[str, Any] = self._build_job_spec(
            job_name=job_name,
            payload=job.serialize(),
            job=job,
        )

        token: str = _read_service_account_token()
        url: str = (
            f"{API_SERVER_URL}/apis/batch/v1/namespaces/{self._config.namespace}/jobs"
        )

        async with httpx.AsyncClient(
            verify=SERVICE_ACCOUNT_CA_PATH,
            timeout=30.0,
        ) as client:
            response: httpx.Response = await client.post(
                url,
                json=job_spec,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "Failed to create K8s Job %s: %d %s",
                job_name,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        logger.info(
            "Created K8s Job %s for %s#%d (%s)",
            job_name,
            job.repo_full_name,
            job.pr_number,
            job.bot_type,
        )

    def _build_job_spec(
        self,
        job_name: str,
        payload: str,
        job: JobPayload,
    ) -> dict[str, Any]:
        """
        Build the Kubernetes Job spec as a JSON-serializable dict.

        Args:
            job_name (str): Unique name for the Job resource.
            payload (str): Serialized JobPayload JSON.
            job (JobPayload): The job payload for label metadata.

        Returns:
            dict[str, Any]: The complete Job resource spec.
        """

        repo_slug: str = _slugify(job.repo_full_name)

        labels: dict[str, str] = {
            "app.kubernetes.io/name": "nominal-code",
            "app.kubernetes.io/component": "job",
            "nominal-code/platform": job.platform,
            "nominal-code/repo": repo_slug,
            "nominal-code/pr-number": str(job.pr_number),
        }

        env_vars: list[dict[str, str]] = [
            {"name": "REVIEW_JOB_PAYLOAD", "value": payload},
        ]

        redis_url: str = os.environ.get("REDIS_URL", "")

        if redis_url:
            env_vars.append({"name": "REDIS_URL", "value": redis_url})

            redis_ttl: str = os.environ.get("REDIS_KEY_TTL_SECONDS", "")

            if redis_ttl:
                env_vars.append(
                    {"name": "REDIS_KEY_TTL_SECONDS", "value": redis_ttl},
                )

        env_from: list[dict[str, Any]] = [
            {"secretRef": {"name": secret_name}}
            for secret_name in self._config.env_from_secrets
        ]

        container: dict[str, Any] = {
            "name": "review",
            "image": self._config.image,
            "command": ["uv", "run", "--no-sync", "nominal-code", "run-job"],
            "env": env_vars,
            "envFrom": env_from,
        }

        if self._config.image_pull_policy:
            container["imagePullPolicy"] = self._config.image_pull_policy

        resources: dict[str, dict[str, str]] = {}
        requests: dict[str, str] = {}
        limits: dict[str, str] = {}

        if self._config.resource_requests_cpu:
            requests["cpu"] = self._config.resource_requests_cpu

        if self._config.resource_requests_memory:
            requests["memory"] = self._config.resource_requests_memory

        if self._config.resource_limits_cpu:
            limits["cpu"] = self._config.resource_limits_cpu

        if self._config.resource_limits_memory:
            limits["memory"] = self._config.resource_limits_memory

        if requests:
            resources["requests"] = requests

        if limits:
            resources["limits"] = limits

        if resources:
            container["resources"] = resources

        pod_spec: dict[str, Any] = {
            "containers": [container],
            "restartPolicy": "Never",
        }

        if self._config.service_account:
            pod_spec["serviceAccountName"] = self._config.service_account

        spec: dict[str, Any] = {
            "apiVersion": "batch/v1",
            "kind": "Job",
            "metadata": {
                "name": job_name,
                "namespace": self._config.namespace,
                "labels": labels,
            },
            "spec": {
                "backoffLimit": self._config.backoff_limit,
                "activeDeadlineSeconds": self._config.active_deadline_seconds,
                "ttlSecondsAfterFinished": self._config.ttl_after_finished,
                "template": {
                    "metadata": {"labels": labels},
                    "spec": pod_spec,
                },
            },
        }

        return spec


def _build_job_name(
    platform: str,
    repo_full_name: str,
    pr_number: int,
) -> str:
    """
    Generate a unique, K8s-valid Job name.

    Format: ``nominal-review-{platform}-{repo_slug}-{pr}-{timestamp}``.
    Truncated to 63 characters (K8s name limit).

    Args:
        platform (str): Platform identifier.
        repo_full_name (str): Full repository name.
        pr_number (int): PR/MR number.

    Returns:
        str: A unique, DNS-compatible Job name.
    """

    repo_slug: str = _slugify(repo_full_name)
    timestamp: str = str(int(time.time()))
    name: str = f"nominal-review-{platform}-{repo_slug}-{pr_number}-{timestamp}"

    return name[:JOB_NAME_MAX_LENGTH].rstrip("-")


def _slugify(text: str) -> str:
    """
    Convert text to a DNS-compatible slug.

    Args:
        text (str): Input text (e.g. ``"owner/repo-name"``).

    Returns:
        str: Lowercased slug with non-alphanumeric chars replaced by ``-``.
    """

    return SLUG_PATTERN.sub("-", text.lower()).strip("-")


def _read_service_account_token() -> str:
    """
    Read the in-cluster service account token.

    Returns:
        str: The bearer token string.

    Raises:
        FileNotFoundError: If not running inside a Kubernetes pod.
    """

    with open(SERVICE_ACCOUNT_TOKEN_PATH, encoding="utf-8") as token_file:
        return token_file.read().strip()
