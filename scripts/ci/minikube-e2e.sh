#!/usr/bin/env bash
#
# Full E2E test for the Kubernetes deployment flow.
#
# Prerequisites (handled by the CI workflow):
#   - minikube is running
#   - controller image built as nominal-code:controller
#   - job image built as nominal-code:job
#
# Required env vars:
#   TEST_GITHUB_TOKEN  — real GitHub token (PR creation + review verification)
#   GOOGLE_API_KEY     — real LLM API key (for job pod)
#
# This script:
#   1. Creates a real PR on gauthierdmn/nominal-code-test with a buggy file
#   2. Deploys the controller to minikube with real secrets
#   3. Sends a synthetic webhook referencing the real PR
#   4. Waits for the K8s Job to complete (real LLM review)
#   5. Verifies a review was posted to the PR
#   6. Cleans up everything

set -euo pipefail

NAMESPACE="nominal-code"
DEPLOY_DIR="deploy/minikube"
WEBHOOK_SECRET="e2e-test-secret"
REVIEWER_BOT="nominal-bot"
ALLOWED_USER="e2e-tester"
CONTROLLER_IMAGE="nominal-code:controller"
JOB_IMAGE="nominal-code:job"
LOCAL_PORT=9090
TIMEOUT=120

REPO="gauthierdmn/nominal-code-test"
BRANCH="test/k8s-e2e-$(date +%s)-$RANDOM"
PR_NUMBER=""
PORT_FORWARD_PID=""

GITHUB_API="https://api.github.com"
AUTH_HEADER="Authorization: token ${TEST_GITHUB_TOKEN:-}"

log() { echo "==> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

cleanup() {
    log "Cleaning up..."
    kill "$PORT_FORWARD_PID" 2>/dev/null || true

    if [ -n "$PR_NUMBER" ]; then
        log "Closing PR #${PR_NUMBER}"
        curl -sf -X PATCH \
            -H "$AUTH_HEADER" \
            -H "Accept: application/vnd.github+json" \
            "$GITHUB_API/repos/$REPO/pulls/$PR_NUMBER" \
            -d '{"state":"closed"}' > /dev/null || true
    fi

    if [ -n "$BRANCH" ]; then
        log "Deleting branch $BRANCH"
        curl -sf -X DELETE \
            -H "$AUTH_HEADER" \
            "$GITHUB_API/repos/$REPO/git/refs/heads/$BRANCH" > /dev/null || true
    fi

    kubectl delete namespace "$NAMESPACE" --ignore-not-found --wait=false 2>/dev/null || true
}

trap cleanup EXIT

if [ -z "${TEST_GITHUB_TOKEN:-}" ]; then
    fail "TEST_GITHUB_TOKEN is required"
fi

if [ -z "${GOOGLE_API_KEY:-}" ]; then
    fail "GOOGLE_API_KEY is required"
fi

BUGGY_CALCULATOR_CONTENT='import os


def add(first, second):
    """Add two numbers together."""

    return first + second


def subtract(first, second):
    """Subtract second from first."""

    return first - second


def multiply(first, second):
    """Multiply two numbers together."""

    return first * second


def power(base, exponent):
    """Raise base to the given exponent."""

    result = 1

    for _ in range(exponent):
        result *= base

    return result


def absolute(value):
    """Return the absolute value of a number."""
    if value < 0:
        return -value
    return value


def negate(value):
    """Negate a number."""

    return -value


def divide(first, second):
    """Divide first by second."""

    if not isinstance(first, (int, float)):
        raise TypeError("first must be a number")

    if not isinstance(second, (int, float)):
        raise TypeError("second must be a number")

    log_message = f"Dividing {first} by {second}"
    print(log_message)

    precision = 10
    rounded = False
    result = first / second

    if precision and rounded:
        result = round(result, precision)

    return result
'

BUGGY_CONTENT_B64=$(echo -n "$BUGGY_CALCULATOR_CONTENT" | base64 | tr -d '\n')

log "Creating test PR on $REPO"

MAIN_SHA=$(curl -sf \
    -H "$AUTH_HEADER" \
    "$GITHUB_API/repos/$REPO/git/ref/heads/main" | jq -r '.object.sha')

log "Main branch SHA: $MAIN_SHA"

curl -sf -X POST \
    -H "$AUTH_HEADER" \
    -H "Accept: application/vnd.github+json" \
    "$GITHUB_API/repos/$REPO/git/refs" \
    -d "{\"ref\":\"refs/heads/$BRANCH\",\"sha\":\"$MAIN_SHA\"}" > /dev/null

log "Created branch: $BRANCH"

sleep 2

EXISTING_SHA=$(curl -s \
    -H "$AUTH_HEADER" \
    "$GITHUB_API/repos/$REPO/contents/src/calculator.py?ref=$BRANCH" | jq -r '.sha // empty')

PUT_BODY="{\"message\":\"test: add buggy calculator\",\"content\":\"$BUGGY_CONTENT_B64\",\"branch\":\"$BRANCH\""
if [ -n "$EXISTING_SHA" ]; then
    PUT_BODY="$PUT_BODY,\"sha\":\"$EXISTING_SHA\""
    log "Updating existing file (sha: $EXISTING_SHA)"
fi
PUT_BODY="$PUT_BODY}"

PUT_RESPONSE=$(curl -s -w "\n%{http_code}" -X PUT \
    -H "$AUTH_HEADER" \
    -H "Accept: application/vnd.github+json" \
    "$GITHUB_API/repos/$REPO/contents/src/calculator.py" \
    -d "$PUT_BODY")

PUT_HTTP_CODE=$(echo "$PUT_RESPONSE" | tail -1)
if [ "$PUT_HTTP_CODE" != "200" ] && [ "$PUT_HTTP_CODE" != "201" ]; then
    echo "$PUT_RESPONSE" | head -n -1 >&2
    fail "Failed to push file (HTTP $PUT_HTTP_CODE)"
fi

log "Pushed buggy calculator file"

PR_RESPONSE=$(curl -s -X POST \
    -H "$AUTH_HEADER" \
    -H "Accept: application/vnd.github+json" \
    "$GITHUB_API/repos/$REPO/pulls" \
    -d "{
        \"title\":\"test: k8s e2e $(date +%s)\",
        \"head\":\"$BRANCH\",
        \"base\":\"main\",
        \"body\":\"Automated K8s E2E test PR. Will be cleaned up automatically.\"
    }")

PR_NUMBER=$(echo "$PR_RESPONSE" | jq -r '.number')
log "Created PR #$PR_NUMBER"

if [ "$PR_NUMBER" = "null" ] || [ -z "$PR_NUMBER" ]; then
    echo "$PR_RESPONSE" >&2
    fail "Failed to create PR"
fi

log "Applying Kubernetes manifests"
kubectl apply -f "$DEPLOY_DIR/namespace.yaml"

kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: nominal-code-secrets
  namespace: $NAMESPACE
type: Opaque
stringData:
  GITHUB_TOKEN: "$TEST_GITHUB_TOKEN"
  GITHUB_WEBHOOK_SECRET: "$WEBHOOK_SECRET"
  GOOGLE_API_KEY: "$GOOGLE_API_KEY"
  AGENT_PROVIDER: "google"
  AGENT_MODEL: "gemini-2.5-flash-lite"
  AGENT_MAX_TURNS: "1"
EOF

kubectl apply -f "$DEPLOY_DIR/rbac.yaml"

kubectl apply -f "$DEPLOY_DIR/deployment.yaml"
kubectl set image -n "$NAMESPACE" deployment/nominal-code-controller \
    controller="$CONTROLLER_IMAGE"
kubectl set env -n "$NAMESPACE" deployment/nominal-code-controller \
    REVIEWER_BOT_USERNAME="$REVIEWER_BOT" \
    ALLOWED_USERS="$ALLOWED_USER" \
    K8S_IMAGE="$JOB_IMAGE" \
    AGENT_PROVIDER="google"

kubectl apply -f "$DEPLOY_DIR/service.yaml"

log "Waiting for controller pod to be ready (timeout: ${TIMEOUT}s)"
if ! kubectl rollout status deployment/nominal-code-controller \
    -n "$NAMESPACE" --timeout="${TIMEOUT}s"; then
    log "Controller pod logs:"
    kubectl logs -n "$NAMESPACE" -l app.kubernetes.io/component=controller --tail=50 || true
    fail "Controller deployment did not become ready"
fi

log "Setting up port-forward to controller (localhost:$LOCAL_PORT)"
kubectl port-forward -n "$NAMESPACE" svc/nominal-code-controller "$LOCAL_PORT:80" &
PORT_FORWARD_PID=$!
sleep 2

if ! curl -sf "http://localhost:$LOCAL_PORT/health" > /dev/null; then
    fail "Controller health check failed"
fi

log "Controller is healthy"

PAYLOAD=$(cat <<PAYLOAD_EOF
{
  "action": "created",
  "issue": {
    "number": $PR_NUMBER,
    "title": "test: k8s e2e",
    "pull_request": {"url": "$GITHUB_API/repos/$REPO/pulls/$PR_NUMBER"}
  },
  "comment": {
    "id": 99999999,
    "body": "@nominal-bot review this PR",
    "user": {"login": "e2e-tester"}
  },
  "repository": {
    "full_name": "$REPO"
  }
}
PAYLOAD_EOF
)

SIGNATURE="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | sed 's/^.* //')"

log "Sending webhook payload for PR #$PR_NUMBER"
HTTP_CODE=$(curl -s -o /tmp/webhook-response.json -w "%{http_code}" \
    -X POST "http://localhost:$LOCAL_PORT/webhooks/github" \
    -H "Content-Type: application/json" \
    -H "X-GitHub-Event: issue_comment" \
    -H "X-Hub-Signature-256: $SIGNATURE" \
    -d "$PAYLOAD")

RESPONSE=$(cat /tmp/webhook-response.json)
log "Webhook response: $HTTP_CODE - $RESPONSE"

if [ "$HTTP_CODE" != "200" ]; then
    fail "Expected HTTP 200, got $HTTP_CODE"
fi

log "Waiting for Kubernetes Job to be created"
JOB_FOUND=false
ELAPSED=0

while [ "$ELAPSED" -lt 30 ]; do
    JOBS=$(kubectl get jobs -n "$NAMESPACE" \
        -l "nominal-code/platform=github,nominal-code/pr-number=$PR_NUMBER" \
        -o json 2>/dev/null)

    JOB_COUNT=$(echo "$JOBS" | jq '.items | length')

    if [ "$JOB_COUNT" -gt 0 ]; then
        JOB_FOUND=true
        break
    fi

    sleep 2
    ELAPSED=$((ELAPSED + 2))
done

if [ "$JOB_FOUND" != "true" ]; then
    log "All jobs in namespace:"
    kubectl get jobs -n "$NAMESPACE" -o wide || true
    log "Controller logs:"
    kubectl logs -n "$NAMESPACE" -l app.kubernetes.io/component=controller --tail=50 || true
    fail "No Kubernetes Job was created within 30 seconds"
fi

log "Kubernetes Job created successfully"

JOB_JSON=$(echo "$JOBS" | jq '.items[0]')
JOB_NAME=$(echo "$JOB_JSON" | jq -r '.metadata.name')

log "Verifying Job spec for: $JOB_NAME"

LABEL_APP=$(echo "$JOB_JSON" | jq -r '.metadata.labels["app.kubernetes.io/name"]')
LABEL_PLATFORM=$(echo "$JOB_JSON" | jq -r '.metadata.labels["nominal-code/platform"]')
LABEL_PR=$(echo "$JOB_JSON" | jq -r '.metadata.labels["nominal-code/pr-number"]')
LABEL_REPO=$(echo "$JOB_JSON" | jq -r '.metadata.labels["nominal-code/repo"]')

[ "$LABEL_APP" = "nominal-code" ] || fail "Label app.kubernetes.io/name: expected 'nominal-code', got '$LABEL_APP'"
[ "$LABEL_PLATFORM" = "github" ] || fail "Label nominal-code/platform: expected 'github', got '$LABEL_PLATFORM'"
[ "$LABEL_PR" = "$PR_NUMBER" ] || fail "Label nominal-code/pr-number: expected '$PR_NUMBER', got '$LABEL_PR'"
[ "$LABEL_REPO" = "gauthierdmn-nominal-code-test" ] || fail "Label nominal-code/repo: expected 'gauthierdmn-nominal-code-test', got '$LABEL_REPO'"

CONTAINER=$(echo "$JOB_JSON" | jq '.spec.template.spec.containers[0]')
IMAGE=$(echo "$CONTAINER" | jq -r '.image')
COMMAND=$(echo "$CONTAINER" | jq -r '.command | join(" ")')

[ "$IMAGE" = "$JOB_IMAGE" ] || fail "Image: expected '$JOB_IMAGE', got '$IMAGE'"
[ "$COMMAND" = "uv run --no-sync nominal-code run-job" ] || fail "Command: expected 'uv run --no-sync nominal-code run-job', got '$COMMAND'"

PAYLOAD_ENV=$(echo "$CONTAINER" | jq -r '.env[] | select(.name == "REVIEW_JOB_PAYLOAD") | .value')
[ -n "$PAYLOAD_ENV" ] || fail "REVIEW_JOB_PAYLOAD env var not found"

PAYLOAD_PLATFORM=$(echo "$PAYLOAD_ENV" | jq -r '.platform')
PAYLOAD_PR=$(echo "$PAYLOAD_ENV" | jq -r '.pr_number')
PAYLOAD_REPO=$(echo "$PAYLOAD_ENV" | jq -r '.repo_full_name')
PAYLOAD_BOT_TYPE=$(echo "$PAYLOAD_ENV" | jq -r '.bot_type')

[ "$PAYLOAD_PLATFORM" = "github" ] || fail "Payload platform: expected 'github', got '$PAYLOAD_PLATFORM'"
[ "$PAYLOAD_PR" = "$PR_NUMBER" ] || fail "Payload pr_number: expected '$PR_NUMBER', got '$PAYLOAD_PR'"
[ "$PAYLOAD_REPO" = "$REPO" ] || fail "Payload repo: expected '$REPO', got '$PAYLOAD_REPO'"
[ "$PAYLOAD_BOT_TYPE" = "reviewer" ] || fail "Payload bot_type: expected 'reviewer', got '$PAYLOAD_BOT_TYPE'"

SECRET_REF=$(echo "$CONTAINER" | jq -r '.envFrom[0].secretRef.name')
[ "$SECRET_REF" = "nominal-code-secrets" ] || fail "envFrom secret: expected 'nominal-code-secrets', got '$SECRET_REF'"

BACKOFF=$(echo "$JOB_JSON" | jq -r '.spec.backoffLimit')
[ "$BACKOFF" = "0" ] || fail "backoffLimit: expected '0', got '$BACKOFF'"

log "Job spec assertions passed"

log "Waiting for Job to complete (timeout: 300s)"
if ! kubectl wait --for=condition=complete job/"$JOB_NAME" \
    -n "$NAMESPACE" --timeout=300s; then
    log "Job did not complete. Pod logs:"
    kubectl logs -n "$NAMESPACE" -l "job-name=$JOB_NAME" --tail=100 || true
    log "Job status:"
    kubectl get job "$JOB_NAME" -n "$NAMESPACE" -o yaml || true
    fail "Job did not complete within 300 seconds"
fi

log "Job completed successfully"

log "Verifying review was posted to PR #$PR_NUMBER"

REVIEW_FOUND=false

REVIEWS=$(curl -sf \
    -H "$AUTH_HEADER" \
    -H "Accept: application/vnd.github+json" \
    "$GITHUB_API/repos/$REPO/pulls/$PR_NUMBER/reviews")

REVIEW_COUNT=$(echo "$REVIEWS" | jq '[.[] | select(.body != null and .body != "")] | length')

if [ "$REVIEW_COUNT" -gt 0 ]; then
    REVIEW_FOUND=true
    log "Found $REVIEW_COUNT review(s) with body on PR #$PR_NUMBER"
fi

if [ "$REVIEW_FOUND" != "true" ]; then
    COMMENTS=$(curl -sf \
        -H "$AUTH_HEADER" \
        -H "Accept: application/vnd.github+json" \
        "$GITHUB_API/repos/$REPO/issues/$PR_NUMBER/comments")

    COMMENT_COUNT=$(echo "$COMMENTS" | jq 'length')

    if [ "$COMMENT_COUNT" -gt 0 ]; then
        REVIEW_FOUND=true
        log "Found $COMMENT_COUNT comment(s) on PR #$PR_NUMBER (fallback check)"
    fi
fi

if [ "$REVIEW_FOUND" != "true" ]; then
    log "Reviews response:"
    echo "$REVIEWS" | jq . || true
    fail "No review or comment found on PR #$PR_NUMBER"
fi

log ""
log "All assertions passed!"
log "  - Labels: correct (app, platform, pr-number, repo)"
log "  - Container image: $JOB_IMAGE"
log "  - Container command: uv run --no-sync nominal-code run-job"
log "  - REVIEW_JOB_PAYLOAD: valid JSON with correct fields"
log "  - envFrom: references nominal-code-secrets"
log "  - backoffLimit: 0"
log "  - Job completed successfully"
log "  - Review posted to PR #$PR_NUMBER"
log ""
log "E2E test PASSED"
