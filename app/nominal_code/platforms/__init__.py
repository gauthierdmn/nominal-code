from __future__ import annotations

from types import ModuleType

import nominal_code.platforms.github as _github  # noqa: F401
import nominal_code.platforms.gitlab as _gitlab  # noqa: F401
from nominal_code.platforms.base import PlatformName
from nominal_code.platforms.registry import build_platforms


def load_platform_ci(platform_name: PlatformName) -> ModuleType:
    """
    Import and return the platform-specific CI module.

    Args:
        platform_name (PlatformName): The target platform.

    Returns:
        ModuleType: The platform CI module exposing ``build_event``,
            ``build_platform``, and ``resolve_workspace``.
    """

    if platform_name == PlatformName.GITHUB:
        from nominal_code.platforms.github import ci as _ci
    else:
        from nominal_code.platforms.gitlab import ci as _ci  # type: ignore[no-redef]

    return _ci


__all__: list[str] = ["build_platforms", "load_platform_ci"]
