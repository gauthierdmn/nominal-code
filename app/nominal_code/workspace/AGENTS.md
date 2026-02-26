# workspace/

Git workspace management — cloning, updating, pushing, and periodic cleanup of PR workspaces.

## Key concepts

- **One workspace per PR** — each PR gets its own shallow clone at `{base_dir}/{owner}/{repo}/pr-{N}/`.
- **Shared deps directory** — a `.deps/` directory at `{base_dir}/{owner}/{repo}/.deps/` is shared across all PRs in the same repository, available for cross-PR dependencies.
- **Shallow clones** — `git clone --depth=1 --single-branch` minimises disk and network usage.
- **Background cleanup** — `WorkspaceCleaner` periodically checks if PRs are still open and deletes workspaces for closed/merged PRs.

## File tree

```
workspace/
├── git.py         # GitWorkspace: clone, update (fetch+reset+clean), push; PushResult dataclass
├── setup.py       # resolve_branch(), create_workspace() (no I/O), setup_workspace() (full clone + deps)
└── cleanup.py     # WorkspaceCleaner: background task scanning and deleting stale PR workspaces
```

## Important details

- **GitWorkspace.ensure_ready()** — clones if the directory doesn't exist; otherwise fetches, hard-resets to `origin/{branch}`, and cleans untracked files.
- **GitWorkspace.push_changes()** — stages all changes, commits with the provided message, and pushes. Returns `PushResult(success=False, commit_sha="")` if there are no changes to commit.
- **All git operations** are async (`asyncio.create_subprocess_exec`) and raise `RuntimeError` on non-zero exit codes.
- **resolve_branch()** fetches the branch from the platform API when the webhook payload doesn't include it. Posts an error reply and returns `None` on failure.
- **create_workspace()** constructs a `GitWorkspace` without any I/O — useful when you want to call `ensure_ready()` inside an `asyncio.gather()`.
- **setup_workspace()** combines `create_workspace()` + `ensure_ready()` + `ensure_deps_dir()` for the common synchronous-style setup.
- **WorkspaceCleaner** scans `pr-{N}` directories, queries all configured platforms, and only deletes if every platform reports the PR as closed/merged. Defaults to keeping the workspace on API errors.
- **Orphaned deps cleanup** — if no `pr-{N}` directories remain for a repo, the `.deps/` directory and empty parent directories are removed.
- Cleanup interval is configured via `CLEANUP_INTERVAL_HOURS` (default 6; 0 disables).
