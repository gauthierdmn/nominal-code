# Scaling GitHub App Authentication

## Problem

The current `GitHubAppAuth` implementation uses a single shared instance across all incoming webhook requests. This works fine when the GitHub App is installed in a single organization, but breaks under concurrent multi-installation traffic.

### How GitHub App auth works

A GitHub App can be installed in multiple organizations. Each installation gets a unique `installation_id`. To make API calls on behalf of an installation, the server must:

1. Generate a short-lived JWT (signed with the App's private key)
2. Exchange the JWT for an installation access token via `POST /app/installations/{installation_id}/access_tokens`
3. Use the installation token (valid for 1 hour) for all subsequent API calls

### Current implementation

`GitHubAppAuth` (in `platforms/github/auth.py`) holds a single mutable state:

```python
class GitHubAppAuth(GitHubAuth):
    def __init__(self, app_id, private_key, installation_id=0):
        self.installation_id: int = installation_id
        self._cached_token: str = ""            # one token
        self._token_expires_at: float = 0.0     # one expiry
```

On every incoming webhook, `parse_event()` in `platform.py` mutates this shared instance:

```python
# platform.py:226
installation_id = payload.get("installation", {}).get("id", 0)
if installation_id:
    self.auth.set_installation_id(installation_id)
```

And `set_installation_id()` invalidates the cache whenever the ID changes:

```python
# auth.py:241-259
def set_installation_id(self, installation_id: int) -> None:
    if installation_id == self.installation_id:
        return
    self.installation_id = installation_id
    self._cached_token = ""              # cache wiped
    self._token_expires_at = 0.0         # forces refresh
```

### Failure mode 1: Cache thrashing

With the App installed in 3 orgs (installations 111, 222, 333):

```
t0: Webhook from org-a → set_installation_id(111) → fetch token for 111, cache it
t1: Webhook from org-b → set_installation_id(222) → cache wiped → fetch token for 222
t2: Webhook from org-a → set_installation_id(111) → cache wiped → fetch token for 111
t3: Webhook from org-c → set_installation_id(333) → cache wiped → fetch token for 333
```

Every installation switch triggers a token refresh (HTTP round-trip to GitHub). With N installations sending interleaved webhooks, the cache hit rate approaches zero. Each refresh adds ~100-200ms of latency and consumes one of the 5,000/hr JWT API rate limit.

### Failure mode 2: Race condition (correctness bug)

Since `GitHubAppAuth` is shared mutable state with no locking, concurrent webhooks from different installations can corrupt each other:

```
t0: Request A (installation 111): set_installation_id(111)
t1: Request A: starts refresh_if_needed() — sends JWT exchange request
t2: Request B (installation 222): set_installation_id(222) — mutates self.installation_id
t3: Request A: refresh completes — stores token, but self.installation_id is now 222
t4: Request A: calls GitHub API for org-a using a token scoped to org-b → 401 or wrong data
```

The token returned by GitHub is scoped to the `installation_id` in the URL of the exchange request. But between sending the request (t1) and storing the result (t3), another request changed `self.installation_id`. The server now associates the wrong token with the wrong installation.

### Failure mode 3: No token sharing across replicas

If the webhook server is scaled to multiple replicas (HPA), each pod has its own `GitHubAppAuth` instance with its own cache. For M replicas × N installations:

- Cold start: M × N token refreshes
- Steady state: M independent refresh cycles (tokens expire at different times per pod)
- No coordination: pods can't reuse each other's valid tokens

This is wasteful but not a correctness issue. At 3 replicas × 50 installations = 150 refreshes/hour, well within GitHub's 5,000/hr JWT rate limit.

## Solution

### Per-installation auth instances with async locking

Replace the single `GitHubAppAuth` with a `GitHubAppAuthManager` that maintains isolated state per installation:

```
GitHubAppAuthManager
├── _app_id: str
├── _private_key: str
├── _instances: dict[int, _InstallationAuth]
│   ├── 111 → _InstallationAuth(token="ghs_...", expires_at=..., lock=asyncio.Lock)
│   ├── 222 → _InstallationAuth(token="ghs_...", expires_at=..., lock=asyncio.Lock)
│   └── 333 → _InstallationAuth(token="ghs_...", expires_at=..., lock=asyncio.Lock)
```

Key properties:

1. **No shared mutable state between installations.** Each installation gets its own `_InstallationAuth` with its own token, expiry, and lock. Webhooks from org-a never touch org-b's state.

2. **Async lock per installation.** When two concurrent webhooks arrive for the same installation, only one refreshes the token. The other awaits the lock and reuses the freshly cached token.

3. **No cache invalidation.** Tokens are never wiped — they expire naturally after 1 hour and are refreshed transparently.

4. **In-memory only.** No Redis, no shared external state. Each pod manages its own token cache. The overhead of redundant refreshes across replicas is negligible (see below).

### Why not Redis-backed token sharing?

Storing installation tokens in Redis would let all replicas share a single token per installation, reducing refreshes. However:

- **Security surface**: installation tokens grant full API access for an organization. Storing them in Redis means any pod (or attacker) that reaches Redis can impersonate any installation.
- **Redis currently has no encryption at rest**, no TLS in the cluster, and no network policy restricting access.
- **Marginal benefit**: token refresh is 1 HTTP call per installation per hour per pod. At 5 replicas × 100 installations = 500 refreshes/hour — 10% of the JWT rate limit. Not worth the added complexity and risk.

### Interface changes

The webhook handler flow changes from:

```python
# Before: parse_event mutates shared auth
event = platform.parse_event(request, body)
# auth.installation_id is now set globally
await platform.ensure_auth()
# uses the single cached token
```

To:

```python
# After: parse_event returns installation_id, auth is resolved per-request
event = platform.parse_event(request, body)
# platform.ensure_auth() internally resolves the right _InstallationAuth
# based on the installation_id extracted from the event
await platform.ensure_auth(installation_id=event.installation_id)
```

The `GitHubPlatform` would hold a `GitHubAppAuthManager` instead of a `GitHubAppAuth`, and API calls would use the installation-scoped token.

### Cost analysis

| Metric | Before (single cache) | After (per-installation) |
|--------|----------------------|-------------------------|
| Token refreshes (1 pod, 50 installations) | Up to 50/min under thrashing | 50/hour steady state |
| Token refreshes (3 pods, 50 installations) | Up to 150/min under thrashing | 150/hour steady state |
| Memory per pod | ~1 KB (1 token) | ~50 KB (50 tokens + locks) |
| Race condition risk | Yes — correctness bug | None — isolated state |
| External dependencies | None | None |
