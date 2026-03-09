# type: ignore
from unittest.mock import MagicMock, patch

from nominal_code.jobs.signals import publish_job_completion


class TestPublishJobCompletion:
    def test_publishes_to_correct_channel(self):
        mock_client = MagicMock()

        with patch("nominal_code.jobs.signals.redis.Redis") as mock_redis_cls:
            mock_redis_cls.from_url.return_value = mock_client

            publish_job_completion(
                redis_url="redis://localhost:6379",
                job_name="nominal-code-abc12345-owner-repo-42",
                status="succeeded",
            )

            mock_client.publish.assert_called_once_with(
                "nc:job:nominal-code-abc12345-owner-repo-42:done",
                "succeeded",
            )
            mock_client.close.assert_called_once()

    def test_publishes_failed_status(self):
        mock_client = MagicMock()

        with patch("nominal_code.jobs.signals.redis.Redis") as mock_redis_cls:
            mock_redis_cls.from_url.return_value = mock_client

            publish_job_completion(
                redis_url="redis://localhost:6379",
                job_name="test-job",
                status="failed",
            )

            mock_client.publish.assert_called_once_with(
                "nc:job:test-job:done",
                "failed",
            )

    def test_handles_redis_error_gracefully(self):
        import redis

        with patch("nominal_code.jobs.signals.redis.Redis") as mock_redis_cls:
            mock_redis_cls.from_url.side_effect = redis.RedisError("connection failed")

            publish_job_completion(
                redis_url="redis://localhost:6379",
                job_name="test-job",
                status="succeeded",
            )
