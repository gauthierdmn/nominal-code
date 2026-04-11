# type: ignore
import json

import pytest

from nominal_code.commands.webhook.jobs.payload import JobPayload
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

SAMPLE_COMMENT_EVENT_WITH_PROMPT = CommentEvent(
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
    mention_prompt="fix this",
)

SAMPLE_COMMENT_JOB = JobPayload(
    event=SAMPLE_COMMENT_EVENT_WITH_PROMPT,
)

SAMPLE_LIFECYCLE_JOB = JobPayload(
    event=SAMPLE_LIFECYCLE_EVENT,
)


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

        assert data["event"]["mention_prompt"] == "fix this"
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
        assert event.mention_prompt == "fix this"


class TestExtraEnv:
    def test_serialize_with_extra_env(self):
        event = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.NOTE,
        )
        payload = JobPayload(
            event=event,
            extra_env={"GITLAB_TOKEN": "glpat-test"},
        )
        serialized = payload.serialize()
        deserialized = JobPayload.deserialize(serialized)

        assert deserialized.extra_env == {"GITLAB_TOKEN": "glpat-test"}

    def test_deserialize_without_extra_env(self):
        event = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.NOTE,
        )
        payload = JobPayload(event=event)
        serialized = payload.serialize()
        deserialized = JobPayload.deserialize(serialized)

        assert deserialized.extra_env == {}

    def test_default_extra_env_empty(self):
        event = CommentEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=1,
            pr_branch="main",
            event_type=EventType.NOTE,
        )
        payload = JobPayload(event=event)

        assert payload.extra_env == {}


class TestBaseBranchSerialization:
    def test_roundtrip_comment_event_preserves_base_branch(self):
        event = CommentEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=42,
            pr_branch="feature",
            base_branch="main",
            event_type=EventType.ISSUE_COMMENT,
        )
        payload = JobPayload(event=event)
        deserialized = JobPayload.deserialize(payload.serialize())

        assert deserialized.event.base_branch == "main"

    def test_roundtrip_lifecycle_event_preserves_base_branch(self):
        event = LifecycleEvent(
            platform=PlatformName.GITLAB,
            repo_full_name="group/repo",
            pr_number=10,
            pr_branch="feature",
            base_branch="develop",
            event_type=EventType.PR_OPENED,
            pr_author="alice",
        )
        payload = JobPayload(event=event)
        deserialized = JobPayload.deserialize(payload.serialize())

        assert deserialized.event.base_branch == "develop"

    def test_base_branch_in_serialized_json(self):
        event = LifecycleEvent(
            platform=PlatformName.GITHUB,
            repo_full_name="owner/repo",
            pr_number=1,
            pr_branch="feature",
            base_branch="main",
            event_type=EventType.PR_OPENED,
        )
        payload = JobPayload(event=event)
        data = json.loads(payload.serialize())

        assert data["event"]["base_branch"] == "main"

    def test_deserialize_without_base_branch_defaults_empty(self):
        data = json.dumps(
            {
                "event": {
                    "platform": "github",
                    "repo_full_name": "owner/repo",
                    "pr_number": 1,
                    "pr_branch": "feature",
                    "event_type": "pr_opened",
                    "is_comment_event": False,
                },
                "namespace": "",
                "extra_env": {},
            }
        )
        payload = JobPayload.deserialize(data)

        assert payload.event.base_branch == ""


class TestJobPayloadDeserialize:
    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            JobPayload.deserialize("not json")

    def test_missing_event_raises(self):
        with pytest.raises(KeyError):
            JobPayload.deserialize("{}")
