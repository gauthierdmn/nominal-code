from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
from pathlib import Path
from typing import Any

logger: logging.Logger = logging.getLogger(__name__)

MAX_GLOB_RESULTS: int = 200
MAX_GREP_OUTPUT_LENGTH: int = 30000
MAX_READ_LINES: int = 2000
MAX_LINE_LENGTH: int = 2000

TOOL_DEFINITIONS: list[dict[str, Any]] = [
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


def get_tool_definitions(
    allowed_tools: list[str] | None,
) -> list[dict[str, Any]]:
    """
    Return tool definitions filtered by the allowed tools list.

    If ``allowed_tools`` is None or empty, all tools are returned.
    Entries like ``"Bash(git clone*)"`` enable the Bash tool; the pattern
    is enforced at execution time by ``execute_tool``.

    Args:
        allowed_tools (list[str] | None): List of allowed tool names/patterns.

    Returns:
        list[dict[str, Any]]: Filtered list of Anthropic API tool definitions.
    """

    if not allowed_tools:
        return list(TOOL_DEFINITIONS)

    allowed_names: set[str] = set()

    for entry in allowed_tools:
        name: str = entry.split("(")[0]
        allowed_names.add(name)

    return [tool for tool in TOOL_DEFINITIONS if tool["name"] in allowed_names]


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


async def execute_tool(
    name: str,
    tool_input: dict[str, Any],
    cwd: str,
    allowed_tools: list[str] | None = None,
) -> str:
    """
    Execute a tool and return the result as a string.

    Args:
        name (str): The tool name (Read, Glob, Grep, Bash).
        tool_input (dict[str, Any]): The tool input parameters from the API response.
        cwd (str): Working directory for the tool execution.
        allowed_tools (list[str] | None): Allowed tools list (for Bash pattern validation).

    Returns:
        str: The tool output as a string, or an error message.
    """

    try:
        if name == "Read":
            return _execute_read(tool_input, cwd)

        if name == "Glob":
            return _execute_glob(tool_input, cwd)

        if name == "Grep":
            return await _execute_grep(tool_input, cwd)

        if name == "Bash":
            return await _execute_bash(tool_input, cwd, allowed_tools)

        return f"Error: Unknown tool '{name}'."
    except Exception as exc:
        logger.debug("Tool %s failed: %s", name, exc)

        return f"Error executing {name}: {exc}"


def _resolve_path(file_path: str, cwd: str) -> str:
    """
    Resolve a file path relative to the working directory.

    Args:
        file_path (str): The path to resolve (absolute or relative).
        cwd (str): The working directory.

    Returns:
        str: The resolved absolute path.
    """

    if os.path.isabs(file_path):
        return file_path

    return os.path.join(cwd, file_path)


def _execute_read(tool_input: dict[str, Any], cwd: str) -> str:
    """
    Read a file and return numbered lines.

    Args:
        tool_input (dict[str, Any]): Must contain ``file_path``, optionally
            ``offset`` and ``limit``.
        cwd (str): Working directory for resolving relative paths.

    Returns:
        str: File contents with line numbers, or an error message.
    """

    file_path: str = _resolve_path(tool_input["file_path"], cwd)

    if not os.path.isfile(file_path):
        return f"Error: File not found: {tool_input['file_path']}"

    offset: int = max(1, tool_input.get("offset", 1))
    limit: int = tool_input.get("limit", MAX_READ_LINES)

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            lines: list[str] = f.readlines()
    except OSError as exc:
        return f"Error reading file: {exc}"

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


def _execute_glob(tool_input: dict[str, Any], cwd: str) -> str:
    """
    Find files matching a glob pattern.

    Args:
        tool_input (dict[str, Any]): Must contain ``pattern``, optionally
            ``path``.
        cwd (str): Working directory for resolving relative paths.

    Returns:
        str: Newline-separated matching file paths, or a message if none found.
    """

    pattern: str = tool_input["pattern"]
    search_dir: str = tool_input.get("path", "") or cwd

    if not os.path.isabs(search_dir):
        search_dir = os.path.join(cwd, search_dir)

    search_path: Path = Path(search_dir)

    if not search_path.is_dir():
        return f"Error: Directory not found: {search_dir}"

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


async def _execute_grep(tool_input: dict[str, Any], cwd: str) -> str:
    """
    Search file contents using grep.

    Args:
        tool_input (dict[str, Any]): Must contain ``pattern``, optionally
            ``path`` and ``include``.
        cwd (str): Working directory for resolving relative paths.

    Returns:
        str: Matching lines with file paths and line numbers.
    """

    pattern: str = tool_input["pattern"]
    search_path: str = tool_input.get("path", "") or "."

    if not os.path.isabs(search_path):
        search_path = os.path.join(cwd, search_path)

    cmd: list[str] = ["grep", "-rn", "--binary-files=without-match"]

    include: str = tool_input.get("include", "")

    if include:
        cmd.extend(["--include", include])

    cmd.extend([pattern, search_path])

    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=30.0,
        )
    except TimeoutError:
        return "Error: grep timed out after 30 seconds."
    except OSError as exc:
        return f"Error running grep: {exc}"

    output: str = stdout_bytes.decode(errors="replace").strip()

    if not output:
        if process.returncode == 1:
            return f"No matches found for pattern: {pattern}"

        stderr: str = stderr_bytes.decode(errors="replace").strip()

        return f"grep error: {stderr}" if stderr else "No matches found."

    if len(output) > MAX_GREP_OUTPUT_LENGTH:
        output = output[:MAX_GREP_OUTPUT_LENGTH] + "\n...(truncated)"

    return output


async def _execute_bash(
    tool_input: dict[str, Any],
    cwd: str,
    allowed_tools: list[str] | None = None,
) -> str:
    """
    Execute a bash command with allowlist validation.

    Args:
        tool_input (dict[str, Any]): Must contain ``command``.
        cwd (str): Working directory for the command.
        allowed_tools (list[str] | None): Allowed tools list for pattern
            validation.

    Returns:
        str: Command output, or an error message if not allowed.
    """

    command: str = tool_input["command"]
    bash_patterns: list[str] = _parse_bash_patterns(allowed_tools)

    if bash_patterns:
        allowed: bool = any(
            fnmatch.fnmatch(command, pattern) for pattern in bash_patterns
        )

        if not allowed:
            return f"Error: Command not allowed. Permitted patterns: {bash_patterns}"

    try:
        process: asyncio.subprocess.Process = await asyncio.create_subprocess_exec(
            "bash",
            "-c",
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
        )

        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=120.0,
        )
    except TimeoutError:
        return "Error: Command timed out after 120 seconds."
    except OSError as exc:
        return f"Error running command: {exc}"

    stdout: str = stdout_bytes.decode(errors="replace").strip()
    stderr: str = stderr_bytes.decode(errors="replace").strip()

    if process.returncode != 0:
        parts: list[str] = [f"Command exited with code {process.returncode}."]

        if stdout:
            parts.append(f"stdout:\n{stdout}")

        if stderr:
            parts.append(f"stderr:\n{stderr}")

        return "\n".join(parts)

    result: str = stdout

    if stderr:
        result += f"\nstderr:\n{stderr}"

    return result if result else "(no output)"
