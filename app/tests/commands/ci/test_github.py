# type: ignore
import json
import os
from unittest.mock import patch

import pytest

from nominal_code.commands.ci.github import build_event, resolve_workspace
from nominal_code.models import EventType
from nominal_code.platforms.base import PlatformName

VALID_PAYLOAD = {
    "repository": {"full_name": "owner/repo"},
    "pull_request": {
        "number": 42,
        "head": {"ref": "feature-branch"},
    },
}


class TestBuildEvent:
    def test_build_event_valid_payload(self, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(VALID_PAYLOAD))

        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file)}):
            event = build_event()

        assert event.platform == PlatformName.GITHUB
        assert event.repo_full_name == "owner/repo"
        assert event.pr_number == 42
        assert event.pr_branch == "feature-branch"
        assert event.clone_url == ""
        assert event.event_type == EventType.PR_OPENED

    def test_build_event_exits_when_event_path_missing(self):
        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": ""}, clear=False):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_event_path_not_a_file(self, tmp_path):
        missing = tmp_path / "does_not_exist.json"

        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(missing)}):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_no_pull_request(self, tmp_path):
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps({"repository": {"full_name": "o/r"}}))

        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file)}):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_repo_name_missing(self, tmp_path):
        payload = {
            "repository": {},
            "pull_request": {"number": 1, "head": {"ref": "main"}},
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload))

        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file)}):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_pr_number_zero(self, tmp_path):
        payload = {
            "repository": {"full_name": "owner/repo"},
            "pull_request": {"number": 0, "head": {"ref": "main"}},
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload))

        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file)}):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_branch_missing(self, tmp_path):
        payload = {
            "repository": {"full_name": "owner/repo"},
            "pull_request": {"number": 1, "head": {}},
        }
        event_file = tmp_path / "event.json"
        event_file.write_text(json.dumps(payload))

        with patch.dict(os.environ, {"GITHUB_EVENT_PATH": str(event_file)}):
            with pytest.raises(SystemExit):
                build_event()


class TestResolveWorkspace:
    def test_resolve_workspace_returns_env_var(self):
        with patch.dict(os.environ, {"GITHUB_WORKSPACE": "/actions/workspace"}):
            result = resolve_workspace()

        assert result == "/actions/workspace"

    def test_resolve_workspace_falls_back_to_cwd(self):
        env = os.environ.copy()
        env.pop("GITHUB_WORKSPACE", None)

        with patch.dict(os.environ, env, clear=True):
            result = resolve_workspace()

        assert result == os.getcwd()
