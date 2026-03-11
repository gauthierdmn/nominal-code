# type: ignore
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.config import KubernetesConfig
from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.queue.redis import RedisJobQueue
from nominal_code.jobs.runner.kubernetes import (
    KubernetesRunner,
    _build_job_name,
    _slugify,
    publish_job_completion,
)
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_config():
    return KubernetesConfig(
        namespace="nominal-code",
        image="nominal-code:dev",
        service_account="nominal-sa",
        image_pull_policy="Never",
        backoff_limit=0,
        active_deadline_seconds=600,
        ttl_after_finished=3600,
        env_from_secrets=("app-secrets",),
        resource_requests_cpu="250m",
        resource_requests_memory="256Mi",
        resource_limits_cpu="1",
        resource_limits_memory="1Gi",
    )


def _make_job():
    event = CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Fix bug",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username="alice",
        body="@bot review",
    )

    return JobPayload(event=event, bot_type="reviewer")


def _make_mock_queue():
    mock_queue = MagicMock(spec=RedisJobQueue)
    mock_queue.enqueue = AsyncMock()
    mock_queue.await_job_completion = AsyncMock(return_value="succeeded")
    mock_queue.set_job_callback = MagicMock()

    return mock_queue


class TestSlugify:
    def test_basic(self):
        assert _slugify("owner/repo") == "owner-repo"

    def test_complex(self):
        assert _slugify("My-Org/My_Repo.Name") == "my-org-my-repo-name"

    def test_leading_trailing_stripped(self):
        assert _slugify("/repo/") == "repo"


class TestBuildJobName:
    def test_format(self):
        name = _build_job_name(
            platform="github", repo_full_name="owner/repo", pr_number=42
        )

        assert name.startswith("nominal-code-")
        parts = name.split("-")
        assert len(parts[2]) == 8
        assert "owner-repo" in name
        assert "42" in name

    def test_uuid_uniqueness(self):
        name_a = _build_job_name(
            platform="github", repo_full_name="owner/repo", pr_number=42
        )
        name_b = _build_job_name(
            platform="github", repo_full_name="owner/repo", pr_number=42
        )

        assert name_a != name_b

    def test_max_length(self):
        name = _build_job_name(
            platform="github",
            repo_full_name="very-long-organization-name/very-long-repository-name",
            pr_number=99999,
        )

        assert len(name) <= 63

    def test_uniqueness_survives_truncation(self):
        name_a = _build_job_name(
            platform="github",
            repo_full_name="very-long-organization-name/very-long-repository-name",
            pr_number=99999,
        )
        name_b = _build_job_name(
            platform="github",
            repo_full_name="very-long-organization-name/very-long-repository-name",
            pr_number=99999,
        )

        assert name_a != name_b


class TestKubernetesRunnerBuildJobSpec:
    def test_spec_structure(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload='{"test": true}',
            job=job,
        )

        assert spec["apiVersion"] == "batch/v1"
        assert spec["kind"] == "Job"
        assert spec["metadata"]["name"] == "test-job"
        assert spec["metadata"]["namespace"] == "nominal-code"

    def test_labels(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        labels = spec["metadata"]["labels"]

        assert labels["app.kubernetes.io/name"] == "nominal-code"
        assert labels["nominal-code/platform"] == "github"
        assert labels["nominal-code/pr-number"] == "42"

    def test_container_command(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]

        assert container["command"] == [
            "uv",
            "run",
            "--no-sync",
            "nominal-code",
            "run-job",
        ]

    def test_env_vars_include_job_name(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload='{"data": "value"}',
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]
        env_vars = {env["name"]: env["value"] for env in container["env"]}

        assert "REVIEW_JOB_PAYLOAD" in env_vars
        assert env_vars["REVIEW_JOB_PAYLOAD"] == '{"data": "value"}'
        assert env_vars["K8S_JOB_NAME"] == "test-job"

    def test_env_from_secrets(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]
        env_from = container["envFrom"]

        assert len(env_from) == 1
        assert env_from[0]["secretRef"]["name"] == "app-secrets"

    def test_resource_limits(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]

        assert container["resources"]["requests"]["cpu"] == "250m"
        assert container["resources"]["limits"]["memory"] == "1Gi"

    def test_image_pull_policy(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]

        assert container["imagePullPolicy"] == "Never"

    def test_service_account(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        pod_spec = spec["spec"]["template"]["spec"]

        assert pod_spec["serviceAccountName"] == "nominal-sa"

    def test_job_spec_fields(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )

        assert spec["spec"]["backoffLimit"] == 0
        assert spec["spec"]["activeDeadlineSeconds"] == 600
        assert spec["spec"]["ttlSecondsAfterFinished"] == 3600

    def test_no_resources_when_empty(self):
        config = KubernetesConfig(
            image="nominal-code:dev",
        )
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]

        assert "resources" not in container

    def test_redis_url_forwarded_when_set(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        with patch.dict("os.environ", {"REDIS_URL": "redis://redis:6379/0"}):
            spec = runner._build_job_spec(
                job_name="test-job",
                payload="{}",
                job=job,
            )

        container = spec["spec"]["template"]["spec"]["containers"][0]
        env_vars = {env["name"]: env["value"] for env in container["env"]}

        assert env_vars["REDIS_URL"] == "redis://redis:6379/0"

    def test_redis_url_and_ttl_forwarded(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        with patch.dict(
            "os.environ",
            {"REDIS_URL": "redis://redis:6379/0", "REDIS_KEY_TTL_SECONDS": "3600"},
        ):
            spec = runner._build_job_spec(
                job_name="test-job",
                payload="{}",
                job=job,
            )

        container = spec["spec"]["template"]["spec"]["containers"][0]
        env_vars = {env["name"]: env["value"] for env in container["env"]}

        assert env_vars["REDIS_URL"] == "redis://redis:6379/0"
        assert env_vars["REDIS_KEY_TTL_SECONDS"] == "3600"

    def test_redis_url_not_set(self):
        config = _make_config()
        runner = KubernetesRunner(config=config, queue=_make_mock_queue())
        job = _make_job()

        with patch.dict("os.environ", {}, clear=False):
            env = dict(**os.environ)
            env.pop("REDIS_URL", None)

            with patch.dict("os.environ", env, clear=True):
                spec = runner._build_job_spec(
                    job_name="test-job",
                    payload="{}",
                    job=job,
                )

        container = spec["spec"]["template"]["spec"]["containers"][0]
        env_names = [env["name"] for env in container["env"]]

        assert "REDIS_URL" not in env_names


class TestKubernetesRunnerEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_delegates_to_queue(self):
        config = _make_config()
        mock_queue = _make_mock_queue()
        runner = KubernetesRunner(config=config, queue=mock_queue)
        job = _make_job()

        await runner.enqueue(job)

        mock_queue.enqueue.assert_called_once_with(job)

    @pytest.mark.asyncio
    async def test_init_registers_callback(self):
        config = _make_config()
        mock_queue = _make_mock_queue()
        KubernetesRunner(config=config, queue=mock_queue)

        mock_queue.set_job_callback.assert_called_once()


class TestKubernetesRunnerExecute:
    @pytest.mark.asyncio
    async def test_create_and_await_success(self):
        config = _make_config()
        mock_queue = _make_mock_queue()
        mock_queue.await_job_completion = AsyncMock(return_value="succeeded")
        runner = KubernetesRunner(config=config, queue=mock_queue)
        job = _make_job()

        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.text = '{"metadata": {"name": "test-job"}}'

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "nominal_code.jobs.runner.kubernetes._read_service_account_token",
                return_value="test-token",
            ),
            patch(
                "nominal_code.jobs.runner.kubernetes.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            await runner._execute(job)

        mock_client.post.assert_called_once()
        mock_queue.await_job_completion.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_and_await_timeout(self):
        config = _make_config()
        mock_queue = _make_mock_queue()
        mock_queue.await_job_completion = AsyncMock(
            side_effect=TimeoutError("timed out"),
        )
        runner = KubernetesRunner(config=config, queue=mock_queue)
        job = _make_job()

        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.text = '{"metadata": {"name": "test-job"}}'

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "nominal_code.jobs.runner.kubernetes._read_service_account_token",
                return_value="test-token",
            ),
            patch(
                "nominal_code.jobs.runner.kubernetes.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            await runner._execute(job)

    @pytest.mark.asyncio
    async def test_create_and_await_uses_correct_timeout(self):
        config = KubernetesConfig(
            image="nominal-code:dev",
            active_deadline_seconds=300,
        )
        mock_queue = _make_mock_queue()
        runner = KubernetesRunner(config=config, queue=mock_queue)
        job = _make_job()

        mock_response = AsyncMock()
        mock_response.status_code = 201
        mock_response.text = "{}"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "nominal_code.jobs.runner.kubernetes._read_service_account_token",
                return_value="test-token",
            ),
            patch(
                "nominal_code.jobs.runner.kubernetes.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            await runner._execute(job)

        call_args = mock_queue.await_job_completion.call_args
        assert call_args[0][1] == 310.0

    @pytest.mark.asyncio
    async def test_failed_job_creation_raises(self):
        config = _make_config()
        mock_queue = _make_mock_queue()
        runner = KubernetesRunner(config=config, queue=mock_queue)
        job = _make_job()

        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_response.raise_for_status = MagicMock(
            side_effect=Exception("403 Forbidden"),
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "nominal_code.jobs.runner.kubernetes._read_service_account_token",
                return_value="test-token",
            ),
            patch(
                "nominal_code.jobs.runner.kubernetes.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(Exception, match="403"),
        ):
            await runner._execute(job)


class TestPublishJobCompletion:
    def test_publishes_to_correct_channel(self):
        pytest.importorskip("redis")
        mock_client = MagicMock()

        with patch("redis.Redis") as mock_redis_cls:
            mock_redis_cls.from_url.return_value = mock_client

            publish_job_completion(
                redis_url="redis://localhost:6379",
                job_name="nominal-code-abc12345-owner-repo-42",
                status="succeeded",
            )

            mock_client.publish.assert_called_once_with(
                "nc:job:nominal-code-abc12345-owner-repo-42:done",
                "succeeded",
            )
            mock_client.close.assert_called_once()

    def test_publishes_failed_status(self):
        pytest.importorskip("redis")
        mock_client = MagicMock()

        with patch("redis.Redis") as mock_redis_cls:
            mock_redis_cls.from_url.return_value = mock_client

            publish_job_completion(
                redis_url="redis://localhost:6379",
                job_name="test-job",
                status="failed",
            )

            mock_client.publish.assert_called_once_with(
                "nc:job:test-job:done",
                "failed",
            )

    def test_handles_redis_error_gracefully(self):
        redis = pytest.importorskip("redis")

        with patch("redis.Redis") as mock_redis_cls:
            mock_redis_cls.from_url.side_effect = redis.RedisError("connection failed")

            publish_job_completion(
                redis_url="redis://localhost:6379",
                job_name="test-job",
                status="succeeded",
            )
