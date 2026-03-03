#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import TypedDict

# ast is imported lazily: only loaded when a file actually needs parsing.
# This saves ~5ms on hot-path runs where every file is cached and unchanged.

CACHE_FILE = Path(__file__).parent.parent / ".claude" / ".cache" / ".docstring_cache.json"


class CacheEntry(TypedDict):
    """
    Cached docstring-check result for a single source file.
    """

    mtime: float
    violations: list[str]


def source_files(cwd: str) -> list[Path]:
    """
    Return all non-test Python source files under app/nominal_code/.

    Args:
        cwd (str): Current working directory path.

    Returns:
        (list[Path]): Sorted list of matching Python file paths.

    """

    source_dir = Path(cwd) / "app" / "nominal_code"

    if not source_dir.is_dir():
        return []

    return sorted(
        path
        for path in source_dir.rglob("*.py")
        if "test" not in path.parts
    )


def load_cache() -> dict[str, CacheEntry]:
    """
    Load the docstring cache from disk.

    Returns:
        (dict[str, CacheEntry]): Mapping of relative file paths to cache entries.

    """

    try:
        return json.loads(CACHE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def save_cache(cache: dict[str, CacheEntry]) -> None:
    """
    Persist the docstring cache to disk.

    Args:
        cache (dict[str, CacheEntry]): Mapping of relative file paths to cache entries.

    """

    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache))
    except OSError:
        pass


def check_file_cached(
    path: Path,
    cwd: str,
    cache: dict[str, CacheEntry],
) -> tuple[list[str], bool]:
    """
    Return violations for a file, using the cache when the file is unchanged.

    Args:
        path (Path): Absolute path to the source file.
        cwd (str): Current working directory used to compute the relative path.
        cache (dict[str, CacheEntry]): Mutable cache mapping relative paths to entries.

    Returns:
        (tuple[list[str], bool]): A tuple of (violations, cache_hit).

    """

    rel = str(path.relative_to(cwd))
    mtime = path.stat().st_mtime
    entry = cache.get(rel)

    if entry and entry.get("mtime") == mtime:
        return entry["violations"], True

    violations = _check_file(path, rel)
    cache[rel] = {"mtime": mtime, "violations": violations}

    return violations, False


def _check_file(path: Path, rel: str) -> list[str]:
    """
    Run all docstring checks on a single source file.

    Args:
        path (Path): Absolute path to the source file.
        rel (str): Path relative to the working directory, used in violation messages.

    Returns:
        (list[str]): List of violation strings found in the file.

    """

    try:
        source = path.read_text(encoding="utf-8")
    except OSError:
        return []

    violations: list[str] = []
    violations.extend(_scan_lines(rel, source.splitlines()))
    violations.extend(_check_ast(rel, source))

    return violations


def _scan_lines(rel: str, lines: list[str]) -> list[str]:
    """
    Single pass for Rule 1 (content on opening line) and Rule 2 (no blank after closing).

    Args:
        rel (str): Relative file path used in violation messages.
        lines (list[str]): Source file lines without trailing newlines.

    Returns:
        (list[str]): List of violation strings.

    """

    violations: list[str] = []
    in_docstring = False

    for index, line in enumerate(lines):
        stripped = line.strip()

        if not in_docstring:
            if not stripped.startswith('"""'):
                continue

            # Rule 1: opening """ with content on same line
            if re.match(r'"""[^"\s]', stripped):
                # Genuine single-line docstring: """content""" — allowed
                if not re.match(r'""".*"""\s*$', stripped):
                    violations.append(
                        f'{rel}:{index + 1} [R1] opening """ has content on same line'
                        f" — {stripped[:60]}"
                    )

            # Track open unless this whole docstring fits on one line
            if not (re.match(r'""".*"""\s*$', stripped) and len(stripped) > 3):
                in_docstring = True

        else:
            if stripped == '"""':
                in_docstring = False

                # Rule 2: next line must be blank
                if index + 1 < len(lines):
                    next_stripped = lines[index + 1].strip()

                    if next_stripped and not next_stripped.startswith("#"):
                        violations.append(
                            f'{rel}:{index + 2} [R2] no blank line after closing """'
                            f" — {next_stripped[:60]}"
                        )

            elif '"""' in stripped:
                in_docstring = False

    return violations


def _check_ast(rel: str, source: str) -> list[str]:
    """
    Check Rules 3, 4, and 5 by walking the AST.

    Imports the ast module lazily to avoid parse overhead on cache-hit runs.

    Args:
        rel (str): Relative file path used in violation messages.
        source (str): Full source text of the file.

    Returns:
        (list[str]): List of violation strings.

    """

    import ast  # noqa: PLC0415

    violations: list[str] = []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    # Rule 5: module-level docstring
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        violations.append(f"{rel}:{tree.body[0].lineno} [R5] module-level docstring")

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        raw_doc = ast.get_docstring(node, clean=False)

        if not raw_doc:
            continue

        lineno = node.body[0].lineno

        violations.extend(_check_args_typed(rel, raw_doc, lineno, node.name))
        violations.extend(_check_returns_typed(rel, raw_doc, lineno, node.name))

    return violations


def _check_args_typed(rel: str, docstring: str, lineno: int, func: str) -> list[str]:
    """
    Check that each documented argument includes a type annotation.

    Args:
        rel (str): Relative file path used in violation messages.
        docstring (str): Raw docstring content.
        lineno (int): Line number of the docstring in the source file.
        func (str): Function name, used in violation messages.

    Returns:
        (list[str]): List of Rule 3 violations.

    """

    violations: list[str] = []
    in_args = False

    for doc_line in docstring.splitlines():
        stripped = doc_line.strip()

        if stripped == "Args:":
            in_args = True
            continue

        if in_args and re.match(r"^[A-Z]\w*:\s*$", stripped):
            in_args = False
            continue

        if in_args and re.match(r"^[a-z_]\w*\s*:", stripped):
            violations.append(
                f"{rel}:{lineno} [R3] {func}() — arg missing type: {stripped[:50]}"
            )

    return violations


def _check_returns_typed(rel: str, docstring: str, lineno: int, func: str) -> list[str]:
    """
    Check that the Returns section includes a type prefix.

    Args:
        rel (str): Relative file path used in violation messages.
        docstring (str): Raw docstring content.
        lineno (int): Line number of the docstring in the source file.
        func (str): Function name, used in violation messages.

    Returns:
        (list[str]): List of Rule 4 violations (zero or one item).

    """

    in_returns = False

    for doc_line in docstring.splitlines():
        stripped = doc_line.strip()

        if stripped == "Returns:":
            in_returns = True
            continue

        if in_returns and re.match(r"^[A-Z]\w*:\s*$", stripped):
            return []

        if not in_returns or not stripped:
            continue

        if not re.match(r"^[\w\[\]|,. ]+\s*:", stripped):
            return [
                f"{rel}:{lineno} [R4] {func}() — Returns missing type prefix: {stripped[:50]}"
            ]

        return []

    return []


def _collect_violations(cwd: str, use_cache: bool) -> list[str]:
    """
    Run checks across all source files and return all violations.

    Args:
        cwd (str): Repository root path.
        use_cache (bool): Whether to load and save the file-mtime cache.

    Returns:
        (list[str]): All violation strings across every source file.

    """

    cache: dict[str, CacheEntry] = load_cache() if use_cache else {}
    all_violations: list[str] = []
    cache_dirty = False

    for path in source_files(cwd):
        violations, cache_hit = check_file_cached(path, cwd, cache)
        all_violations.extend(violations)

        if not cache_hit:
            cache_dirty = True

    if use_cache and cache_dirty:
        save_cache(cache)

    return all_violations


def _run_hook() -> None:
    """
    Run in Claude hook mode: read JSON from stdin, output a block decision if violations exist.

    """

    try:
        hook_input: dict[str, object] = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    if hook_input.get("stop_hook_active", False):
        sys.exit(0)

    cwd: str = str(hook_input.get("cwd", Path.cwd()))
    all_violations = _collect_violations(cwd, use_cache=True)

    if not all_violations:
        sys.exit(0)

    violation_list = "\n".join(all_violations)
    reason = (
        "Docstring violations detected in nominal_code/. "
        "Please invoke /check-docstrings to fix the following:\n\n"
        + violation_list
    )

    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


def _run_ci() -> None:
    """
    Run in CI mode: print violations to stdout and exit 1 if any are found.

    Must be called from the repository root so that app/nominal_code/ is resolvable.

    """

    all_violations = _collect_violations(str(Path.cwd()), use_cache=False)

    if not all_violations:
        sys.exit(0)

    for violation in all_violations:
        print(violation)

    sys.exit(1)


def main() -> None:
    """
    Entry point supporting both Claude hook mode and CI mode.

    Args:
        --ci: Run as a CI check (human-readable output, exits 1 on violations).
              Omit to run as a Claude Stop hook (JSON output, exits 0 always).

    """

    parser = argparse.ArgumentParser(description="Check docstring conventions in nominal_code/.")
    parser.add_argument(
        "--ci",
        action="store_true",
        help="CI mode: print violations to stdout and exit 1 on failure.",
    )
    args = parser.parse_args()

    if args.ci:
        _run_ci()
    else:
        _run_hook()


if __name__ == "__main__":
    main()
