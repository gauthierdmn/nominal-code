# type: ignore
import json

import pytest

from nominal_code.jobs.payload import JobPayload
from nominal_code.models import EventType
from nominal_code.platforms.base import CommentEvent, LifecycleEvent, PlatformName

SAMPLE_COMMENT_EVENT = CommentEvent(
    platform=PlatformName.GITHUB,
    repo_full_name="owner/repo",
    pr_number=42,
    pr_branch="feature-branch",
    pr_title="Add new feature",
    event_type=EventType.ISSUE_COMMENT,
    comment_id=100,
    author_username="alice",
    body="@bot fix this",
    diff_hunk="@@ -1,3 +1,4 @@",
    file_path="src/main.py",
    discussion_id="",
)

SAMPLE_LIFECYCLE_EVENT = LifecycleEvent(
    platform=PlatformName.GITLAB,
    repo_full_name="group/project",
    pr_number=10,
    pr_branch="main",
    pr_title="MR title",
    event_type=EventType.PR_OPENED,
    pr_author="bob",
)

SAMPLE_COMMENT_JOB = JobPayload(
    event=SAMPLE_COMMENT_EVENT,
    prompt="fix this",
    bot_type="worker",
)

SAMPLE_LIFECYCLE_JOB = JobPayload(
    event=SAMPLE_LIFECYCLE_EVENT,
    prompt="",
    bot_type="reviewer",
)


class TestJobPayloadProperties:
    def test_platform_delegates_to_event(self):
        assert SAMPLE_COMMENT_JOB.platform == "github"

    def test_repo_full_name_delegates_to_event(self):
        assert SAMPLE_COMMENT_JOB.repo_full_name == "owner/repo"

    def test_pr_number_delegates_to_event(self):
        assert SAMPLE_COMMENT_JOB.pr_number == 42


class TestJobPayloadSerialize:
    def test_roundtrip_comment_event(self):
        serialized = SAMPLE_COMMENT_JOB.serialize()
        deserialized = JobPayload.deserialize(serialized)

        assert deserialized == SAMPLE_COMMENT_JOB

    def test_roundtrip_lifecycle_event(self):
        serialized = SAMPLE_LIFECYCLE_JOB.serialize()
        deserialized = JobPayload.deserialize(serialized)

        assert deserialized == SAMPLE_LIFECYCLE_JOB

    def test_serialize_produces_valid_json(self):
        serialized = SAMPLE_COMMENT_JOB.serialize()
        data = json.loads(serialized)

        assert data["bot_type"] == "worker"
        assert data["prompt"] == "fix this"
        assert data["event"]["platform"] == "github"
        assert data["event"]["pr_number"] == 42
        assert data["event"]["is_comment_event"] is True

    def test_lifecycle_event_serializes_discriminator(self):
        serialized = SAMPLE_LIFECYCLE_JOB.serialize()
        data = json.loads(serialized)

        assert data["event"]["is_comment_event"] is False
        assert data["event"]["pr_author"] == "bob"

    def test_roundtrip_preserves_all_comment_fields(self):
        deserialized = JobPayload.deserialize(SAMPLE_COMMENT_JOB.serialize())
        event = deserialized.event

        assert isinstance(event, CommentEvent)
        assert event.comment_id == 100
        assert event.author_username == "alice"
        assert event.body == "@bot fix this"
        assert event.diff_hunk == "@@ -1,3 +1,4 @@"
        assert event.file_path == "src/main.py"


class TestJobPayloadDeserialize:
    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            JobPayload.deserialize("not json")

    def test_missing_event_raises(self):
        with pytest.raises(KeyError):
            JobPayload.deserialize('{"prompt": "", "bot_type": "worker"}')
