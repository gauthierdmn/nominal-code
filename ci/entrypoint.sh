#!/usr/bin/env bash
set -euo pipefail

if [ -n "${GITHUB_ACTIONS:-}" ]; then
    exec uv run --no-sync nominal-code ci github "$@"
elif [ -n "${GITLAB_CI:-}" ]; then
    exec uv run --no-sync nominal-code ci gitlab "$@"
else
    echo "Error: Unknown CI environment."
    echo "This entrypoint is designed for GitHub Actions or GitLab CI."
    echo "Set GITHUB_ACTIONS=true or GITLAB_CI=true if running manually."
    exit 1
fi
