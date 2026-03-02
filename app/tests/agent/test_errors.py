# type: ignore
from unittest.mock import AsyncMock, MagicMock

import pytest

from nominal_code.agent.errors import handle_agent_errors
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_event():
    return CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=1,
        pr_branch="main",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=10,
        author_username="alice",
        body="fix this",
    )


def _make_platform():
    platform = MagicMock()
    platform.post_reply = AsyncMock()

    return platform


class TestHandleAgentErrors:
    @pytest.mark.asyncio
    async def test_handle_agent_errors_no_error_runs_body(self):
        event = _make_event()
        platform = _make_platform()
        executed = []

        async with handle_agent_errors(event, platform, "worker"):
            executed.append(True)

        assert executed == [True]
        platform.post_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_handle_agent_errors_catches_runtime_error(self):
        event = _make_event()
        platform = _make_platform()

        async with handle_agent_errors(event, platform, "worker"):
            raise RuntimeError("workspace setup failed")

        platform.post_reply.assert_awaited_once()
        reply = platform.post_reply.call_args.kwargs["reply"]

        assert "git workspace" in reply.body.lower()

    @pytest.mark.asyncio
    async def test_handle_agent_errors_catches_generic_exception(self):
        event = _make_event()
        platform = _make_platform()

        async with handle_agent_errors(event, platform, "worker"):
            raise ValueError("unexpected crash")

        platform.post_reply.assert_awaited_once()
        reply = platform.post_reply.call_args.kwargs["reply"]

        assert "unexpected error" in reply.body.lower()

    @pytest.mark.asyncio
    async def test_handle_agent_errors_runtime_error_uses_event(self):
        event = _make_event()
        platform = _make_platform()

        async with handle_agent_errors(event, platform, "worker"):
            raise RuntimeError("boom")

        call_kwargs = platform.post_reply.call_args.kwargs

        assert call_kwargs["event"] is event

    @pytest.mark.asyncio
    async def test_handle_agent_errors_generic_exception_uses_event(self):
        event = _make_event()
        platform = _make_platform()

        async with handle_agent_errors(event, platform, "reviewer"):
            raise Exception("boom")

        call_kwargs = platform.post_reply.call_args.kwargs

        assert call_kwargs["event"] is event

    @pytest.mark.asyncio
    async def test_handle_agent_errors_runtime_post_reply_failure_is_swallowed(self):
        event = _make_event()
        platform = _make_platform()
        platform.post_reply.side_effect = Exception("post failed")

        async with handle_agent_errors(event, platform, "worker"):
            raise RuntimeError("workspace failed")

    @pytest.mark.asyncio
    async def test_handle_agent_errors_generic_post_reply_failure_is_swallowed(self):
        event = _make_event()
        platform = _make_platform()
        platform.post_reply.side_effect = Exception("post failed")

        async with handle_agent_errors(event, platform, "reviewer"):
            raise Exception("agent failed")

    @pytest.mark.asyncio
    async def test_handle_agent_errors_returns_none_on_success(self):
        event = _make_event()
        platform = _make_platform()
        result = None

        async with handle_agent_errors(event, platform, "worker") as value:
            result = value

        assert result is None
