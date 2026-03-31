# type: ignore
import os
from unittest.mock import patch

import pytest

from nominal_code.commands.ci.gitlab import build_event, resolve_workspace
from nominal_code.models import EventType
from nominal_code.platforms.base import PlatformName

GITLAB_ENV = {
    "CI_PROJECT_PATH": "group/project",
    "CI_MERGE_REQUEST_IID": "7",
    "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": "fix-bug",
}


class TestBuildEvent:
    def test_build_event_valid_variables(self):
        with patch.dict(os.environ, GITLAB_ENV):
            event = build_event()

        assert event.platform == PlatformName.GITLAB
        assert event.repo_full_name == "group/project"
        assert event.pr_number == 7
        assert event.pr_branch == "fix-bug"
        assert event.clone_url == ""
        assert event.event_type == EventType.PR_OPENED

    def test_build_event_exits_when_project_path_missing(self):
        env = {**GITLAB_ENV, "CI_PROJECT_PATH": ""}

        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_mr_iid_missing(self):
        env = {**GITLAB_ENV, "CI_MERGE_REQUEST_IID": ""}

        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_branch_missing(self):
        env = {**GITLAB_ENV, "CI_MERGE_REQUEST_SOURCE_BRANCH_NAME": ""}

        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                build_event()

    def test_build_event_exits_when_mr_iid_not_integer(self):
        env = {**GITLAB_ENV, "CI_MERGE_REQUEST_IID": "not-a-number"}

        with patch.dict(os.environ, env, clear=False):
            with pytest.raises(SystemExit):
                build_event()


class TestResolveWorkspace:
    def test_resolve_workspace_returns_env_var(self):
        with patch.dict(os.environ, {"CI_PROJECT_DIR": "/builds/group/project"}):
            result = resolve_workspace()

        assert result == "/builds/group/project"

    def test_resolve_workspace_falls_back_to_cwd(self):
        env = os.environ.copy()
        env.pop("CI_PROJECT_DIR", None)

        with patch.dict(os.environ, env, clear=True):
            result = resolve_workspace()

        assert result == os.getcwd()
