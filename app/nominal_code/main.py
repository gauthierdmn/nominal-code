from __future__ import annotations

import asyncio
import logging
import os
import sys

from aiohttp import web

from nominal_code.config import Config
from nominal_code.platforms import build_platforms
from nominal_code.platforms.base import Platform
from nominal_code.session import SessionQueue, SessionStore
from nominal_code.webhook_server import create_app
from nominal_code.workspace_cleanup import WorkspaceCleaner


def _setup_logging() -> None:
    """
    Configure root logging with a timestamped format.

    Respects the ``LOG_LEVEL`` environment variable (default: ``INFO``).
    """

    level_name: str = os.environ.get("LOG_LEVEL", "INFO").upper()
    level: int = getattr(logging, level_name, logging.INFO)

    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


async def _async_main() -> None:
    """
    Async core: load config, build platforms, create app, and start server.
    """

    logger: logging.Logger = logging.getLogger(__name__)

    logger.info("Loading configuration from environment")

    try:
        config: Config = Config.from_env()
    except (OSError, ValueError) as exc:
        logger.error("Configuration error: %s", exc)
        sys.exit(1)

    platforms: dict[str, Platform] = build_platforms()

    if not platforms:
        logger.error(
            "No platforms configured. "
            "Set GITHUB_TOKEN or GITLAB_TOKEN to enable a platform."
        )
        sys.exit(1)

    session_store: SessionStore = SessionStore()
    session_queue: SessionQueue = SessionQueue()

    app: web.Application = create_app(
        config=config,
        platforms=platforms,
        session_store=session_store,
        session_queue=session_queue,
    )

    enabled: list[str] = list(platforms.keys())

    bots: list[str] = []

    if config.worker is not None:
        bots.append(f"worker=@{config.worker.bot_username}")

    if config.reviewer is not None:
        bots.append(f"reviewer=@{config.reviewer.bot_username}")

    logger.info(
        "Starting server on %s:%d | platforms=%s | %s | allowed_users=%s",
        config.webhook_host,
        config.webhook_port,
        enabled,
        " | ".join(bots),
        config.allowed_users,
    )

    cleaner: WorkspaceCleaner | None = None

    if config.cleanup_interval_hours > 0:
        cleaner = WorkspaceCleaner(
            base_dir=config.workspace_base_dir,
            platforms=platforms,
            interval_seconds=config.cleanup_interval_hours * 3600,
        )

    runner: web.AppRunner = web.AppRunner(app)

    await runner.setup()

    site: web.TCPSite = web.TCPSite(
        runner,
        config.webhook_host,
        config.webhook_port,
    )

    try:
        if cleaner is not None:
            await cleaner.run_once()
            await cleaner.start()

        await site.start()
        logger.info("Server is running, waiting for webhooks...")

        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Shutting down...")

        if cleaner is not None:
            await cleaner.stop()

        await runner.cleanup()


def main() -> None:
    """
    Entry point: set up logging and run the async main loop.
    """

    _setup_logging()

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
