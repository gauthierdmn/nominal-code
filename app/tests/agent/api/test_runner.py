# type: ignore
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.api.runner import run_api_agent
from nominal_code.agent.invoke import (
    invoke_agent,
    prepare_conversation,
    save_conversation,
)
from nominal_code.agent.result import AgentResult
from nominal_code.config import ApiAgentConfig, ProviderConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.llm.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from nominal_code.models import EventType, ProviderName
from nominal_code.platforms.base import CommentEvent, PlatformName


def _make_text_response(text, usage=None):
    return LLMResponse(
        content=[TextBlock(text=text)],
        stop_reason=StopReason.END_TURN,
        usage=usage,
    )


def _make_tool_use_response(tool_id, name, tool_input, usage=None):
    return LLMResponse(
        content=[
            ToolUseBlock(id=tool_id, name=name, input=tool_input),
        ],
        stop_reason=StopReason.TOOL_USE,
        usage=usage,
    )


class TestRunAgentApi:
    @pytest.mark.asyncio
    async def test_simple_text_response(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response(text="All good!"),
        )

        result = await run_api_agent(
            prompt="Review this code",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert isinstance(result, AgentResult)
        assert result.output == "All good!"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_empty_text_response(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[],
                stop_reason=StopReason.END_TURN,
            ),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.output == "Done, no output."

    @pytest.mark.asyncio
    async def test_tool_use_loop(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')")

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=[
                _make_tool_use_response(
                    tool_id="t1",
                    name="Read",
                    tool_input={"file_path": str(test_file)},
                ),
                _make_text_response(text="The file prints hello."),
            ],
        )

        result = await run_api_agent(
            prompt="Read hello.py",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.output == "The file prints hello."
        assert result.num_turns == 1
        assert mock_provider.send.call_count == 2

    @pytest.mark.asyncio
    async def test_submit_review_returns_json(self, tmp_path):
        review_input = {
            "summary": "Looks good",
            "comments": [
                {"path": "a.py", "line": 1, "body": "Nice"},
            ],
        }
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[
                    ToolUseBlock(
                        id="t1",
                        name="submit_review",
                        input=review_input,
                    ),
                ],
                stop_reason=StopReason.TOOL_USE,
            ),
        )

        result = await run_api_agent(
            prompt="Review this",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            allowed_tools=["Read", "submit_review"],
        )

        parsed = json.loads(result.output)

        assert parsed["summary"] == "Looks good"
        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_max_turns_stops_loop(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_tool_use_response(
                tool_id="t1",
                name="Glob",
                tool_input={"pattern": "**/*.py"},
            ),
        )

        result = await run_api_agent(
            prompt="Find files",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            max_turns=2,
        )

        assert result.output == "Max turns reached."
        assert result.num_turns == 2

    @pytest.mark.asyncio
    async def test_provider_error_returns_error_result(self, tmp_path):
        from nominal_code.llm.provider import ProviderError

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=ProviderError("API down"),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is True
        assert "API error" in result.output

    @pytest.mark.asyncio
    async def test_unexpected_error_returns_error_result(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=RuntimeError("boom"),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is True
        assert "Unexpected error" in result.output

    @pytest.mark.asyncio
    async def test_prior_messages_prepended(self, tmp_path):
        captured_messages = []

        async def capture_send(**kwargs):
            captured_messages.extend(list(kwargs["messages"]))

            return _make_text_response(text="second reply")

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=capture_send)

        prior = [
            Message(role="user", content=[TextBlock(text="first question")]),
            Message(role="assistant", content=[TextBlock(text="first reply")]),
        ]

        result = await run_api_agent(
            prompt="follow up",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            prior_messages=prior,
        )

        assert result.output == "second reply"
        assert len(captured_messages) == 3
        assert captured_messages[0].content[0].text == "first question"
        assert captured_messages[1].content[0].text == "first reply"
        assert captured_messages[2].content[0].text == "follow up"

    @pytest.mark.asyncio
    async def test_result_includes_messages(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response(text="done"),
        )

        result = await run_api_agent(
            prompt="hello",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert len(result.messages) == 2
        assert result.messages[0].role == "user"
        assert result.messages[1].role == "assistant"

    @pytest.mark.asyncio
    async def test_error_result_has_no_messages(self, tmp_path):
        from nominal_code.llm.provider import ProviderError

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=ProviderError("fail"),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is True
        assert result.messages == ()


class TestCompactionIntegration:
    @pytest.mark.asyncio
    async def test_compaction_not_triggered_when_disabled(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Done."),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
        )

        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_compaction_noop_when_few_messages(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Done."),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            enable_compaction=True,
        )

        assert result.is_error is False

    @pytest.mark.asyncio
    async def test_full_messages_preserved_after_compaction(self, tmp_path):
        call_count = 0

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count <= 6:
                return _make_tool_use_response(
                    f"t{call_count}",
                    "Read",
                    {"file_path": "test.py"},
                )

            return _make_text_response("Done.")

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=side_effect)

        with patch(
            "nominal_code.agent.api.runner.execute_tool",
            new_callable=AsyncMock,
            return_value=("x" * 5000, False),
        ):
            result = await run_api_agent(
                prompt="test",
                cwd=tmp_path,
                model="test-model",
                provider=mock_provider,
                max_turns=10,
                enable_compaction=True,
            )

        assert len(result.messages) >= 9


class TestRunAgentApiCost:
    @pytest.mark.asyncio
    async def test_cost_summary_present_on_simple_response(self, tmp_path):
        usage = TokenUsage(input_tokens=100, output_tokens=50)
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response(text="done", usage=usage),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.cost is not None
        assert result.cost.total_input_tokens == 100
        assert result.cost.total_output_tokens == 50
        assert result.cost.provider == "openai"
        assert result.cost.model == "gpt-4.1"
        assert result.cost.num_api_calls == 1

    @pytest.mark.asyncio
    async def test_cost_accumulates_across_turns(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')")

        usage1 = TokenUsage(input_tokens=100, output_tokens=50)
        usage2 = TokenUsage(input_tokens=200, output_tokens=80)

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=[
                _make_tool_use_response(
                    tool_id="t1",
                    name="Read",
                    tool_input={"file_path": str(test_file)},
                    usage=usage1,
                ),
                _make_text_response(text="The file prints hello.", usage=usage2),
            ],
        )

        result = await run_api_agent(
            prompt="Read hello.py",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.cost is not None
        assert result.cost.total_input_tokens == 300
        assert result.cost.total_output_tokens == 130
        assert result.cost.num_api_calls == 2
        assert result.cost.total_cost_usd is not None

    @pytest.mark.asyncio
    async def test_cost_present_on_error(self, tmp_path):
        from nominal_code.llm.provider import ProviderError

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            side_effect=ProviderError("fail"),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.is_error is True
        assert result.cost is not None
        assert result.cost.num_api_calls == 0

    @pytest.mark.asyncio
    async def test_cost_with_no_usage_from_provider(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[TextBlock(text="done")],
                stop_reason=StopReason.END_TURN,
                usage=None,
            ),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="gpt-4.1",
            provider=mock_provider,
            provider_name=ProviderName.OPENAI,
        )

        assert result.cost is not None
        assert result.cost.total_input_tokens == 0
        assert result.cost.total_cost_usd is None


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


def _make_api_config():
    config = MagicMock()
    config.agent = ApiAgentConfig(
        provider=ProviderConfig(name=ProviderName.OPENAI, model="gpt-4.1"),
    )

    return config


class TestConversationLifecycle:
    @pytest.mark.asyncio
    async def test_prepare_loads_prior_messages_from_store(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()
        prior_msg = Message(role="user", content=[TextBlock(text="prior")])
        store.set_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            value=[prior_msg],
        )
        captured = {}

        async def mock_run(**kwargs):
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

        conversation_id, prior_messages = prepare_conversation(
            event=event,
            agent_config=config.agent,
            conversation_store=store,
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_api_agent",
                side_effect=mock_run,
            ),
            patch(
                "nominal_code.agent.invoke.create_provider",
                return_value=AsyncMock(),
            ),
        ):
            await invoke_agent(
                prompt="fix it",
                cwd=Path("/tmp"),
                system_prompt="sys",
                agent_config=config.agent,
                conversation_id=conversation_id,
                prior_messages=prior_messages,
            )

        assert captured["prior_messages"] == [prior_msg]

    @pytest.mark.asyncio
    async def test_save_stores_messages_after_success(self):
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

        save_conversation(
            event=event,
            result=agent_result,
            agent_config=config.agent,
            conversation_store=store,
        )

        stored_msgs = store.get_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
        )

        assert stored_msgs is not None
        assert len(stored_msgs) == 2

        stored_id = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
        )

        assert stored_id == "resp-42"

    @pytest.mark.asyncio
    async def test_save_skips_store_on_error(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()

        agent_result = AgentResult(
            output="API error: boom",
            is_error=True,
            num_turns=0,
            duration_ms=100,
        )

        save_conversation(
            event=event,
            result=agent_result,
            agent_config=config.agent,
            conversation_store=store,
        )

        stored = store.get_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
        )

        assert stored is None

    @pytest.mark.asyncio
    async def test_prepare_without_store_returns_none(self):
        event = _make_event()
        config = _make_api_config()
        captured = {}

        async def mock_run(**kwargs):
            captured.update(kwargs)

            return AgentResult(
                output="Done",
                is_error=False,
                num_turns=1,
                duration_ms=100,
                messages=(Message(role="user", content=[TextBlock(text="x")]),),
            )

        conversation_id, prior_messages = prepare_conversation(
            event=event,
            agent_config=config.agent,
            conversation_store=None,
        )

        with (
            patch(
                "nominal_code.agent.invoke.run_api_agent",
                side_effect=mock_run,
            ),
            patch(
                "nominal_code.agent.invoke.create_provider",
                return_value=AsyncMock(),
            ),
        ):
            await invoke_agent(
                prompt="fix it",
                cwd=Path("/tmp"),
                system_prompt="sys",
                agent_config=config.agent,
                conversation_id=conversation_id,
                prior_messages=prior_messages,
            )

        assert captured["prior_messages"] is None

    @pytest.mark.asyncio
    async def test_save_stores_conversation_id(self):
        event = _make_event()
        config = _make_api_config()
        store = MemoryConversationStore()

        agent_result = AgentResult(
            output="Done",
            is_error=False,
            num_turns=1,
            duration_ms=100,
            conversation_id="api-sess",
        )

        save_conversation(
            event=event,
            result=agent_result,
            agent_config=config.agent,
            conversation_store=store,
        )

        stored_id = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
        )

        assert stored_id == "api-sess"
