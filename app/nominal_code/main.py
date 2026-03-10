from __future__ import annotations

import asyncio
import logging
import sys

from environs import Env

logger: logging.Logger = logging.getLogger(__name__)
env: Env = Env()

USAGE: str = """\
Usage: nominal-code <command> [args]

Commands:
  serve              Start the webhook server
  review             One-shot CLI review (e.g. nominal-code review owner/repo#42)
  ci <platform>      Run a CI review (github or gitlab)
  run-job            Execute a single job in a K8s pod (internal)"""


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


def main() -> None:
    """
    Entry point: dispatch to serve, review, ci, or run-job.
    """

    command: str = sys.argv[1] if len(sys.argv) > 1 else ""

    if command == "serve":
        setup_logging()

        try:
            from nominal_code.commands.webhook.server import run_webhook_server

            asyncio.run(run_webhook_server())

        except KeyboardInterrupt:
            pass

        return

    if command == "review":
        from nominal_code.commands.cli import cli_main

        cli_main()

        return

    if command == "ci":
        if len(sys.argv) < 3:
            print("Usage: nominal-code ci <platform>", file=sys.stderr)
            sys.exit(1)

        from nominal_code.commands.ci import run_ci_review

        setup_logging()

        platform_name: str = sys.argv[2]
        exit_code: int = asyncio.run(run_ci_review(platform_name))
        sys.exit(exit_code)

    if command == "run-job":
        from nominal_code.commands.webhook.entrypoint import run_job_main

        setup_logging()
        exit_code = asyncio.run(run_job_main())
        sys.exit(exit_code)

    print(USAGE, file=sys.stderr)
    sys.exit(1)


if __name__ == "__main__":
    main()
