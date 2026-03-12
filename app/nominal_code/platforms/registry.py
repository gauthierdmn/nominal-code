from __future__ import annotations

import logging
from collections.abc import Callable

from nominal_code.platforms.base import Platform

PlatformFactory = Callable[[], Platform | None]

_REGISTRY: dict[str, PlatformFactory] = {}

logger: logging.Logger = logging.getLogger(__name__)


def register_platform(
    name: str,
    factory: PlatformFactory,
    *,
    allow_replace: bool = False,
) -> None:
    """
    Register a platform factory under the given name.

    Called by each platform module at import time to make itself available.

    Args:
        name (str): Unique platform identifier (e.g. ``"github"``).
        factory (PlatformFactory): Callable that returns a Platform instance
            or None if the platform is not configured.
        allow_replace (bool): If True, silently replace an existing factory
            with the same name instead of raising.

    Raises:
        ValueError: If a platform with the same name is already registered
            and ``allow_replace`` is False.
    """

    if name in _REGISTRY and not allow_replace:
        raise ValueError(f"Platform '{name}' is already registered")

    _REGISTRY[name] = factory


def build_platforms() -> dict[str, Platform]:
    """
    Invoke all registered factories and return configured platforms.

    Factories that return None (unconfigured) are silently skipped.

    Returns:
        dict[str, Platform]: Mapping of platform names to their instances.
    """

    platforms: dict[str, Platform] = {}

    for name, factory in _REGISTRY.items():
        platform: Platform | None = factory()

        if platform is not None:
            platforms[name] = platform
            logger.info("Platform '%s' enabled", name)
        else:
            logger.debug("Platform '%s' not configured, skipping", name)

    return platforms
