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
            if len(order) == 0:
                await asyncio.sleep(0.02)
            order.append("done")

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        assert order == ["done", "done"]

    @pytest.mark.asyncio
    async def test_enqueue_different_keys_run_concurrently(self):
        queue = AsyncioJobQueue()
        order = []

        async def callback(job):
            if job.event.pr_number == 1:
                await asyncio.sleep(0.05)
                order.append("slow")
            else:
                order.append("fast")

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job(pr_number=1))
        await queue.enqueue(_make_job(pr_number=2))
        await asyncio.sleep(0.1)

        assert order == ["fast", "slow"]

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
    async def test_enqueue_different_bot_types_run_concurrently(self):
        queue = AsyncioJobQueue()
        order = []

        async def callback(job):
            if job.bot_type == "worker":
                await asyncio.sleep(0.05)
                order.append("worker")
            else:
                order.append("reviewer")

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job(bot_type="worker"))
        await queue.enqueue(_make_job(bot_type="reviewer"))
        await asyncio.sleep(0.1)

        assert order == ["reviewer", "worker"]


class TestJobQueueInit:
    def test_job_queue_init_creates_empty_queues(self):
        queue = AsyncioJobQueue()

        assert queue._queues == {}

    def test_job_queue_init_creates_empty_consumers(self):
        queue = AsyncioJobQueue()

        assert queue._consumers == {}

    def test_job_queue_init_instances_are_independent(self):
        queue_a = AsyncioJobQueue()
        queue_b = AsyncioJobQueue()

        assert queue_a._queues is not queue_b._queues


class TestJobQueueConsume:
    @pytest.mark.asyncio
    async def test_consume_runs_job_and_cleans_up(self):
        queue = AsyncioJobQueue()
        executed = []

        async def callback(job):
            executed.append(len(executed))

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert len(executed) == 2
        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_consume_logs_exception_and_continues(self):
        queue = AsyncioJobQueue()
        second_ran = []
        call_count = 0

        async def callback(job):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                raise RuntimeError("oops")

            second_ran.append(True)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        assert second_ran == [True]

    @pytest.mark.asyncio
    async def test_consume_removes_key_after_drain(self):
        queue = AsyncioJobQueue()

        async def callback(job):
            pass

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job(pr_number=99))
        await queue.enqueue(_make_job(pr_number=99))
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 99, "worker")

        assert key not in queue._queues


class TestJobQueueEdgeCases:
    @pytest.mark.asyncio
    async def test_single_job_consumer_exits_and_cleans_up(self):
        queue = AsyncioJobQueue()
        executed = []

        async def callback(job):
            executed.append(True)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert executed == [True]
        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_re_enqueue_after_consumer_exits_spawns_new_consumer(self):
        queue = AsyncioJobQueue()
        executed = []

        async def callback(job):
            executed.append(len(executed))

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        assert executed == [0]

        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        assert executed == [0, 1]

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_single_failing_job_cleans_up(self):
        queue = AsyncioJobQueue()

        async def callback(job):
            raise RuntimeError("boom")

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_all_jobs_fail_still_cleans_up(self):
        queue = AsyncioJobQueue()
        attempted = []

        async def callback(job):
            attempted.append(len(attempted))

            raise ValueError("error")

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job())
        await queue.enqueue(_make_job())
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert len(attempted) == 2
        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_multiple_keys_single_job_each_all_clean_up(self):
        queue = AsyncioJobQueue()
        executed = []

        async def callback(job):
            executed.append(job.event.pr_number)

        queue.set_job_callback(callback)

        await queue.enqueue(_make_job(pr_number=1))
        await queue.enqueue(_make_job(pr_number=2))
        await asyncio.sleep(0.1)

        assert 1 in executed
        assert 2 in executed
        assert queue._queues == {}
        assert queue._consumers == {}

    @pytest.mark.asyncio
    async def test_rapid_sequential_enqueue_to_same_key(self):
        queue = AsyncioJobQueue()
        executed = []
        call_count = 0

        async def callback(job):
            nonlocal call_count
            executed.append(call_count)
            call_count += 1

        queue.set_job_callback(callback)

        for _ in range(5):
            await queue.enqueue(_make_job())

        await asyncio.sleep(0.2)

        assert executed == [0, 1, 2, 3, 4]

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers
