# type: ignore
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.jobs.payload import JobPayload
from nominal_code.jobs.redis_queue import RedisJobQueue, _build_queue_key
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_job(pr_number=42, bot_type="reviewer"):
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


class TestBuildQueueKey:
    def test_format(self):
        job = _make_job()
        key = _build_queue_key(job)

        assert key == "nc:queue:github:owner/repo:42:reviewer"

    def test_different_pr_numbers_produce_different_keys(self):
        key_a = _build_queue_key(_make_job(pr_number=1))
        key_b = _build_queue_key(_make_job(pr_number=2))

        assert key_a != key_b

    def test_different_bot_types_produce_different_keys(self):
        key_a = _build_queue_key(_make_job(bot_type="reviewer"))
        key_b = _build_queue_key(_make_job(bot_type="worker"))

        assert key_a != key_b


class TestRedisJobQueueEnqueue:
    @pytest.mark.asyncio
    async def test_enqueue_creates_consumer(self):
        job = _make_job()

        with patch("nominal_code.jobs.redis_queue.aioredis") as mock_aioredis:
            mock_redis = AsyncMock()
            mock_aioredis.from_url.return_value = mock_redis
            mock_redis.lpush = AsyncMock()
            mock_redis.brpop = AsyncMock(return_value=None)

            queue = RedisJobQueue("redis://localhost:6379")
            queue.set_job_callback(AsyncMock())

            await queue.enqueue(job)
            await asyncio.sleep(0.1)

            mock_redis.lpush.assert_called_once()
            call_args = mock_redis.lpush.call_args
            assert "nc:queue:" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_serial_execution_same_key(self):
        order = []

        async def track_job(job):
            order.append(job.event.pr_number)
            await asyncio.sleep(0.01)

        with patch("nominal_code.jobs.redis_queue.aioredis") as mock_aioredis:
            mock_redis = AsyncMock()
            mock_aioredis.from_url.return_value = mock_redis

            job_a = _make_job(pr_number=42)
            job_b = _make_job(pr_number=42)

            payloads = [job_b.serialize(), job_a.serialize()]
            mock_redis.lpush = AsyncMock()
            mock_redis.brpop = AsyncMock(
                side_effect=lambda key, timeout: (
                    (b"key", payloads.pop().encode()) if payloads else None
                ),
            )

            queue = RedisJobQueue("redis://localhost:6379")
            queue.set_job_callback(track_job)

            await queue.enqueue(job_a)
            await asyncio.sleep(0.05)
            await queue.enqueue(job_b)
            await asyncio.sleep(0.2)

            assert order == [42, 42]


def _make_mock_pubsub():
    mock_pubsub = AsyncMock()
    mock_pubsub.subscribe = AsyncMock()
    mock_pubsub.unsubscribe = AsyncMock()
    mock_pubsub.close = AsyncMock()

    return mock_pubsub


class TestRedisJobQueueAwaitCompletion:
    @pytest.mark.asyncio
    async def test_await_job_completion_succeeds(self):
        with patch("nominal_code.jobs.redis_queue.aioredis") as mock_aioredis:
            mock_redis = AsyncMock()
            mock_aioredis.from_url.return_value = mock_redis

            mock_pubsub = _make_mock_pubsub()
            mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
            mock_pubsub.get_message = AsyncMock(
                return_value={
                    "type": "message",
                    "data": b"succeeded",
                },
            )

            queue = RedisJobQueue("redis://localhost:6379")
            status = await queue.await_job_completion("test-job", timeout_seconds=5.0)

            assert status == "succeeded"
            mock_pubsub.subscribe.assert_called_once_with("nc:job:test-job:done")

    @pytest.mark.asyncio
    async def test_await_job_completion_timeout(self):
        with patch("nominal_code.jobs.redis_queue.aioredis") as mock_aioredis:
            mock_redis = AsyncMock()
            mock_aioredis.from_url.return_value = mock_redis

            mock_pubsub = _make_mock_pubsub()
            mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
            mock_pubsub.get_message = AsyncMock(return_value=None)

            queue = RedisJobQueue("redis://localhost:6379")

            with pytest.raises(TimeoutError, match="Timed out"):
                await queue.await_job_completion(
                    "test-job",
                    timeout_seconds=0.1,
                )

    @pytest.mark.asyncio
    async def test_await_job_completion_failed_status(self):
        with patch("nominal_code.jobs.redis_queue.aioredis") as mock_aioredis:
            mock_redis = AsyncMock()
            mock_aioredis.from_url.return_value = mock_redis

            mock_pubsub = _make_mock_pubsub()
            mock_redis.pubsub = MagicMock(return_value=mock_pubsub)
            mock_pubsub.get_message = AsyncMock(
                return_value={
                    "type": "message",
                    "data": b"failed",
                },
            )

            queue = RedisJobQueue("redis://localhost:6379")
            status = await queue.await_job_completion("test-job", timeout_seconds=5.0)

            assert status == "failed"
