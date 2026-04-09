# type: ignore
import json
from unittest.mock import AsyncMock

import pytest

from nominal_code.agent.sub_agents.planner import (
    build_planner_user_message,
    parse_planner_response,
    plan_exploration_groups,
)
from nominal_code.agent.sub_agents.result import ExploreGroup
from nominal_code.llm.messages import LLMResponse, StopReason, TextBlock


class TestParsePlannerResponse:
    def test_valid_json(self):
        response = json.dumps([
            {
                "label": "auth",
                "files": ["src/auth.py"],
                "prompt": "Explore auth changes.",
            },
            {
                "label": "models",
                "files": ["src/models.py"],
                "prompt": "Explore model changes.",
            },
        ])
        changed_files = ["src/auth.py", "src/models.py"]

        groups = parse_planner_response(response, changed_files)

        assert groups is not None
        assert len(groups) == 2
        assert groups[0].label == "auth"
        assert groups[0].files == ["src/auth.py"]
        assert groups[0].prompt == "Explore auth changes."
        assert groups[1].label == "models"

    def test_markdown_fenced_json(self):
        response = (
            "Here are the groups:\n"
            "```json\n"
            '[{"label": "api", "files": ["src/api.py"], '
            '"prompt": "Explore API."}]\n'
            "```\n"
        )
        changed_files = ["src/api.py"]

        groups = parse_planner_response(response, changed_files)

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].label == "api"

    def test_invalid_json_returns_none(self):
        result = parse_planner_response("not json at all", ["a.py"])

        assert result is None

    def test_missing_fields_skipped(self):
        response = json.dumps([
            {"label": "good", "files": ["a.py"], "prompt": "explore"},
            {"label": "no-prompt", "files": ["b.py"]},
            {"files": ["c.py"], "prompt": "no label"},
            {"label": "no-files", "prompt": "explore"},
        ])
        changed_files = ["a.py", "b.py", "c.py"]

        groups = parse_planner_response(response, changed_files)

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].label == "good"

    def test_unknown_files_filtered(self):
        response = json.dumps([
            {
                "label": "mixed",
                "files": ["known.py", "unknown.py"],
                "prompt": "explore",
            },
        ])
        changed_files = ["known.py"]

        groups = parse_planner_response(response, changed_files)

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].files == ["known.py"]

    def test_all_unknown_files_returns_none(self):
        response = json.dumps([
            {"label": "ghost", "files": ["unknown.py"], "prompt": "explore"},
        ])
        changed_files = ["actual.py"]

        groups = parse_planner_response(response, changed_files)

        assert groups is None

    def test_empty_array_returns_none(self):
        result = parse_planner_response("[]", ["a.py"])

        assert result is None

    def test_not_array_returns_none(self):
        response = json.dumps({"label": "obj", "files": ["a.py"], "prompt": "x"})

        result = parse_planner_response(response, ["a.py"])

        assert result is None


class TestBuildPlannerUserMessage:
    def test_includes_file_paths_and_line_counts(self):
        changed_files = ["src/auth.py", "src/models.py"]
        diffs = {
            "src/auth.py": "+added line\n-removed line\n context",
            "src/models.py": "+new\n+another",
        }

        message = build_planner_user_message(changed_files, diffs)

        assert "src/auth.py" in message
        assert "src/models.py" in message
        assert "+1 -1" in message
        assert "+2 -0" in message

    def test_missing_diff_shows_zero_counts(self):
        message = build_planner_user_message(["missing.py"], {})

        assert "missing.py" in message
        assert "+0 -0" in message


class TestPlanExplorationGroups:
    @pytest.mark.asyncio
    async def test_successful_planning(self):
        response_json = json.dumps([
            {
                "label": "core",
                "files": ["src/core.py"],
                "prompt": "Explore core changes.",
            },
        ])

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[TextBlock(text=response_json)],
                stop_reason=StopReason.END_TURN,
            ),
        )

        groups = await plan_exploration_groups(
            changed_files=["src/core.py"],
            diffs={"src/core.py": "+new line"},
            provider=mock_provider,
            model="test-model",
        )

        assert groups is not None
        assert len(groups) == 1
        assert groups[0].label == "core"
        mock_provider.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_provider_error_returns_none(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(side_effect=RuntimeError("API error"))

        groups = await plan_exploration_groups(
            changed_files=["src/core.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
        )

        assert groups is None

    @pytest.mark.asyncio
    async def test_uses_custom_system_prompt(self):
        response_json = json.dumps([
            {
                "label": "all",
                "files": ["a.py"],
                "prompt": "explore",
            },
        ])

        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[TextBlock(text=response_json)],
                stop_reason=StopReason.END_TURN,
            ),
        )

        await plan_exploration_groups(
            changed_files=["a.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
            system_prompt="Custom planner prompt.",
        )

        call_kwargs = mock_provider.send.call_args
        assert call_kwargs.kwargs["system_prompt"] == "Custom planner prompt."

    @pytest.mark.asyncio
    async def test_empty_response_returns_none(self):
        mock_provider = AsyncMock()
        mock_provider.send = AsyncMock(
            return_value=LLMResponse(
                content=[TextBlock(text="   ")],
                stop_reason=StopReason.END_TURN,
            ),
        )

        groups = await plan_exploration_groups(
            changed_files=["a.py"],
            diffs={},
            provider=mock_provider,
            model="test-model",
        )

        assert groups is None
