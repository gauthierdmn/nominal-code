import asyncio
import logging
from collections.abc import Awaitable, Callable

from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName

logger: logging.Logger = logging.getLogger(__name__)

SessionKey = tuple[str, str, int, str]


class SessionStore:
    """
    In-memory mapping of PR/MR threads to agent session IDs.

    Each unique (platform, repo, pr_number, bot_type) tuple maps to a single
    agent session, allowing multi-turn conversations within a PR.
    """

    def __init__(self) -> None:
        """
        Initialize an empty session store.
        """

        self._sessions: dict[SessionKey, str] = {}

    def get(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
    ) -> str | None:
        """
        Look up the agent session ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.

        Returns:
            str | None: The session ID, or None if no session exists.
        """

        return self._sessions.get((platform.value, repo, pr_number, bot_type.value))

    def set(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        session_id: str,
    ) -> None:
        """
        Store an agent session ID for a PR/MR thread.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (BotType): The type of bot.
            session_id (str): The agent session ID to store.
        """

        self._sessions[(platform.value, repo, pr_number, bot_type.value)] = session_id


class SessionQueue:
    """
    Per-PR async job queue ensuring serial execution within a session.

    When a job is enqueued for a session key that has no active consumer,
    a new consumer task is spawned. The consumer pulls jobs one at a time,
    guaranteeing that concurrent comments in the same PR never race on the
    same agent session. Idle queues are cleaned up automatically.
    """

    def __init__(self) -> None:
        """
        Initialize an empty queue manager.
        """

        self._queues: dict[
            SessionKey,
            asyncio.Queue[Callable[[], Awaitable[None]] | None],
        ] = {}
        self._consumers: dict[SessionKey, asyncio.Task[None]] = {}

    async def enqueue(
        self,
        platform: PlatformName,
        repo: str,
        pr_number: int,
        bot_type: BotType,
        job: Callable[[], Awaitable[None]],
    ) -> None:
        """
        Enqueue an async job for serial execution within a PR session.

        If no consumer task exists for the given session key, one is
        spawned automatically.

        Args:
            platform (PlatformName): The platform name.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (str): The type of bot.
            job (Callable[[], Awaitable[None]]): A zero-argument async
                callable to execute.
        """

        key: SessionKey = (platform.value, repo, pr_number, bot_type.value)

        if key not in self._queues:
            self._queues[key] = asyncio.Queue()

        await self._queues[key].put(job)

        if key not in self._consumers or self._consumers[key].done():
            self._consumers[key] = asyncio.create_task(self._consume(key))
        else:
            await self._queues[key].put(None)

    async def _consume(self, key: SessionKey) -> None:
        """
        Consume jobs from the queue for a specific session key.

        Runs until a sentinel None is received, then cleans up.

        Args:
            key (SessionKey): The (platform, repo, pr_number, bot_type) session key.
        """

        queue: asyncio.Queue[Callable[[], Awaitable[None]] | None] = self._queues[key]

        while True:
            job: Callable[[], Awaitable[None]] | None = await queue.get()

            if job is None:
                queue.task_done()
                break

            try:
                await job()
            except Exception:
                logger.exception("Job failed for session %s", key)
            finally:
                queue.task_done()

        self._queues.pop(key, None)
        self._consumers.pop(key, None)
