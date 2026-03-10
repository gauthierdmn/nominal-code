# type: ignore
from nominal_code.server.mention import extract_mention


class TestExtractMention:
    def test_extract_mention_basic(self):
        result = extract_mention(
            text="@claude-bot fix the bug", bot_username="claude-bot"
        )

        assert result == "fix the bug"

    def test_extract_mention_case_insensitive(self):
        result = extract_mention(
            text="@Claude-Bot fix the bug", bot_username="claude-bot"
        )

        assert result == "fix the bug"

    def test_extract_mention_no_mention_returns_none(self):
        result = extract_mention(
            text="just a regular comment", bot_username="claude-bot"
        )

        assert result is None

    def test_extract_mention_mention_only_returns_none(self):
        result = extract_mention(text="@claude-bot", bot_username="claude-bot")

        assert result is None

    def test_extract_mention_whitespace_only_after_mention_returns_none(self):
        result = extract_mention(text="@claude-bot   ", bot_username="claude-bot")

        assert result is None

    def test_extract_mention_in_middle_of_text(self):
        result = extract_mention(
            text="hey @claude-bot please review this", bot_username="claude-bot"
        )

        assert result == "please review this"

    def test_extract_mention_with_newlines(self):
        result = extract_mention(
            text="@claude-bot\nplease fix\nthis issue", bot_username="claude-bot"
        )

        assert result == "please fix\nthis issue"

    def test_extract_mention_does_not_match_partial_username(self):
        result = extract_mention(
            text="@claude-botv2 fix this", bot_username="claude-bot"
        )

        assert result is None

    def test_extract_mention_special_chars_in_username(self):
        result = extract_mention(text="@my.bot fix this", bot_username="my.bot")

        assert result == "fix this"
