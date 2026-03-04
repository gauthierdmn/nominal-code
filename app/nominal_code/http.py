import asyncio
import logging
from typing import Any

import httpx

TRANSIENT_STATUS_CODES: set[int] = {502, 503, 504}
TRANSIENT_MAX_RETRIES: int = 3
TRANSIENT_RETRY_DELAY: float = 2.0

logger: logging.Logger = logging.getLogger(__name__)


async def request_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs: Any,
) -> httpx.Response:
    """
    Send an HTTP request with retries on transient server errors.

    Retries up to ``TRANSIENT_MAX_RETRIES`` times when the response status
    code is 502, 503, or 504, using linear backoff.

    Args:
        client (httpx.AsyncClient): The HTTP client.
        method (str): HTTP method (GET, POST, PUT, PATCH, DELETE).
        url (str): Request URL or path.
        **kwargs (Any): Additional arguments forwarded to ``client.request``.

    Returns:
        httpx.Response: The HTTP response.
    """

    for attempt in range(1, TRANSIENT_MAX_RETRIES + 1):
        response: httpx.Response = await client.request(method, url, **kwargs)

        if response.status_code not in TRANSIENT_STATUS_CODES:
            return response

        logger.warning(
            "Transient %d from %s %s (attempt %d/%d)",
            response.status_code,
            method,
            url,
            attempt,
            TRANSIENT_MAX_RETRIES,
        )

        if attempt < TRANSIENT_MAX_RETRIES:
            await asyncio.sleep(TRANSIENT_RETRY_DELAY * attempt)

    return response
