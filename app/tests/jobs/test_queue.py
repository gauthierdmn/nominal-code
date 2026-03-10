# type: ignore
import asyncio

import pytest

from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.queue import AsyncioJobQueue
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_job(pr_number=1, bot_type="worker"):
    event = CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=pr_number,
        pr_branch="feature",
        pr_title="Fix bug",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=100,
        author_username="alice",
        body="@bot review",
    )

    return JobPayload(event=event, bot_type=bot_type)


class TestJobQueue:
    @pytest.mark.asyncio
    async def test_enqueue_executes_job(self):
        queue = AsyncioJobQueue()
        executed = []

        async def callback(job):
            executed.append(True)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await asyncio.sleep(0.05)

        assert executed == [True]

    @pytest.mark.asyncio
    async def test_enqueue_serializes_jobs_for_same_key(self):
        queue = AsyncioJobQueue()
        order = []

        async def callback(job):
            if not order:
                await asyncio.sleep(0.02)
            order.append(job.event.pr_number)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job(pr_number=1))
        await queue.enqueue(_make_job(pr_number=1))
        await asyncio.sleep(0.1)

        assert order == [1, 1]

    @pytest.mark.asyncio
    async def test_enqueue_different_keys_run_concurrently(self):
        queue = AsyncioJobQueue()
        order = []

        async def callback(job):
            if job.event.pr_number == 1:
                await asyncio.sleep(0.05)
            order.append(job.event.pr_number)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job(pr_number=1))
        await queue.enqueue(_make_job(pr_number=2))
        await asyncio.sleep(0.1)

        assert order == [2, 1]

    @pytest.mark.asyncio
    async def test_enqueue_failing_job_does_not_block_next(self):
        queue = AsyncioJobQueue()
        executed = []
        call_count = 0

        async def callback(job):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                raise ValueError("boom")

            executed.append(True)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await queue.enqueue(_make_job())
        await asyncio.sleep(0.05)

        assert executed == [True]

    @pytest.mark.asyncio
    async def test_consumer_cleans_up_after_drain(self):
        queue = AsyncioJobQueue()
        queue.set_job_callback(lambda job: asyncio.sleep(0))

        await queue.enqueue(_make_job())
        await asyncio.sleep(0.05)

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers
