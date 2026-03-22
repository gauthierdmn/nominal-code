# type: ignore
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.workspace.git import GitWorkspace, PushResult


@pytest.fixture
def workspace(tmp_path):
    return GitWorkspace(
        base_dir=tmp_path,
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="https://token@github.com/owner/repo.git",
        branch="feature-branch",
    )


class TestInit:
    def test_repo_path_constructed_correctly(self, workspace, tmp_path):
        expected = tmp_path / "owner" / "repo" / "pr-42"

        assert workspace.repo_path == expected


class TestEnsureReady:
    @pytest.mark.asyncio
    async def test_ensure_ready_clones_when_no_git_dir(self, workspace):
        with patch.object(workspace, "_clone", new_callable=AsyncMock) as mock_clone:
            await workspace.ensure_ready()

            mock_clone.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_ready_updates_when_git_dir_exists(self, workspace):
        git_dir = Path(workspace.repo_path) / ".git"
        git_dir.mkdir(parents=True)

        with patch.object(workspace, "_update", new_callable=AsyncMock) as mock_update:
            await workspace.ensure_ready()

            mock_update.assert_called_once()


class TestPushChanges:
    @pytest.mark.asyncio
    async def test_push_changes_no_changes(self, workspace):
        with patch.object(
            workspace,
            "_run_git",
            new_callable=AsyncMock,
            return_value="",
        ):
            result = await workspace.push_changes("test commit")

        assert result.success is True
        assert result.commit_sha == ""

    @pytest.mark.asyncio
    async def test_push_changes_with_changes(self, workspace):
        call_count = 0
        call_returns = [
            " M file.py",
            "",
            "",
            "abc1234",
            "",
        ]

        async def mock_run_git(*args):
            nonlocal call_count
            result = call_returns[call_count]
            call_count += 1

            return result

        with patch.object(workspace, "_run_git", side_effect=mock_run_git):
            result = await workspace.push_changes("fix: update code")

        assert result.success is True
        assert result.commit_sha == "abc1234"


class TestDepsPath:
    def test_deps_path_is_sibling_of_repo_path(self, workspace, tmp_path):
        expected = tmp_path / "owner" / "repo" / ".deps"

        assert workspace.deps_path == expected

    def test_maybe_create_deps_dir_creates_directory(self, workspace):
        workspace.maybe_create_deps_dir()

        assert workspace.deps_path.is_dir()

    def test_maybe_create_deps_dir_idempotent(self, workspace):
        workspace.maybe_create_deps_dir()
        workspace.maybe_create_deps_dir()

        assert workspace.deps_path.is_dir()


class TestPushResult:
    def test_push_result_defaults(self):
        result = PushResult(success=True)

        assert result.commit_sha == ""

    def test_push_result_with_sha(self):
        result = PushResult(success=True, commit_sha="abc123")

        assert result.commit_sha == "abc123"


class TestRedactUrl:
    def test_redact_url_replaces_token(self):
        from nominal_code.agent.sandbox import redact_url

        url = "https://x-access-token:ghp_secret123@github.com/owner/repo.git"
        result = redact_url(url)

        assert "ghp_secret123" not in result
        assert "***" in result
        assert "github.com/owner/repo.git" in result

    def test_redact_url_no_token_unchanged(self):
        from nominal_code.agent.sandbox import redact_url

        url = "https://github.com/owner/repo.git"
        result = redact_url(url)

        assert result == url

    def test_redact_url_oauth2_token(self):
        from nominal_code.agent.sandbox import redact_url

        url = "https://oauth2:glpat-mysecret@gitlab.com/group/repo.git"
        result = redact_url(url)

        assert "glpat-mysecret" not in result
        assert "***" in result


class TestGitWorkspaceInitFull:
    def test_init_sets_repo_path_using_pr_number(self, tmp_path):
        ws = GitWorkspace(
            base_dir=tmp_path,
            repo_full_name="acme/backend",
            pr_number=99,
            clone_url="https://token@github.com/acme/backend.git",
            branch="hotfix",
        )

        assert ws.repo_path == tmp_path / "acme" / "backend" / "pr-99"

    def test_init_stores_clone_url(self, tmp_path):
        url = "https://token@github.com/acme/backend.git"
        ws = GitWorkspace(
            base_dir=tmp_path,
            repo_full_name="acme/backend",
            pr_number=1,
            clone_url=url,
            branch="main",
        )

        assert ws._clone_url == url

    def test_init_stores_branch(self, tmp_path):
        ws = GitWorkspace(
            base_dir=tmp_path,
            repo_full_name="acme/backend",
            pr_number=1,
            clone_url="https://token@github.com/acme/backend.git",
            branch="develop",
        )

        assert ws._branch == "develop"


class TestRunCommand:
    @pytest.mark.asyncio
    async def test_run_command_returns_stdout(self, workspace, tmp_path):
        result = await workspace._run_command("echo", "hello")

        assert result.strip() == "hello"

    @pytest.mark.asyncio
    async def test_run_command_raises_on_nonzero_exit(self, workspace):
        with pytest.raises(RuntimeError):
            await workspace._run_command("false")

    @pytest.mark.asyncio
    async def test_run_command_with_cwd(self, workspace, tmp_path):
        result = await workspace._run_command("pwd", cwd=tmp_path)

        assert str(tmp_path) in result
