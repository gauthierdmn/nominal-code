# type: ignore
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.config import KubernetesConfig
from nominal_code.jobs.kubernetes import (
    KubernetesRunner,
    _build_job_name,
    _slugify,
)
from nominal_code.jobs.payload import ReviewJob


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
    return ReviewJob(
        platform="github",
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        pr_title="Fix bug",
        event_type="issue_comment",
        is_comment_event=True,
        author_username="alice",
        comment_body="@bot review",
        comment_id=100,
        diff_hunk="",
        file_path="",
        discussion_id="",
        prompt="review",
        pr_author="",
        bot_type="reviewer",
    )


class TestSlugify:
    def test_basic(self):
        assert _slugify("owner/repo") == "owner-repo"

    def test_complex(self):
        assert _slugify("My-Org/My_Repo.Name") == "my-org-my-repo-name"

    def test_leading_trailing_stripped(self):
        assert _slugify("/repo/") == "repo"


class TestBuildJobName:
    def test_format(self):
        name = _build_job_name("github", "owner/repo", 42)

        assert name.startswith("nominal-review-github-owner-repo-42-")

    def test_max_length(self):
        name = _build_job_name(
            "github",
            "very-long-organization-name/very-long-repository-name",
            99999,
        )

        assert len(name) <= 63


class TestKubernetesRunnerBuildJobSpec:
    def test_spec_structure(self):
        config = _make_config()
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
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

    def test_env_vars(self):
        config = _make_config()
        runner = KubernetesRunner(config)
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

    def test_env_from_secrets(self):
        config = _make_config()
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
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
        runner = KubernetesRunner(config)
        job = _make_job()

        spec = runner._build_job_spec(
            job_name="test-job",
            payload="{}",
            job=job,
        )
        container = spec["spec"]["template"]["spec"]["containers"][0]

        assert "resources" not in container


class TestKubernetesRunnerRun:
    @pytest.mark.asyncio
    async def test_successful_job_creation(self):
        config = _make_config()
        runner = KubernetesRunner(config)
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
                "nominal_code.jobs.kubernetes._read_service_account_token",
                return_value="test-token",
            ),
            patch(
                "nominal_code.jobs.kubernetes.httpx.AsyncClient",
                return_value=mock_client,
            ),
        ):
            await runner.run(job)

        mock_client.post.assert_called_once()
        call_kwargs = mock_client.post.call_args
        assert "Authorization" in call_kwargs.kwargs["headers"]
        assert call_kwargs.kwargs["headers"]["Authorization"] == "Bearer test-token"

    @pytest.mark.asyncio
    async def test_failed_job_creation_raises(self):
        config = _make_config()
        runner = KubernetesRunner(config)
        job = _make_job()

        mock_response = AsyncMock()
        mock_response.status_code = 403
        mock_response.text = "Forbidden"
        mock_response.raise_for_status = MagicMock(
            side_effect=Exception("403 Forbidden")
        )

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(
                "nominal_code.jobs.kubernetes._read_service_account_token",
                return_value="test-token",
            ),
            patch(
                "nominal_code.jobs.kubernetes.httpx.AsyncClient",
                return_value=mock_client,
            ),
            pytest.raises(Exception, match="403"),
        ):
            await runner.run(job)
