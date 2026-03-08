from __future__ import annotations

import asyncio
import logging
import sys
from datetime import timedelta

from aiohttp import web
from environs import Env

from nominal_code.agent.cli.job import JobQueue
from nominal_code.agent.memory import ConversationStore
from nominal_code.config import Config
from nominal_code.platforms import build_platforms
from nominal_code.platforms.base import Platform
from nominal_code.webhooks.server import create_app
from nominal_code.workspace.cleanup import WorkspaceCleaner

logger: logging.Logger = logging.getLogger(__name__)
env: Env = Env()


def setup_logging() -> None:
    """
    Configure root logging with a timestamped format.

    Respects the ``LOG_LEVEL`` environment variable (default: ``INFO``).
    """

    level_name: str = env.str("LOG_LEVEL", "INFO").upper()
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

    conversation_store: ConversationStore = ConversationStore()
    job_queue: JobQueue = JobQueue()

    if config.kubernetes is not None:
        from nominal_code.jobs.kubernetes import KubernetesRunner

        runner: KubernetesRunner | InProcessRunner = KubernetesRunner(
            config.kubernetes,
        )
    else:
        from nominal_code.jobs.in_process import InProcessRunner

        runner = InProcessRunner(
            config=config,
            platforms=platforms,
            conversation_store=conversation_store,
            job_queue=job_queue,
        )

    app: web.Application = create_app(
        config=config,
        platforms=platforms,
        runner=runner,
    )

    enabled: list[str] = list(platforms.keys())

    bots: list[str] = []

    if config.worker is not None:
        bots.append(f"worker=@{config.worker.bot_username}")

    if config.reviewer is not None:
        bots.append(f"reviewer=@{config.reviewer.bot_username}")

    runner_mode: str = "kubernetes" if config.kubernetes is not None else "in-process"

    logger.info(
        "Starting server on %s:%d | platforms=%s | %s | runner=%s | allowed_users=%s",
        config.webhook_host,
        config.webhook_port,
        enabled,
        " | ".join(bots),
        runner_mode,
        config.allowed_users,
    )

    cleaner: WorkspaceCleaner | None = None

    if config.cleanup_interval_hours > 0:
        cleaner = WorkspaceCleaner(
            base_dir=config.workspace_base_dir,
            platforms=platforms,
            cleanup_wait=timedelta(hours=config.cleanup_interval_hours),
        )

    web_runner: web.AppRunner = web.AppRunner(app)

    await web_runner.setup()

    site: web.TCPSite = web.TCPSite(
        web_runner,
        config.webhook_host,
        config.webhook_port,
    )

    try:
        if cleaner is not None:
            cleaner.purge()
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

        await web_runner.cleanup()


def main() -> None:
    """Entry point: dispatch to run-job, CLI review, CI, or start the webhook server."""

    if len(sys.argv) > 1 and sys.argv[1] == "run-job":
        from nominal_code.jobs.entrypoint import run_job_main

        setup_logging()
        exit_code: int = asyncio.run(run_job_main())
        sys.exit(exit_code)

    if len(sys.argv) > 1 and sys.argv[1] == "review":
        from nominal_code.cli import cli_main

        cli_main()

        return

    if len(sys.argv) > 2 and sys.argv[1] == "ci":
        from nominal_code.ci import run_ci_review

        setup_logging()

        platform_name: str = sys.argv[2]
        exit_code = asyncio.run(run_ci_review(platform_name))
        sys.exit(exit_code)

    setup_logging()

    try:
        asyncio.run(_async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
