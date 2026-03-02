import asyncio
import logging
from collections.abc import Awaitable, Callable

logger: logging.Logger = logging.getLogger(__name__)

SessionKey = tuple[str, str, int, str]


class SessionStore:
    """
    In-memory mapping of PR/MR threads to agent session IDs.

    Each unique (platform, repo, pr_number, bot_type) tuple maps to a single
    agent session, allowing multi-turn conversations within a PR. No lock is
    needed because the bot runs on a single asyncio event loop.
    """

    def __init__(self) -> None:
        """
        Initialize an empty session store.
        """

        self._sessions: dict[SessionKey, str] = {}

    def get(
        self,
        platform: str,
        repo: str,
        pr_number: int,
        bot_type: str,
    ) -> str | None:
        """
        Look up the agent session ID for a PR/MR thread.

        Args:
            platform (str): The platform identifier (``github`` or ``gitlab``).
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (str): The bot type (``worker`` or ``reviewer``).

        Returns:
            str | None: The session ID, or None if no session exists.
        """

        return self._sessions.get((platform, repo, pr_number, bot_type))

    def set(
        self,
        platform: str,
        repo: str,
        pr_number: int,
        bot_type: str,
        session_id: str,
    ) -> None:
        """
        Store an agent session ID for a PR/MR thread.

        Args:
            platform (str): The platform identifier.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (str): The bot type (``worker`` or ``reviewer``).
            session_id (str): The agent session ID to store.
        """

        self._sessions[(platform, repo, pr_number, bot_type)] = session_id


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
            asyncio.Queue[Callable[[], Awaitable[None]]],
        ] = {}
        self._consumers: dict[SessionKey, asyncio.Task[None]] = {}

    async def enqueue(
        self,
        platform: str,
        repo: str,
        pr_number: int,
        bot_type: str,
        job: Callable[[], Awaitable[None]],
    ) -> None:
        """
        Enqueue an async job for serial execution within a PR session.

        If no consumer task exists for the given session key, one is
        spawned automatically.

        Args:
            platform (str): The platform identifier.
            repo (str): The full repository name.
            pr_number (int): The pull/merge request number.
            bot_type (str): The bot type (``worker`` or ``reviewer``).
            job (Callable[[], Awaitable[None]]): A zero-argument async
                callable to execute.
        """

        key: SessionKey = (platform, repo, pr_number, bot_type)

        if key not in self._queues:
            self._queues[key] = asyncio.Queue()

        await self._queues[key].put(job)

        if key not in self._consumers or self._consumers[key].done():
            self._consumers[key] = asyncio.create_task(self._consume(key))

    async def _consume(self, key: SessionKey) -> None:
        """
        Consume jobs from the queue for a specific session key.

        Runs until the queue is empty, then cleans up.

        Args:
            key (SessionKey): The (platform, repo, pr_number, bot_type) session key.
        """

        queue: asyncio.Queue[Callable[[], Awaitable[None]]] = self._queues[key]

        while True:
            try:
                job: Callable[[], Awaitable[None]] = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            try:
                await job()
            except Exception:
                logger.exception(
                    "Job failed for session %s",
                    key,
                )
            finally:
                queue.task_done()

            if queue.empty():
                await asyncio.sleep(0)

        del self._queues[key]
        del self._consumers[key]
