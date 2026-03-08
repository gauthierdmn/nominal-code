# type: ignore
import pytest

from nominal_code.models import EventType
from nominal_code.platforms.base import (
    CommentEvent,
    CommentReply,
    ExistingComment,
    LifecycleEvent,
    PlatformName,
    PullRequestEvent,
    ReviewerPlatform,
)


class TestPlatformName:
    def test_github_value(self):
        assert PlatformName.GITHUB == "github"

    def test_gitlab_value(self):
        assert PlatformName.GITLAB == "gitlab"

    def test_is_str_enum(self):
        assert isinstance(PlatformName.GITHUB, str)


class TestPullRequestEvent:
    def test_create_event(self):
        event = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="feature",
            event_type=EventType.ISSUE_COMMENT,
        )

        assert event.platform == PlatformName.GITHUB
        assert event.repo_full_name == "owner/repo"
        assert event.pr_number == 42
        assert event.pr_branch == "feature"

    def test_defaults(self):
        event = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_OPENED,
        )

        assert event.clone_url == ""
        assert event.pr_title == ""

    def test_is_frozen(self):
        event = PullRequestEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_OPENED,
        )

        with pytest.raises(AttributeError):
            event.pr_number = 99


class TestCommentEvent:
    def test_create_comment_event(self):
        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=10,
            pr_branch="fix-bug",
            event_type=EventType.ISSUE_COMMENT,
            comment_id=123,
            author_username="user1",
            body="@bot fix this",
        )

        assert event.comment_id == 123
        assert event.author_username == "user1"
        assert event.body == "@bot fix this"

    def test_inherits_from_pull_request_event(self):
        event = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/project",
            pr_number=5,
            pr_branch="main",
            event_type=EventType.NOTE,
        )

        assert isinstance(event, PullRequestEvent)

    def test_defaults(self):
        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.REVIEW_COMMENT,
        )

        assert event.comment_id == 0
        assert event.author_username == ""
        assert event.body == ""
        assert event.diff_hunk == ""
        assert event.file_path == ""
        assert event.discussion_id == ""


class TestLifecycleEvent:
    def test_create_lifecycle_event(self):
        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=7,
            pr_branch="feature",
            event_type=EventType.PR_OPENED,
            pr_author="developer",
        )

        assert event.pr_author == "developer"

    def test_inherits_from_pull_request_event(self):
        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_PUSH,
        )

        assert isinstance(event, PullRequestEvent)

    def test_default_pr_author(self):
        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.PR_OPENED,
        )

        assert event.pr_author == ""


class TestCommentReply:
    def test_create_reply(self):
        reply = CommentReply(body="Looks good!")

        assert reply.body == "Looks good!"
        assert reply.commit_sha == ""

    def test_with_commit_sha(self):
        reply = CommentReply(body="Fixed", commit_sha="abc123")

        assert reply.commit_sha == "abc123"

    def test_is_frozen(self):
        reply = CommentReply(body="test")

        with pytest.raises(AttributeError):
            reply.body = "changed"


class TestExistingComment:
    def test_create_comment(self):
        comment = ExistingComment(
            author="reviewer",
            body="Please fix this",
            file_path="src/main.py",
            line=42,
        )

        assert comment.author == "reviewer"
        assert comment.body == "Please fix this"
        assert comment.file_path == "src/main.py"
        assert comment.line == 42

    def test_defaults(self):
        comment = ExistingComment(author="user", body="comment")

        assert comment.file_path == ""
        assert comment.line == 0
        assert comment.is_resolved is False
        assert comment.created_at == ""

    def test_is_frozen(self):
        comment = ExistingComment(author="user", body="text")

        with pytest.raises(AttributeError):
            comment.author = "other"


class TestReviewerPlatformProtocol:
    def test_is_runtime_checkable(self):
        assert hasattr(ReviewerPlatform, "__protocol_attrs__") or callable(
            getattr(ReviewerPlatform, "__instancecheck__", None)
        )
