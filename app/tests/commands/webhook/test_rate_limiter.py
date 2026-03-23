# type: ignore
import time
from unittest.mock import patch

from nominal_code.commands.webhook.rate_limiter import WebhookRateLimiter


class TestWebhookRateLimiter:
    def test_allows_requests_under_limit(self):
        limiter = WebhookRateLimiter(max_requests=5, window_seconds=60)

        for _ in range(5):
            assert limiter.is_allowed("192.168.1.1") is True

    def test_blocks_requests_over_limit(self):
        limiter = WebhookRateLimiter(max_requests=3, window_seconds=60)

        for _ in range(3):
            limiter.is_allowed("192.168.1.1")

        assert limiter.is_allowed("192.168.1.1") is False

    def test_different_ips_independent(self):
        limiter = WebhookRateLimiter(max_requests=2, window_seconds=60)

        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")

        assert limiter.is_allowed("10.0.0.2") is True

    def test_expired_requests_pruned(self):
        limiter = WebhookRateLimiter(max_requests=2, window_seconds=1)

        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")

        with patch("nominal_code.commands.webhook.rate_limiter.time") as mock_time:
            mock_time.time.return_value = time.time() + 2

            assert limiter.is_allowed("10.0.0.1") is True

    def test_get_remaining_returns_correct_count(self):
        limiter = WebhookRateLimiter(max_requests=10, window_seconds=60)

        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")

        assert limiter.get_remaining("10.0.0.1") == 7

    def test_reset_clears_state(self):
        limiter = WebhookRateLimiter(max_requests=2, window_seconds=60)

        limiter.is_allowed("10.0.0.1")
        limiter.is_allowed("10.0.0.1")

        limiter.reset("10.0.0.1")

        assert limiter.is_allowed("10.0.0.1") is True

    def test_cleanup_removes_expired_ips(self):
        limiter = WebhookRateLimiter(max_requests=10, window_seconds=1)

        limiter.is_allowed("10.0.0.1")

        with patch("nominal_code.commands.webhook.rate_limiter.time") as mock_time:
            mock_time.time.return_value = time.time() + 2

            limiter.cleanup()

            assert "10.0.0.1" not in limiter._requests
