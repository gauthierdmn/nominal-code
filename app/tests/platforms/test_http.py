# type: ignore
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from nominal_code.platforms.http import (
    TRANSIENT_MAX_RETRIES,
    TRANSIENT_STATUS_CODES,
    request_with_retry,
)

SLEEP_PATH = "nominal_code.platforms.http.asyncio.sleep"


def _mock_response(status_code):
    response = AsyncMock(spec=httpx.Response)
    response.status_code = status_code
    return response


def _mock_client(*responses):
    client = AsyncMock(spec=httpx.AsyncClient)
    client.request = AsyncMock(side_effect=responses)
    return client


@pytest.mark.asyncio
async def test_request_with_retry_success_on_first_attempt():
    ok = _mock_response(200)
    client = _mock_client(ok)

    result = await request_with_retry(client, "GET", "/test")

    assert result is ok
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_request_with_retry_retries_on_transient_then_succeeds():
    bad = _mock_response(502)
    ok = _mock_response(200)
    client = _mock_client(bad, ok)

    with patch(SLEEP_PATH, new_callable=AsyncMock) as mock_sleep:
        result = await request_with_retry(client, "POST", "/test")

    assert result is ok
    assert client.request.await_count == 2
    mock_sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", sorted(TRANSIENT_STATUS_CODES))
async def test_request_with_retry_retries_all_transient_codes(status_code):
    transient = _mock_response(status_code)
    ok = _mock_response(200)
    client = _mock_client(transient, ok)

    with patch(SLEEP_PATH, new_callable=AsyncMock):
        result = await request_with_retry(client, "GET", "/test")

    assert result is ok
    assert client.request.await_count == 2


@pytest.mark.asyncio
async def test_request_with_retry_exhausts_retries():
    responses = [_mock_response(503) for _ in range(TRANSIENT_MAX_RETRIES)]
    client = _mock_client(*responses)

    with patch(SLEEP_PATH, new_callable=AsyncMock) as mock_sleep:
        result = await request_with_retry(client, "GET", "/test")

    assert result.status_code == 503
    assert client.request.await_count == TRANSIENT_MAX_RETRIES
    assert mock_sleep.await_count == TRANSIENT_MAX_RETRIES - 1


@pytest.mark.asyncio
async def test_request_with_retry_backoff_increases_linearly():
    responses = [_mock_response(504) for _ in range(TRANSIENT_MAX_RETRIES)]
    client = _mock_client(*responses)

    with patch(SLEEP_PATH, new_callable=AsyncMock) as mock_sleep:
        await request_with_retry(client, "GET", "/test")

    delays = [call.args[0] for call in mock_sleep.await_args_list]
    assert delays == [2.0, 4.0]


@pytest.mark.asyncio
async def test_request_with_retry_no_retry_on_client_error():
    bad_request = _mock_response(400)
    client = _mock_client(bad_request)

    result = await request_with_retry(client, "GET", "/test")

    assert result is bad_request
    assert client.request.await_count == 1


@pytest.mark.asyncio
async def test_request_with_retry_forwards_kwargs():
    ok = _mock_response(200)
    client = _mock_client(ok)

    await request_with_retry(
        client,
        "POST",
        "/test",
        json={"key": "value"},
        headers={"X-Custom": "header"},
    )

    client.request.assert_awaited_once_with(
        "POST",
        "/test",
        json={"key": "value"},
        headers={"X-Custom": "header"},
    )


@pytest.mark.asyncio
async def test_request_with_retry_no_sleep_on_last_attempt():
    responses = [_mock_response(502) for _ in range(TRANSIENT_MAX_RETRIES)]
    client = _mock_client(*responses)

    with patch(SLEEP_PATH, new_callable=AsyncMock) as mock_sleep:
        await request_with_retry(client, "GET", "/test")

    assert mock_sleep.await_count == TRANSIENT_MAX_RETRIES - 1
