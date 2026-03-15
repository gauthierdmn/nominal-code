from __future__ import annotations

import os
import re
from collections.abc import Iterable

SAFE_ENV_VARS: frozenset[str] = frozenset(
    {
        "PATH",
        "HOME",
        "LANG",
        "LC_ALL",
        "TERM",
        "TMPDIR",
        "USER",
        "LOGNAME",
        "SHELL",
    }
)

SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"glpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"gh[ps]_[A-Za-z0-9]{36,}"),
    re.compile(r"sk-[A-Za-z0-9]{32,}"),
    re.compile(r"AIza[A-Za-z0-9_-]{35}"),
    re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{20,}"),
]

REDACTED: str = "[REDACTED]"


def build_sanitized_env(
    extra_safe_vars: Iterable[str] = (),
) -> dict[str, str]:
    """
    Return ``os.environ`` filtered to only safe variable names.

    Uses an allowlist approach — only explicitly listed variables are kept.
    This prevents secrets (``GITLAB_TOKEN``, ``REDIS_URL``, API keys, etc.)
    from leaking into subprocess environments.

    Args:
        extra_safe_vars (Iterable[str]): Additional variable names to allow
            beyond the default ``SAFE_ENV_VARS`` set.

    Returns:
        dict[str, str]: Filtered environment dictionary.
    """

    allowed: frozenset[str] = SAFE_ENV_VARS | frozenset(extra_safe_vars)

    return {key: value for key, value in os.environ.items() if key in allowed}


def sanitize_output(text: str) -> str:
    """
    Redact known secret patterns from tool output text.

    Scans the text for patterns matching common API tokens, private keys,
    and bearer tokens. Any match is replaced with ``[REDACTED]``.

    Args:
        text (str): The raw output text to sanitize.

    Returns:
        str: The text with secret patterns replaced.
    """

    result: str = text

    for pattern in SECRET_PATTERNS:
        result = pattern.sub(REDACTED, result)

    return result
