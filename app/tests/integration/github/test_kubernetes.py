import hashlib
import hmac
import json
import os
import signal
import subprocess
import time
from collections.abc import Generator
from typing import Any

import httpx
import pytest

from tests.integration.conftest import PrInfo
from tests.integration.github import api as github_api
from tests.integration.helpers.fixtures import GITHUB_TEST_REPO

pytestmark = [pytest.mark.integration_kubernetes]

NAMESPACE = "nominal-code"
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "test-secret")
LOCAL_PORT = 9090
SERVER_URL = f"http://localhost:{LOCAL_PORT}"
JOB_IMAGE = "nominal-code"

JOB_CREATION_TIMEOUT = 30
JOB_COMPLETION_TIMEOUT = 300
HEALTH_CHECK_RETRIES = 10


def _kubectl(
    *args: str,
    check: bool = True,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:

    return subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
    )


def _kubectl_json(*args: str) -> dict[str, Any]:

    result = _kubectl(*args, "-o", "json")

    return json.loads(result.stdout)


def _sign_payload(payload: str, secret: str) -> str:

    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256)

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


@pytest.fixture()
def port_forward() -> Generator[str]:

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

    time.sleep(2)

    for _ in range(HEALTH_CHECK_RETRIES):
        try:
            resp = httpx.get(f"{SERVER_URL}/health", timeout=2.0)

            if resp.status_code == 200:
                break
        except httpx.ConnectError:
            time.sleep(1)
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

    payload = _build_webhook_payload(pr_info.repo, pr_info.number)
    signature = _sign_payload(payload, WEBHOOK_SECRET)

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
        result = _kubectl(
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

        if result.returncode == 0:
            jobs = json.loads(result.stdout)

            if jobs.get("items"):
                job_json = jobs["items"][0]

                break

        time.sleep(2)

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

    _kubectl(
        "wait",
        "--for=condition=complete",
        f"job/{job_name}",
        "-n",
        NAMESPACE,
        f"--timeout={JOB_COMPLETION_TIMEOUT}s",
        timeout=JOB_COMPLETION_TIMEOUT + 30,
    )

    reviews = await github_api.fetch_pr_reviews(
        github_token,
        pr_info.repo,
        pr_info.number,
    )
    reviews_with_body = [review for review in reviews if review.get("body")]

    if not reviews_with_body:
        comments = await github_api.fetch_pr_comments(
            github_token,
            pr_info.repo,
            pr_info.number,
        )

        assert len(comments) > 0, f"No review or comment found on PR #{pr_info.number}"
