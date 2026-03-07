# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.cli.session import SessionStore
from nominal_code.agent.cli.tracking import run_and_track_session
from nominal_code.agent.runner import AgentResult
from nominal_code.config import CliAgentConfig
from nominal_code.models import BotType, EventType
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_event():
    return CommentEvent(
        platform=PlatformName.GITHUB,
        repo_full_name="owner/repo",
        pr_number=42,
        pr_branch="feature",
        event_type=EventType.ISSUE_COMMENT,
        comment_id=10,
        author_username="alice",
        body="fix this",
    )


def _make_config():
    config = MagicMock()
    config.agent = CliAgentConfig()

    return config


def _make_agent_result(session_id="new-sess-42"):
    return AgentResult(
        output="Done",
        is_error=False,
        num_turns=2,
        duration_ms=1000,
        session_id=session_id,
    )


class TestRunAndTrackSession:
    @pytest.mark.asyncio
    async def test_run_and_track_session_returns_result(self):
        event = _make_event()
        config = _make_config()
        expected = _make_agent_result()

        with patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new=AsyncMock(return_value=expected),
        ):
            result = await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=None,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert result is expected

    @pytest.mark.asyncio
    async def test_run_and_track_session_stores_session_id(self):
        event = _make_event()
        config = _make_config()
        store = SessionStore()
        agent_result = _make_agent_result(session_id="stored-sess")

        with patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=store,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        stored = store.get(PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER)

        assert stored == "stored-sess"

    @pytest.mark.asyncio
    async def test_run_and_track_session_uses_existing_session_from_store(self):
        event = _make_event()
        config = _make_config()
        store = SessionStore()
        store.set(PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, "prev-sess")
        captured: dict = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.tracking.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=store,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert captured["session_id"] == "prev-sess"

    @pytest.mark.asyncio
    async def test_run_and_track_session_override_wins_over_store(self):
        event = _make_event()
        config = _make_config()
        store = SessionStore()
        store.set(PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, "store-sess")
        captured: dict = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.tracking.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=store,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                session_id_override="override-sess",
            )

        assert captured["session_id"] == "override-sess"

    @pytest.mark.asyncio
    async def test_run_and_track_session_none_store_skips_storage(self):
        event = _make_event()
        config = _make_config()
        agent_result = _make_agent_result(session_id="some-sess")

        with patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            result = await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=None,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert result.session_id == "some-sess"

    @pytest.mark.asyncio
    async def test_run_and_track_session_empty_session_id_skips_store(self):
        event = _make_event()
        config = _make_config()
        store = SessionStore()
        agent_result = _make_agent_result(session_id="")

        with patch(
            "nominal_code.agent.cli.tracking.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=store,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        stored = store.get(PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER)

        assert stored is None

    @pytest.mark.asyncio
    async def test_run_and_track_session_no_store_no_existing_session(self):
        event = _make_event()
        config = _make_config()
        captured: dict = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.tracking.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=None,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert captured["session_id"] == ""

    @pytest.mark.asyncio
    async def test_run_and_track_session_passes_allowed_tools(self):
        event = _make_event()
        config = _make_config()
        captured: dict = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.tracking.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_session(
                event=event,
                bot_type=BotType.WORKER,
                session_store=None,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                allowed_tools=["Read", "Write"],
            )

        assert captured["allowed_tools"] == ["Read", "Write"]
