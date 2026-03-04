# CI Mode

Run automated code reviews on every pull request directly from your CI pipeline. CI mode calls the Anthropic API directly — it does not require the Claude Code CLI.

## GitHub Actions

=== "Minimal Setup"

    Add a workflow file to your repository:

    ```yaml
    # .github/workflows/review.yml
    name: Code Review
    on:
      pull_request:
        types: [opened, synchronize, reopened, ready_for_review]

    permissions:
      contents: read
      pull-requests: write

    jobs:
      review:
        runs-on: ubuntu-latest
        steps:
          - uses: actions/checkout@v4
          - uses: gauthierdmn/nominal-code@main
            with:
              anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
              github_token: ${{ secrets.GITHUB_TOKEN }}
    ```

    The action runs inside a Docker container (`ghcr.io/gauthierdmn/nominal-code`), reads the pull request event payload from `$GITHUB_EVENT_PATH`, reviews the diff using the Anthropic API, and posts structured inline comments back to the PR.

=== "With Custom Options"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        model: claude-sonnet-4-20250514
        prompt: "focus on security vulnerabilities and SQL injection"
    ```

=== "With Coding Guidelines"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        coding_guidelines: ".nominal/guidelines.md"
    ```

### Versioning

Pin to a specific release tag for stability:

```yaml
- uses: gauthierdmn/nominal-code@0.1.0
```

Or track the latest changes on `main` (may include breaking changes):

```yaml
- uses: gauthierdmn/nominal-code@main
```

### Inputs

| Input | Required | Default | Description |
|---|---|---|---|
| `anthropic_api_key` | Yes | — | Anthropic API key for Claude |
| `github_token` | Yes | — | GitHub token for posting review comments |
| `model` | No | `claude-sonnet-4-20250514` | Claude model to use |
| `max_turns` | No | `0` (unlimited) | Maximum agentic turns |
| `prompt` | No | — | Custom review instructions appended to the default prompt |
| `coding_guidelines` | No | — | Path to a coding guidelines file (relative to repo root) |

## GitLab CI

=== "Direct Job"

    Add a job to your `.gitlab-ci.yml`:

    ```yaml
    nominal-code-review:
      image: ghcr.io/gauthierdmn/nominal-code:0.1.0
      variables:
        ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
      script:
        - cd "$CI_PROJECT_DIR" && uv run nominal-code ci gitlab
      rules:
        - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    ```

=== "CI Component Template"

    ```yaml
    include:
      - component: ghcr.io/gauthierdmn/nominal-code/ci/templates/gitlab-ci.yml

    nominal-code-review:
      variables:
        ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
    ```

The job runs on merge request pipelines. It reads `$CI_PROJECT_PATH`, `$CI_MERGE_REQUEST_IID`, and `$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` from the GitLab CI environment. For self-hosted instances, `$CI_SERVER_URL` is used automatically as the API base.

### Versioning

Pin to a specific release tag for stability:

```yaml
image: ghcr.io/gauthierdmn/nominal-code:0.1.0
```

Or use `latest` to track the `main` branch:

```yaml
image: ghcr.io/gauthierdmn/nominal-code:latest
```

The CI component template always uses `:latest`.

### Template Inputs

| Input | Default | Description |
|---|---|---|
| `model` | — | Claude model to use |
| `max_turns` | `0` (unlimited) | Maximum agentic turns |
| `prompt` | — | Custom review instructions |
| `coding_guidelines` | — | Path to a coding guidelines file |
| `stage` | `test` | Pipeline stage to run in |

### Required Variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Anthropic API key for Claude |
| `GITLAB_TOKEN` | GitLab token for posting review comments and fetching MR data |

### Example with Overrides

```yaml
include:
  - component: ghcr.io/gauthierdmn/nominal-code/ci/templates/gitlab-ci.yml
    inputs:
      model: claude-sonnet-4-20250514
      prompt: "focus on error handling"
      stage: review
```

## How It Works

1. The CI runner checks out the repository (the workspace is reused as-is).
2. `nominal-code ci {platform}` loads the platform-specific CI module (`platforms/github/ci.py` or `platforms/gitlab/ci.py`).
3. The platform module builds the event, platform client, and workspace path from CI environment variables.
4. The review runs using the **Anthropic API** directly (tool use loop with Read, Glob, Grep, and Bash).
5. Structured findings are posted as native inline comments on the PR/MR.

CI mode uses the same review logic as CLI and webhook modes — the same diff fetching, prompt composition, JSON parsing, and finding filtering. The only difference is the agent runner: CI uses the Anthropic API directly, while CLI and webhook modes use the Claude Code CLI.

## What's Different

CI mode calls the Anthropic API directly and always requires an `ANTHROPIC_API_KEY` (per-token billing). It does not support session continuity. The workspace is the CI runner's checkout directory — no cloning is needed.

CLI and webhook modes use the Claude Code CLI, supporting Claude Pro and Max subscriptions as an alternative to per-token billing. See the [mode comparison](../reference/configuration.md#mode-comparison) for a full breakdown.

For the complete list of CI-specific environment variables, see [Environment Variables](../reference/env-vars.md#ci-inputs).
