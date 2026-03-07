# type: ignore
from nominal_code.agent.memory import (
    ConversationStore,
    truncate_messages,
)
from nominal_code.agent.providers.types import Message, TextBlock, ToolResultBlock
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName


class TestConversationStoreConversationId:
    def test_get_returns_none_when_empty(self):
        store = ConversationStore()
        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_set_and_get(self):
        store = ConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER, "conv-123"
        )
        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER
        )

        assert result == "conv-123"

    def test_different_keys_are_independent(self):
        store = ConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "conv-1"
        )
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER, "conv-2"
        )

        assert (
            store.get_conversation_id(
                PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
            )
            == "conv-1"
        )
        assert (
            store.get_conversation_id(
                PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER
            )
            == "conv-2"
        )

    def test_set_overwrites_existing(self):
        store = ConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "old"
        )
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "new"
        )

        assert (
            store.get_conversation_id(
                PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
            )
            == "new"
        )

    def test_different_pr_numbers(self):
        store = ConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, "conv-pr1"
        )

        assert (
            store.get_conversation_id(
                PlatformName.GITHUB, "owner/repo", 2, BotType.WORKER
            )
            is None
        )


class TestConversationStoreMessages:
    def test_get_returns_none_when_empty(self):
        store = ConversationStore()
        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_set_and_get(self):
        store = ConversationStore()
        messages = [Message(role="user", content=[TextBlock(text="hi")])]
        store.set_messages(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER, messages
        )
        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER
        )

        assert result is messages
        assert len(result) == 1

    def test_different_keys_are_independent(self):
        store = ConversationStore()
        msgs1 = [Message(role="user", content=[TextBlock(text="msg1")])]
        msgs2 = [Message(role="user", content=[TextBlock(text="msg2")])]
        store.set_messages(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, msgs1)
        store.set_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER, msgs2
        )

        assert (
            store.get_messages(PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER)
            is msgs1
        )
        assert (
            store.get_messages(PlatformName.GITHUB, "owner/repo", 1, BotType.REVIEWER)
            is msgs2
        )

    def test_set_overwrites_existing(self):
        store = ConversationStore()
        old_msgs = [Message(role="user", content=[TextBlock(text="old")])]
        new_msgs = [Message(role="user", content=[TextBlock(text="new")])]
        store.set_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, old_msgs
        )
        store.set_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER, new_msgs
        )

        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is new_msgs


class TestTruncateMessages:
    def test_empty_list(self):
        assert truncate_messages([]) == []

    def test_within_budget_unchanged(self):
        messages = [
            Message(role="user", content=[TextBlock(text="hi")]),
            Message(role="assistant", content=[TextBlock(text="hello")]),
        ]
        result = truncate_messages(messages, max_chars=100_000)

        assert result == messages

    def test_drops_oldest_pair(self):
        messages = [
            Message(role="user", content=[TextBlock(text="a" * 100)]),
            Message(role="assistant", content=[TextBlock(text="b" * 100)]),
            Message(role="user", content=[TextBlock(text="c" * 100)]),
            Message(role="assistant", content=[TextBlock(text="d" * 100)]),
        ]
        result = truncate_messages(messages, max_chars=250)

        assert len(result) == 2
        assert result[0].content[0].text == "c" * 100
        assert result[1].content[0].text == "d" * 100

    def test_preserves_last_message_even_over_budget(self):
        messages = [
            Message(role="user", content=[TextBlock(text="x" * 1000)]),
        ]
        result = truncate_messages(messages, max_chars=10)

        assert len(result) == 1

    def test_tool_result_blocks_use_fixed_estimate(self):
        messages = [
            Message(
                role="user",
                content=[
                    ToolResultBlock(tool_use_id="t1", content="ok", is_error=False),
                ],
            ),
        ]
        result = truncate_messages(messages, max_chars=500_000)

        assert result == messages
