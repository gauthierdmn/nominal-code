# type: ignore
from pathlib import Path

import pytest

from nominal_code.agent.api.tools import (
    MAX_LINE_LENGTH,
    SUBMIT_REVIEW_TOOL_NAME,
    TOOL_DEFINITIONS,
    ToolError,
    _execute_glob,
    _execute_read,
    _parse_bash_patterns,
    _resolve_path,
    execute_tool,
    get_tool_definitions,
)


class TestGetToolDefinitions:
    def test_returns_all_tools_when_allowed_is_none(self):
        result = get_tool_definitions(None)

        assert len(result) == len(TOOL_DEFINITIONS)

    def test_returns_all_tools_when_allowed_is_empty(self):
        result = get_tool_definitions([])

        assert len(result) == len(TOOL_DEFINITIONS)

    def test_filters_to_allowed_tools(self):
        result = get_tool_definitions(["Read", "Glob"])

        names = [tool["name"] for tool in result]
        assert names == ["Read", "Glob"]

    def test_bash_pattern_enables_bash_tool(self):
        result = get_tool_definitions(["Bash(git clone*)"])

        names = [tool["name"] for tool in result]
        assert "Bash" in names

    def test_appends_submit_review_tool(self):
        result = get_tool_definitions([SUBMIT_REVIEW_TOOL_NAME])

        names = [tool["name"] for tool in result]
        assert SUBMIT_REVIEW_TOOL_NAME in names

    def test_does_not_include_submit_review_by_default(self):
        result = get_tool_definitions(None)

        names = [tool["name"] for tool in result]
        assert SUBMIT_REVIEW_TOOL_NAME not in names


class TestParseBashPatterns:
    def test_returns_empty_when_none(self):
        assert _parse_bash_patterns(None) == []

    def test_returns_empty_when_no_bash_entries(self):
        assert _parse_bash_patterns(["Read", "Glob"]) == []

    def test_extracts_bash_patterns(self):
        result = _parse_bash_patterns(["Bash(git clone*)", "Bash(ls *)"])

        assert result == ["git clone*", "ls *"]

    def test_ignores_non_bash_entries(self):
        result = _parse_bash_patterns(["Read", "Bash(git *)"])

        assert result == ["git *"]


class TestResolvePath:
    def test_absolute_path_returned_as_is(self):
        result = _resolve_path("/absolute/path.py", Path("/cwd"))

        assert result == Path("/absolute/path.py")

    def test_relative_path_resolved_against_cwd(self):
        result = _resolve_path("src/main.py", Path("/workspace"))

        assert result == Path("/workspace/src/main.py")


class TestExecuteRead:
    def test_reads_file_with_line_numbers(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("line one\nline two\nline three\n")

        result = _execute_read({"file_path": str(test_file)}, tmp_path)

        assert "1\tline one" in result
        assert "2\tline two" in result
        assert "3\tline three" in result

    def test_raises_on_missing_file(self, tmp_path):
        with pytest.raises(ToolError, match="File not found"):
            _execute_read({"file_path": "nonexistent.py"}, tmp_path)

    def test_respects_offset(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("line one\nline two\nline three\n")

        result = _execute_read(
            {"file_path": str(test_file), "offset": 2},
            tmp_path,
        )

        assert "1\t" not in result
        assert "2\tline two" in result

    def test_respects_limit(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("line one\nline two\nline three\n")

        result = _execute_read(
            {"file_path": str(test_file), "limit": 1},
            tmp_path,
        )

        lines = result.strip().split("\n")
        assert len(lines) == 1

    def test_truncates_long_lines(self, tmp_path):
        test_file = tmp_path / "test.py"
        test_file.write_text("x" * (MAX_LINE_LENGTH + 100) + "\n")

        result = _execute_read({"file_path": str(test_file)}, tmp_path)

        assert result.endswith("...")


class TestExecuteGlob:
    def test_finds_matching_files(self, tmp_path):
        (tmp_path / "foo.py").write_text("pass")
        (tmp_path / "bar.py").write_text("pass")
        (tmp_path / "baz.txt").write_text("hello")

        result = _execute_glob({"pattern": "*.py"}, tmp_path)

        assert "foo.py" in result
        assert "bar.py" in result
        assert "baz.txt" not in result

    def test_returns_no_match_message(self, tmp_path):
        result = _execute_glob({"pattern": "*.rs"}, tmp_path)

        assert "No files matched" in result

    def test_raises_on_missing_directory(self, tmp_path):
        with pytest.raises(ToolError, match="Directory not found"):
            _execute_glob({"pattern": "*.py", "path": "/nonexistent"}, tmp_path)


class TestExecuteTool:
    @pytest.mark.asyncio
    async def test_read_tool(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')\n")

        result, is_error = await execute_tool(
            "Read",
            {"file_path": str(test_file)},
            tmp_path,
        )

        assert not is_error
        assert "print('hello')" in result

    @pytest.mark.asyncio
    async def test_glob_tool(self, tmp_path):
        (tmp_path / "test.py").write_text("pass")

        result, is_error = await execute_tool(
            "Glob",
            {"pattern": "*.py"},
            tmp_path,
        )

        assert not is_error
        assert "test.py" in result

    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tmp_path):
        result, is_error = await execute_tool("Unknown", {}, tmp_path)

        assert is_error
        assert "Unknown tool" in result

    @pytest.mark.asyncio
    async def test_tool_error_returns_error_flag(self, tmp_path):
        result, is_error = await execute_tool(
            "Read",
            {"file_path": "nonexistent.py"},
            tmp_path,
        )

        assert is_error
        assert "File not found" in result

    @pytest.mark.asyncio
    async def test_bash_tool_executes_command(self, tmp_path):
        result, is_error = await execute_tool(
            "Bash",
            {"command": "echo hello"},
            tmp_path,
        )

        assert not is_error
        assert "hello" in result

    @pytest.mark.asyncio
    async def test_bash_tool_rejects_disallowed_command(self, tmp_path):
        result, is_error = await execute_tool(
            "Bash",
            {"command": "rm -rf /"},
            tmp_path,
            allowed_tools=["Bash(echo *)"],
        )

        assert is_error
        assert "not allowed" in result

    @pytest.mark.asyncio
    async def test_grep_tool(self, tmp_path):
        test_file = tmp_path / "hello.py"
        test_file.write_text("print('hello')\n")

        result, is_error = await execute_tool(
            "Grep",
            {"pattern": "hello", "path": str(tmp_path)},
            tmp_path,
        )

        assert not is_error
        assert "hello" in result


class TestToolError:
    def test_is_exception(self):
        error = ToolError("something went wrong")

        assert isinstance(error, Exception)
        assert str(error) == "something went wrong"
