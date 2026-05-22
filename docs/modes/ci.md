# CI Mode

Run automated code reviews on every pull request directly from your CI pipeline. CI mode calls the LLM provider API directly — it does not require the Claude Code CLI.

## GitHub Actions

=== "Anthropic (default)"

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

=== "OpenAI"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        openai_api_key: ${{ secrets.OPENAI_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        provider: openai
        model: gpt-4.1
    ```

=== "Google Gemini"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        google_api_key: ${{ secrets.GOOGLE_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        provider: google
    ```

=== "OpenAI-compatible (DeepSeek, Groq, ...)"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        openai_api_key: ${{ secrets.DEEPSEEK_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        provider: deepseek
    ```

    The `openai_api_key` input is used for all OpenAI-compatible providers. Set the appropriate API key for your provider.

=== "With Custom Options"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        model: your-preferred-model
        prompt: "focus on security vulnerabilities and SQL injection"
    ```

=== "With Coding Guidelines"

    ```yaml
    - uses: gauthierdmn/nominal-code@main
      with:
        anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
        github_token: ${{ secrets.GITHUB_TOKEN }}
        coding_guidelines_file: ".nominal/guidelines.md"
    ```

The action uses the all-in-one `ghcr.io/gauthierdmn/nominal-code` image, which works with any provider.

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

All inputs except `provider`, `image`, and `github_token` default to empty, which means "no override — use the binary's default." Set them only when you need to deviate.

**Authentication**

| Input | Required | Default | Description |
|---|---|---|---|
| `anthropic_api_key` | When provider is `anthropic` | — | Anthropic API key |
| `openai_api_key` | When provider is `openai`, `deepseek`, `groq`, `together`, or `fireworks` | — | OpenAI-compatible API key |
| `google_api_key` | When provider is `google` | — | Google API key |
| `github_token` | Yes | — | GitHub token for posting review comments |

**Reviewer / explorer config**

| Input | Default | Description |
|---|---|---|
| `provider` | `anthropic` | LLM provider for the reviewer (also the default for the explorer) |
| `image` | `ghcr.io/gauthierdmn/nominal-code:latest` | Docker image variant (all-in-one; provider-specific images available for smaller footprint) |
| `model` | — | Reviewer model. Empty uses the provider's default |
| `max_turns` | — | Reviewer turn budget. Empty uses the bundled default (`8`); `0` means unlimited |
| `explorer_provider` | — | Explorer sub-agent provider. Empty inherits from `provider` |
| `explorer_model` | — | Explorer sub-agent model. Empty inherits from `model`. Pair a strong reviewer with a cheaper explorer for cost savings |
| `explorer_max_turns` | — | Explorer turn budget. Empty uses the bundled default (`32`); `0` means unlimited |

**Prompts and guidelines**

| Input | Default | Description |
|---|---|---|
| `prompt` | — | Custom review instructions **appended** to the default reviewer prompt |
| `coding_guidelines` | — | Inline coding guidelines content. Appended to the reviewer prompt |
| `coding_guidelines_file` | — | Path to a coding guidelines file (relative to repo root). Wins over `coding_guidelines` if both are set |
| `reviewer_system_prompt` | — | Inline reviewer system prompt that fully **replaces** the bundled prompt |
| `reviewer_system_prompt_file` | — | Path to a file whose contents fully **replace** the bundled reviewer system prompt |
| `explorer_system_prompt` | — | Inline explorer system prompt that fully **replaces** the bundled prompt |
| `explorer_system_prompt_file` | — | Path to a file whose contents fully **replace** the bundled explorer system prompt |
| `language_guidelines_dir` | — | Directory of per-language guideline files (e.g. `python.md`). Overrides bundled language guidelines |

For each prompt: `*_file` > inline > bundled default. If both are set, the file wins and a warning is logged.

**Behavior toggles**

| Input | Default | Description |
|---|---|---|
| `inline_suggestions` | — | When `false`, disable inline code suggestions. Empty keeps the default (on) |
| `ignore_existing_comments` | — | When `true`, skip fetching prior PR comments so they don't bias the review (useful for re-runs) |
| `dry_run` | — | When `true`, run the full pipeline but skip posting comments to the PR |

## GitLab CI

Include the template via `include: remote:` pointed at the raw GitHub
URL. The examples below pin to `main` to track the latest template
alongside the `image: ghcr.io/gauthierdmn/nominal-code:latest` default.
Pin to a release tag instead (e.g. `.../0.28.0/ci/templates/...`) for
reproducible builds — the same tag should then pin both the template
URL and the `image:` input.

GitLab's `include: component:` is **not** supported — it only resolves
entries published to a GitLab CI/CD Catalog project, which is hosted on
GitLab and cannot be backed by a GitHub repository.

=== "Anthropic (default)"

    ```yaml
    include:
      - remote: "https://raw.githubusercontent.com/gauthierdmn/nominal-code/main/ci/templates/gitlab-ci.yml"

    nominal-code-review:
      variables:
        ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
    ```

=== "OpenAI"

    ```yaml
    include:
      - remote: "https://raw.githubusercontent.com/gauthierdmn/nominal-code/main/ci/templates/gitlab-ci.yml"
        inputs:
          provider: openai

    nominal-code-review:
      variables:
        OPENAI_API_KEY: $OPENAI_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
    ```

=== "Google Gemini"

    ```yaml
    include:
      - remote: "https://raw.githubusercontent.com/gauthierdmn/nominal-code/main/ci/templates/gitlab-ci.yml"
        inputs:
          provider: google

    nominal-code-review:
      variables:
        GOOGLE_API_KEY: $GOOGLE_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
    ```

=== "OpenAI-compatible (DeepSeek, Groq, ...)"

    ```yaml
    include:
      - remote: "https://raw.githubusercontent.com/gauthierdmn/nominal-code/main/ci/templates/gitlab-ci.yml"
        inputs:
          provider: deepseek

    nominal-code-review:
      variables:
        OPENAI_API_KEY: $DEEPSEEK_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
    ```

=== "Direct Job"

    Add a job directly to your `.gitlab-ci.yml`:

    ```yaml
    nominal-code-review:
      image: ghcr.io/gauthierdmn/nominal-code:latest
      variables:
        ANTHROPIC_API_KEY: $ANTHROPIC_API_KEY
        GITLAB_TOKEN: $GITLAB_TOKEN
      script:
        - /entrypoint.sh
      rules:
        - if: $CI_PIPELINE_SOURCE == "merge_request_event"
    ```

The job runs on merge request pipelines. It reads `$CI_PROJECT_PATH`, `$CI_MERGE_REQUEST_IID`, and `$CI_MERGE_REQUEST_SOURCE_BRANCH_NAME` from the GitLab CI environment. For self-hosted instances, `$CI_SERVER_URL` is used automatically as the API base.

### Docker Images

| Provider | Image |
|---|---|
| All providers (default) | `ghcr.io/gauthierdmn/nominal-code` |
| Anthropic only | `ghcr.io/gauthierdmn/nominal-code-anthropic` |
| OpenAI-compatible only | `ghcr.io/gauthierdmn/nominal-code-openai` |
| Google only | `ghcr.io/gauthierdmn/nominal-code-google` |

### Versioning

Pin to a specific release tag for stability:

```yaml
image: ghcr.io/gauthierdmn/nominal-code:0.1.0
```

Or use `latest` to track the `main` branch:

```yaml
image: ghcr.io/gauthierdmn/nominal-code:latest
```

### Template Inputs

All inputs except `provider`, `image`, and `stage` default to empty, which means "no override — use the binary's default." Set them only when you need to deviate.

**Reviewer / explorer config**

| Input | Default | Description |
|---|---|---|
| `provider` | `anthropic` | LLM provider for the reviewer (also the default for the explorer) |
| `image` | `ghcr.io/gauthierdmn/nominal-code:latest` | Docker image variant (all-in-one; provider-specific images available for smaller footprint) |
| `model` | — | Reviewer model. Empty uses the provider's default |
| `max_turns` | — | Reviewer turn budget. Empty uses the bundled default (`8`); `0` means unlimited |
| `explorer_provider` | — | Explorer sub-agent provider. Empty inherits from `provider` |
| `explorer_model` | — | Explorer sub-agent model. Empty inherits from `model`. Pair a strong reviewer with a cheaper explorer for cost savings |
| `explorer_max_turns` | — | Explorer turn budget. Empty uses the bundled default (`32`); `0` means unlimited |

**Prompts and guidelines**

| Input | Default | Description |
|---|---|---|
| `prompt` | — | Custom review instructions **appended** to the default reviewer prompt |
| `coding_guidelines` | — | Inline coding guidelines content. Appended to the reviewer prompt |
| `coding_guidelines_file` | — | Path to a coding guidelines file (relative to repo root). Wins over `coding_guidelines` if both are set |
| `reviewer_system_prompt` | — | Inline reviewer system prompt that fully **replaces** the bundled prompt |
| `reviewer_system_prompt_file` | — | Path to a file whose contents fully **replace** the bundled reviewer system prompt |
| `explorer_system_prompt` | — | Inline explorer system prompt that fully **replaces** the bundled prompt |
| `explorer_system_prompt_file` | — | Path to a file whose contents fully **replace** the bundled explorer system prompt |
| `language_guidelines_dir` | — | Directory of per-language guideline files (e.g. `python.md`). Overrides bundled language guidelines |

For each prompt: `*_file` > inline > bundled default. If both are set, the file wins and a warning is logged.

**Behavior toggles**

| Input | Default | Description |
|---|---|---|
| `inline_suggestions` | — | When `false`, disable inline code suggestions. Empty keeps the default (on) |
| `ignore_existing_comments` | — | When `true`, skip fetching prior MR comments so they don't bias the review (useful for re-runs) |
| `dry_run` | — | When `true`, run the full pipeline but skip posting comments to the MR |
| `stage` | `test` | Pipeline stage to run in |

### Required Variables

| Variable | When | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Provider is `anthropic` | Anthropic API key |
| `OPENAI_API_KEY` | Provider is `openai`, `deepseek`, `groq`, `together`, or `fireworks` | OpenAI-compatible API key |
| `GOOGLE_API_KEY` | Provider is `google` | Google API key |
| `GITLAB_TOKEN` | Always | GitLab token for posting review comments and fetching MR data |

### Example with Overrides

```yaml
include:
  - remote: "https://raw.githubusercontent.com/gauthierdmn/nominal-code/main/ci/templates/gitlab-ci.yml"
    inputs:
      model: your-preferred-model
      prompt: "focus on error handling"
      stage: review
```

## How It Works

1. The CI runner checks out the repository (the workspace is reused as-is).
2. `nominal-code ci {platform}` loads the platform-specific CI module (`commands/ci/github.py` or `commands/ci/gitlab.py`).
3. The platform module builds the event, platform client, and workspace path from CI environment variables.
4. The review runs using the configured LLM provider API directly (tool use loop with Read, Glob, Grep, and Bash).
5. Structured findings are posted as native inline comments on the PR/MR.
6. A cost summary is logged with token usage and dollar cost (when pricing is available for the model).

CI mode uses the same review logic as CLI and webhook modes — the same diff fetching, prompt composition, JSON parsing, and finding filtering. The only difference is the agent runner: CI uses the LLM provider API directly, while CLI and webhook modes use the Claude Code CLI.

## What's Different

CI mode calls the LLM provider API directly and requires a provider API key (per-token billing). It supports multiple providers (Anthropic, OpenAI, Google Gemini, DeepSeek, Groq, Together, Fireworks). It does not support conversation continuity. The workspace is the CI runner's checkout directory — no cloning is needed.

CLI and webhook modes use the Claude Code CLI, supporting Claude Pro and Max subscriptions as an alternative to per-token billing. See the [mode comparison](../reference/configuration.md#mode-comparison) for a full breakdown.

For the complete list of CI-specific environment variables, see [Environment Variables](../reference/env-vars.md#ci-inputs).
