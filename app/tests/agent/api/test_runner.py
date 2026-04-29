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
from nominal_code.config import AgentRoleConfig, ApiAgentConfig
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.llm.messages import (
    LLMResponse,
    Message,
    StopReason,
    TextBlock,
    TokenUsage,
    ToolUseBlock,
)
from nominal_code.models import ErrorType, EventType, InvocationError, ProviderName
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
            provider_name=ProviderName.GOOGLE,
        )

        assert isinstance(result, AgentResult)
        assert result.output == "All good!"
        assert result.error is None

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
            provider_name=ProviderName.GOOGLE,
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
            provider_name=ProviderName.GOOGLE,
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
            provider_name=ProviderName.GOOGLE,
            allowed_tools=["Read", "submit_review"],
        )

        parsed = json.loads(result.output)

        assert parsed["summary"] == "Looks good"
        assert result.error is None

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
            provider_name=ProviderName.GOOGLE,
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
            provider_name=ProviderName.GOOGLE,
        )

        assert result.error is not None
        assert "API error" in result.output
        # Structured error fields let downstream observability route
        # provider failures separately from runtime bugs without
        # pattern-matching on ``output``.
        assert result.error.type == ErrorType.PROVIDER_ERROR
        assert result.error.message == "API down"

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
            provider_name=ProviderName.GOOGLE,
        )

        assert result.error is not None
        assert "Unexpected error" in result.output
        assert result.error.type == ErrorType.RUNTIME_ERROR
        assert result.error.message == "boom"

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
            provider_name=ProviderName.GOOGLE,
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
            provider_name=ProviderName.GOOGLE,
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
            provider_name=ProviderName.GOOGLE,
        )

        assert result.error is not None
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
            provider_name=ProviderName.GOOGLE,
        )

        assert result.error is None

    @pytest.mark.asyncio
    async def test_compaction_skipped_without_notes_file(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_text_response("Done."),
        )

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test-model",
            provider=mock_provider,
            provider_name=ProviderName.GOOGLE,
        )

        assert result.error is None

    @pytest.mark.asyncio
    async def test_compaction_skipped_when_notes_empty(self, tmp_path):
        notes_file = tmp_path / "notes.md"
        notes_file.write_text("")

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
            return_value=("content", False),
        ):
            result = await run_api_agent(
                prompt="test",
                cwd=tmp_path,
                model="test-model",
                provider=mock_provider,
                provider_name=ProviderName.GOOGLE,
                max_turns=10,
                notes_file_path=notes_file,
            )

        assert len(result.messages) >= 9

    @pytest.mark.asyncio
    async def test_messages_compacted_when_token_threshold_exceeded(self, tmp_path):
        notes_file = tmp_path / "notes.md"
        notes_file.write_text("## Callers\nFound a caller.")

        call_count = 0
        large_usage = TokenUsage(input_tokens=95_000, output_tokens=6_000)

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count <= 6:
                return _make_tool_use_response(
                    f"t{call_count}",
                    "Read",
                    {"file_path": "test.py"},
                    usage=large_usage,
                )

            return _make_text_response("Done.", usage=large_usage)

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=side_effect)

        with patch(
            "nominal_code.agent.api.runner.execute_tool",
            new_callable=AsyncMock,
            return_value=("content", False),
        ):
            result = await run_api_agent(
                prompt="test",
                cwd=tmp_path,
                model="test-model",
                provider=mock_provider,
                provider_name=ProviderName.GOOGLE,
                max_turns=10,
                notes_file_path=notes_file,
            )

        assert len(result.messages) < 13

    @pytest.mark.asyncio
    async def test_no_compaction_below_token_threshold(self, tmp_path):
        notes_file = tmp_path / "notes.md"
        notes_file.write_text("## Callers\nFound a caller.")

        call_count = 0
        small_usage = TokenUsage(input_tokens=5_000, output_tokens=200)

        async def side_effect(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count <= 3:
                return _make_tool_use_response(
                    f"t{call_count}",
                    "Read",
                    {"file_path": "test.py"},
                    usage=small_usage,
                )

            return _make_text_response("Done.", usage=small_usage)

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=side_effect)

        with patch(
            "nominal_code.agent.api.runner.execute_tool",
            new_callable=AsyncMock,
            return_value=("content", False),
        ):
            result = await run_api_agent(
                prompt="test",
                cwd=tmp_path,
                model="test-model",
                provider=mock_provider,
                provider_name=ProviderName.GOOGLE,
                max_turns=10,
                notes_file_path=notes_file,
            )

        assert len(result.messages) == 8


class TestLastTurnWarning:
    @pytest.mark.asyncio
    async def test_last_turn_injects_warning_message(self, tmp_path):
        call_count = 0

        async def mock_send(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return _make_tool_use_response(
                    tool_id="t1",
                    name="Glob",
                    tool_input={"pattern": "*.py"},
                )

            return _make_tool_use_response(
                tool_id="t2",
                name="submit_review",
                tool_input={"summary": "ok", "comments": []},
            )

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=mock_send)

        await run_api_agent(
            prompt="Review",
            cwd=tmp_path,
            model="test",
            provider=mock_provider,
            provider_name=ProviderName.GOOGLE,
            max_turns=2,
            allowed_tools=["Glob", "submit_review"],
        )

        last_call_messages = mock_provider.send.call_args_list[-1].kwargs["messages"]
        user_texts = [
            block.text
            for msg in last_call_messages
            if msg.role == "user"
            for block in msg.content
            if hasattr(block, "text")
        ]

        assert any("last turn" in text.lower() for text in user_texts)

    @pytest.mark.asyncio
    async def test_exhausted_without_review_flag_set(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_tool_use_response(
                tool_id="t1",
                name="Glob",
                tool_input={"pattern": "*.py"},
            ),
        )

        result = await run_api_agent(
            prompt="Review",
            cwd=tmp_path,
            model="test",
            provider=mock_provider,
            provider_name=ProviderName.GOOGLE,
            max_turns=2,
            allowed_tools=["Glob", "submit_review"],
        )

        assert result.exhausted_without_review is True

    @pytest.mark.asyncio
    async def test_exhausted_flag_false_without_submit_review(self, tmp_path):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_tool_use_response(
                tool_id="t1",
                name="Glob",
                tool_input={"pattern": "*.py"},
            ),
        )

        result = await run_api_agent(
            prompt="Find files",
            cwd=tmp_path,
            model="test",
            provider=mock_provider,
            provider_name=ProviderName.GOOGLE,
            max_turns=1,
        )

        assert result.exhausted_without_review is False


class TestAgentToolDispatch:
    @pytest.mark.asyncio
    async def test_agent_tool_spawns_sub_agent(self, tmp_path):
        from nominal_code.agent.sub_agent import SubAgentConfig

        call_count = 0

        async def mock_send(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return _make_tool_use_response(
                    tool_id="t1",
                    name="Agent",
                    tool_input={
                        "subagent_type": "explore",
                        "prompt": "find callers",
                    },
                )

            return _make_tool_use_response(
                tool_id="t2",
                name="submit_review",
                tool_input={"summary": "ok", "comments": []},
            )

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=mock_send)

        sub_provider = AsyncMock()
        sub_provider.send = AsyncMock(
            return_value=_make_text_response("done"),
        )

        sub_configs = {
            "explore": SubAgentConfig(
                provider=sub_provider,
                model="cheap-model",
                provider_name=ProviderName.GOOGLE,
                system_prompt="You are an explorer.",
                max_turns=2,
                allowed_tools=["Read", "Grep"],
                description="Fast explorer",
            ),
        }

        result = await run_api_agent(
            prompt="Review",
            cwd=tmp_path,
            model="test",
            provider=mock_provider,
            provider_name=ProviderName.GOOGLE,
            allowed_tools=["submit_review"],
            sub_agent_configs=sub_configs,
        )

        assert result.error is None
        sub_provider.send.assert_called()

    @pytest.mark.asyncio
    async def test_unknown_agent_type_returns_error(self, tmp_path):
        from nominal_code.agent.sub_agent import SubAgentConfig

        call_count = 0

        async def mock_send(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return _make_tool_use_response(
                    tool_id="t1",
                    name="Agent",
                    tool_input={
                        "subagent_type": "nonexistent",
                        "prompt": "do stuff",
                    },
                )

            return _make_text_response("done")

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=mock_send)

        sub_configs = {
            "explore": SubAgentConfig(
                provider=AsyncMock(),
                model="m",
                provider_name=ProviderName.GOOGLE,
                system_prompt="",
                description="",
            ),
        }

        result = await run_api_agent(
            prompt="test",
            cwd=tmp_path,
            model="test",
            provider=mock_provider,
            provider_name=ProviderName.GOOGLE,
            max_turns=3,
            sub_agent_configs=sub_configs,
        )

        assert result.error is None

    @pytest.mark.asyncio
    async def test_parallel_agent_calls_run_concurrently(self, tmp_path):
        import asyncio

        from nominal_code.agent.sub_agent import SubAgentConfig

        call_count = 0
        concurrent_high_water = 0
        active_count = 0

        async def tracking_handle_agent(*args, **kwargs):
            nonlocal active_count, concurrent_high_water

            active_count += 1

            if active_count > concurrent_high_water:
                concurrent_high_water = active_count

            await asyncio.sleep(0.01)
            active_count -= 1

            return "## Found something", False, None

        async def mock_send(**kwargs):
            nonlocal call_count
            call_count += 1

            if call_count == 1:
                return LLMResponse(
                    content=[
                        ToolUseBlock(
                            id="t1",
                            name="Agent",
                            input={
                                "subagent_type": "explore",
                                "prompt": "find callers",
                            },
                        ),
                        ToolUseBlock(
                            id="t2",
                            name="Agent",
                            input={
                                "subagent_type": "explore",
                                "prompt": "check tests",
                            },
                        ),
                    ],
                    stop_reason=StopReason.TOOL_USE,
                )

            return _make_tool_use_response(
                tool_id="t3",
                name="submit_review",
                tool_input={"summary": "ok", "comments": []},
            )

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=mock_send)

        sub_configs = {
            "explore": SubAgentConfig(
                provider=AsyncMock(),
                model="m",
                provider_name=ProviderName.GOOGLE,
                system_prompt="",
                allowed_tools=["Read"],
                description="Fast explorer",
            ),
        }

        with patch(
            "nominal_code.agent.api.runner._handle_agent_tool",
            side_effect=tracking_handle_agent,
        ):
            result = await run_api_agent(
                prompt="Review",
                cwd=tmp_path,
                model="test",
                provider=mock_provider,
                provider_name=ProviderName.GOOGLE,
                allowed_tools=["submit_review"],
                sub_agent_configs=sub_configs,
            )

        assert result.error is None
        assert concurrent_high_water == 2


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

        assert result.error is not None
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
        reviewer=AgentRoleConfig(name=ProviderName.OPENAI, model="gpt-4.1"),
        explorer=AgentRoleConfig(name=ProviderName.OPENAI, model="gpt-4.1-mini"),
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
            num_turns=0,
            duration_ms=100,
            error=InvocationError(
                type=ErrorType.PROVIDER_ERROR,
                message="boom",
            ),
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
