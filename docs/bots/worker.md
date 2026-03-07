# Worker Bot (Beta)

!!! warning "Beta Feature"
    The worker bot is currently in **beta**. It is functional but may change in future releases. For production use, the [Reviewer Bot](reviewer.md) is the recommended starting point.

The worker bot receives a prompt from a PR comment, clones the repository, runs an AI agent with full tool access, and posts the result back as a comment.

## How to Trigger

Mention the worker bot by its configured username in a PR/MR comment:

```
@my-worker-bot fix the typo in config.py
```

Everything after the `@mention` is passed as the prompt to the agent.

## What It Does

1. Clones the repository (or updates an existing workspace) to the PR's head branch.
2. Creates a shared `.deps/` directory for private dependency cloning.
3. Builds a prompt that includes:
   - The file path and diff hunk (if the comment is on a specific line)
   - The branch name and PR number
   - The user's request text
   - The path to the `.deps/` directory
4. Runs the agent in the cloned workspace.
5. Posts the agent's text output as a reply on the PR/MR.

The agent has full access to the repository and can read, write, and execute commands — including committing and pushing changes.

## Full Tool Access

The worker runs with `bypassPermissions` mode and all tools available. The agent can:

- Read, edit, and create files
- Run shell commands
- Install packages
- Commit and push changes to the PR branch

!!! danger "Security considerations"
    The worker bot runs with full tool access and can execute arbitrary commands. A successful prompt injection could result in unauthorized code changes. Only enable it in trusted, private repositories with a restricted set of allowed users. See [Security](../security.md#llm-security) for details.

## Conversation Continuity

The worker maintains conversation continuity within the same PR. When you send multiple comments, the agent resumes from its previous conversation, preserving context from earlier interactions. Conversations are keyed by `(platform, repo, pr_number, "worker")`.
