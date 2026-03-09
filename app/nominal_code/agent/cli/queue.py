from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from nominal_code.models import BotType, PRKey
from nominal_code.platforms.base import PlatformName

logger: logging.Logger = logging.getLogger(__name__)


class JobQueue:
    """
    Per-PR async job queue ensuring serial execution within a conversation.

    When a job is enqueued for a PR key that has no active consumer,
    a new consumer task is spawned. The consumer pulls jobs one at a time,
    guaranteeing that concurrent comments in the same PR never race on the
    same conversation. Idle queues are cleaned up automatically.
    """

    def __init__(self) -> None:
        """
        Initialize an empty queue manager.
        """

        self._queues: dict[
            PRKey,
            asyncio.Queue[Callable[[], Awaitable[None]]],
        ] = {}
        self._consumers: dict[PRKey, asyncio.Task[None]] = {}

    async def enqueue(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        job: Callable[[], Awaitable[None]],
    ) -> None:
        """
        Enqueue an async job for serial execution within a PR conversation.

        If no consumer task exists for the given PR key, one is
        spawned automatically.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            job (Callable[[], Awaitable[None]]): A zero-argument async
                callable to execute.
        """

        key: PRKey = (platform.value, repo, pr_number, bot_type.value)

        if key not in self._queues:
            self._queues[key] = asyncio.Queue()

        await self._queues[key].put(job)

        if key not in self._consumers or self._consumers[key].done():
            self._consumers[key] = asyncio.create_task(self._consume(key))

    async def _consume(self, key: PRKey) -> None:
        """
        Consume jobs from the queue for a specific key.

        Processes all queued jobs, then exits. A new consumer is spawned
        if more jobs arrive after this one finishes.

        Args:
            key (PRKey): The (platform, repo, pr_number, bot_type) key.
        """

        queue: asyncio.Queue[Callable[[], Awaitable[None]]] = self._queues[key]

        while not queue.empty():
            job: Callable[[], Awaitable[None]] = await queue.get()

            try:
                await job()
            except Exception:
                logger.exception("Job failed for key %s", key)
            finally:
                queue.task_done()

        self._queues.pop(key, None)
        self._consumers.pop(key, None)
