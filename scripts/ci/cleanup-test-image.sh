#!/usr/bin/env bash
set -euo pipefail

# Delete a SHA-tagged container image from GHCR.
#
# Required env vars:
#   GH_TOKEN    — GitHub token with packages:write permission
#   IMAGE_TAG   — The tag to find and delete (typically a commit SHA)

PACKAGE="nominal-code"

VERSION_ID=$(gh api \
  "/user/packages/container/${PACKAGE}/versions" \
  --paginate --jq \
  ".[] | select(.metadata.container.tags[] == \"${IMAGE_TAG}\") | .id")

if [ -n "${VERSION_ID}" ]; then
  echo "Deleting ${PACKAGE}:${IMAGE_TAG} (version ${VERSION_ID})..."
  gh api --method DELETE \
    "/user/packages/container/${PACKAGE}/versions/${VERSION_ID}" || true
else
  echo "No version found for tag ${IMAGE_TAG}, skipping."
fi
