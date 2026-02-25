# type: ignore
from nominal_code.mention import extract_mention


class TestExtractMention:
    def test_extract_mention_basic(self):
        result = extract_mention("@claude-bot fix the bug", "claude-bot")

        assert result == "fix the bug"

    def test_extract_mention_case_insensitive(self):
        result = extract_mention("@Claude-Bot fix the bug", "claude-bot")

        assert result == "fix the bug"

    def test_extract_mention_no_mention_returns_none(self):
        result = extract_mention("just a regular comment", "claude-bot")

        assert result is None

    def test_extract_mention_mention_only_returns_none(self):
        result = extract_mention("@claude-bot", "claude-bot")

        assert result is None

    def test_extract_mention_whitespace_only_after_mention_returns_none(self):
        result = extract_mention("@claude-bot   ", "claude-bot")

        assert result is None

    def test_extract_mention_in_middle_of_text(self):
        result = extract_mention("hey @claude-bot please review this", "claude-bot")

        assert result == "please review this"

    def test_extract_mention_with_newlines(self):
        result = extract_mention("@claude-bot\nplease fix\nthis issue", "claude-bot")

        assert result == "please fix\nthis issue"

    def test_extract_mention_does_not_match_partial_username(self):
        result = extract_mention("@claude-botv2 fix this", "claude-bot")

        assert result is None

    def test_extract_mention_special_chars_in_username(self):
        result = extract_mention("@my.bot fix this", "my.bot")

        assert result == "fix this"
