#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"

CHECKS: list[tuple[str, list[str], Path]] = [
    ("ruff format", ["uv", "run", "ruff", "format", "--check", "nominal_code/", "tests/"], APP_DIR),
    ("ruff check", ["uv", "run", "ruff", "check", "nominal_code/", "tests/"], APP_DIR),
    ("mypy", ["uv", "run", "mypy", "nominal_code/"], APP_DIR),
    ("pytest", ["uv", "run", "pytest", "-x", "-q"], APP_DIR),
    ("docstrings", ["python3", str(REPO_ROOT / "scripts" / "check_docstrings.py"), "--ci"], REPO_ROOT),
]


def run_checks() -> list[tuple[str, str]]:
    failures: list[tuple[str, str]] = []

    for name, cmd, cwd in CHECKS:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=120,
        )

        if result.returncode != 0:
            output = (result.stdout + result.stderr).strip()
            failures.append((name, output))

    return failures


def main() -> None:
    try:
        hook_input: dict[str, object] = json.loads(sys.stdin.read() or "{}")
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    if hook_input.get("stop_hook_active", False):
        sys.exit(0)

    failures = run_checks()

    if not failures:
        sys.exit(0)

    sections: list[str] = []
    docstring_output: str | None = None

    for name, output in failures:
        if name == "docstrings":
            docstring_output = output
        else:
            sections.append(f"## {name}\n\n{output}")

    reason_parts: list[str] = ["Quality checks failed. Fix all issues below before finishing."]

    if sections:
        reason_parts.append("\n\n".join(sections))

    if docstring_output:
        reason_parts.append(
            "## docstrings\n\n"
            "Invoke `/check-docstrings` to fix the following violations:\n\n"
            + docstring_output
        )

    reason = "\n\n".join(reason_parts)

    print(json.dumps({"decision": "block", "reason": reason}))
    sys.exit(0)


if __name__ == "__main__":
    main()
