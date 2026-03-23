from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from nominal_code.commands.webhook.rate_limiter import WebhookRateLimiter

logger: logging.Logger = logging.getLogger(__name__)

RATE_LIMIT_BYPASS_TOKEN: str = os.environ.get("RATE_LIMIT_BYPASS_TOKEN", "")

_Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def build_rate_limit_middleware(
    limiter: WebhookRateLimiter,
) -> Any:
    """
    Build an aiohttp middleware that enforces webhook rate limits.

    Checks the client IP against the rate limiter before passing the
    request to the handler. Returns 429 if the limit is exceeded.

    The ``X-Forwarded-For`` header is used to extract the real client
    IP when behind a reverse proxy.

    Args:
        limiter (WebhookRateLimiter): The rate limiter instance.

    Returns:
        Any: The configured aiohttp middleware.
    """

    @web.middleware
    async def rate_limit_middleware(
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """
        Rate limit middleware handler.

        Args:
            request (web.Request): The incoming request.
            handler (_Handler): The next handler in the chain.

        Returns:
            web.StreamResponse: The HTTP response.
        """

        bypass_token: str = request.headers.get("X-Rate-Limit-Bypass", "")

        if bypass_token and bypass_token == RATE_LIMIT_BYPASS_TOKEN:
            return await handler(request)

        client_ip: str = _extract_client_ip(request)
        start_time: float = time.time()

        if not limiter.is_allowed(client_ip):
            remaining: int = limiter.get_remaining(client_ip)

            return web.json_response(
                {
                    "status": "error",
                    "message": "Rate limit exceeded",
                    "retry_after": limiter.window_seconds,
                },
                status=429,
                headers={
                    "Retry-After": str(limiter.window_seconds),
                    "X-RateLimit-Remaining": str(remaining),
                },
            )

        response: web.StreamResponse = await handler(request)
        elapsed: float = time.time() - start_time

        remaining = limiter.get_remaining(client_ip)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Limit"] = str(limiter.max_requests)
        response.headers["X-Response-Time"] = f"{elapsed * 1000:.1f}ms"

        return response

    return rate_limit_middleware


def _extract_client_ip(request: web.Request) -> str:
    """
    Extract the client IP from the request.

    Uses ``X-Forwarded-For`` if present, otherwise falls back to the
    connection's remote address.

    Args:
        request (web.Request): The incoming request.

    Returns:
        str: The client's IP address.
    """

    forwarded_for: str = request.headers.get("X-Forwarded-For", "")

    if forwarded_for:
        return forwarded_for.split(",")[-1].strip()

    transport = request.transport

    if transport is not None:
        peer: tuple[str, int] | None = transport.get_extra_info("peername")

        if peer is not None:
            return peer[0]

    return "unknown"
