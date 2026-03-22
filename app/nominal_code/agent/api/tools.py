from __future__ import annotations

import asyncio
import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

from nominal_code.agent.sandbox import build_sanitized_env, sanitize_output
from nominal_code.llm.messages import ToolDefinition

logger: logging.Logger = logging.getLogger(__name__)


MAX_GLOB_RESULTS: int = 200
MAX_GREP_OUTPUT_LENGTH: int = 30_000
MAX_READ_LINES: int = 2000
MAX_LINE_LENGTH: int = 2000

SHELL_INJECTION_PATTERN: re.Pattern[str] = re.compile(
    r"[$`|;&]|\b(eval|exec|source)\b",
)

DEFAULT_ALLOWED_CLONE_HOSTS: frozenset[str] = frozenset(
    {
        "github.com",
        "gitlab.com",
    }
)

GIT_CLONE_PATTERN: re.Pattern[str] = re.compile(
    r"^git\s+clone\s+",
)

SUBMIT_REVIEW_TOOL_NAME: str = "submit_review"

SUBMIT_REVIEW_TOOL: ToolDefinition = {
    "name": SUBMIT_REVIEW_TOOL_NAME,
    "description": (
        "Submit your final code review. You MUST call this tool with your "
        "review summary and inline comments when you have finished reviewing. "
        "Do not output raw JSON — always use this tool."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A brief overall assessment of the changes.",
            },
            "comments": {
                "type": "array",
                "description": "Inline review comments.",
                "items": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": ("File path relative to repository root."),
                        },
                        "line": {
                            "type": "integer",
                            "description": (
                                "Line number in the new version of the file."
                            ),
                        },
                        "body": {
                            "type": "string",
                            "description": ("The review comment explaining the issue."),
                        },
                        "side": {
                            "type": "string",
                            "enum": ["LEFT", "RIGHT"],
                            "description": (
                                "Which side of the diff. LEFT for deleted "
                                "lines, RIGHT for additions and context."
                            ),
                        },
                        "suggestion": {
                            "type": "string",
                            "description": (
                                "Exact replacement code for a one-click-apply "
                                "suggestion."
                            ),
                        },
                        "start_line": {
                            "type": "integer",
                            "description": (
                                "First line of a multi-line range. "
                                "Must be <= line. Works with or without a suggestion."
                            ),
                        },
                    },
                    "required": ["path", "line", "body"],
                },
            },
        },
        "required": ["summary", "comments"],
    },
}

TOOL_DEFINITIONS: list[ToolDefinition] = [
    {
        "name": "Read",
        "description": (
            "Read the contents of a file. Returns the file content with line "
            "numbers. Use this to inspect source files in the repository."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": (
                        "Path to the file to read (absolute or relative to "
                        "working directory)."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Line number to start reading from (1-indexed). "
                        "Optional, defaults to 1."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "Maximum number of lines to read. Optional, defaults "
                        f"to {MAX_READ_LINES}."
                    ),
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Glob",
        "description": (
            "Find files matching a glob pattern. Returns matching file paths "
            "sorted by name, one per line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": (
                        "Glob pattern to match (e.g. '**/*.py', 'src/**/*.ts')."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Directory to search in. Defaults to working directory."
                    ),
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": (
            "Search file contents using a regex pattern. Returns matching "
            "lines with file paths and line numbers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regex pattern to search for.",
                },
                "path": {
                    "type": "string",
                    "description": (
                        "File or directory to search in. Defaults to working directory."
                    ),
                },
                "include": {
                    "type": "string",
                    "description": (
                        "Glob pattern to filter files (e.g. '*.py'). Optional."
                    ),
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Bash",
        "description": (
            "Execute a bash command. Only specific commands are allowed "
            "(e.g. git clone)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The bash command to execute.",
                },
            },
            "required": ["command"],
        },
    },
]


class ToolError(Exception):
    """
    Raised when a tool execution fails.

    The message is sent back to the model as the tool result content.
    """


def get_tool_definitions(
    allowed_tools: list[str] | None,
) -> list[ToolDefinition]:
    """
    Return tool definitions filtered by the allowed tools list.

    If ``allowed_tools`` is None or empty, all tools are returned.
    Entries like ``"Bash(git clone*)"`` enable the Bash tool; the pattern
    is enforced at execution time by ``execute_tool``.

    When ``submit_review`` appears in the allowed list, the structured-output
    tool ``SUBMIT_REVIEW_TOOL`` is appended. The API runner intercepts calls
    to this tool and returns the input as JSON output.

    Args:
        allowed_tools (list[str] | None): List of allowed tool names/patterns.

    Returns:
        list[ToolDefinition]: Filtered list of tool definitions.
    """

    if not allowed_tools:
        return list(TOOL_DEFINITIONS)

    allowed_names: set[str] = set()

    for entry in allowed_tools:
        # extract tools names even with arguments e.g. Bash(git clone*)
        name: str = entry.split("(")[0]
        allowed_names.add(name)

    tools: list[ToolDefinition] = [
        tool for tool in TOOL_DEFINITIONS if tool["name"] in allowed_names
    ]

    if SUBMIT_REVIEW_TOOL_NAME in allowed_names:
        tools.append(SUBMIT_REVIEW_TOOL)

    return tools


async def execute_tool(
    name: str,
    tool_input: dict[str, Any],
    cwd: Path,
    allowed_tools: list[str] | None = None,
) -> tuple[str, bool]:
    """
    Execute a tool and return the result with an error flag.

    Tool output is passed through ``sanitize_output`` to redact any secret
    patterns before being returned to the LLM. Subprocesses (Grep, Bash)
    run with a sanitized environment that strips secrets, and ``git clone``
    commands are restricted to ``DEFAULT_ALLOWED_CLONE_HOSTS``.

    Args:
        name (str): The tool name (Read, Glob, Grep, Bash).
        tool_input (dict[str, Any]): The tool input parameters from the API response.
        cwd (Path): Working directory for the tool execution.
        allowed_tools (list[str] | None): Allowed tools list
            (for Bash pattern validation).

    Returns:
        tuple[str, bool]: The tool output and whether the execution failed.
    """

    try:
        if name == "Read":
            output: str = _execute_read(tool_input=tool_input, cwd=cwd)

            return sanitize_output(output), False

        if name == "Glob":
            output = _execute_glob(tool_input=tool_input, cwd=cwd)

            return sanitize_output(output), False

        if name == "Grep":
            output = await _execute_grep(tool_input=tool_input, cwd=cwd)

            return sanitize_output(output), False

        if name == "Bash":
            output = await _execute_bash(
                tool_input=tool_input,
                cwd=cwd,
                allowed_tools=allowed_tools,
            )

            return sanitize_output(output), False

        raise ToolError(f"Unknown tool '{name}'")

    except ToolError as exc:
        logger.debug("Tool %s failed: %s", name, exc)

        return str(exc), True

    except Exception as exc:
        logger.debug("Tool %s failed unexpectedly: %s", name, exc)

        return f"Unexpected error executing {name}: {exc}", True


def _parse_bash_patterns(allowed_tools: list[str] | None) -> list[str]:
    """
    Extract Bash command patterns from the allowed tools list.

    Args:
        allowed_tools (list[str] | None): List of allowed tool names/patterns.

    Returns:
        list[str]: List of glob patterns for allowed Bash commands.
    """

    if not allowed_tools:
        return []

    patterns: list[str] = []

    for entry in allowed_tools:
        if entry.startswith("Bash(") and entry.endswith(")"):
            patterns.append(entry[5:-1])

    return patterns


def _validate_bash_command(command: str) -> None:
    """
    Reject commands containing shell metacharacters that enable injection.

    Blocks ``$``, backticks, pipes, semicolons, ``&&``, ``||``, and
    dangerous builtins (``eval``, ``exec``, ``source``) that could be
    used to read environment variables or chain commands within an
    otherwise-allowed fnmatch pattern.

    Args:
        command (str): The bash command string to validate.

    Raises:
        ToolError: If the command contains disallowed shell metacharacters.
    """

    if SHELL_INJECTION_PATTERN.search(command):
        raise ToolError("Command contains disallowed shell metacharacters")


def _validate_clone_host(
    command: str,
    allowed_hosts: frozenset[str],
) -> None:
    """
    Validate that a ``git clone`` command targets an allowed hostname.

    Checks for ``host/`` or ``host:`` substrings in the command, which
    covers HTTPS (``https://github.com/...``) and SSH
    (``git@github.com:...``) URL formats. Rejects ``file://`` URLs
    unconditionally.

    Args:
        command (str): The full ``git clone`` command string.
        allowed_hosts (frozenset[str]): Set of permitted hostnames.

    Raises:
        ToolError: If the command contains a ``file://`` URL or does not
            match any allowed hostname.
    """

    if "file://" in command:
        raise ToolError("file:// protocol is not allowed")

    for host in allowed_hosts:
        if f"{host}/" in command or f"{host}:" in command:
            return

    raise ToolError(
        f"git clone target host is not allowed. "
        f"Permitted hosts: {sorted(allowed_hosts)}",
    )


def _resolve_path(file_path: str, cwd: Path) -> Path:
    """
    Resolve a file path relative to the working directory.

    Args:
        file_path (str): The path to resolve (absolute or relative).
        cwd (Path): The working directory.

    Returns:
        Path: The resolved absolute path.
    """

    resolved: Path = Path(file_path)

    if resolved.is_absolute():
        return resolved

    return cwd / resolved


def _execute_read(tool_input: dict[str, Any], cwd: Path) -> str:
    """
    Read a file and return numbered lines.

    Args:
        tool_input (dict[str, Any]): Must contain ``file_path``, optionally
            ``offset`` and ``limit``.
        cwd (Path): Working directory for resolving relative paths.

    Returns:
        str: File contents with line numbers.

    Raises:
        ToolError: If the file does not exist or cannot be read.
    """

    file_path: Path = _resolve_path(
        file_path=tool_input["file_path"],
        cwd=cwd,
    )

    if not file_path.is_file():
        raise ToolError(f"File not found: {tool_input['file_path']}")

    offset: int = max(1, tool_input.get("offset", 1))
    limit: int = tool_input.get("limit", MAX_READ_LINES)

    try:
        with file_path.open(encoding="utf-8", errors="replace") as f:
            lines: list[str] = f.readlines()
    except OSError as exc:
        raise ToolError(f"Error reading file: {exc}") from exc

    start: int = offset - 1
    end: int = start + limit
    selected: list[str] = lines[start:end]

    result_lines: list[str] = []

    for i, line in enumerate(selected, start=offset):
        content: str = line.rstrip("\n\r")

        if len(content) > MAX_LINE_LENGTH:
            content = content[:MAX_LINE_LENGTH] + "..."

        result_lines.append(f"{i:>6}\t{content}")

    return "\n".join(result_lines)


def _execute_glob(tool_input: dict[str, Any], cwd: Path) -> str:
    """
    Find files matching a glob pattern.

    Args:
        tool_input (dict[str, Any]): Must contain ``pattern``, optionally
            ``path``.
        cwd (Path): Working directory for resolving relative paths.

    Returns:
        str: Newline-separated matching file paths, or a message if none found.

    Raises:
        ToolError: If the search directory does not exist.
    """

    pattern: str = tool_input["pattern"]
    raw_path: str = tool_input.get("path", "")
    search_path: Path = Path(raw_path) if raw_path else cwd

    if not search_path.is_absolute():
        search_path = cwd / search_path

    if not search_path.is_dir():
        raise ToolError(f"Directory not found: {search_path}")

    matches: list[str] = []

    for match in sorted(search_path.glob(pattern)):
        if match.is_file():
            try:
                rel: str = str(match.relative_to(cwd))
            except ValueError:
                rel = str(match)

            matches.append(rel)

            if len(matches) >= MAX_GLOB_RESULTS:
                matches.append(f"... (truncated, {MAX_GLOB_RESULTS} results shown)")

                break

    if not matches:
        return f"No files matched pattern: {pattern}"

    return "\n".join(matches)


async def _execute_grep(
    tool_input: dict[str, Any],
    cwd: Path,
) -> str:
    """
    Search file contents using grep.

    Runs with a sanitized environment that strips secrets.

    Args:
        tool_input (dict[str, Any]): Must contain ``pattern``, optionally
            ``path`` and ``include``.
        cwd (Path): Working directory for resolving relative paths.

    Returns:
        str: Matching lines with file paths and line numbers.

    Raises:
        ToolError: If grep times out, fails to start, or exits with an error.
    """

    sanitized_env: dict[str, str] = build_sanitized_env()
    pattern: str = tool_input["pattern"]
    raw_path: str = tool_input.get("path", "")
    search_path: Path = Path(raw_path) if raw_path else cwd

    if not search_path.is_absolute():
        search_path = cwd / search_path

    cmd: list[str] = ["grep", "-rn", "--binary-files=without-match"]

    include: str = tool_input.get("include", "")

    if include:
        cmd.extend(["--include", include])

    cmd.extend([pattern, str(search_path)])

    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=sanitized_env,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=30.0,
        )
    except TimeoutError as exc:
        raise ToolError("grep timed out after 30 seconds") from exc
    except OSError as exc:
        raise ToolError(f"Error running grep: {exc}") from exc

    output: str = stdout_bytes.decode(errors="replace").strip()

    if not output:
        if process.returncode == 1:
            return f"No matches found for pattern: {pattern}"

        stderr: str = stderr_bytes.decode(errors="replace").strip()

        raise ToolError(f"grep error: {stderr}" if stderr else "grep failed")

    if len(output) > MAX_GREP_OUTPUT_LENGTH:
        output = output[:MAX_GREP_OUTPUT_LENGTH] + "\n...(truncated)"

    return output


async def _execute_bash(
    tool_input: dict[str, Any],
    cwd: Path,
    allowed_tools: list[str] | None = None,
) -> str:
    """
    Execute a bash command with allowlist validation.

    Commands are checked for shell metacharacters that could enable injection
    attacks (``$``, backticks, pipes, etc.). For ``git clone`` commands, the
    target URL hostname is validated against ``DEFAULT_ALLOWED_CLONE_HOSTS``.
    Runs with a sanitized environment that strips secrets.

    Args:
        tool_input (dict[str, Any]): Must contain ``command``.
        cwd (Path): Working directory for the command.
        allowed_tools (list[str] | None): Allowed tools list for pattern
            validation.

    Returns:
        str: Command output.

    Raises:
        ToolError: If the command is not allowed, contains shell injection,
            targets a disallowed host, times out, fails to start, or exits
            with a non-zero code.
    """

    sanitized_env: dict[str, str] = build_sanitized_env()
    command: str = tool_input["command"]
    bash_patterns: list[str] = _parse_bash_patterns(allowed_tools=allowed_tools)

    if bash_patterns:
        _validate_bash_command(command)

        allowed: bool = any(
            fnmatch.fnmatch(name=command, pat=pattern) for pattern in bash_patterns
        )

        if not allowed:
            raise ToolError(
                f"Command not allowed. Permitted patterns: {bash_patterns}",
            )

        if GIT_CLONE_PATTERN.search(command):
            _validate_clone_host(
                command=command,
                allowed_hosts=DEFAULT_ALLOWED_CLONE_HOSTS,
            )

    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=sanitized_env,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=120.0,
        )
    except TimeoutError as exc:
        raise ToolError("Command timed out after 120 seconds") from exc
    except OSError as exc:
        raise ToolError(f"Error running command: {exc}") from exc

    stdout: str = stdout_bytes.decode(errors="replace").strip()
    stderr: str = stderr_bytes.decode(errors="replace").strip()

    if process.returncode != 0:
        parts: list[str] = [f"Command exited with code {process.returncode}."]

        if stdout:
            parts.append(f"stdout:\n{stdout}")

        if stderr:
            parts.append(f"stderr:\n{stderr}")

        raise ToolError("\n".join(parts))

    result: str = stdout

    if stderr:
        result += f"\nstderr:\n{stderr}"

    return result if result else "(no output)"
