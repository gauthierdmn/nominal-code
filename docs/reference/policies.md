# Policies

Nominal Code separates webhook event handling into two frozen Pydantic models: **FilteringPolicy** (which events to process) and **RoutingPolicy** (how to dispatch them). Both live in `nominal_code.config.policies` and are composed into the top-level `Config`.

## FilteringPolicy

Controls which webhook events are accepted before any dispatch decision.

```python
from nominal_code.config.policies import FilteringPolicy

filtering = FilteringPolicy(
    allowed_users=frozenset({"alice", "bob"}),
    allowed_repos=frozenset({"owner/repo-a"}),
    pr_title_include_tags=frozenset({"nominalbot"}),
    pr_title_exclude_tags=frozenset({"skip"}),
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `allowed_users` | `frozenset[str]` | `frozenset()` | Usernames permitted to trigger bots via `@mention`. Empty means all users are allowed. |
| `allowed_repos` | `frozenset[str]` | `frozenset()` | Repository full names (`owner/repo`) to process. Empty means all repositories. |
| `pr_title_include_tags` | `frozenset[str]` | `frozenset()` | Allowlist of `[tag]` patterns in PR titles. Empty means no include filter. |
| `pr_title_exclude_tags` | `frozenset[str]` | `frozenset()` | Blocklist of `[tag]` patterns in PR titles. Takes priority over include tags. |

All fields are frozen — instances are immutable after creation.

### YAML mapping

The YAML config file maps to `FilteringPolicy` fields under the `access` section:

```yaml
access:
  allowed_users:
    - alice
    - bob
  allowed_repos:
    - owner/repo-a
  pr_title_include_tags:
    - nominalbot
  pr_title_exclude_tags:
    - skip
```

## RoutingPolicy

Controls how accepted events are dispatched to bots.

```python
from nominal_code.config.policies import RoutingPolicy
from nominal_code.models import EventType

routing = RoutingPolicy(
    reviewer_triggers=frozenset({EventType.PR_OPENED, EventType.PR_PUSH}),
    reviewer_bot_username="nominalbot",
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `reviewer_triggers` | `frozenset[EventType]` | `frozenset()` | PR lifecycle events that auto-trigger the reviewer bot. Empty disables auto-trigger. |
| `reviewer_bot_username` | `str \| None` | `None` | The `@mention` name for the reviewer bot. `None` disables the reviewer. |

### YAML mapping

Routing fields map to the `reviewer` YAML section:

```yaml
reviewer:
  bot_username: "nominalbot"
  triggers:
    - pr_opened
    - pr_push
```

## How they fit into Config

The top-level `Config` model holds both policies:

```python
from nominal_code.config import Config

config: Config = load_config()

config.filtering.allowed_users      # frozenset[str]
config.routing.reviewer_triggers    # frozenset[EventType]
```

When `load_config()` reads the YAML file and environment variables, it constructs `FilteringPolicy` and `RoutingPolicy` internally and assigns them to `Config.filtering` and `Config.routing`.

## Public dispatch functions

The webhook server exposes standalone dispatch functions that accept policies directly. This makes them reusable outside the built-in webhook handler — for example, in an enterprise multi-tenant wrapper that constructs per-organization policies.

### filter_event

```python
from nominal_code.commands.webhook.main import filter_event

reason: str | None = filter_event(event, filtering)
```

Applies `allowed_repos` and PR title tag filters. Returns a reason string (`"filtered"`) if the event should be skipped, or `None` if it passes.

### dispatch_lifecycle_event

```python
from nominal_code.commands.webhook.main import dispatch_lifecycle_event

response = await dispatch_lifecycle_event(
    event=event,
    filtering=filtering,
    routing=routing,
    platform=platform,
    runner=runner,
    namespace="tenant-123",       # optional, for multi-tenant isolation
    extra_env={"CUSTOM": "val"},  # optional, injected into job environment
)
```

Dispatches a PR lifecycle event (open, push, reopen, ready-for-review) to the reviewer bot. Checks that the event type is in `routing.reviewer_triggers`, acknowledges it, and enqueues a reviewer job.

### dispatch_comment_event

```python
from nominal_code.commands.webhook.main import dispatch_comment_event

response = await dispatch_comment_event(
    event=event,
    filtering=filtering,
    routing=routing,
    platform=platform,
    runner=runner,
    namespace="tenant-123",
    extra_env={"CUSTOM": "val"},
)
```

Dispatches a comment event to the reviewer bot. Checks for `@mentions` of the reviewer bot using the username from `routing`, authorizes the comment author against `filtering.allowed_users`, and enqueues the job.

### build_runner

```python
from nominal_code.commands.webhook.jobs.runner import build_runner

runner = build_runner(config, platforms)
```

Factory function that constructs a `JobRunner` from the application config. Returns a `KubernetesRunner` when `config.kubernetes` is set, otherwise a `ProcessRunner`.

## Per-organization overrides (multi-tenant)

The policy models are designed to be constructed independently from `Config`. An enterprise wrapper can build per-organization policies by combining global defaults with organization-specific settings:

```python
from nominal_code.config.policies import FilteringPolicy, RoutingPolicy
from nominal_code.models import EventType

# Global defaults from Config
global_filtering: FilteringPolicy = config.filtering
global_routing: RoutingPolicy = config.routing

# Override for a specific organization
org_filtering = FilteringPolicy(
    allowed_users=global_filtering.allowed_users,
    allowed_repos=frozenset({"org/repo-x", "org/repo-y"}),
    pr_title_include_tags=global_filtering.pr_title_include_tags,
    pr_title_exclude_tags=global_filtering.pr_title_exclude_tags,
)

org_routing = RoutingPolicy(
    reviewer_triggers=frozenset({EventType.PR_OPENED}),
    reviewer_bot_username=global_routing.reviewer_bot_username,
)

# Use with dispatch functions
await dispatch_lifecycle_event(
    event=event,
    filtering=org_filtering,
    routing=org_routing,
    platform=platform,
    runner=runner,
    namespace="org-123",
)
```

Because both models are frozen Pydantic instances, they are safe to cache, compare, and share across concurrent requests.
