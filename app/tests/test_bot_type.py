# type: ignore
from nominal_code.bot_type import COMMENT_EVENT_TYPES, EventType


class TestEventType:
    def test_event_type_values(self):
        assert EventType.ISSUE_COMMENT == "issue_comment"
        assert EventType.REVIEW_COMMENT == "review_comment"
        assert EventType.REVIEW == "review"
        assert EventType.NOTE == "note"
        assert EventType.PR_OPENED == "pr_opened"
        assert EventType.PR_PUSH == "pr_push"
        assert EventType.PR_REOPENED == "pr_reopened"
        assert EventType.PR_READY_FOR_REVIEW == "pr_ready_for_review"

    def test_event_type_count(self):
        assert len(EventType) == 8


class TestCommentEventTypes:
    def test_comment_event_types_contains_comment_events(self):
        assert EventType.ISSUE_COMMENT in COMMENT_EVENT_TYPES
        assert EventType.REVIEW_COMMENT in COMMENT_EVENT_TYPES
        assert EventType.REVIEW in COMMENT_EVENT_TYPES
        assert EventType.NOTE in COMMENT_EVENT_TYPES

    def test_comment_event_types_excludes_lifecycle_events(self):
        assert EventType.PR_OPENED not in COMMENT_EVENT_TYPES
        assert EventType.PR_PUSH not in COMMENT_EVENT_TYPES
        assert EventType.PR_REOPENED not in COMMENT_EVENT_TYPES
        assert EventType.PR_READY_FOR_REVIEW not in COMMENT_EVENT_TYPES

    def test_comment_event_types_count(self):
        assert len(COMMENT_EVENT_TYPES) == 4
