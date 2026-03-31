from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nominal_code.platforms.base import Platform, PlatformAuth, PlatformName
from nominal_code.platforms.github.platform import create_github_platform
from nominal_code.platforms.gitlab.platform import create_gitlab_platform

if TYPE_CHECKING:
    from nominal_code.config.settings import Config

logger: logging.Logger = logging.getLogger(__name__)


def build_platforms(config: Config) -> dict[str, Platform]:
    """
    Build all configured platform clients from config.

    Platforms whose credentials are missing are silently skipped.

    Args:
        config (Config): The application configuration.

    Returns:
        dict[str, Platform]: Mapping of platform names to their instances.
    """

    platforms: dict[str, Platform] = {}

    github: Platform | None = create_github_platform(config.github)

    if github is not None:
        platforms["github"] = github
        logger.info("Platform 'github' enabled")
    else:
        logger.debug("Platform 'github' not configured, skipping")

    gitlab: Platform | None = create_gitlab_platform(config.gitlab)

    if gitlab is not None:
        platforms["gitlab"] = gitlab
        logger.info("Platform 'gitlab' enabled")
    else:
        logger.debug("Platform 'gitlab' not configured, skipping")

    return platforms


def build_platform(platform_name: PlatformName, config: Config) -> Platform:
    """
    Build a single platform client from config.

    Args:
        platform_name (PlatformName): The target platform.
        config (Config): The application configuration.

    Returns:
        Platform: The constructed platform client.

    Raises:
        ValueError: If the platform is not configured.
    """

    if platform_name == PlatformName.GITHUB:
        platform: Platform | None = create_github_platform(config.github)
    else:
        platform = create_gitlab_platform(config.gitlab)

    if platform is None:
        raise ValueError(
            f"Platform '{platform_name.value}' is not configured (missing credentials)",
        )

    return platform


__all__: list[str] = [
    "PlatformAuth",
    "build_platform",
    "build_platforms",
]
