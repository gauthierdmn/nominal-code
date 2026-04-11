# type: ignore
from unittest.mock import AsyncMock

import pytest

from nominal_code.llm.messages import (
    LLMResponse,
    StopReason,
    ToolChoice,
    ToolUseBlock,
)
from nominal_code.review.explore.planner import (
    SUBMIT_PLAN_TOOL_NAME,
    build_planner_user_message,
    parse_plan_tool_input,
    plan_exploration_groups,
)


def _make_plan_response(groups):
    return LLMResponse(
        content=[
            ToolUseBlock(
                id="tool_1",
                name=SUBMIT_PLAN_TOOL_NAME,
                input={"groups": groups},
            ),
        ],
        stop_reason=StopReason.TOOL_USE,
    )


class TestParsePlanToolInput:
    def test_valid_groups(self):
        tool_input = {
            "groups": [
                {"label": "callers", "prompt": "Search for callers."},
                {"label": "tests", "prompt": "Check test coverage."},
            ],
        }

        groups = parse_plan_tool_input(tool_input)

        assert groups is not None
        assert len(groups) == 2
        assert groups[0].label == "callers"
        assert groups[0].prompt == "Search for callers."
        assert groups[1].label == "tests"

    def test_missing_fields_skipped(self):
        tool_input = {
            "groups": [
                {"label": "good", "prompt": "explore"},
                {"label": "no-prompt"},
                {"prompt": "no label"},
            ],
        }

        groups = parse_plan_tool_input(tool_input)

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].label == "good"

    def test_empty_groups_returns_none(self):
        result = parse_plan_tool_input({"groups": []})

        assert result is None

    def test_missing_groups_key_returns_none(self):
        result = parse_plan_tool_input({})

        assert result is None

    def test_extra_fields_ignored(self):
        tool_input = {
            "groups": [
                {
                    "label": "callers",
                    "prompt": "Search for callers.",
                    "extra": "ignored",
                },
            ],
        }

        groups = parse_plan_tool_input(tool_input)

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].label == "callers"


class TestBuildPlannerUserMessage:
    def test_includes_file_paths_and_line_counts(self):
        changed_files = ["src/auth.py", "src/models.py"]
        diffs = {
            "src/auth.py": "+added line\n-removed line\n context",
            "src/models.py": "+new\n+another",
        }

        message = build_planner_user_message(changed_files, diffs, guidelines="")

        assert "src/auth.py" in message
        assert "src/models.py" in message
        assert "+1 -1" in message
        assert "+2 -0" in message

    def test_missing_diff_shows_zero_counts(self):
        message = build_planner_user_message(["missing.py"], {}, guidelines="")

        assert "missing.py" in message
        assert "+0 -0" in message

    def test_includes_total_file_count(self):
        message = build_planner_user_message(
            ["a.py", "b.py", "c.py"],
            {},
            guidelines="",
        )

        assert "Total: 3 files" in message

    def test_includes_guidelines_when_provided(self):
        guidelines = "Use type annotations everywhere.\nWrite tests for all functions."

        message = build_planner_user_message(["a.py"], {}, guidelines=guidelines)

        assert "Coding guidelines:" in message
        assert "Use type annotations everywhere." in message
        assert "Write tests for all functions." in message

    def test_omits_guidelines_section_when_empty(self):
        message = build_planner_user_message(["a.py"], {}, guidelines="")

        assert "Coding guidelines:" not in message


class TestPlanExplorationGroups:
    @pytest.mark.asyncio
    async def test_successful_planning(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_plan_response(
                [{"label": "core", "prompt": "Explore core changes."}],
            ),
        )

        groups = await plan_exploration_groups(
            changed_files=["src/core.py"],
            diffs={"src/core.py": "+new line"},
            provider=mock_provider,
            model="test-model",
            guidelines="",
        )

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].label == "core"
        mock_provider.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_forces_tool_use(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_plan_response(
                [{"label": "all", "prompt": "explore"}],
            ),
        )

        await plan_exploration_groups(
            changed_files=["a.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
            guidelines="",
        )

        call_kwargs = mock_provider.send.call_args.kwargs

        assert call_kwargs["tool_choice"] == ToolChoice.REQUIRED
        assert len(call_kwargs["tools"]) == 1
        assert call_kwargs["tools"][0]["name"] == SUBMIT_PLAN_TOOL_NAME

    @pytest.mark.asyncio
    async def test_provider_error_returns_none(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=RuntimeError("API error"))

        groups = await plan_exploration_groups(
            changed_files=["src/core.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
            guidelines="",
        )

        assert groups is None

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_plan_response(
                [{"label": "all", "prompt": "explore"}],
            ),
        )

        await plan_exploration_groups(
            changed_files=["a.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
            guidelines="",
            system_prompt="Custom planner prompt.",
        )

        call_kwargs = mock_provider.send.call_args
        assert call_kwargs.kwargs["system_prompt"] == "Custom planner prompt."

    @pytest.mark.asyncio
    async def test_no_tool_call_returns_none(self):
        from nominal_code.llm.messages import TextBlock

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[TextBlock(text="I cannot help with that.")],
                stop_reason=StopReason.END_TURN,
            ),
        )

        groups = await plan_exploration_groups(
            changed_files=["a.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
            guidelines="",
        )

        assert groups is None

    @pytest.mark.asyncio
    async def test_passes_guidelines_to_provider(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=_make_plan_response(
                [{"label": "types", "prompt": "Check types."}],
            ),
        )

        await plan_exploration_groups(
            changed_files=["a.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
            guidelines="Always use type annotations.",
        )

        call_args = mock_provider.send.call_args
        user_message = call_args.kwargs["messages"][0].content[0].text

        assert "Always use type annotations." in user_message
