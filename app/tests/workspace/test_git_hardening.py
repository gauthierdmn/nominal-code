# type: ignore
from unittest.mock import AsyncMock, patch

import pytest

from nominal_code.workspace.git import GitWorkspace


@pytest.fixture
def workspace(tmp_path):
    return GitWorkspace(
        base_dir=tmp_path,
        repo_full_name="owner/repo",
        pr_number=42,
        clone_url="https://token@github.com/owner/repo.git",
        branch="feature-branch",
    )


class TestCloneHardening:
    @pytest.mark.asyncio
    async def test_clone_disables_hooks(self, workspace):
        with patch.object(
            workspace,
            "_run_command",
            new_callable=AsyncMock,
        ) as mock_run:
            await workspace._clone()

        args = mock_run.call_args
        command_args = [str(arg) for arg in args[0]]

        assert "--config" in command_args

        config_pairs = []
        for index, arg in enumerate(command_args):
            if arg == "--config" and index + 1 < len(command_args):
                config_pairs.append(command_args[index + 1])

        assert "core.hooksPath=/dev/null" in config_pairs

    @pytest.mark.asyncio
    async def test_clone_disables_symlinks(self, workspace):
        with patch.object(
            workspace,
            "_run_command",
            new_callable=AsyncMock,
        ) as mock_run:
            await workspace._clone()

        args = mock_run.call_args
        command_args = [str(arg) for arg in args[0]]

        config_pairs = []
        for index, arg in enumerate(command_args):
            if arg == "--config" and index + 1 < len(command_args):
                config_pairs.append(command_args[index + 1])

        assert "core.symlinks=false" in config_pairs

    @pytest.mark.asyncio
    async def test_clone_blocks_file_protocol(self, workspace):
        with patch.object(
            workspace,
            "_run_command",
            new_callable=AsyncMock,
        ) as mock_run:
            await workspace._clone()

        args = mock_run.call_args
        command_args = [str(arg) for arg in args[0]]

        config_pairs = []
        for index, arg in enumerate(command_args):
            if arg == "--config" and index + 1 < len(command_args):
                config_pairs.append(command_args[index + 1])

        assert "protocol.file.allow=never" in config_pairs


class TestUpdateHardening:
    @pytest.mark.asyncio
    async def test_update_sets_hooks_path(self, workspace):
        with patch.object(
            workspace,
            "_run_git",
            new_callable=AsyncMock,
        ) as mock_git:
            await workspace._update()

        mock_git.assert_any_call("config", "core.hooksPath", "/dev/null")

    @pytest.mark.asyncio
    async def test_update_disables_symlinks(self, workspace):
        with patch.object(
            workspace,
            "_run_git",
            new_callable=AsyncMock,
        ) as mock_git:
            await workspace._update()

        mock_git.assert_any_call("config", "core.symlinks", "false")

    @pytest.mark.asyncio
    async def test_update_blocks_file_protocol(self, workspace):
        with patch.object(
            workspace,
            "_run_git",
            new_callable=AsyncMock,
        ) as mock_git:
            await workspace._update()

        mock_git.assert_any_call("config", "protocol.file.allow", "never")

    @pytest.mark.asyncio
    async def test_update_sets_config_before_fetch(self, workspace):
        with patch.object(
            workspace,
            "_run_git",
            new_callable=AsyncMock,
        ) as mock_git:
            await workspace._update()

        call_list = [c[0] for c in mock_git.call_args_list]

        config_indices = [
            index for index, args in enumerate(call_list) if args[0] == "config"
        ]
        fetch_index = next(
            index for index, args in enumerate(call_list) if args[0] == "fetch"
        )

        for config_index in config_indices:
            assert config_index < fetch_index
