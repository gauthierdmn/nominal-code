# type: ignore
from nominal_code.conversation.memory import MemoryConversationStore
from nominal_code.llm.messages import Message, TextBlock
from nominal_code.models import BotType
from nominal_code.platforms.base import PlatformName


class TestConversationStoreConversationId:
    def test_get_returns_none_when_empty(self):
        store = MemoryConversationStore()
        result = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
        )

        assert result is None

    def test_set_and_get(self):
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.REVIEWER,
            value="conv-123",
        )
        result = store.get_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.REVIEWER,
        )

        assert result == "conv-123"

    def test_different_keys_are_independent(self):
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value="conv-1",
        )
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.REVIEWER,
            value="conv-2",
        )

        assert (
            store.get_conversation_id(
                platform=PlatformName.GITHUB,
                repo="owner/repo",
                pr_number=1,
                bot_type=BotType.WORKER,
            )
            == "conv-1"
        )
        assert (
            store.get_conversation_id(
                platform=PlatformName.GITHUB,
                repo="owner/repo",
                pr_number=1,
                bot_type=BotType.REVIEWER,
            )
            == "conv-2"
        )

    def test_set_overwrites_existing(self):
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value="old",
        )
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value="new",
        )

        assert (
            store.get_conversation_id(
                platform=PlatformName.GITHUB,
                repo="owner/repo",
                pr_number=1,
                bot_type=BotType.WORKER,
            )
            == "new"
        )

    def test_different_pr_numbers(self):
        store = MemoryConversationStore()
        store.set_conversation_id(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value="conv-pr1",
        )

        assert (
            store.get_conversation_id(
                platform=PlatformName.GITHUB,
                repo="owner/repo",
                pr_number=2,
                bot_type=BotType.WORKER,
            )
            is None
        )


class TestConversationStoreMessages:
    def test_get_returns_none_when_empty(self):
        store = MemoryConversationStore()
        result = store.get_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
        )

        assert result is None

    def test_set_and_get(self):
        store = MemoryConversationStore()
        messages = [Message(role="user", content=[TextBlock(text="hi")])]
        store.set_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.REVIEWER,
            value=messages,
        )
        result = store.get_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=42,
            bot_type=BotType.REVIEWER,
        )

        assert result is messages
        assert len(result) == 1

    def test_different_keys_are_independent(self):
        store = MemoryConversationStore()
        msgs1 = [Message(role="user", content=[TextBlock(text="msg1")])]
        msgs2 = [Message(role="user", content=[TextBlock(text="msg2")])]
        store.set_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value=msgs1,
        )
        store.set_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.REVIEWER,
            value=msgs2,
        )

        assert (
            store.get_messages(
                platform=PlatformName.GITHUB,
                repo="owner/repo",
                pr_number=1,
                bot_type=BotType.WORKER,
            )
            is msgs1
        )
        assert (
            store.get_messages(
                platform=PlatformName.GITHUB,
                repo="owner/repo",
                pr_number=1,
                bot_type=BotType.REVIEWER,
            )
            is msgs2
        )

    def test_set_overwrites_existing(self):
        store = MemoryConversationStore()
        old_msgs = [Message(role="user", content=[TextBlock(text="old")])]
        new_msgs = [Message(role="user", content=[TextBlock(text="new")])]
        store.set_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value=old_msgs,
        )
        store.set_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
            value=new_msgs,
        )

        result = store.get_messages(
            platform=PlatformName.GITHUB,
            repo="owner/repo",
            pr_number=1,
            bot_type=BotType.WORKER,
        )

        assert result is new_msgs
