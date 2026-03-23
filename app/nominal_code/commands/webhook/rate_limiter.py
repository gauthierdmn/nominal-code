from __future__ import annotations

import logging
import time
from collections import defaultdict

logger: logging.Logger = logging.getLogger(__name__)

MAX_REQUESTS_PER_WINDOW: int = 100
WINDOW_SECONDS: int = 60


class WebhookRateLimiter:
    """
    In-memory sliding window rate limiter for webhook endpoints.

    Tracks request counts per client IP within a configurable time
    window. Intended to protect against webhook replay attacks and
    accidental retry storms.

    Attributes:
        max_requests (int): Maximum requests allowed per window.
        window_seconds (int): Duration of the sliding window.
    """

    def __init__(
        self,
        max_requests: int = MAX_REQUESTS_PER_WINDOW,
        window_seconds: int = WINDOW_SECONDS,
    ) -> None:
        """
        Initialize the rate limiter.

        Args:
            max_requests (int): Maximum requests per window.
            window_seconds (int): Window duration in seconds.
        """

        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, client_ip: str) -> bool:
        """
        Check whether a request from the given IP should be allowed.

        Prunes expired timestamps and checks whether the client has
        exceeded the request limit within the current window.

        Args:
            client_ip (str): The client's IP address.

        Returns:
            bool: True if the request is within limits.
        """

        now: float = time.time()
        cutoff: float = now - self.window_seconds
        timestamps: list[float] = self._requests[client_ip]

        self._requests[client_ip] = [ts for ts in timestamps if ts > cutoff]

        if len(self._requests[client_ip]) >= self.max_requests:
            logger.warning(
                "Rate limit exceeded for %s: %d requests in %ds",
                client_ip,
                len(self._requests[client_ip]),
                self.window_seconds,
            )

            return False

        self._requests[client_ip].append(now)

        return True

    def reset(self, client_ip: str) -> None:
        """
        Clear rate limit state for a specific IP.

        Args:
            client_ip (str): The client's IP address.
        """

        del self._requests[client_ip]

    def get_remaining(self, client_ip: str) -> int:
        """
        Return the number of requests remaining for a client.

        Args:
            client_ip (str): The client's IP address.

        Returns:
            int: Remaining requests in the current window.
        """

        now: float = time.time()
        cutoff: float = now - self.window_seconds
        timestamps: list[float] = self._requests[client_ip]
        active: list[float] = [ts for ts in timestamps if ts > cutoff]

        return self.max_requests - len(active)

    def cleanup(self) -> None:
        """
        Remove all expired entries from the internal state.

        Should be called periodically to prevent memory growth from
        IPs that no longer send requests.
        """

        now: float = time.time()
        cutoff: float = now - self.window_seconds
        expired_keys: list[str] = []

        for ip, timestamps in self._requests.items():
            active = [ts for ts in timestamps if ts > cutoff]

            if active:
                self._requests[ip] = active
            else:
                expired_keys.append(ip)

        for ip in expired_keys:
            del self._requests[ip]

        logger.debug("Rate limiter cleanup: removed %d expired IPs", len(expired_keys))
