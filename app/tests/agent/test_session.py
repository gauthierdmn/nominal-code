# type: ignore
import asyncio

import pytest

from nominal_code.agent.cli.session import SessionQueue, SessionStore
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName


class TestSessionStore:
    def test_get_returns_none_for_unknown_key(self):
        store = SessionStore()

        assert store.get(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER) is None

    def test_set_and_get(self):
        store = SessionStore()
        store.set(PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, "session-abc")

        assert (
            store.get(PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER)
            == "session-abc"
        )

    def test_get_different_keys_are_independent(self):
        store = SessionStore()
        store.set(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "session-1")
        store.set(PlatformName.GITHUB, "owner/repo", 2, BotType.WORKER, "session-2")

        assert (
            store.get(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER)
            == "session-1"
        )
        assert (
            store.get(PlatformName.GITHUB, "owner/repo", 2, BotType.WORKER)
            == "session-2"
        )

    def test_set_overwrites_existing(self):
        store = SessionStore()
        store.set(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "old")
        store.set(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "new")

        assert store.get(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER) == "new"

    def test_different_platforms_are_independent(self):
        store = SessionStore()
        store.set(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "gh-session")
        store.set(PlatformName.GITLAB, "owner/repo", 1, BotType.WORKER, "gl-session")

        assert (
            store.get(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER)
            == "gh-session"
        )
        assert (
            store.get(PlatformName.GITLAB, "owner/repo", 1, BotType.WORKER)
            == "gl-session"
        )

    def test_different_bot_types_are_independent(self):
        store = SessionStore()
        store.set(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "worker-session"
        )
        store.set(
            PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER, "reviewer-session"
        )

        assert (
            store.get(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER)
            == "worker-session"
        )
        assert (
            store.get(PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER)
            == "reviewer-session"
        )


class TestSessionQueue:
    @pytest.mark.asyncio
    async def test_enqueue_executes_job(self):
        queue = SessionQueue()
        executed = []

        async def job():
            executed.append(True)

        await queue.enqueue(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, job)
        await asyncio.sleep(0.05)

        assert executed == [True]

    @pytest.mark.asyncio
    async def test_enqueue_serializes_jobs_for_same_key(self):
        queue = SessionQueue()
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
        queue = SessionQueue()
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
        queue = SessionQueue()
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
        queue = SessionQueue()
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


class TestSessionStoreInit:
    def test_session_store_init_creates_empty_sessions(self):
        store = SessionStore()

        assert store._sessions == {}

    def test_session_store_init_sessions_dict_is_independent(self):
        store_a = SessionStore()
        store_b = SessionStore()
        store_a.set(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "s1")

        assert store_b.get(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER) is None


class TestSessionQueueInit:
    def test_session_queue_init_creates_empty_queues(self):
        queue = SessionQueue()

        assert queue._queues == {}

    def test_session_queue_init_creates_empty_consumers(self):
        queue = SessionQueue()

        assert queue._consumers == {}

    def test_session_queue_init_instances_are_independent(self):
        queue_a = SessionQueue()
        queue_b = SessionQueue()

        assert queue_a._queues is not queue_b._queues


class TestSessionQueueConsume:
    @pytest.mark.asyncio
    async def test_consume_runs_job_and_cleans_up(self):
        queue = SessionQueue()
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
        queue = SessionQueue()
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
        queue = SessionQueue()

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


class TestSessionQueueEdgeCases:
    @pytest.mark.asyncio
    async def test_single_job_consumer_exits_and_cleans_up(self):
        queue = SessionQueue()
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
        queue = SessionQueue()
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
        queue = SessionQueue()

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
        queue = SessionQueue()
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
        queue = SessionQueue()
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
        queue = SessionQueue()
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
