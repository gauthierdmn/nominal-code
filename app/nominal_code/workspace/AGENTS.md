# workspace/

Git workspace management — cloning, updating, pushing, and periodic cleanup of PR workspaces.

## Key concepts

- **One workspace per PR** — each PR gets its own shallow clone at `{base_dir}/{owner}/{repo}/pr-{N}/`.
- **Shared deps directory** — a `.deps/` directory at `{base_dir}/{owner}/{repo}/.deps/` is shared across all PRs in the same repository, available for cross-PR dependencies.
- **Shallow clones** — `git clone --depth=1 --single-branch` minimises disk and network usage.

## File tree

```
workspace/
├── git.py         # GitWorkspace: clone, update (fetch+reset+clean), push; PushResult dataclass
└── setup.py       # resolve_branch(), create_workspace() (no I/O), setup_workspace() (full clone + deps)
```

## Important details

- **GitWorkspace.ensure_ready()** — clones if the directory doesn't exist; otherwise fetches, hard-resets to `origin/{branch}`, and cleans untracked files.
- **GitWorkspace.push_changes()** — stages all changes, commits with the provided message, and pushes. Returns `PushResult(success=False, commit_sha="")` if there are no changes to commit.
- **All git operations** are async (`asyncio.create_subprocess_exec`) and raise `RuntimeError` on non-zero exit codes.
- **resolve_branch()** fetches the branch from the platform API when the webhook payload doesn't include it. Posts an error reply and returns `None` on failure.
- **create_workspace()** constructs a `GitWorkspace` without any I/O — useful when you want to call `ensure_ready()` inside an `asyncio.gather()`.
- **setup_workspace()** combines `create_workspace()` + `ensure_ready()` + `maybe_create_deps_dir()` for the common synchronous-style setup.
