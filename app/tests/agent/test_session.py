# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.cli.session import run_and_track_conversation
from nominal_code.agent.router import AgentResult
from nominal_code.config import ApiAgentConfig, CliAgentConfig, ProviderConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.llm.messages import Message, TextBlock
from nominal_code.models import BotType, EventType, ProviderName
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


def _make_api_config():
    config = MagicMock()
    config.agent = ApiAgentConfig(
        provider=ProviderConfig(name=ProviderName.OPENAI, model="gpt-4.1"),
    )

    return config


def _make_agent_result(conversation_id="new-sess-42"):
    return AgentResult(
        output="Done",
        is_error=False,
        num_turns=2,
        duration_ms=1000,
        conversation_id=conversation_id,
    )


class TestRunAndTrackConversation:
    @pytest.mark.asyncio
    async def test_run_and_track_conversation_returns_result(self):
        event = _make_event()
        config = _make_config()
        expected = _make_agent_result()

        with patch(
            "nominal_code.agent.cli.session.run_agent",
            new=AsyncMock(return_value=expected),
        ):
            result = await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert result is expected

    @pytest.mark.asyncio
    async def test_run_and_track_conversation_stores_conversation_id(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        agent_result = _make_agent_result(conversation_id="stored-sess")

        with patch(
            "nominal_code.agent.cli.session.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        stored = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert stored == "stored-sess"

    @pytest.mark.asyncio
    async def test_run_and_track_conversation_uses_existing_id_from_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, "prev-sess"
        )
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
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
    async def test_run_and_track_conversation_override_wins_over_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, "store-sess"
        )
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
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
    async def test_run_and_track_conversation_none_store_skips_storage(self):
        event = _make_event()
        config = _make_config()
        agent_result = _make_agent_result(conversation_id="some-sess")

        with patch(
            "nominal_code.agent.cli.session.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            result = await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert result.conversation_id == "some-sess"

    @pytest.mark.asyncio
    async def test_run_and_track_conversation_empty_conversation_id_skips_store(self):
        event = _make_event()
        config = _make_config()
        store = MemoryConversationStore()
        agent_result = _make_agent_result(conversation_id=None)

        with patch(
            "nominal_code.agent.cli.session.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        stored = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert stored is None

    @pytest.mark.asyncio
    async def test_run_and_track_conversation_no_store_no_existing_id(self):
        event = _make_event()
        config = _make_config()
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert captured["conversation_id"] is None

    @pytest.mark.asyncio
    async def test_run_and_track_conversation_passes_allowed_tools(self):
        event = _make_event()
        config = _make_config()
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result()

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                allowed_tools=["Read", "Write"],
            )

        assert captured["allowed_tools"] == ["Read", "Write"]

    @pytest.mark.asyncio
    async def test_run_and_track_conversation_api_config_skips_id_lookup(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER, "prev-sess"
        )
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return _make_agent_result(conversation_id="api-sess")

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        assert captured["conversation_id"] is None

        stored = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert stored == "api-sess"

    @pytest.mark.asyncio
    async def test_api_config_loads_prior_messages_from_store(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()
        prior_msg = Message(role="user", content=[TextBlock(text="prior")])
        store.set_messages(
            PlatformName.GITHUB,
            "owner/repo",
            42,
            BotType.WORKER,
            [prior_msg],
        )
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return AgentResult(
                output="Done",
                is_error=False,
                num_turns=1,
                duration_ms=100,
                messages=(
                    prior_msg,
                    Message(
                        role="assistant",
                        content=[TextBlock(text="ok")],
                    ),
                ),
            )

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        assert captured["prior_messages"] == [prior_msg]

    @pytest.mark.asyncio
    async def test_api_config_stores_messages_after_success(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()

        result_messages = (
            Message(role="user", content=[TextBlock(text="hello")]),
            Message(role="assistant", content=[TextBlock(text="hi")]),
        )
        agent_result = AgentResult(
            output="hi",
            is_error=False,
            num_turns=1,
            duration_ms=100,
            messages=result_messages,
            conversation_id="resp-42",
        )

        with patch(
            "nominal_code.agent.cli.session.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        stored_msgs = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert stored_msgs is not None
        assert len(stored_msgs) == 2

        stored_id = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert stored_id == "resp-42"

    @pytest.mark.asyncio
    async def test_api_config_skips_store_on_error(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()

        agent_result = AgentResult(
            output="API error: boom",
            is_error=True,
            num_turns=0,
            duration_ms=100,
        )

        with patch(
            "nominal_code.agent.cli.session.run_agent",
            new=AsyncMock(return_value=agent_result),
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
                conversation_store=store,
            )

        stored = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 42, BotType.WORKER
        )

        assert stored is None

    @pytest.mark.asyncio
    async def test_api_config_no_store_skips_memory(self):
        event = _make_event()
        config = _make_api_config()
        captured = {}

        async def mock_run_agent(**kwargs):
            captured.update(kwargs)

            return AgentResult(
                output="Done",
                is_error=False,
                num_turns=1,
                duration_ms=100,
                messages=(Message(role="user", content=[TextBlock(text="x")]),),
            )

        with patch(
            "nominal_code.agent.cli.session.run_agent", side_effect=mock_run_agent
        ):
            await run_and_track_conversation(
                event=event,
                bot_type=BotType.WORKER,
                system_prompt="sys",
                prompt="fix it",
                cwd=Path("/tmp"),
                config=config,
            )

        assert captured["prior_messages"] is None
