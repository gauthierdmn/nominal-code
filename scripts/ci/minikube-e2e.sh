#!/usr/bin/env bash
#
# End-to-end test for the Kubernetes job dispatch flow.
#
# Prerequisites (handled by the CI workflow):
#   - minikube is running
#   - controller image built as nominal-code:controller
#   - job image built as nominal-code:job
#
# This script:
#   1. Applies Kubernetes manifests (namespace, RBAC, secrets, deployment, service)
#   2. Waits for the controller pod to become ready
#   3. Port-forwards the controller service
#   4. Sends a synthetic GitHub webhook (issue_comment) to the controller
#   5. Verifies a Kubernetes Job is created with the correct spec
#   6. Cleans up

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

log() { echo "==> $*"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

cleanup() {
    log "Cleaning up..."
    kill "$PORT_FORWARD_PID" 2>/dev/null || true
    kubectl delete namespace "$NAMESPACE" --ignore-not-found --wait=false 2>/dev/null || true
}

trap cleanup EXIT

# --------------------------------------------------------------------------- #
# 1. Apply manifests with test-specific overrides
# --------------------------------------------------------------------------- #

log "Applying Kubernetes manifests"
kubectl apply -f "$DEPLOY_DIR/namespace.yaml"

# Create secret with known webhook secret
kubectl apply -f - <<EOF
apiVersion: v1
kind: Secret
metadata:
  name: nominal-code-secrets
  namespace: $NAMESPACE
type: Opaque
stringData:
  GITHUB_TOKEN: "ghp_fake_for_e2e"
  GITHUB_WEBHOOK_SECRET: "$WEBHOOK_SECRET"
  ANTHROPIC_API_KEY: "sk-ant-fake-for-e2e"
EOF

kubectl apply -f "$DEPLOY_DIR/rbac.yaml"

# Apply deployment with test-specific env overrides
kubectl apply -f "$DEPLOY_DIR/deployment.yaml"
kubectl set image -n "$NAMESPACE" deployment/nominal-code-controller \
    controller="$CONTROLLER_IMAGE"
kubectl set env -n "$NAMESPACE" deployment/nominal-code-controller \
    REVIEWER_BOT_USERNAME="$REVIEWER_BOT" \
    ALLOWED_USERS="$ALLOWED_USER" \
    K8S_IMAGE="$JOB_IMAGE"

kubectl apply -f "$DEPLOY_DIR/service.yaml"

# --------------------------------------------------------------------------- #
# 2. Wait for the controller pod to be ready
# --------------------------------------------------------------------------- #

log "Waiting for controller pod to be ready (timeout: ${TIMEOUT}s)"
if ! kubectl rollout status deployment/nominal-code-controller \
    -n "$NAMESPACE" --timeout="${TIMEOUT}s"; then
    log "Controller pod logs:"
    kubectl logs -n "$NAMESPACE" -l app.kubernetes.io/component=controller --tail=50 || true
    fail "Controller deployment did not become ready"
fi

# --------------------------------------------------------------------------- #
# 3. Port-forward the controller service
# --------------------------------------------------------------------------- #

log "Setting up port-forward to controller (localhost:$LOCAL_PORT)"
kubectl port-forward -n "$NAMESPACE" svc/nominal-code-controller "$LOCAL_PORT:80" &
PORT_FORWARD_PID=$!
sleep 2

# Verify health endpoint
if ! curl -sf "http://localhost:$LOCAL_PORT/health" > /dev/null; then
    fail "Controller health check failed"
fi

log "Controller is healthy"

# --------------------------------------------------------------------------- #
# 4. Send a synthetic GitHub webhook
# --------------------------------------------------------------------------- #

PAYLOAD=$(cat <<'PAYLOAD_EOF'
{
  "action": "created",
  "issue": {
    "number": 999,
    "title": "test: e2e kubernetes dispatch",
    "pull_request": {"url": "https://api.github.com/repos/test-org/test-repo/pulls/999"}
  },
  "comment": {
    "id": 12345,
    "body": "@nominal-bot review this PR",
    "user": {"login": "e2e-tester"}
  },
  "repository": {
    "full_name": "test-org/test-repo"
  }
}
PAYLOAD_EOF
)

SIGNATURE="sha256=$(echo -n "$PAYLOAD" | openssl dgst -sha256 -hmac "$WEBHOOK_SECRET" | sed 's/^.* //')"

log "Sending webhook payload"
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

# --------------------------------------------------------------------------- #
# 5. Wait for a Kubernetes Job to be created
# --------------------------------------------------------------------------- #

log "Waiting for Kubernetes Job to be created"
JOB_FOUND=false
ELAPSED=0

while [ "$ELAPSED" -lt 30 ]; do
    JOBS=$(kubectl get jobs -n "$NAMESPACE" \
        -l "nominal-code/platform=github,nominal-code/pr-number=999" \
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

# --------------------------------------------------------------------------- #
# 6. Verify Job spec
# --------------------------------------------------------------------------- #

JOB_JSON=$(echo "$JOBS" | jq '.items[0]')
JOB_NAME=$(echo "$JOB_JSON" | jq -r '.metadata.name')

log "Verifying Job spec for: $JOB_NAME"

# Check labels
LABEL_APP=$(echo "$JOB_JSON" | jq -r '.metadata.labels["app.kubernetes.io/name"]')
LABEL_PLATFORM=$(echo "$JOB_JSON" | jq -r '.metadata.labels["nominal-code/platform"]')
LABEL_PR=$(echo "$JOB_JSON" | jq -r '.metadata.labels["nominal-code/pr-number"]')
LABEL_REPO=$(echo "$JOB_JSON" | jq -r '.metadata.labels["nominal-code/repo"]')

[ "$LABEL_APP" = "nominal-code" ] || fail "Label app.kubernetes.io/name: expected 'nominal-code', got '$LABEL_APP'"
[ "$LABEL_PLATFORM" = "github" ] || fail "Label nominal-code/platform: expected 'github', got '$LABEL_PLATFORM'"
[ "$LABEL_PR" = "999" ] || fail "Label nominal-code/pr-number: expected '999', got '$LABEL_PR'"
[ "$LABEL_REPO" = "test-org-test-repo" ] || fail "Label nominal-code/repo: expected 'test-org-test-repo', got '$LABEL_REPO'"

# Check container spec
CONTAINER=$(echo "$JOB_JSON" | jq '.spec.template.spec.containers[0]')
IMAGE=$(echo "$CONTAINER" | jq -r '.image')
COMMAND=$(echo "$CONTAINER" | jq -r '.command | join(" ")')

[ "$IMAGE" = "$JOB_IMAGE" ] || fail "Image: expected '$JOB_IMAGE', got '$IMAGE'"
[ "$COMMAND" = "uv run --no-sync nominal-code run-job" ] || fail "Command: expected 'uv run --no-sync nominal-code run-job', got '$COMMAND'"

# Check REVIEW_JOB_PAYLOAD env var exists
PAYLOAD_ENV=$(echo "$CONTAINER" | jq -r '.env[] | select(.name == "REVIEW_JOB_PAYLOAD") | .value')
[ -n "$PAYLOAD_ENV" ] || fail "REVIEW_JOB_PAYLOAD env var not found"

# Verify the payload is valid JSON and contains expected fields
PAYLOAD_PLATFORM=$(echo "$PAYLOAD_ENV" | jq -r '.platform')
PAYLOAD_PR=$(echo "$PAYLOAD_ENV" | jq -r '.pr_number')
PAYLOAD_REPO=$(echo "$PAYLOAD_ENV" | jq -r '.repo_full_name')
PAYLOAD_BOT_TYPE=$(echo "$PAYLOAD_ENV" | jq -r '.bot_type')

[ "$PAYLOAD_PLATFORM" = "github" ] || fail "Payload platform: expected 'github', got '$PAYLOAD_PLATFORM'"
[ "$PAYLOAD_PR" = "999" ] || fail "Payload pr_number: expected '999', got '$PAYLOAD_PR'"
[ "$PAYLOAD_REPO" = "test-org/test-repo" ] || fail "Payload repo: expected 'test-org/test-repo', got '$PAYLOAD_REPO'"
[ "$PAYLOAD_BOT_TYPE" = "reviewer" ] || fail "Payload bot_type: expected 'reviewer', got '$PAYLOAD_BOT_TYPE'"

# Check envFrom secrets reference
SECRET_REF=$(echo "$CONTAINER" | jq -r '.envFrom[0].secretRef.name')
[ "$SECRET_REF" = "nominal-code-secrets" ] || fail "envFrom secret: expected 'nominal-code-secrets', got '$SECRET_REF'"

# Check job-level spec fields
BACKOFF=$(echo "$JOB_JSON" | jq -r '.spec.backoffLimit')
[ "$BACKOFF" = "0" ] || fail "backoffLimit: expected '0', got '$BACKOFF'"

log ""
log "All assertions passed!"
log "  - Labels: correct (app, platform, pr-number, repo)"
log "  - Container image: $JOB_IMAGE"
log "  - Container command: uv run --no-sync nominal-code run-job"
log "  - REVIEW_JOB_PAYLOAD: valid JSON with correct fields"
log "  - envFrom: references nominal-code-secrets"
log "  - backoffLimit: 0"
log ""
log "E2E test PASSED"
