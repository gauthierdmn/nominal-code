# type: ignore
import json

import pytest

from nominal_code.jobs.payload import ReviewJob

SAMPLE_JOB = ReviewJob(
    platform="github",
    repo_full_name="owner/repo",
    pr_number=42,
    pr_branch="feature-branch",
    pr_title="Add new feature",
    event_type="issue_comment",
    is_comment_event=True,
    author_username="alice",
    comment_body="@bot fix this",
    comment_id=100,
    diff_hunk="@@ -1,3 +1,4 @@",
    file_path="src/main.py",
    discussion_id="",
    prompt="fix this",
    pr_author="",
    bot_type="worker",
)


class TestReviewJobSerialize:
    def test_roundtrip(self):
        serialized = SAMPLE_JOB.serialize()
        deserialized = ReviewJob.deserialize(serialized)

        assert deserialized == SAMPLE_JOB

    def test_serialize_produces_valid_json(self):
        serialized = SAMPLE_JOB.serialize()
        data = json.loads(serialized)

        assert data["platform"] == "github"
        assert data["pr_number"] == 42
        assert data["is_comment_event"] is True

    def test_roundtrip_lifecycle_event(self):
        job = ReviewJob(
            platform="gitlab",
            repo_full_name="group/project",
            pr_number=10,
            pr_branch="main",
            pr_title="MR title",
            event_type="pr_opened",
            is_comment_event=False,
            author_username="",
            comment_body="",
            comment_id=0,
            diff_hunk="",
            file_path="",
            discussion_id="",
            prompt="",
            pr_author="bob",
            bot_type="reviewer",
        )
        deserialized = ReviewJob.deserialize(job.serialize())

        assert deserialized == job
        assert deserialized.is_comment_event is False
        assert deserialized.pr_author == "bob"


class TestReviewJobDeserialize:
    def test_invalid_json_raises(self):
        with pytest.raises(json.JSONDecodeError):
            ReviewJob.deserialize("not json")

    def test_missing_fields_raises(self):
        with pytest.raises(TypeError):
            ReviewJob.deserialize('{"platform": "github"}')

    def test_extra_fields_raises(self):
        data = json.loads(SAMPLE_JOB.serialize())
        data["unexpected_field"] = "value"

        with pytest.raises(TypeError):
            ReviewJob.deserialize(json.dumps(data))
