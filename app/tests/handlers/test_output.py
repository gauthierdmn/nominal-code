# type: ignore
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nominal_code.agent.result import AgentResult
from nominal_code.config import CliAgentConfig, ReviewerConfig
from nominal_code.handlers.output import (
    FALLBACK_MESSAGE,
    build_fallback_comment,
    extract_json_substring,
    parse_finding,
    parse_review_output,
    repair_review_output,
)
from nominal_code.models import DiffSide


def _make_config():
    config = MagicMock()
    config.allowed_users = frozenset(["alice"])
    config.workspace_base_dir = "/tmp/workspaces"
    config.agent = CliAgentConfig()
    config.coding_guidelines = "Use snake_case."
    config.language_guidelines = {"python": "Python style rules."}
    config.worker = None
    config.reviewer = ReviewerConfig(
        bot_username="claude-reviewer",
        system_prompt="Review code.",
    )

    return config


class TestParseReviewOutput:
    def test_parse_review_output_valid_json(self):
        output = json.dumps(
            {
                "summary": "Looks good overall",
                "comments": [
                    {"path": "src/main.py", "line": 10, "body": "Bug here"},
                    {"path": "src/utils.py", "line": 5, "body": "Perf issue"},
                ],
            }
        )
        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "Looks good overall"
        assert len(result.findings) == 2
        assert result.findings[0].file_path == "src/main.py"
        assert result.findings[0].line == 10
        assert result.findings[1].body == "Perf issue"

    def test_parse_review_output_valid_json_empty_comments(self):
        output = json.dumps(
            {
                "summary": "No issues found",
                "comments": [],
            }
        )
        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "No issues found"
        assert result.findings == []

    def test_parse_review_output_malformed_json(self):
        result = parse_review_output(output="not json at all")

        assert result is None

    def test_parse_review_output_missing_summary(self):
        output = json.dumps({"comments": []})
        result = parse_review_output(output=output)

        assert result is None

    def test_parse_review_output_invalid_comment_missing_path(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [{"line": 10, "body": "test"}],
            }
        )
        result = parse_review_output(output=output)

        assert result is None

    def test_parse_review_output_invalid_comment_bad_line(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [{"path": "a.py", "line": -1, "body": "test"}],
            }
        )
        result = parse_review_output(output=output)

        assert result is None

    def test_parse_review_output_strips_markdown_fences(self):
        output = '```json\n{"summary": "Good", "comments": []}\n```'
        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "Good"

    def test_parse_review_output_not_a_dict(self):
        result = parse_review_output(output="[1, 2, 3]")

        assert result is None

    def test_parse_review_output_repairs_unescaped_quotes(self):
        broken = (
            '{"summary": "ok", "comments": '
            '[{"path": "f.py", "line": 1, "body": "use "foo" here"}]}'
        )

        result = parse_review_output(output=broken)

        assert result is not None
        assert result.summary == "ok"
        assert result.findings[0].body == 'use "foo" here'

    def test_parse_review_output_extracts_json_from_prose(self):
        output = 'Here is my review:\n{"summary": "Looks good", "comments": []}\nDone!'

        result = parse_review_output(output=output)

        assert result is not None
        assert result.summary == "Looks good"

    def test_parse_review_output_repairs_trailing_comma(self):
        broken = (
            '{"summary": "ok", "comments": '
            '[{"path": "a.py", "line": 1, "body": "fix",}],}'
        )

        result = parse_review_output(output=broken)

        assert result is not None
        assert result.summary == "ok"

    def test_parse_review_output_empty_string(self):
        result = parse_review_output(output="")

        assert result is None

    def test_parse_review_output_repairs_suggestion_with_quotes(self):
        broken = (
            '{"summary": "Fix SQL", "comments": [{"path": "db.py", "line": 10, '
            '"body": "SQL injection", '
            '"suggestion": "query = "SELECT * FROM users WHERE id = ?""}]}'
        )

        result = parse_review_output(output=broken)

        assert result is not None
        assert result.findings[0].suggestion is not None
        assert "SELECT" in result.findings[0].suggestion

    def test_parse_review_output_left_side_finding(self):
        output = json.dumps(
            {
                "summary": "Found deletion issue",
                "comments": [
                    {
                        "path": "src/main.py",
                        "line": 5,
                        "body": "Removed code had a bug",
                        "side": "LEFT",
                    },
                ],
            }
        )
        result = parse_review_output(output=output)

        assert result is not None
        assert result.findings[0].side == DiffSide.LEFT

    def test_parse_review_output_invalid_side_returns_none(self):
        output = json.dumps(
            {
                "summary": "Review",
                "comments": [
                    {"path": "a.py", "line": 1, "body": "test", "side": "INVALID"},
                ],
            }
        )
        result = parse_review_output(output=output)

        assert result is None


class TestParseReviewOutputWithSuggestions:
    def test_parse_review_output_with_suggestions(self):
        output = json.dumps(
            {
                "summary": "Found issues",
                "comments": [
                    {
                        "path": "src/main.py",
                        "line": 10,
                        "body": "Use snake_case",
                        "suggestion": "user_count = len(users)",
                    },
                    {
                        "path": "src/main.py",
                        "line": 20,
                        "body": "Simplify",
                        "suggestion": "if items:\n    process(items)",
                        "start_line": 18,
                    },
                ],
            }
        )

        result = parse_review_output(output=output)

        assert result is not None
        assert len(result.findings) == 2
        assert result.findings[0].suggestion == "user_count = len(users)"
        assert result.findings[0].start_line is None
        assert result.findings[1].suggestion == "if items:\n    process(items)"
        assert result.findings[1].start_line == 18


class TestParseFinding:
    def test_parse_finding_valid(self):
        item = {"path": "src/main.py", "line": 10, "body": "Fix this"}

        result = parse_finding(item=item)

        assert result.file_path == "src/main.py"
        assert result.line == 10
        assert result.body == "Fix this"

    def test_parse_finding_defaults_side_to_right(self):
        item = {"path": "src/main.py", "line": 5, "body": "Note"}

        result = parse_finding(item=item)

        assert result.side == DiffSide.RIGHT

    def test_parse_finding_explicit_left_side(self):
        item = {"path": "src/main.py", "line": 5, "body": "Note", "side": "LEFT"}

        result = parse_finding(item=item)

        assert result.side == DiffSide.LEFT

    def test_parse_finding_non_dict_raises(self):
        with pytest.raises(ValueError, match="not a dict"):
            parse_finding(item="not a dict")

    def test_parse_finding_missing_path_raises(self):
        with pytest.raises(ValueError, match="invalid path"):
            parse_finding(item={"line": 5, "body": "text"})

    def test_parse_finding_empty_path_raises(self):
        with pytest.raises(ValueError, match="invalid path"):
            parse_finding(item={"path": "", "line": 5, "body": "text"})

    def test_parse_finding_non_string_path_raises(self):
        with pytest.raises(ValueError, match="invalid path"):
            parse_finding(item={"path": 123, "line": 5, "body": "text"})

    def test_parse_finding_boolean_line_raises(self):
        with pytest.raises(ValueError, match="invalid line"):
            parse_finding(item={"path": "src/main.py", "line": True, "body": "text"})

    def test_parse_finding_line_zero_raises(self):
        with pytest.raises(ValueError, match="invalid line"):
            parse_finding(item={"path": "src/main.py", "line": 0, "body": "text"})

    def test_parse_finding_negative_line_raises(self):
        with pytest.raises(ValueError, match="invalid line"):
            parse_finding(item={"path": "src/main.py", "line": -1, "body": "text"})

    def test_parse_finding_missing_body_raises(self):
        with pytest.raises(ValueError, match="invalid body"):
            parse_finding(item={"path": "src/main.py", "line": 5})

    def test_parse_finding_empty_body_raises(self):
        with pytest.raises(ValueError, match="invalid body"):
            parse_finding(item={"path": "src/main.py", "line": 5, "body": ""})

    def test_parse_finding_invalid_side_raises(self):
        with pytest.raises(ValueError, match="invalid side"):
            parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 5,
                    "body": "text",
                    "side": "MIDDLE",
                }
            )

    def test_parse_finding_with_suggestion(self):
        item = {
            "path": "src/main.py",
            "line": 10,
            "body": "Use snake_case",
            "suggestion": "user_count = len(users)",
        }

        result = parse_finding(item=item)

        assert result.suggestion == "user_count = len(users)"
        assert result.start_line is None

    def test_parse_finding_with_multiline_suggestion(self):
        item = {
            "path": "src/main.py",
            "line": 20,
            "body": "Simplify this",
            "suggestion": "if items:\n    process(items)",
            "start_line": 18,
        }

        result = parse_finding(item=item)

        assert result.suggestion == "if items:\n    process(items)"
        assert result.start_line == 18

    def test_parse_finding_suggestion_empty_string_raises(self):
        with pytest.raises(ValueError, match="invalid suggestion"):
            parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 10,
                    "body": "Fix",
                    "suggestion": "",
                }
            )

    def test_parse_finding_boolean_start_line_raises(self):
        with pytest.raises(ValueError, match="invalid start_line"):
            parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 10,
                    "body": "Fix",
                    "suggestion": "new code",
                    "start_line": True,
                }
            )

    def test_parse_finding_suggestion_start_line_greater_than_line_raises(self):
        with pytest.raises(ValueError, match="start_line must be <= line"):
            parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 5,
                    "body": "Fix",
                    "suggestion": "new code",
                    "start_line": 10,
                }
            )

    def test_parse_finding_start_line_without_suggestion(self):
        item = {
            "path": "src/main.py",
            "line": 24,
            "body": "Hardcoded credentials",
            "start_line": 20,
        }

        result = parse_finding(item=item)

        assert result.start_line == 20
        assert result.suggestion is None

    def test_parse_finding_suggestion_on_left_side_raises(self):
        with pytest.raises(ValueError, match="suggestion not allowed on LEFT side"):
            parse_finding(
                item={
                    "path": "src/main.py",
                    "line": 5,
                    "body": "Fix",
                    "side": "LEFT",
                    "suggestion": "new code",
                }
            )


class TestExtractJsonSubstring:
    def test_extracts_json_from_prose(self):
        text = 'Here is the JSON: {"summary": "ok", "comments": []} done.'

        result = extract_json_substring(text=text)

        assert result == '{"summary": "ok", "comments": []}'

    def test_returns_original_when_no_braces(self):
        result = extract_json_substring(text="no json here")

        assert result == "no json here"

    def test_returns_original_when_only_open_brace(self):
        result = extract_json_substring(text="just { open")

        assert result == "just { open"

    def test_handles_nested_braces(self):
        text = '{"outer": {"inner": 1}}'

        result = extract_json_substring(text=text)

        assert result == '{"outer": {"inner": 1}}'

    def test_strips_markdown_around_json(self):
        text = 'Sure, here is the review:\n```json\n{"summary": "ok"}\n```'

        result = extract_json_substring(text=text)

        assert result == '{"summary": "ok"}'

    def test_returns_original_for_empty_string(self):
        result = extract_json_substring(text="")

        assert result == ""

    def test_returns_original_when_closing_before_opening(self):
        result = extract_json_substring(text="} before {")

        assert result == "} before {"


class TestRepairReviewOutput:
    @pytest.mark.asyncio
    async def test_repair_succeeds_on_first_llm_attempt(self):
        config = _make_config()
        valid_json = json.dumps({"summary": "Fixed", "comments": []})

        with patch(
            "nominal_code.handlers.output.invoke_agent_stateless",
            new_callable=AsyncMock,
            return_value=AgentResult(
                output=valid_json,
                is_error=False,
                num_turns=1,
                duration_ms=100,
            ),
        ) as mock_run:
            result = await repair_review_output(
                broken_output="bad json", config=config, cwd=Path("/tmp")
            )

        assert result is not None
        assert result.summary == "Fixed"
        assert mock_run.call_count == 1

    @pytest.mark.asyncio
    async def test_repair_succeeds_on_second_llm_attempt(self):
        config = _make_config()
        valid_json = json.dumps({"summary": "Fixed", "comments": []})

        with patch(
            "nominal_code.handlers.output.invoke_agent_stateless",
            new_callable=AsyncMock,
            side_effect=[
                AgentResult(
                    output="still broken",
                    is_error=False,
                    num_turns=1,
                    duration_ms=100,
                ),
                AgentResult(
                    output=valid_json,
                    is_error=False,
                    num_turns=1,
                    duration_ms=100,
                ),
            ],
        ) as mock_run:
            result = await repair_review_output(
                broken_output="bad json", config=config, cwd=Path("/tmp")
            )

        assert result is not None
        assert result.summary == "Fixed"
        assert mock_run.call_count == 2

    @pytest.mark.asyncio
    async def test_repair_extracts_json_before_sending_to_llm(self):
        config = _make_config()
        valid_json = json.dumps({"summary": "Fixed", "comments": []})
        wrapped = 'Here is the review:\n{"summary": "broken}\nDone.'

        with patch(
            "nominal_code.handlers.output.invoke_agent_stateless",
            new_callable=AsyncMock,
            return_value=AgentResult(
                output=valid_json,
                is_error=False,
                num_turns=1,
                duration_ms=100,
            ),
        ) as mock_run:
            await repair_review_output(
                broken_output=wrapped, config=config, cwd=Path("/tmp")
            )

        prompt_sent = mock_run.call_args.kwargs["prompt"]

        assert "Here is the review" not in prompt_sent
        assert "Done." not in prompt_sent

    @pytest.mark.asyncio
    async def test_repair_returns_none_when_all_strategies_fail(self):
        config = _make_config()

        with patch(
            "nominal_code.handlers.output.invoke_agent_stateless",
            new_callable=AsyncMock,
            return_value=AgentResult(
                output="gibberish",
                is_error=False,
                num_turns=1,
                duration_ms=100,
            ),
        ):
            result = await repair_review_output(
                broken_output="total nonsense", config=config, cwd=Path("/tmp")
            )

        assert result is None


class TestBuildFallbackComment:
    def test_extracts_summary_from_broken_json(self):
        broken = '{"summary": "This PR has issues", "comments": [bad stuff}'

        result = build_fallback_comment(raw_output=broken)

        assert "This PR has issues" in result
        assert "unable to produce inline review comments" in result

    def test_handles_escaped_quotes_in_summary(self):
        broken = '{"summary": "Found \\"critical\\" bugs", "comments": []bad'

        result = build_fallback_comment(raw_output=broken)

        assert 'Found "critical" bugs' in result

    def test_summary_variant_includes_contact_admin(self):
        broken = '{"summary": "Has bugs", "comments": [bad}'

        result = build_fallback_comment(raw_output=broken)

        assert "contact your administrator" in result

    def test_returns_generic_message_when_no_summary(self):
        result = build_fallback_comment(raw_output="total nonsense")

        assert result == FALLBACK_MESSAGE

    def test_returns_generic_message_for_empty_output(self):
        result = build_fallback_comment(raw_output="")

        assert result == FALLBACK_MESSAGE
