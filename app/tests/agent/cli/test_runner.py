# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from claude_agent_sdk import SystemMessage
from claude_agent_sdk._errors import MessageParseError

from nominal_code.agent.cli.runner import _patched_parse_message
from nominal_code.agent.invoke import (
    invoke_agent,
    prepare_conversation,
    save_conversation,
)
from nominal_code.agent.result import AgentResult
from nominal_code.config import CliAgentConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.models import EventType
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


class TestInvokeAgent:
    @pytest.mark.asyncio
    async def test_returns_result(self):
        config = _make_config()
        expected = _make_agent_result()

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            new=AsyncMock(return_value=expected),
        ):
            result = await invoke_agent(
                prompt="fix it",
                cwd=Path("/tmp"),
                system_prompt="sys",
                agent_config=config.agent,
            )

        assert result is expected

    @pytest.mark.asyncio
    async def test_passes_allowed_tools(self):
        config = _make_config()
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            side_effect=mock_run,
        ):
            await invoke_agent(
                prompt="fix it",
                cwd=Path("/tmp"),
                system_prompt="sys",
                agent_config=config.agent,
                allowed_tools=["Read", "Write"],
            )

        assert captured["allowed_tools"] == ["Read", "Write"]

    @pytest.mark.asyncio
    async def test_passes_conversation_id(self):
        config = _make_config()
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.invoke.run_cli_agent",
            side_effect=mock_run,
        ):
            await invoke_agent(
                prompt="fix it",
                cwd=Path("/tmp"),
                system_prompt="sys",
                agent_config=config.agent,
                conversation_id="prev-sess",
            )

        assert captured["conversation_id"] == "prev-sess"


class TestConversationLifecycle:
    def test_prepare_loads_existing_id_from_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            value="prev-sess",
        )

        conversation_id, prior_messages = prepare_conversation(
            event=event,
            agent_config=config.agent,
            conversation_store=store,
        )

        assert conversation_id == "prev-sess"
        assert prior_messages is None

    def test_prepare_without_store_returns_none(self):
        event = _make_event()
        config = _make_config()

        conversation_id, prior_messages = prepare_conversation(
            event=event,
            agent_config=config.agent,
            conversation_store=None,
        )

        assert conversation_id is None
        assert prior_messages is None

    def test_save_stores_conversation_id(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        agent_result = _make_agent_result(conversation_id="stored-sess")

        save_conversation(
            event=event,
            result=agent_result,
            agent_config=config.agent,
            conversation_store=store,
        )

        stored = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
        )

        assert stored == "stored-sess"

    def test_save_skips_store_when_conversation_id_none(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        agent_result = _make_agent_result(conversation_id=None)

        save_conversation(
            event=event,
            result=agent_result,
            agent_config=config.agent,
            conversation_store=store,
        )

        stored = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
        )

        assert stored is None

    def test_save_skips_when_no_store(self):
        event = _make_event()
        config = _make_config()
        agent_result = _make_agent_result(conversation_id="some-sess")

        save_conversation(
            event=event,
            result=agent_result,
            agent_config=config.agent,
            conversation_store=None,
        )
