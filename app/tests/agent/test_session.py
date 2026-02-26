# type: ignore
import asyncio

import pytest

from nominal_code.agent.session import SessionQueue, SessionStore


class TestSessionStore:
    def test_get_returns_none_for_unknown_key(self):
        store = SessionStore()

        assert store.get("github", "owner/repo", 1, "worker") is None

    def test_set_and_get(self):
        store = SessionStore()
        store.set("github", "owner/repo", 42, "worker", "session-abc")

        assert store.get("github", "owner/repo", 42, "worker") == "session-abc"

    def test_get_different_keys_are_independent(self):
        store = SessionStore()
        store.set("github", "owner/repo", 1, "worker", "session-1")
        store.set("github", "owner/repo", 2, "worker", "session-2")

        assert store.get("github", "owner/repo", 1, "worker") == "session-1"
        assert store.get("github", "owner/repo", 2, "worker") == "session-2"

    def test_set_overwrites_existing(self):
        store = SessionStore()
        store.set("github", "owner/repo", 1, "worker", "old")
        store.set("github", "owner/repo", 1, "worker", "new")

        assert store.get("github", "owner/repo", 1, "worker") == "new"

    def test_different_platforms_are_independent(self):
        store = SessionStore()
        store.set("github", "owner/repo", 1, "worker", "gh-session")
        store.set("gitlab", "owner/repo", 1, "worker", "gl-session")

        assert store.get("github", "owner/repo", 1, "worker") == "gh-session"
        assert store.get("gitlab", "owner/repo", 1, "worker") == "gl-session"

    def test_different_bot_types_are_independent(self):
        store = SessionStore()
        store.set("github", "owner/repo", 1, "worker", "worker-session")
        store.set("github", "owner/repo", 1, "reviewer", "reviewer-session")

        assert store.get("github", "owner/repo", 1, "worker") == "worker-session"
        assert store.get("github", "owner/repo", 1, "reviewer") == "reviewer-session"


class TestSessionQueue:
    @pytest.mark.asyncio
    async def test_enqueue_executes_job(self):
        queue = SessionQueue()
        executed = []

        async def job():
            executed.append(True)

        await queue.enqueue("github", "owner/repo", 1, "worker", job)
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

        await queue.enqueue("github", "owner/repo", 1, "worker", job_a)
        await queue.enqueue("github", "owner/repo", 1, "worker", job_b)
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

        await queue.enqueue("github", "owner/repo", 1, "worker", slow_job)
        await queue.enqueue("github", "owner/repo", 2, "worker", fast_job)
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

        await queue.enqueue("github", "owner/repo", 1, "worker", bad_job)
        await queue.enqueue("github", "owner/repo", 1, "worker", good_job)
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

        await queue.enqueue("github", "owner/repo", 1, "worker", slow_job)
        await queue.enqueue("github", "owner/repo", 1, "reviewer", fast_job)
        await asyncio.sleep(0.1)

        assert order == ["reviewer", "worker"]
