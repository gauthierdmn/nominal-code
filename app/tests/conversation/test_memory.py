# type: ignore
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.llm.messages import Message, TextBlock
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName


class TestConversationStoreConversationId:
    def test_get_returns_none_when_empty(self):
        store = MemoryConversationStore()
        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_set_and_get(self):
        store = MemoryConversationStore()
        store.set_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER, "conv-123"
        )
        result = store.get_conversation_id(
            PlatformName.GITHUB, "owner/repo", 42, BotType.REVIEWER
        )

        assert result == "conv-123"

    def test_different_keys_are_independent(self):
        store = MemoryConversationStore()
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
        store = MemoryConversationStore()
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
        store = MemoryConversationStore()
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
        store = MemoryConversationStore()
        result = store.get_messages(
            PlatformName.GITHUB, "owner/repo", 1, BotType.WORKER
        )

        assert result is None

    def test_set_and_get(self):
        store = MemoryConversationStore()
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
        store = MemoryConversationStore()
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
        store = MemoryConversationStore()
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
