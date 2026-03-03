#!/usr/bin/env bash
set -euo pipefail

# Clean up stale integration test resources:
#   - Close old PRs/MRs and delete their branches
#   - Delete orphaned test branches
#   - Delete stale webhooks (leftover from crashed test runs)
#
# Required env vars:
#   GH_TOKEN              — GitHub token (used by gh CLI)
#
# Optional env vars:
#   TEST_GITLAB_TOKEN     — GitLab token (skips GitLab cleanup if unset)

REPO="gauthierdmn/nominal-code-test"
TITLE_PREFIX="test:"
BRANCH_PREFIX="test/"
CUTOFF_MINUTES=30

CUTOFF=$(date -u -d "${CUTOFF_MINUTES} minutes ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null \
  || date -u -v-${CUTOFF_MINUTES}M +%Y-%m-%dT%H:%M:%SZ)

echo "Cleaning GitHub PRs and branches older than ${CUTOFF}..."
gh pr list --repo "${REPO}" --state open \
  --json number,title,headRefName,createdAt | \
  jq -r --arg cutoff "${CUTOFF}" \
    ".[] | select(.title | startswith(\"${TITLE_PREFIX}\")) | select(.createdAt < \$cutoff) | \"\(.number) \(.headRefName)\"" | \
  while read -r pr_number branch; do
    gh pr close "${pr_number}" --repo "${REPO}" || true
    gh api -X DELETE "repos/${REPO}/git/refs/heads/${branch}" || true
  done

echo "Cleaning stale GitHub webhooks..."
gh api "repos/${REPO}/hooks" --paginate --jq \
  '.[] | select(.config.url | contains("trycloudflare.com")) | .id' | \
  while read -r hook_id; do
    echo "Deleting GitHub webhook: ${hook_id}"
    gh api -X DELETE "repos/${REPO}/hooks/${hook_id}" || true
  done

echo "Cleaning orphaned GitHub test branches older than ${CUTOFF}..."
gh api "repos/${REPO}/branches" --paginate --jq \
  ".[] | select(.name | startswith(\"${BRANCH_PREFIX}\")) | .name" | \
  while read -r branch; do
    commit_date=$(gh api "repos/${REPO}/commits/${branch}" --jq ".commit.committer.date" 2>/dev/null || echo "")
    if [ -n "${commit_date}" ] && [[ "${commit_date}" < "${CUTOFF}" ]]; then
      echo "Deleting orphaned branch: ${branch}"
      gh api -X DELETE "repos/${REPO}/git/refs/heads/${branch}" || true
    fi
  done

if [ -n "${TEST_GITLAB_TOKEN:-}" ]; then
  PROJECT="gauthierdmn%2Fnominal-code-test"

  echo "Cleaning GitLab MRs and branches older than ${CUTOFF}..."
  curl -s --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
    "https://gitlab.com/api/v4/projects/${PROJECT}/merge_requests?state=opened" | \
    jq -r --arg cutoff "${CUTOFF}" \
      ".[] | select(.title | startswith(\"${TITLE_PREFIX}\")) | select(.created_at < \$cutoff) | \"\(.iid) \(.source_branch)\"" | \
    while read -r mr_iid branch; do
      curl -s --request PUT --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
        "https://gitlab.com/api/v4/projects/${PROJECT}/merge_requests/${mr_iid}?state_event=close" || true
      curl -s --request DELETE --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
        "https://gitlab.com/api/v4/projects/${PROJECT}/repository/branches/${branch}" || true
    done

  echo "Cleaning stale GitLab webhooks..."
  curl -s --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
    "https://gitlab.com/api/v4/projects/${PROJECT}/hooks" | \
    jq -r '.[] | select(.url | contains("trycloudflare.com")) | .id' | \
    while read -r hook_id; do
      echo "Deleting GitLab webhook: ${hook_id}"
      curl -s --request DELETE --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
        "https://gitlab.com/api/v4/projects/${PROJECT}/hooks/${hook_id}" || true
    done

  echo "Cleaning orphaned GitLab test branches older than ${CUTOFF}..."
  curl -s --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
    "https://gitlab.com/api/v4/projects/${PROJECT}/repository/branches?search=^${BRANCH_PREFIX}&per_page=100" | \
    jq -r --arg cutoff "${CUTOFF}" \
      ".[] | select(.commit.committed_date < \$cutoff) | .name" | \
    while read -r branch; do
      echo "Deleting orphaned branch: ${branch}"
      curl -s --request DELETE --header "PRIVATE-TOKEN: ${TEST_GITLAB_TOKEN}" \
        "https://gitlab.com/api/v4/projects/${PROJECT}/repository/branches/${branch}" || true
    done
else
  echo "TEST_GITLAB_TOKEN not set, skipping GitLab cleanup."
fi
