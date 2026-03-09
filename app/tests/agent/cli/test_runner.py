# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import SystemMessage
from claude_agent_sdk._errors import MessageParseError

from nominal_code.agent.cli.runner import _patched_parse_message, handle_event
from nominal_code.agent.result import AgentResult
from nominal_code.config import CliAgentConfig
from nominal_code.conversation.memory import MemoryConversationStore
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


def _make_agent_result(conversation_id="new-sess-42"):
    return AgentResult(
        output="Done",
        is_error=False,
        num_turns=2,
        duration_ms=1000,
        conversation_id=conversation_id,
    )


class TestPatchedParseMessage:
    def test_passes_through_valid_message(self):
        mock_message = MagicMock()

        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            return_value=mock_message,
        ):
            result = _patched_parse_message({"type": "assistant"})

        assert result is mock_message

    def test_returns_system_message_on_parse_error(self):
        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=MessageParseError("Unknown type"),
        ):
            result = _patched_parse_message({"type": "rate_limit_event"})

        assert isinstance(result, SystemMessage)
        assert result.subtype == "rate_limit_event"

    def test_returns_unknown_subtype_for_non_dict(self):
        with patch(
            "nominal_code.agent.cli.runner._original_parse_message",
            side_effect=MessageParseError("Unknown type"),
        ):
            result = _patched_parse_message("not a dict")

        assert isinstance(result, SystemMessage)
        assert result.subtype == "unknown"
        assert result.data == {}


class TestHandleEvent:
    @pytest.mark.asyncio
    async def test_returns_result(self):
        event = _make_event()
        config = _make_config()
        expected = _make_agent_result()

        with patch(
            "nominal_code.agent.cli.runner.run",
            new=AsyncMock(return_value=expected),
        ):
            result = await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert result is expected

    @pytest.mark.asyncio
    async def test_stores_conversation_id(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        agent_result = _make_agent_result(conversation_id="stored-sess")

        with patch(
            "nominal_code.agent.cli.runner.run",
            new=AsyncMock(return_value=agent_result),
        ):
            await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        stored = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.WORKER,
        )

        assert stored == "stored-sess"

    @pytest.mark.asyncio
    async def test_uses_existing_id_from_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.WORKER,
            value="prev-sess",
        )
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.runner.run",
            side_effect=mock_run,
        ):
            await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        assert captured["conversation_id"] == "prev-sess"

    @pytest.mark.asyncio
    async def test_override_wins_over_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.WORKER,
            value="store-sess",
        )
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.runner.run",
            side_effect=mock_run,
        ):
            await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_id_override="override-sess",
                conversation_store=store,
            )

        assert captured["conversation_id"] == "override-sess"

    @pytest.mark.asyncio
    async def test_none_store_skips_storage(self):
        event = _make_event()
        config = _make_config()
        agent_result = _make_agent_result(conversation_id="some-sess")

        with patch(
            "nominal_code.agent.cli.runner.run",
            new=AsyncMock(return_value=agent_result),
        ):
            result = await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert result.conversation_id == "some-sess"

    @pytest.mark.asyncio
    async def test_empty_conversation_id_skips_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        agent_result = _make_agent_result(conversation_id=None)

        with patch(
            "nominal_code.agent.cli.runner.run",
            new=AsyncMock(return_value=agent_result),
        ):
            await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        stored = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.WORKER,
        )

        assert stored is None

    @pytest.mark.asyncio
    async def test_no_store_no_existing_id(self):
        event = _make_event()
        config = _make_config()
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.runner.run",
            side_effect=mock_run,
        ):
            await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert captured["conversation_id"] is None

    @pytest.mark.asyncio
    async def test_passes_allowed_tools(self):
        event = _make_event()
        config = _make_config()
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.runner.run",
            side_effect=mock_run,
        ):
            await handle_event(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                allowed_tools=["Read", "Write"],
            )

        assert captured["allowed_tools"] == ["Read", "Write"]
