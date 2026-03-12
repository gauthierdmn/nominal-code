from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

import httpx
from environs import Env

from nominal_code.config import KubernetesConfig
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.queue.redis import RedisJobQueue

if TYPE_CHECKING:
    import redis

_env: Env = Env()
logger: logging.Logger = logging.getLogger(__name__)

SERVICE_ACCOUNT_TOKEN_PATH: str = "/var/run/secrets/kubernetes.io/serviceaccount/token"
SERVICE_ACCOUNT_CA_PATH: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
API_SERVER_URL: str = "https://kubernetes.default.svc"
JOB_NAME_MAX_LENGTH: int = 63
SLUG_PATTERN: re.Pattern[str] = re.compile(pattern=r"[^a-z0-9]")
DEFAULT_JOB_TIMEOUT_MARGIN_SECONDS: int = 10
JOB_CHANNEL_PREFIX: str = "nc:job"


class KubernetesRunner:
    """
    Dispatches review jobs by creating Kubernetes ``batch/v1`` Jobs.

    Jobs are enqueued via a ``RedisJobQueue`` for per-PR serial execution.
    The runner creates K8s Jobs and awaits their completion via Redis
    pub/sub signals.

    Attributes:
        _config (KubernetesConfig): Kubernetes-specific configuration.
        _queue (RedisJobQueue): Redis-backed per-PR job queue.
    """

    def __init__(self, config: KubernetesConfig, queue: RedisJobQueue) -> None:
        """
        Initialize the Kubernetes runner.

        Registers itself as the job callback on the queue so that
        dequeued jobs are processed via ``_execute``.

        Args:
            config (KubernetesConfig): Kubernetes-specific configuration.
            queue (RedisJobQueue): Redis-backed job queue.
        """

        self._config = config
        self._queue = queue
        self._queue.set_job_callback(self._execute)

    async def enqueue(self, job: JobPayload) -> None:
        """
        Enqueue a job for serial per-PR execution.

        The queue's consumer will call ``_execute`` for each
        dequeued job.

        Args:
            job (JobPayload): The review job to dispatch.
        """

        await self._queue.enqueue(job)

    async def _execute(self, job: JobPayload) -> None:
        """
        Create a K8s Job and wait for it to complete via pub/sub.

        Builds the Job spec, POSTs it to the K8s API, then waits for
        the job pod to publish a completion signal on Redis.

        Args:
            job (JobPayload): The review job to execute.
        """

        channel_key: str = build_job_channel_key(job)
        await self._create_k8s_job(job=job)

        timeout_seconds: float = (
            self._config.active_deadline_seconds + DEFAULT_JOB_TIMEOUT_MARGIN_SECONDS
        )

        try:
            status: str = await self._queue.await_job_completion(
                channel_key,
                timeout_seconds,
            )

            logger.info(
                "K8s Job for %s#%d (%s) completed with status: %s",
                job.event.repo_full_name,
                job.event.pr_number,
                job.bot_type,
                status,
            )
        except TimeoutError:
            logger.error(
                "K8s Job for %s#%d (%s) timed out after %ds",
                job.event.repo_full_name,
                job.event.pr_number,
                job.bot_type,
                timeout_seconds,
            )

    async def _create_k8s_job(self, job: JobPayload) -> None:
        """
        POST a Job spec to the Kubernetes API.

        Args:
            job (JobPayload): The review job payload.
        """

        job_spec: dict[str, Any] = self._build_job_spec(
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
                url=url,
                json=job_spec,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
            )

        if response.status_code >= 400:
            logger.error(
                "Failed to create K8s Job for %s#%d: %d %s",
                job.event.repo_full_name,
                job.event.pr_number,
                response.status_code,
                response.text,
            )
            response.raise_for_status()

        logger.info(
            "Created K8s Job for %s#%d (%s)",
            job.event.repo_full_name,
            job.event.pr_number,
            job.bot_type,
        )

    def _build_job_spec(
        self,
        payload: str,
        job: JobPayload,
    ) -> dict[str, Any]:
        """
        Build the Kubernetes Job spec as a JSON-serializable dict.

        Args:
            payload (str): Serialized JobPayload JSON.
            job (JobPayload): The job payload for label metadata.

        Returns:
            dict[str, Any]: The complete Job resource spec.
        """

        repo_slug: str = _slugify(job.event.repo_full_name)

        labels: dict[str, str] = {
            "app.kubernetes.io/name": "nominal-code",
            "app.kubernetes.io/component": "job",
            "nominal-code/platform": job.event.platform,
            "nominal-code/repo": repo_slug,
            "nominal-code/pr-number": str(job.event.pr_number),
        }

        generate_name: str = f"nominal-code-job-{repo_slug}-{job.event.pr_number}-"
        generate_name = generate_name[:JOB_NAME_MAX_LENGTH].rstrip("-") + "-"

        env_vars: list[dict[str, str]] = [
            {"name": "REVIEW_JOB_PAYLOAD", "value": payload},
        ]

        redis_url: str = _env.str("REDIS_URL", "")

        if redis_url:
            env_vars.append({"name": "REDIS_URL", "value": redis_url})

            redis_ttl: str = _env.str("REDIS_KEY_TTL_SECONDS", "")

            if redis_ttl:
                env_vars.append(
                    {"name": "REDIS_KEY_TTL_SECONDS", "value": redis_ttl},
                )

        for env_name, env_value in job.extra_env.items():
            env_vars.append({"name": env_name, "value": env_value})

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
                "generateName": generate_name,
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


def publish_job_completion(
    redis_url: str,
    channel_key: str,
    status: str,
) -> None:
    """
    Publish a job completion signal to Redis pub/sub.

    Called from the K8s Job pod at the end of ``run_job_main()`` to
    notify the server that the job has finished.

    Args:
        redis_url (str): Redis connection URL.
        channel_key (str): The Redis channel key for this job.
        status (str): Completion status (``"succeeded"`` or ``"failed"``).
    """

    import redis as _redis

    try:
        client: redis.Redis = _redis.Redis.from_url(redis_url)

        try:
            client.publish(channel_key, status)
            logger.info("Published completion on %s: %s", channel_key, status)
        finally:
            client.close()
    except _redis.RedisError:
        logger.warning(
            "Failed to publish completion on %s",
            channel_key,
            exc_info=True,
        )


def build_job_channel_key(job: JobPayload) -> str:
    """
    Build a deterministic Redis pub/sub channel key for a job.

    The key is derived from ``(platform, repo, pr_number, bot_type)``
    which uniquely identifies the serialized job slot. Since the job
    queue guarantees only one job runs per slot at a time, this key
    is safe to use without a UUID.

    Args:
        job (JobPayload): The job payload.

    Returns:
        str: The Redis channel key.
    """

    return (
        f"{JOB_CHANNEL_PREFIX}:{job.event.platform}:"
        f"{job.event.repo_full_name}:{job.event.pr_number}:{job.bot_type}"
    )


def _slugify(text: str) -> str:
    """
    Convert text to a DNS-compatible slug.

    Args:
        text (str): Input text (e.g. ``"owner/repo-name"``).

    Returns:
        str: Lowercased slug with non-alphanumeric chars replaced by ``-``.
    """

    return SLUG_PATTERN.sub(repl="-", string=text.lower()).strip("-")


def _read_service_account_token() -> str:
    """
    Read the in-cluster service account token.

    Returns:
        str: The bearer token string.

    Raises:
        FileNotFoundError: If not running inside a Kubernetes pod.
    """

    with open(file=SERVICE_ACCOUNT_TOKEN_PATH, encoding="utf-8") as token_file:
        return token_file.read().strip()
