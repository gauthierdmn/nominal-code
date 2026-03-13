# type: ignore
import asyncio
import hashlib
import hmac
import json
import os
import signal
import subprocess
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import pytest
import pytest_asyncio

from tests.integration.conftest import PrInfo
from tests.integration.github import api as github_api
from tests.integration.helpers.fixtures import GITHUB_TEST_REPO

pytestmark = [pytest.mark.integration_kubernetes]

NAMESPACE = "nominal-code"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "test-secret")
LOCAL_PORT = 9090
SERVER_URL = f"http://localhost:{LOCAL_PORT}"
JOB_IMAGE = os.environ.get("K8S_IMAGE", "nominal-code")

JOB_CREATION_TIMEOUT = 30
JOB_COMPLETION_TIMEOUT = 300
HEALTH_CHECK_RETRIES = 10


async def _kubectl(
    *args: str,
    check: bool = True,
    timeout: int = 30,
) -> tuple[str, str, int]:

    proc = await asyncio.create_subprocess_exec(
        "kubectl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.communicate()

        raise

    returncode = proc.returncode or 0

    if check and returncode != 0:
        raise subprocess.CalledProcessError(
            returncode=returncode,
            cmd=["kubectl", *args],
            output=stderr_bytes.decode(),
        )

    return stdout_bytes.decode(), stderr_bytes.decode(), returncode


async def _kubectl_json(*args: str) -> dict[str, Any]:

    stdout, _, _ = await _kubectl(*args, "-o", "json")

    return json.loads(stdout)


def _sign_payload(payload: str, secret: str) -> str:

    mac = hmac.new(
        key=secret.encode(),
        msg=payload.encode(),
        digestmod=hashlib.sha256,
    )

    return f"sha256={mac.hexdigest()}"


def _build_webhook_payload(repo: str, pr_number: int) -> str:

    return json.dumps(
        {
            "action": "created",
            "issue": {
                "number": pr_number,
                "title": "test: k8s integration test",
                "pull_request": {
                    "url": f"https://api.github.com/repos/{repo}/pulls/{pr_number}",
                },
            },
            "comment": {
                "id": 99999999,
                "body": "@nominal-bot review this PR",
                "user": {"login": "test-user"},
            },
            "repository": {"full_name": repo},
        },
    )


@pytest_asyncio.fixture()
async def port_forward() -> AsyncGenerator[str]:

    proc = subprocess.Popen(
        [
            "kubectl",
            "port-forward",
            "-n",
            NAMESPACE,
            "svc/nominal-code-server",
            f"{LOCAL_PORT}:80",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    await asyncio.sleep(2)

    for _ in range(HEALTH_CHECK_RETRIES):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{SERVER_URL}/health", timeout=2.0)

                if resp.status_code == 200:
                    break
        except httpx.ConnectError:
            await asyncio.sleep(1)
    else:
        proc.kill()
        pytest.fail("Server health check failed after port-forward")

    try:
        yield SERVER_URL
    finally:
        proc.send_signal(signal.SIGTERM)

        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.mark.asyncio
async def test_kubernetes_job_dispatch(
    github_token: str,
    buggy_pr: PrInfo,
    port_forward: str,
) -> None:
    pr_info = buggy_pr
    base_url = port_forward

    payload = _build_webhook_payload(repo=pr_info.repo, pr_number=pr_info.number)
    signature = _sign_payload(payload=payload, secret=WEBHOOK_SECRET)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{base_url}/webhooks/github",
            content=payload,
            headers={
                "Content-Type": "application/json",
                "X-GitHub-Event": "issue_comment",
                "X-Hub-Signature-256": signature,
            },
        )

    assert resp.status_code == 200, f"Webhook returned {resp.status_code}: {resp.text}"

    job_json: dict[str, Any] | None = None

    for _ in range(JOB_CREATION_TIMEOUT // 2):
        stdout, _, returncode = await _kubectl(
            "get",
            "jobs",
            "-n",
            NAMESPACE,
            "-l",
            f"nominal-code/platform=github,nominal-code/pr-number={pr_info.number}",
            "-o",
            "json",
            check=False,
        )

        if returncode == 0:
            jobs = json.loads(stdout)

            if jobs.get("items"):
                job_json = jobs["items"][0]

                break

        await asyncio.sleep(2)

    assert job_json is not None, f"No K8s Job created within {JOB_CREATION_TIMEOUT}s"

    job_name = job_json["metadata"]["name"]
    labels = job_json["metadata"]["labels"]

    assert labels["app.kubernetes.io/name"] == "nominal-code"
    assert labels["nominal-code/platform"] == "github"
    assert labels["nominal-code/pr-number"] == str(pr_info.number)

    expected_repo_label = GITHUB_TEST_REPO.replace("/", "-")
    assert labels["nominal-code/repo"] == expected_repo_label

    container = job_json["spec"]["template"]["spec"]["containers"][0]

    assert container["image"] == JOB_IMAGE
    assert container["command"] == ["uv", "run", "--no-sync", "nominal-code", "run-job"]

    payload_env = next(
        (
            env["value"]
            for env in container["env"]
            if env["name"] == "REVIEW_JOB_PAYLOAD"
        ),
        None,
    )
    assert payload_env is not None, "REVIEW_JOB_PAYLOAD env var not found on job pod"

    job_payload = json.loads(payload_env)
    assert job_payload["event"]["platform"] == "github"
    assert job_payload["event"]["pr_number"] == pr_info.number
    assert job_payload["event"]["repo_full_name"] == pr_info.repo
    assert job_payload["bot_type"] == "reviewer"

    env_from = container.get("envFrom", [])
    secret_refs = [
        entry["secretRef"]["name"] for entry in env_from if "secretRef" in entry
    ]
    assert "nominal-code-secrets" in secret_refs

    assert job_json["spec"]["backoffLimit"] == 0

    await _kubectl(
        "wait",
        "--for=condition=complete",
        f"job/{job_name}",
        "-n",
        NAMESPACE,
        f"--timeout={JOB_COMPLETION_TIMEOUT}s",
        timeout=JOB_COMPLETION_TIMEOUT + 30,
    )

    reviews = await github_api.fetch_pr_reviews(
        token=github_token,
        repo=pr_info.repo,
        pr_number=pr_info.number,
    )
    reviews_with_body = [review for review in reviews if review.get("body")]

    if not reviews_with_body:
        comments = await github_api.fetch_pr_comments(
            token=github_token,
            repo=pr_info.repo,
            pr_number=pr_info.number,
        )

        assert len(comments) > 0, f"No review or comment found on PR #{pr_info.number}"
