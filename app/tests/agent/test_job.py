# type: ignore
import asyncio

import pytest

from nominal_code.agent.cli.queue import JobQueue
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName


class TestJobQueue:
    @pytest.mark.asyncio
    async def test_enqueue_executes_job(self):
        queue = JobQueue()
        executed = []

        async def job():
            executed.append(True)

        await queue.enqueue(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, job)
        await asyncio.sleep(0.05)

        assert executed == [True]

    @pytest.mark.asyncio
    async def test_enqueue_serializes_jobs_for_same_key(self):
        queue = JobQueue()
        order = []

        async def job_a():
            await asyncio.sleep(0.02)
            order.append("a")

        async def job_b():
            order.append("b")

        await queue.enqueue(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, job_a)
        await queue.enqueue(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, job_b)
        await asyncio.sleep(0.1)

        assert order == ["a", "b"]

    @pytest.mark.asyncio
    async def test_enqueue_different_keys_run_concurrently(self):
        queue = JobQueue()
        order = []

        async def slow_job():
            await asyncio.sleep(0.05)
            order.append("slow")

        async def fast_job():
            order.append("fast")

        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, slow_job
        )
        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 2, BotType.WORKER, fast_job
        )
        await asyncio.sleep(0.1)

        assert order == ["fast", "slow"]

    @pytest.mark.asyncio
    async def test_enqueue_failing_job_does_not_block_next(self):
        queue = JobQueue()
        executed = []

        async def bad_job():
            raise ValueError("boom")

        async def good_job():
            executed.append(True)

        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, bad_job
        )
        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, good_job
        )
        await asyncio.sleep(0.05)

        assert executed == [True]

    @pytest.mark.asyncio
    async def test_enqueue_different_bot_types_run_concurrently(self):
        queue = JobQueue()
        order = []

        async def slow_job():
            await asyncio.sleep(0.05)
            order.append("worker")

        async def fast_job():
            order.append("reviewer")

        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, slow_job
        )
        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER, fast_job
        )
        await asyncio.sleep(0.1)

        assert order == ["reviewer", "worker"]


class TestJobQueueInit:
    def test_job_queue_init_creates_empty_queues(self):
        queue = JobQueue()

        assert queue._queues == {}

    def test_job_queue_init_creates_empty_consumers(self):
        queue = JobQueue()

        assert queue._consumers == {}

    def test_job_queue_init_instances_are_independent(self):
        queue_a = JobQueue()
        queue_b = JobQueue()

        assert queue_a._queues is not queue_b._queues


class TestJobQueueConsume:
    @pytest.mark.asyncio
    async def test_consume_runs_job_and_cleans_up(self):
        queue = JobQueue()
        executed = []

        async def first_job():
            executed.append("first")

        async def second_job():
            executed.append("second")

        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, first_job
        )
        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, second_job
        )
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert "first" in executed
        assert "second" in executed
        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_consume_logs_exception_and_continues(self):
        queue = JobQueue()
        second_ran = []

        async def bad_job():
            raise RuntimeError("oops")

        async def good_job():
            second_ran.append(True)

        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, bad_job
        )
        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, good_job
        )
        await asyncio.sleep(0.1)

        assert second_ran == [True]

    @pytest.mark.asyncio
    async def test_consume_removes_key_after_sentinel(self):
        queue = JobQueue()

        async def first():
            pass

        async def second():
            pass

        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 99, BotType.WORKER, first
        )
        await queue.enqueue(
            PlatformName.GITHUB, "owner/repo", 99, BotType.WORKER, second
        )
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 99, "worker")

        assert key not in queue._queues


class TestJobQueueEdgeCases:
    @pytest.mark.asyncio
    async def test_single_job_consumer_exits_and_cleans_up(self):
        queue = JobQueue()
        executed = []

        async def job():
            executed.append(True)

        await queue.enqueue(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, job)
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert executed == [True]
        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_re_enqueue_after_consumer_exits_spawns_new_consumer(self):
        queue = JobQueue()
        executed = []

        async def first_job():
            executed.append("first")

        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            1,
            BotType.WORKER,
            first_job,
        )
        await asyncio.sleep(0.1)

        assert executed == ["first"]

        async def second_job():
            executed.append("second")

        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            1,
            BotType.WORKER,
            second_job,
        )
        await asyncio.sleep(0.1)

        assert executed == ["first", "second"]

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_single_failing_job_cleans_up(self):
        queue = JobQueue()

        async def bad_job():
            raise RuntimeError("boom")

        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            1,
            BotType.WORKER,
            bad_job,
        )
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_all_jobs_fail_still_cleans_up(self):
        queue = JobQueue()
        attempted = []

        async def bad_first():
            attempted.append("first")

            raise ValueError("first error")

        async def bad_second():
            attempted.append("second")

            raise ValueError("second error")

        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            1,
            BotType.WORKER,
            bad_first,
        )
        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            1,
            BotType.WORKER,
            bad_second,
        )
        await asyncio.sleep(0.1)

        key = ("github", "owner/repo", 1, "worker")

        assert attempted == ["first", "second"]
        assert key not in queue._queues
        assert key not in queue._consumers

    @pytest.mark.asyncio
    async def test_multiple_keys_single_job_each_all_clean_up(self):
        queue = JobQueue()
        executed = []

        async def job_pr1():
            executed.append("pr1")

        async def job_pr2():
            executed.append("pr2")

        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            1,
            BotType.WORKER,
            job_pr1,
        )
        await queue.enqueue(
            PlatformName.GITHUB,
            "owner/repo",
            2,
            BotType.WORKER,
            job_pr2,
        )
        await asyncio.sleep(0.1)

        assert "pr1" in executed
        assert "pr2" in executed
        assert queue._queues == {}
        assert queue._consumers == {}

    @pytest.mark.asyncio
    async def test_rapid_sequential_enqueue_to_same_key(self):
        queue = JobQueue()
        executed = []

        for index in range(5):

            async def job(number=index):
                executed.append(number)

            await queue.enqueue(
                PlatformName.GITHUB,
                "owner/repo",
                1,
                BotType.WORKER,
                job,
            )

        await asyncio.sleep(0.2)

        assert executed == [0, 1, 2, 3, 4]

        key = ("github", "owner/repo", 1, "worker")

        assert key not in queue._queues
        assert key not in queue._consumers
