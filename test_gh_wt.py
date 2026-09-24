"""Tests for gh-wt CLI tool."""

import json
import subprocess
import sys
import io
import contextlib
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import gh_wt
from gh_wt import get_repo_root, get_default_branch_name, cli, check_current_dir, hint_if_cwd_removed


@dataclass
class CliResult:
    output: str
    exit_code: int


def run_cli(args: list[str]) -> CliResult:
    """Invoke cli(args) capturing stdout+stderr and trapping SystemExit."""
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            cli(args)
        return CliResult(output=buf.getvalue(), exit_code=0)
    except SystemExit as e:
        return CliResult(output=buf.getvalue(), exit_code=e.code if e.code is not None else 0)


class TestStatusCommand:
    """Tests for the status command."""

    def test_status_excludes_bare_directory_from_output(self, tmp_path: Path) -> None:
        """Status should NOT show .bare directory as a worktree entry."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        # git worktree list outputs .bare directory itself plus actual worktrees
        worktree_output = f"{bare_dir}\t\t(bare)\n{tmp_path}/main\t\t\t(main)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.side_effect = [
                    worktree_output,
                    "main",  # rev-parse for main worktree
                    "",  # status porcelain (clean)
                    "0", "0",  # ahead, behind
                    "abc123 refs/heads/main",  # ls-remote
                    "",  # @{upstream}
                ]
                result = run_cli(["status"])

        assert result.exit_code == 0
        assert ".bare" not in result.output
        assert "main" in result.output

    def test_status_shows_no_worktrees_when_empty(self, tmp_path: Path) -> None:
        """Status command should show message when no worktrees exist."""
        # Create a bare repo structure
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                # Mock empty worktree list
                mock_run_git.return_value = ""
                result = run_cli(["status"])

        assert result.exit_code == 0

    def test_status_shows_worktree_folder_names(self, tmp_path: Path) -> None:
        """Status command should show worktree folder names."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/main\t\t\t(main)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.side_effect = [
                    worktree_output,
                    "main",  # rev-parse
                    "",  # status porcelain (clean)
                    "0", "0",  # ahead, behind
                ]
                result = run_cli(["status"])

        assert result.exit_code == 0
        assert "main" in result.output

    def test_status_shows_branch_names(self, tmp_path: Path) -> None:
        """Status command should show branch name for each worktree."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/main\t\t\t(main)"

        def subprocess_side_effect(cmd, **kwargs):
            """Handle both git and gh commands."""
            if cmd[0] == "gh":
                return MagicMock(stdout="", returncode=1)
            return MagicMock(stdout="", returncode=0)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                with patch("subprocess.run") as mock_subprocess:
                    mock_run_git.side_effect = [
                        worktree_output,
                        "main",  # rev-parse
                        "",  # status porcelain
                        "0", "0",  # ahead, behind
                        "abc123 refs/heads/main",  # ls-remote
                        "",  # @{upstream}
                    ]
                    mock_subprocess.side_effect = subprocess_side_effect
                    result = run_cli(["status"])

        assert result.exit_code == 0
        assert "Branch: main" in result.output

    def test_status_shows_clean_when_no_changes(self, tmp_path: Path) -> None:
        """Status should show 'Clean' when no uncommitted changes."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/main\t\t\t(main)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.side_effect = [
                    worktree_output,  # worktree list
                    "main",  # rev-parse for branch
                    "",  # status --porcelain (empty = no changes)
                ]
                result = run_cli(["status"])

        assert result.exit_code == 0
        assert "Clean" in result.output

    def test_status_shows_uncommitted_changes(self, tmp_path: Path) -> None:
        """Status should show uncommitted changes count."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/main\t\t\t(main)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.side_effect = [
                    worktree_output,  # worktree list
                    "main",  # rev-parse for branch
                    "M  file1.py\nM  file2.py\n?? new.py",  # 2 modified + 1 untracked
                    "0", "0",  # ahead, behind
                ]
                result = run_cli(["status"])

        # Exit code 1 because uncommitted changes need attention
        assert result.exit_code == 1
        assert "uncommitted" in result.output.lower()

    def test_status_shows_ahead_behind_origin(self, tmp_path: Path) -> None:
        """Status should show commits ahead/behind origin."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/main\t\t\t(main)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.side_effect = [
                    worktree_output,  # worktree list
                    "main",  # rev-parse for branch
                    "",  # status --porcelain (clean)
                    "0",  # rev-list --count origin/main..HEAD (ahead)
                    "2",  # rev-list --count HEAD..origin/main (behind)
                ]
                result = run_cli(["status"])

        assert result.exit_code == 1  # Behind origin = attention needed
        assert "behind" in result.output.lower()
        assert "2" in result.output

    def test_status_shows_pr_info_when_available(self, tmp_path: Path) -> None:
        """Status should show PR number and state when gh CLI works."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/feature\t\t\t(feature)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                with patch("subprocess.run") as mock_subprocess:
                    mock_run_git.side_effect = [
                        worktree_output,
                        "feature",  # rev-parse
                        "",  # status porcelain
                        "0", "0",  # ahead, behind
                    ]
                    # Mock gh pr view response
                    mock_subprocess.return_value = MagicMock(
                        stdout='{"number": 45, "state": "OPEN", "url": "https://github.com/owner/repo/pull/45"}',
                        returncode=0,
                    )
                    result = run_cli(["status"])

        assert result.exit_code == 0
        assert "#45" in result.output
        assert "OPEN" in result.output

    def test_status_shows_draft_when_pr_is_draft(self, tmp_path: Path) -> None:
        """Status should show (DRAFT) instead of (OPEN) when gh reports isDraft."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/feature\t\t\t(feature)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                with patch("subprocess.run") as mock_subprocess:
                    mock_run_git.side_effect = [
                        worktree_output,
                        "feature",  # rev-parse
                        "",  # status porcelain
                        "0", "0",  # ahead, behind
                    ]
                    # Mock gh pr view response with isDraft: true
                    mock_subprocess.return_value = MagicMock(
                        stdout='{"number": 2271, "state": "OPEN", "url": "https://github.com/owner/repo/pull/2271", "isDraft": true}',
                        returncode=0,
                    )
                    result = run_cli(["status"])

        assert result.exit_code == 0
        assert "#2271" in result.output
        assert "(DRAFT)" in result.output
        assert "(OPEN)" not in result.output
        # Confirm the draft state is what reaches the line, not the raw OPEN
        assert "PR: #2271 (DRAFT) -" in result.output

    def test_status_shows_no_pr_when_not_found(self, tmp_path: Path) -> None:
        """Status should indicate when no PR exists for branch."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/feature\t\t\t(feature)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                with patch("subprocess.run") as mock_subprocess:
                    mock_run_git.side_effect = [
                        worktree_output,
                        "feature",
                        "",
                        "0", "0",
                    ]
                    # Mock gh pr view failure (no PR)
                    mock_subprocess.return_value = MagicMock(
                        stdout="",
                        returncode=1,
                    )
                    result = run_cli(["status"])

        assert result.exit_code == 0
        assert "Not found" in result.output or "no PR" in result.output.lower()


class TestGetRepoRoot:
    """Test finding the bare repo root by locating .bare/ directory."""

    def test_finds_bare_directory_in_current_directory(self, tmp_path: Path) -> None:
        """Should find .bare/ when running from within the repo."""
        # Arrange: Create repo structure repo/.bare/
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)

        # Act: Run from inside repo
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(repo_root)
            result = get_repo_root()
        finally:
            os.chdir(original_cwd)

        # Assert
        assert result == repo_root

    def test_finds_bare_directory_from_subdirectory(self, tmp_path: Path) -> None:
        """Should find .bare/ when running from a subdirectory."""
        # Arrange
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)
        subdir = repo_root / "src" / "components"
        subdir.mkdir(parents=True)

        # Act: Run from deep subdirectory
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(subdir)
            result = get_repo_root()
        finally:
            os.chdir(original_cwd)

        # Assert
        assert result == repo_root

    def test_returns_none_when_not_in_bare_repo(self, tmp_path: Path) -> None:
        """Should return None when no .bare/ directory exists."""
        # Arrange: Regular directory without .bare/
        regular_dir = tmp_path / "regular"
        regular_dir.mkdir()

        # Act: Run from outside any bare repo
        original_cwd = Path.cwd()
        try:
            import os
            os.chdir(regular_dir)
            result = get_repo_root()
        finally:
            os.chdir(original_cwd)

        # Assert
        assert result is None


class TestGetDefaultBranchName:
    """Test extracting default branch from origin/HEAD."""

    def test_extracts_branch_from_origin_head(self, tmp_path: Path) -> None:
        """Should extract 'main' from 'origin/main'."""
        # Arrange: Create .bare/ directory
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)

        # Mock run_git to return 'origin/main'
        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.return_value = "origin/main"

            # Act
            result = get_default_branch_name(repo_root)

        # Assert
        assert result == "main"
        mock_run_git.assert_called_once_with(
            ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
            cwd=str(bare_dir),
            check=False,
        )

    def test_handles_remote_prefix_in_branch_name(self, tmp_path: Path) -> None:
        """Should handle branches with slashes like origin/feature/x."""
        # Arrange
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)

        # Mock run_git to return branch with slash that starts with origin/
        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.return_value = "origin/mr/some-branch"

            # Act
            result = get_default_branch_name(repo_root)

        # Assert: Should strip first 'origin/' only
        assert result == "mr/some-branch"

    def test_falls_back_to_master_when_origin_head_is_missing(self, tmp_path: Path) -> None:
        """Should use origin/master when origin/HEAD is missing and main does not exist."""
        # Arrange
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = ["", "origin/master", ""]

            # Act
            result = get_default_branch_name(repo_root)

        # Assert
        assert result == "master"
        mock_run_git.assert_any_call(
            ["branch", "-r", "--format=%(refname:short)"],
            cwd=str(bare_dir),
            check=False,
        )

    def test_falls_back_to_local_master_when_origin_head_and_remote_branches_are_missing(self, tmp_path: Path) -> None:
        """Should use local master in bare clones that do not have remote-tracking refs."""
        # Arrange
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = ["", "", "master"]

            # Act
            result = get_default_branch_name(repo_root)

        # Assert
        assert result == "master"
        mock_run_git.assert_any_call(
            ["branch", "--format=%(refname:short)"],
            cwd=str(bare_dir),
            check=False,
        )

    def test_fallbacks_to_main_when_command_fails(self, tmp_path: Path) -> None:
        """Should return 'main' when git command fails."""
        # Arrange
        repo_root = tmp_path / "myrepo"
        bare_dir = repo_root / ".bare"
        bare_dir.mkdir(parents=True)

        # Mock run_git to return empty string (command failed)
        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.return_value = ""

            # Act
            result = get_default_branch_name(repo_root)

        # Assert
        assert result == "main"


class TestCloneWorkflow:
    """Integration-style tests for clone command behavior."""

    @patch("gh_wt.subprocess.run")
    @patch("gh_wt.run_git")
    def test_clone_creates_directory_structure(self, mock_run_git, mock_subprocess_run, tmp_path: Path) -> None:
        """Clone should create repo/.bare/ directory structure."""
        # Arrange
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            mock_run_git.return_value = "main"  # Default branch

            run_cli(["clone", "owner/test-repo"])

            # Assert: Directory structure was created
            assert (tmp_path / "test-repo" / ".bare").exists(), "Expected .bare/ to be created"

            # Assert: git clone --bare was called
            subprocess_calls = [c for c in mock_subprocess_run.call_args_list]
            assert len(subprocess_calls) > 0, "Expected subprocess.run to be called"
        finally:
            os.chdir(original_cwd)

    @patch("gh_wt.subprocess.run")
    @patch("gh_wt.run_git")
    def test_clone_writes_executable_setup_worktree_script(self, mock_run_git, mock_subprocess_run, tmp_path: Path) -> None:
        """Clone should scaffold an executable worktree-setup.sh in the repo root."""
        import os
        original_cwd = os.getcwd()
        try:
            os.chdir(tmp_path)
            mock_run_git.return_value = "main"

            run_cli(["clone", "owner/test-repo"])

            script_path = tmp_path / "test-repo" / "worktree-setup.sh"
            assert script_path.exists(), "Expected worktree-setup.sh to be created"
            assert os.access(script_path, os.X_OK), "Expected worktree-setup.sh to be executable"

            contents = script_path.read_text()
            assert 'target_dir="$script_dir/$worktree"' in contents
            assert "exec" not in contents, "Starter script must not drop into a subshell"
        finally:
            os.chdir(original_cwd)


class TestAddCommand:
    """Tests for the add command."""

    def test_add_uses_explicit_branch_name_with_custom_folder(self, tmp_path: Path) -> None:
        """Add should allow the worktree folder to differ from the branch name."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli([
                            "add",
                            "FIN-361-webflow-handler",
                            "--branch-name",
                            "billw/FIN-361-webflow-handler",
                    ])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(["fetch", "--prune", "origin"], cwd=str(bare_dir))
        mock_run_git.assert_any_call(
            ["branch", "-r", "--list", "origin/billw/FIN-361-webflow-handler"],
            cwd=str(bare_dir),
        )
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "billw/FIN-361-webflow-handler",
                str(tmp_path / "FIN-361-webflow-handler"),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )
        mock_run_git.assert_any_call(
            ["push", "-u", "origin", "billw/FIN-361-webflow-handler"],
            cwd=str(tmp_path / "FIN-361-webflow-handler"),
        )
        assert "Worktree created. To use it, run:\n\ncd FIN-361-webflow-handler" in result.output

    def test_add_supports_short_branch_name_flag(self, tmp_path: Path) -> None:
        """Add should support -b as shorthand for --branch-name."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli([
                            "add",
                            "FIN-361-webflow-handler",
                            "-b",
                            "billw/FIN-361-webflow-handler",
                    ])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "billw/FIN-361-webflow-handler",
                str(tmp_path / "FIN-361-webflow-handler"),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )

    def test_add_uses_local_base_branch_when_remote_tracking_ref_is_missing(self, tmp_path: Path) -> None:
        """Add should work in bare clones where fetched branches live under refs/heads."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        def fake_run_git(args, cwd=None, check=True):
            if args == ["rev-parse", "--verify", "--quiet", "main"]:
                return "abc123"
            return ""

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=fake_run_git) as mock_run_git:
                    result = run_cli(["add", "billw/DESN-1192-sunset-max-builds"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "billw/DESN-1192-sunset-max-builds",
                str(tmp_path / "DESN-1192-sunset-max-builds"),
                "main",
            ],
            cwd=str(bare_dir),
        )

    def test_add_uses_base_branch_option_for_new_worktrees(self, tmp_path: Path) -> None:
        """Add should support --base-branch for selecting a new branch base."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name") as mock_default_branch:
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "feature-branch", "--base-branch", "release/candidate"])

        assert result.exit_code == 0
        mock_default_branch.assert_not_called()
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "feature-branch",
                str(tmp_path / "feature-branch"),
                "origin/release/candidate",
            ],
            cwd=str(bare_dir),
        )

    def test_add_supports_short_base_branch_flag(self, tmp_path: Path) -> None:
        """Add should support -B as shorthand for --base-branch."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name") as mock_default_branch:
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "feature-branch", "-B", "release/candidate"])

        assert result.exit_code == 0
        mock_default_branch.assert_not_called()
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "feature-branch",
                str(tmp_path / "feature-branch"),
                "origin/release/candidate",
            ],
            cwd=str(bare_dir),
        )

    def test_add_rejects_old_base_option(self, tmp_path: Path) -> None:
        """Add should not keep the old --base option."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                result = run_cli(["add", "feature-branch", "--base", "main"])

        assert result.exit_code == 2
        assert "unrecognized arguments" in result.output
        mock_run_git.assert_not_called()

    def test_add_without_branch_name_keeps_last_segment_folder_default(self, tmp_path: Path) -> None:
        """Add should keep deriving the folder from the branch's last segment."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "billw/foo"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["branch", "-r", "--list", "origin/billw/foo"],
            cwd=str(bare_dir),
        )
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "billw/foo",
                str(tmp_path / "foo"),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )

    def test_add_runs_executable_setup_hook_with_folder_name(self, tmp_path: Path, monkeypatch) -> None:
        """Add should run setup-worktree.sh from the invocation directory when executable."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        setup_script = tmp_path / "setup-worktree.sh"
        setup_script.write_text("#!/bin/sh\n")
        setup_script.chmod(0o755)
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.subprocess.run") as mock_subprocess_run:
                        mock_run_git.return_value = ""

                        result = run_cli(["add", "hide-fixed-steps"])

        assert result.exit_code == 0
        mock_subprocess_run.assert_called_once_with(
            [str(setup_script), "hide-fixed-steps"],
            cwd=str(tmp_path),
            check=True,
        )

    def test_add_skips_missing_setup_hook(self, tmp_path: Path, monkeypatch) -> None:
        """Add should not require setup-worktree.sh to exist."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.subprocess.run") as mock_subprocess_run:
                        mock_run_git.return_value = ""

                        result = run_cli(["add", "feature-branch"])

        assert result.exit_code == 0
        mock_subprocess_run.assert_not_called()

    def test_add_skips_non_executable_setup_hook(self, tmp_path: Path, monkeypatch) -> None:
        """Add should only run setup-worktree.sh when it is executable."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        setup_script = tmp_path / "setup-worktree.sh"
        setup_script.write_text("#!/bin/sh\n")
        setup_script.chmod(0o644)
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.subprocess.run") as mock_subprocess_run:
                        mock_run_git.return_value = ""

                        result = run_cli(["add", "feature-branch"])

        assert result.exit_code == 0
        mock_subprocess_run.assert_not_called()

    def test_add_exits_nonzero_when_setup_hook_fails(self, tmp_path: Path, monkeypatch) -> None:
        """Add should fail when an executable setup hook exits non-zero."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        setup_script = tmp_path / "setup-worktree.sh"
        setup_script.write_text("#!/bin/sh\nexit 7\n")
        setup_script.chmod(0o755)
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.subprocess.run") as mock_subprocess_run:
                        mock_run_git.return_value = ""
                        mock_subprocess_run.side_effect = subprocess.CalledProcessError(
                            7,
                            ["./setup-worktree.sh", "feature-branch"],
                        )

                        result = run_cli(["add", "feature-branch"])

        assert result.exit_code == 7
        assert "Created feature-branch" not in result.output


class TestRemoveMergedFlag:
    """Tests for --merged flag on remove command."""

    def test_remove_merged_without_folder_requires_flag(self, tmp_path: Path) -> None:
        """Remove without folder should error unless --merged flag is passed."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            result = run_cli(["rm"])

        assert result.exit_code != 0
        assert "folder" in result.output.lower() or "merged" in result.output.lower() or "-m" in result.output

    def test_remove_with_both_folder_and_merged_errors(self, tmp_path: Path) -> None:
        """Cannot specify both folder and --merged flag."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            result = run_cli(["rm", "feature-branch", "-m"])

        assert result.exit_code != 0
        assert "cannot" in result.output.lower() or "together" in result.output.lower() or "both" in result.output.lower()

    def test_remove_merged_removes_worktrees_with_merged_or_closed_prs(self, tmp_path: Path) -> None:
        """--merged should remove all worktrees with merged or closed PRs."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        # Create worktree directories
        (tmp_path / "feature-1").mkdir()
        (tmp_path / "feature-2").mkdir()
        (tmp_path / "feature-3").mkdir()
        (tmp_path / "main").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.run_git") as mock_run_git:
                        with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                            # Four worktrees
                            mock_get_branches.return_value = [
                                ("feature-1", "feature-1", str(tmp_path / "feature-1")),
                                ("feature-2", "feature-2", str(tmp_path / "feature-2")),
                                ("feature-3", "feature-3", str(tmp_path / "feature-3")),
                                ("main", "main", str(tmp_path / "main")),
                            ]
                            # feature-1 is merged, feature-2 is closed, feature-3 is open, main has no PR
                            mock_get_pr_info.side_effect = [
                                {"number": 1, "state": "MERGED", "url": "http://..."},
                                {"number": 2, "state": "CLOSED", "url": "http://..."},
                                {"number": 3, "state": "OPEN", "url": "http://..."},
                                None,  # no PR for main
                            ]

                            result = run_cli(["rm", "-m"])

        assert result.exit_code == 0
        assert "Removing feature-1 (feature-1)..." in result.output
        assert "Removing feature-2 (feature-2)..." in result.output
        assert mock_remove_worktree.call_count == 2
        mock_remove_worktree.assert_any_call(str(tmp_path / "feature-1"), str(bare_dir), False)
        mock_remove_worktree.assert_any_call(str(tmp_path / "feature-2"), str(bare_dir), False)
        mock_run_git.assert_any_call(["branch", "-D", "feature-1"], cwd=str(bare_dir))
        mock_run_git.assert_any_call(["branch", "-D", "feature-2"], cwd=str(bare_dir))

    def test_remove_merged_continues_past_a_failed_removal(self, tmp_path: Path) -> None:
        """One worktree that will not delete should not strand the ones behind it."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        (tmp_path / "feature-1").mkdir()
        (tmp_path / "feature-2").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.run_git") as mock_run_git:
                        with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                            mock_get_branches.return_value = [
                                ("feature-1", "feature-1", str(tmp_path / "feature-1")),
                                ("feature-2", "feature-2", str(tmp_path / "feature-2")),
                            ]
                            mock_get_pr_info.return_value = {"number": 1, "state": "MERGED", "url": "http://..."}
                            mock_remove_worktree.side_effect = [False, True]

                            result = run_cli(["rm", "-m"])

        assert result.exit_code == 1
        assert mock_remove_worktree.call_count == 2
        assert "feature-1" in result.output
        assert "Removed 1 merged or closed worktree(s)" in result.output
        # The failed worktree keeps its branch; the one behind it still loses its own.
        branch_deletes = [
            call.args[0] for call in mock_run_git.call_args_list
            if call.args and call.args[0][:2] == ["branch", "-D"]
        ]
        assert branch_deletes == [["branch", "-D", "feature-2"]]

    def test_remove_merged_deletes_local_branch_by_default(self, tmp_path: Path) -> None:
        """--merged should delete local branch by default (no flag needed)."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "merged-feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.run_git") as mock_run_git:
                        with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                            mock_get_branches.return_value = [
                                ("merged-feature", "merged-feature", str(tmp_path / "merged-feature")),
                            ]
                            mock_get_pr_info.return_value = {
                                "number": 1, "state": "MERGED", "url": "http://..."
                            }

                            result = run_cli(["rm", "-m"])

        assert result.exit_code == 0
        # Should remove worktree AND delete local branch (no flag needed)
        mock_remove_worktree.assert_called_once_with(str(tmp_path / "merged-feature"), str(bare_dir), False)
        mock_run_git.assert_any_call(["branch", "-D", "merged-feature"], cwd=str(bare_dir))
        # Should NOT try to delete from remote (no --delete-remote flag)
        remote_delete_calls = [
            call for call in mock_run_git.call_args_list
            if call.args[0][:2] == ["push", "origin"]
        ]
        assert len(remote_delete_calls) == 0

    def test_remove_merged_with_delete_remote_deletes_remote(self, tmp_path: Path) -> None:
        """--merged --delete-remote should also delete from origin."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "merged-feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.run_git") as mock_run_git:
                        with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                            mock_get_branches.return_value = [
                                ("merged-feature", "merged-feature", str(tmp_path / "merged-feature")),
                            ]
                            mock_get_pr_info.return_value = {
                                "number": 1, "state": "MERGED", "url": "http://..."
                            }

                            result = run_cli(["rm", "-m", "-d"])

        assert result.exit_code == 0
        # Should remove worktree, delete local branch, AND delete from remote
        mock_remove_worktree.assert_called_once_with(str(tmp_path / "merged-feature"), str(bare_dir), False)
        mock_run_git.assert_any_call(["branch", "-D", "merged-feature"], cwd=str(bare_dir))
        mock_run_git.assert_any_call(["push", "origin", ":" + "merged-feature"], cwd=str(bare_dir), check=False)

    def test_remove_merged_skips_open_pr_worktrees(self, tmp_path: Path) -> None:
        """--merged should NOT remove worktrees with open PRs."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.run_git") as mock_run_git:
                        with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                            mock_get_branches.return_value = [
                                ("feature", "feature", str(tmp_path / "feature")),
                            ]
                            mock_get_pr_info.return_value = {
                                "number": 1, "state": "OPEN", "url": "http://..."
                            }

                            result = run_cli(["rm", "-m"])

        assert result.exit_code == 0
        # Should not remove anything
        mock_remove_worktree.assert_not_called()
        mock_run_git.assert_not_called()


class TestGetWorktreeBranches:
    """Tests for get_worktree_branches helper function."""

    def test_get_worktree_branches_returns_folder_and_branch(self, tmp_path: Path) -> None:
        """Should return list of (folder, branch, path) tuples."""
        from gh_wt import get_worktree_branches
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{tmp_path}/main\t\t\t(main)\n{tmp_path}/feature-x\t\t\t(feature-x)\n{bare_dir}\t\t(bare)"

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = [
                worktree_output,  # worktree list
                "main",  # rev-parse for main
                "feature-x",  # rev-parse for feature-x
            ]
            result = get_worktree_branches(tmp_path)

        assert len(result) == 2  # Excludes bare directory
        assert ("main", "main", str(tmp_path / "main")) in result
        assert ("feature-x", "feature-x", str(tmp_path / "feature-x")) in result

    def test_get_worktree_branches_skips_bare_directory(self, tmp_path: Path) -> None:
        """Should not include the .bare directory itself."""
        from gh_wt import get_worktree_branches
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        worktree_output = f"{bare_dir}\t\t(bare)\n{tmp_path}/main\t\t\t(main)"

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = [
                worktree_output,
                "main",
            ]
            result = get_worktree_branches(tmp_path)

        assert len(result) == 1
        assert result[0][0] == "main"


class TestIsBranchSafeToDelete:
    """Tests for is_branch_safe_to_delete helper function."""

    def test_safe_when_local_equals_origin(self, tmp_path: Path) -> None:
        """Branch is safe when local matches origin."""
        from gh_wt import is_branch_safe_to_delete
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.run_git") as mock_run_git:
            # Local and origin have same commit
            mock_run_git.side_effect = [
                "abc123",  # rev-parse local branch
                "abc123",  # rev-parse origin/branch
                "abc123",  # merge-base (same)
            ]
            result = is_branch_safe_to_delete("feature", str(bare_dir))

        assert result is True

    def test_safe_when_local_behind_origin(self, tmp_path: Path) -> None:
        """Branch is safe when local is behind origin."""
        from gh_wt import is_branch_safe_to_delete
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = [
                "abc123",  # local (behind)
                "def456",  # origin (ahead)
                "abc123",  # merge-base equals local
            ]
            result = is_branch_safe_to_delete("feature", str(bare_dir))

        assert result is True

    def test_unsafe_when_local_ahead_of_origin(self, tmp_path: Path) -> None:
        """Branch is unsafe when local has unpushed commits."""
        from gh_wt import is_branch_safe_to_delete
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = [
                "def456",  # local (ahead)
                "abc123",  # origin (behind)
                "abc123",  # merge-base equals origin, not local
            ]
            result = is_branch_safe_to_delete("feature", str(bare_dir))

        assert result is False

    def test_unsafe_when_no_origin_branch(self, tmp_path: Path) -> None:
        """Branch is unsafe when no origin branch exists."""
        from gh_wt import is_branch_safe_to_delete
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.side_effect = [
                "abc123",  # local exists
                "",        # origin branch doesn't exist
            ]
            result = is_branch_safe_to_delete("feature", str(bare_dir))

        assert result is False

    def test_unsafe_when_local_branch_missing(self, tmp_path: Path) -> None:
        """Branch is unsafe when local branch doesn't exist."""
        from gh_wt import is_branch_safe_to_delete
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.run_git") as mock_run_git:
            mock_run_git.return_value = ""  # local doesn't exist
            result = is_branch_safe_to_delete("feature", str(bare_dir))

        assert result is False


class TestRemovePreflightChecks:
    """Tests for remove command preflight checks."""

    def test_single_remove_prunes_matching_stale_worktree_record(self, tmp_path: Path) -> None:
        """Remove should prune stale metadata when the requested folder is already gone."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        stale_path = tmp_path / "deleted-parent" / "feature"
        porcelain_output = f"worktree {stale_path}\nprunable gitdir file points to non-existent location"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.side_effect = [
                    porcelain_output,
                    "Removing worktrees/feature: gitdir file points to non-existent location",
                ]

                result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        assert "stale Git worktree record exists for feature" in result.output
        assert "Removed stale worktree record: feature" in result.output
        mock_run_git.assert_any_call(["worktree", "list", "--porcelain"], cwd=str(bare_dir), check=False)
        mock_run_git.assert_any_call(["worktree", "prune", "-v"], cwd=str(bare_dir), check=False)
        branch_delete_calls = [
            call for call in mock_run_git.call_args_list
            if call.args[0][:2] == ["branch", "-D"]
        ]
        assert len(branch_delete_calls) == 0

    def test_single_remove_missing_folder_without_stale_record_still_errors(self, tmp_path: Path) -> None:
        """Remove should keep the existing error when no matching stale record exists."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.return_value = ""

                result = run_cli(["rm", "feature"])

        assert result.exit_code == 1
        assert "Worktree folder not found: feature" in result.output
        mock_run_git.assert_called_once_with(["worktree", "list", "--porcelain"], cwd=str(bare_dir), check=False)

    def test_single_remove_uses_external_registered_detached_worktree(self, tmp_path: Path) -> None:
        """Remove should resolve external registered worktrees by folder basename."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        external_path = tmp_path / "outside" / "modcloud-pr1176"
        external_path.mkdir(parents=True)
        porcelain_output = f"worktree {external_path}\nHEAD abc123"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                    mock_run_git.side_effect = [porcelain_output, "HEAD"]

                    result = run_cli(["rm", "modcloud-pr1176"])

        assert result.exit_code == 0
        assert "Removing modcloud-pr1176 (detached)..." in result.output
        mock_run_git.assert_any_call(["worktree", "list", "--porcelain"], cwd=str(bare_dir), check=False)
        mock_run_git.assert_any_call(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(external_path),
        )
        mock_remove_worktree.assert_called_once_with(str(external_path), str(bare_dir), False)
        branch_delete_calls = [
            call for call in mock_run_git.call_args_list
            if call.args[0][:2] == ["branch", "-D"]
        ]
        assert len(branch_delete_calls) == 0

    def test_single_remove_uses_external_registered_branch_worktree(self, tmp_path: Path) -> None:
        """Remove should preserve branch deletion behavior for external branch worktrees."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        external_path = tmp_path / "outside" / "feature"
        external_path.mkdir(parents=True)
        porcelain_output = f"worktree {external_path}\nbranch refs/heads/billw/feature"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=True) as mock_safe:
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                        mock_run_git.side_effect = [porcelain_output, "billw/feature", ""]

                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        assert "Removing feature (billw/feature)..." in result.output
        mock_safe.assert_called_once_with("billw/feature", str(bare_dir))
        mock_remove_worktree.assert_called_once_with(str(external_path), str(bare_dir), False)
        mock_run_git.assert_any_call(["branch", "-D", "billw/feature"], cwd=str(bare_dir))

    def test_single_remove_deletes_checked_out_branch_not_folder_name(self, tmp_path: Path) -> None:
        """Remove should resolve the branch checked out in the worktree."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "FIN-361-webflow-handler"
        worktree_path.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=True) as mock_safe:
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                        mock_run_git.return_value = "billw/FIN-361-webflow-handler"

                        result = run_cli(["rm", "FIN-361-webflow-handler"])

        assert result.exit_code == 0
        assert "Removing FIN-361-webflow-handler (billw/FIN-361-webflow-handler)..." in result.output
        mock_run_git.assert_any_call(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(worktree_path),
        )
        mock_safe.assert_called_once_with("billw/FIN-361-webflow-handler", str(bare_dir))
        mock_remove_worktree.assert_called_once_with(str(worktree_path), str(bare_dir), False)
        mock_run_git.assert_any_call(
            ["branch", "-D", "billw/FIN-361-webflow-handler"],
            cwd=str(bare_dir),
        )

    def test_single_remove_forces_retry_after_deinitializing_submodules(self, tmp_path: Path) -> None:
        """Remove should force the retry after deinitializing submodules."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"
        worktree_path.mkdir()

        submodule_error = "fatal: working trees containing submodules cannot be moved or removed"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.run_git_result") as mock_run_git_result:
                        mock_run_git.return_value = "feature"
                        mock_run_git_result.side_effect = [
                            subprocess.CompletedProcess(
                                ["git", "worktree", "remove", str(worktree_path)],
                                returncode=128,
                                stderr=submodule_error,
                            ),
                            subprocess.CompletedProcess(
                                ["git", "worktree", "remove", str(worktree_path)],
                                returncode=0,
                            ),
                        ]

                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["submodule", "deinit", "-f", "--all"],
            cwd=str(worktree_path),
        )
        assert mock_run_git_result.call_args_list[0].args[0] == [
            "worktree",
            "remove",
            str(worktree_path),
        ]
        assert mock_run_git_result.call_args_list[1].args[0] == [
            "worktree",
            "remove",
            "--force",
            str(worktree_path),
        ]
        mock_run_git.assert_any_call(["branch", "-D", "feature"], cwd=str(bare_dir))

    def test_single_remove_passes_force_to_git_worktree_remove(self, tmp_path: Path) -> None:
        """Remove --force should pass --force to git worktree remove."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"
        worktree_path.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=False):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.run_git_result") as mock_run_git_result:
                        mock_run_git.return_value = "feature"
                        mock_run_git_result.return_value = subprocess.CompletedProcess(
                            ["git", "worktree", "remove", "--force", str(worktree_path)],
                            returncode=0,
                        )

                        result = run_cli(["rm", "feature", "-f"])

        assert result.exit_code == 0
        mock_run_git_result.assert_called_once_with(
            ["worktree", "remove", "--force", str(worktree_path)],
            cwd=str(bare_dir),
        )
        mock_run_git.assert_any_call(["branch", "-D", "feature"], cwd=str(bare_dir))

    def test_single_remove_delete_remote_uses_checked_out_branch(self, tmp_path: Path) -> None:
        """Remote deletion should use the resolved branch, not the folder name."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "FIN-361-webflow-handler"
        worktree_path.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.remove_worktree"):
                        mock_run_git.return_value = "billw/FIN-361-webflow-handler"

                        result = run_cli(["rm", "FIN-361-webflow-handler", "-d"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["push", "origin", ":billw/FIN-361-webflow-handler"],
            cwd=str(bare_dir),
            check=False,
        )

    def test_single_remove_delete_remote_warns_when_origin_branch_missing(self, tmp_path: Path) -> None:
        """Missing origin branch should warn but not fail removal."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"
        worktree_path.mkdir()

        def run_git_side_effect(args, **kwargs):
            if args == ["rev-parse", "--abbrev-ref", "HEAD"]:
                return "feature"
            if args == ["rev-parse", "--verify", "--quiet", "refs/remotes/origin/feature"]:
                return ""
            return ""

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                with patch("gh_wt.run_git", side_effect=run_git_side_effect) as mock_run_git:
                    with patch("gh_wt.remove_worktree"):
                        result = run_cli(["rm", "feature", "-d"])

        assert result.exit_code == 0
        assert "Warning: Remote branch not found: origin/feature" in result.output
        assert "Removed feature" in result.output
        push_calls = [
            call for call in mock_run_git.call_args_list
            if call.args[0][:2] == ["push", "origin"]
        ]
        assert push_calls == []

    def test_single_remove_fails_if_branch_unsafe(self, tmp_path: Path) -> None:
        """Should error and not remove worktree if local branch has unpushed commits."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=False):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 1
        # Should NOT remove worktree (preflight failed)
        mock_remove_worktree.assert_not_called()
        # Should NOT delete branch
        branch_delete_calls = [
            call for call in mock_run_git.call_args_list
            if call.args[0][:2] == ["branch", "-D"]
        ]
        assert len(branch_delete_calls) == 0
        # Should error about local branch
        assert "local branch" in result.output.lower()
        assert "unpushed" in result.output.lower()

    def test_single_remove_bypasses_safety_check_if_forced(self, tmp_path: Path) -> None:
        """Should remove worktree and delete branch even if local branch has unpushed commits if force is True."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=False):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                        mock_run_git.return_value = "feature"
                        result = run_cli(["rm", "feature", "-f"])

        assert result.exit_code == 0
        # Should remove worktree and force-delete branch since force was specified
        mock_remove_worktree.assert_called_once_with(str(tmp_path / "feature"), str(bare_dir), True)
        mock_run_git.assert_any_call(
            ["branch", "-D", "feature"],
            cwd=str(bare_dir)
        )

    def test_single_remove_deletes_branch_if_safe(self, tmp_path: Path) -> None:
        """Should delete branch if local is at or behind origin."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                with patch("gh_wt.run_git") as mock_run_git:
                    with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                        mock_run_git.return_value = "feature"

                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        # Should remove worktree AND delete branch
        mock_remove_worktree.assert_called_once_with(str(tmp_path / "feature"), str(bare_dir), False)
        mock_run_git.assert_any_call(
            ["branch", "-D", "feature"],
            cwd=str(bare_dir)
        )

    def test_merged_removal_fails_if_branch_unsafe(self, tmp_path: Path) -> None:
        """Should error and not remove worktrees if any local branch has unpushed commits."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "merged-feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.is_branch_safe_to_delete", return_value=False):
                        with patch("gh_wt.run_git") as mock_run_git:
                            with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                                mock_get_branches.return_value = [
                                    ("merged-feature", "merged-feature", str(tmp_path / "merged-feature")),
                                ]
                                mock_get_pr_info.return_value = {
                                    "number": 1, "state": "MERGED", "url": "http://..."
                                }

                                result = run_cli(["rm", "-m", "-d"])

        assert result.exit_code == 1
        # Should NOT remove worktree (preflight failed)
        mock_remove_worktree.assert_not_called()
        # Should NOT delete branch
        branch_delete_calls = [
            call for call in mock_run_git.call_args_list
            if call.args[0][:2] == ["branch", "-D"]
        ]
        assert len(branch_delete_calls) == 0
        # Should error about local branch(es)
        assert "local branch" in result.output.lower()
        assert "unpushed" in result.output.lower()

    def test_merged_removal_bypasses_safety_check_if_forced(self, tmp_path: Path) -> None:
        """Should remove worktrees even if they have unpushed commits if force is True."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "merged-feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.is_branch_safe_to_delete", return_value=False):
                        with patch("gh_wt.run_git") as mock_run_git:
                            with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                                mock_get_branches.return_value = [
                                    ("merged-feature", "merged-feature", str(tmp_path / "merged-feature")),
                                ]
                                mock_get_pr_info.return_value = {
                                    "number": 1, "state": "MERGED", "url": "http://..."
                                }

                                result = run_cli(["rm", "-m", "-d", "-f"])

        assert result.exit_code == 0
        # Should remove worktree AND delete branch
        mock_remove_worktree.assert_called_once_with(str(tmp_path / "merged-feature"), str(bare_dir), True)
        mock_run_git.assert_any_call(
            ["branch", "-D", "merged-feature"],
            cwd=str(bare_dir)
        )

    def test_merged_removal_deletes_safe_branches(self, tmp_path: Path) -> None:
        """Should delete branch when safe to do so."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "merged-feature").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                        with patch("gh_wt.run_git") as mock_run_git:
                            with patch("gh_wt.remove_worktree") as mock_remove_worktree:
                                mock_get_branches.return_value = [
                                    ("merged-feature", "merged-feature", str(tmp_path / "merged-feature")),
                                ]
                                mock_get_pr_info.return_value = {
                                    "number": 1, "state": "MERGED", "url": "http://..."
                                }

                                result = run_cli(["rm", "-m", "-d"])

        assert result.exit_code == 0
        # Should remove worktree AND delete branch
        mock_remove_worktree.assert_called_once_with(str(tmp_path / "merged-feature"), str(bare_dir), False)
        mock_run_git.assert_any_call(
            ["branch", "-D", "merged-feature"],
            cwd=str(bare_dir)
        )


class TestRemoveWorktree:
    """Tests for the remove_worktree helper function."""

    def test_success_reports_removal(self, tmp_path: Path) -> None:
        """A clean git removal returns True and skips the fallback."""
        from gh_wt import remove_worktree
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"

        with patch("gh_wt.run_git_result") as mock_run_git_result:
            mock_run_git_result.return_value = subprocess.CompletedProcess([], returncode=0)

            assert remove_worktree(str(worktree_path), str(bare_dir)) is True

        mock_run_git_result.assert_called_once_with(
            ["worktree", "remove", str(worktree_path)],
            cwd=str(bare_dir),
        )

    def test_force_is_passed_through(self, tmp_path: Path) -> None:
        """force adds --force to the git invocation."""
        from gh_wt import remove_worktree
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"

        with patch("gh_wt.run_git_result") as mock_run_git_result:
            mock_run_git_result.return_value = subprocess.CompletedProcess([], returncode=0)

            assert remove_worktree(str(worktree_path), str(bare_dir), True) is True

        mock_run_git_result.assert_called_once_with(
            ["worktree", "remove", "--force", str(worktree_path)],
            cwd=str(bare_dir),
        )

    def test_leftover_folder_is_cleaned_up_and_pruned(self, tmp_path: Path) -> None:
        """A folder Git could not rmdir - the .DS_Store case - is deleted here instead."""
        from gh_wt import remove_worktree
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"
        worktree_path.mkdir()
        (worktree_path / ".DS_Store").write_bytes(b"finder")

        with patch("gh_wt.run_git") as mock_run_git:
            with patch("gh_wt.run_git_result") as mock_run_git_result:
                mock_run_git_result.return_value = subprocess.CompletedProcess(
                    [],
                    returncode=1,
                    stderr=f"error: failed to delete '{worktree_path}': Directory not empty",
                )

                assert remove_worktree(str(worktree_path), str(bare_dir)) is True

        assert not worktree_path.exists()
        mock_run_git.assert_called_once_with(["worktree", "prune"], cwd=str(bare_dir))

    def test_failure_without_leftover_folder_reports_false(self, tmp_path: Path) -> None:
        """A git failure with nothing left on disk is a real error, not a cleanup case."""
        from gh_wt import remove_worktree
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"

        with patch("gh_wt.run_git") as mock_run_git:
            with patch("gh_wt.run_git_result") as mock_run_git_result:
                mock_run_git_result.return_value = subprocess.CompletedProcess(
                    [], returncode=1, stderr=f"error: failed to delete '{worktree_path}': Directory not empty",
                )

                assert remove_worktree(str(worktree_path), str(bare_dir)) is False

        mock_run_git.assert_not_called()

    def test_dirty_worktree_refusal_is_left_alone(self, tmp_path: Path) -> None:
        """Git guarding uncommitted work is not a cleanup case - the folder must survive."""
        from gh_wt import remove_worktree
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"
        worktree_path.mkdir()
        (worktree_path / "notes.txt").write_text("unsaved work")

        with patch("gh_wt.run_git") as mock_run_git:
            with patch("gh_wt.run_git_result") as mock_run_git_result:
                mock_run_git_result.return_value = subprocess.CompletedProcess(
                    [],
                    returncode=1,
                    stderr=f"fatal: '{worktree_path}' contains modified or untracked files, use --force to delete it",
                )

                assert remove_worktree(str(worktree_path), str(bare_dir)) is False

        assert (worktree_path / "notes.txt").read_text() == "unsaved work"
        mock_run_git.assert_not_called()

    def test_undeletable_folder_reports_false(self, tmp_path: Path) -> None:
        """When even the fallback cannot clear the folder, say so instead of claiming success."""
        from gh_wt import remove_worktree
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_path = tmp_path / "feature"
        worktree_path.mkdir()

        with patch("gh_wt.run_git"):
            with patch("gh_wt.run_git_result") as mock_run_git_result:
                with patch("shutil.rmtree"):
                    mock_run_git_result.return_value = subprocess.CompletedProcess(
                        [],
                        returncode=1,
                        stderr=f"error: failed to delete '{worktree_path}': Directory not empty",
                    )

                    assert remove_worktree(str(worktree_path), str(bare_dir)) is False

        assert worktree_path.exists()


class TestParseLinearIssueUrl:
    """Tests for parse_linear_issue_url."""

    def test_parses_full_issue_url(self) -> None:
        """A full issue URL becomes ID-title-slug."""
        from gh_wt import parse_linear_issue_url

        url = "https://linear.app/modularml/issue/MKT-176/add-redirect-for-mojo-package-submission-page"
        assert parse_linear_issue_url(url) == "MKT-176-add-redirect-for-mojo-package-submission-page"

    def test_parses_url_without_title_slug(self) -> None:
        """A URL with only the issue ID yields just the ID."""
        from gh_wt import parse_linear_issue_url

        assert parse_linear_issue_url("https://linear.app/modularml/issue/MKT-176") == "MKT-176"

    def test_ignores_query_string_and_fragment(self) -> None:
        """Query strings and fragments copied from the browser are stripped."""
        from gh_wt import parse_linear_issue_url

        url = "https://linear.app/modularml/issue/MKT-176/add-redirect?noRedirect=1#comment-abc"
        assert parse_linear_issue_url(url) == "MKT-176-add-redirect"

    def test_ignores_trailing_slash(self) -> None:
        """A trailing slash does not add an empty segment."""
        from gh_wt import parse_linear_issue_url

        url = "https://linear.app/modularml/issue/MKT-176/add-redirect/"
        assert parse_linear_issue_url(url) == "MKT-176-add-redirect"

    def test_accepts_any_team_key_and_digit_count(self) -> None:
        """Parsing keys off the /issue/ segment, not a team-key pattern."""
        from gh_wt import parse_linear_issue_url

        assert parse_linear_issue_url("https://linear.app/x/issue/DESN-9/fix") == "DESN-9-fix"
        assert parse_linear_issue_url("https://linear.app/x/issue/LONGTEAM-123456/t") == "LONGTEAM-123456-t"

    def test_rejects_url_without_issue_segment(self) -> None:
        """URLs without /issue/{id} are not Linear issue URLs."""
        from gh_wt import parse_linear_issue_url

        assert parse_linear_issue_url("https://linear.app/modularml") is None
        assert parse_linear_issue_url("https://linear.app/modularml/issue") is None
        assert parse_linear_issue_url("not a url") is None
        assert parse_linear_issue_url("") is None


class TestLinearFlag:
    """Tests for the add --linear/-l flag."""

    HERO_URL = "https://linear.app/modularml/issue/MKT-176/add-redirect-for-mojo-package-submission-page"
    HERO_NAME = "MKT-176-add-redirect-for-mojo-package-submission-page"

    def test_linear_appends_parsed_name_to_prefix(self, tmp_path: Path) -> None:
        """`add billw/ -l URL` should create branch billw/{parsed} in folder {parsed}."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "billw/", "-l", self.HERO_URL])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                f"billw/{self.HERO_NAME}",
                str(tmp_path / self.HERO_NAME),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )
        mock_run_git.assert_any_call(
            ["push", "-u", "origin", f"billw/{self.HERO_NAME}"],
            cwd=str(tmp_path / self.HERO_NAME),
        )

    def test_linear_joins_prefix_without_trailing_slash(self, tmp_path: Path) -> None:
        """`add billw -l URL` should also create branch billw/{parsed}."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "billw", "-l", self.HERO_URL])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                f"billw/{self.HERO_NAME}",
                str(tmp_path / self.HERO_NAME),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )

    def test_linear_without_positional_uses_parsed_name_alone(self, tmp_path: Path) -> None:
        """`add -l URL` should create branch {parsed} with no prefix."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "-l", self.HERO_URL])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                self.HERO_NAME,
                str(tmp_path / self.HERO_NAME),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )

    def test_linear_composes_with_base_branch(self, tmp_path: Path) -> None:
        """`-l` should respect --base-branch for the new worktree's start point."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                mock_run_git.return_value = ""

                result = run_cli(["add", "-l", self.HERO_URL, "-B", "release/candidate"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                self.HERO_NAME,
                str(tmp_path / self.HERO_NAME),
                "origin/release/candidate",
            ],
            cwd=str(bare_dir),
        )

    def test_linear_composes_with_local(self, tmp_path: Path) -> None:
        """`-l` with -L should create the branch without pushing to origin."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "billw/", "-l", self.HERO_URL, "-L"])

        assert result.exit_code == 0
        pushes = [c for c in mock_run_git.call_args_list if c.args[0][0] == "push"]
        assert pushes == []

    def test_linear_rejects_branch_name_flag(self, tmp_path: Path) -> None:
        """`-l` and --branch-name both control the branch name, so they conflict."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                result = run_cli(["add", "-l", self.HERO_URL, "-b", "other-name"])

        assert result.exit_code == 1
        assert "Error: Cannot use --linear with --branch-name" in result.output
        mock_run_git.assert_not_called()

    def test_linear_rejects_unparseable_value(self, tmp_path: Path) -> None:
        """A value without /issue/{id} should fail with a clear error."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                result = run_cli(["add", "billw/", "-l", "https://linear.app/modularml"])

        assert result.exit_code == 1
        assert "Error: Not a Linear issue URL: https://linear.app/modularml" in result.output
        mock_run_git.assert_not_called()

    def test_add_requires_positional_without_linear(self, tmp_path: Path) -> None:
        """Bare `add` should still demand the folder-or-branch argument."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                result = run_cli(["add"])

        assert result.exit_code == 1
        assert "FOLDER_OR_BRANCH argument is required" in result.output
        mock_run_git.assert_not_called()


class TestLocalShortFlag:
    """Tests for --local's short flag moving to -L."""

    def test_local_uses_uppercase_short_flag(self, tmp_path: Path) -> None:
        """`add foo -L` should create the branch locally without pushing."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git") as mock_run_git:
                    mock_run_git.return_value = ""

                    result = run_cli(["add", "feature-branch", "-L"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "feature-branch",
                str(tmp_path / "feature-branch"),
                "main",
            ],
            cwd=str(bare_dir),
        )
        pushes = [c for c in mock_run_git.call_args_list if c.args[0][0] == "push"]
        assert pushes == []

    def test_lowercase_l_no_longer_means_local(self, tmp_path: Path) -> None:
        """`-l` now takes a URL value; bare `-l` is a usage error."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git") as mock_run_git:
                result = run_cli(["add", "feature-branch", "-l"])

        assert result.exit_code == 2
        mock_run_git.assert_not_called()


class TestLoadConfig:
    """Tests for worktree-config.toml loading."""

    def test_missing_config_returns_empty_dict(self, tmp_path: Path) -> None:
        """A repo without a config file behaves as fully unconfigured."""
        from gh_wt import load_config

        assert load_config(tmp_path) == {}

    def test_reads_branch_prefix_and_setup_script(self, tmp_path: Path) -> None:
        """Both supported keys should come back as written."""
        from gh_wt import load_config

        (tmp_path / "worktree-config.toml").write_text(
            'branch-prefix = "billw"\nsetup-script = "worktree-setup.sh"\n'
        )

        config = load_config(tmp_path)

        assert config["branch-prefix"] == "billw"
        assert config["setup-script"] == "worktree-setup.sh"

    def test_invalid_toml_exits_with_parse_error(self, tmp_path: Path, capsys) -> None:
        """Broken TOML should fail loudly, never be silently ignored."""
        from gh_wt import load_config

        (tmp_path / "worktree-config.toml").write_text("branch-prefix = [unclosed\n")

        with pytest.raises(SystemExit) as excinfo:
            load_config(tmp_path)

        assert excinfo.value.code == 1
        assert "Error: Cannot parse worktree-config.toml" in capsys.readouterr().err

    def test_non_string_value_exits_with_parse_error(self, tmp_path: Path, capsys) -> None:
        """Known keys must be strings."""
        from gh_wt import load_config

        (tmp_path / "worktree-config.toml").write_text("branch-prefix = 7\n")

        with pytest.raises(SystemExit) as excinfo:
            load_config(tmp_path)

        assert excinfo.value.code == 1
        assert "branch-prefix must be a string" in capsys.readouterr().err

    def test_unknown_keys_are_ignored(self, tmp_path: Path) -> None:
        """Unknown keys pass through without complaint (forward compatibility)."""
        from gh_wt import load_config

        (tmp_path / "worktree-config.toml").write_text('future-option = "whatever"\n')

        assert load_config(tmp_path)["future-option"] == "whatever"


def make_remote_listing_run_git(listings: dict[str, str]):
    """Fake run_git that answers `branch -r --list` from a lookup table."""
    def fake_run_git(args, cwd=None, check=True):
        if args[:3] == ["branch", "-r", "--list"]:
            return listings.get(args[3], "")
        return ""
    return fake_run_git


class TestBranchPrefix:
    """Tests for the config branch-prefix rules in add."""

    def write_prefix_config(self, repo_root: Path) -> None:
        (repo_root / "worktree-config.toml").write_text('branch-prefix = "billw"\n')

    def test_prefixes_new_branch_from_bare_name(self, tmp_path: Path) -> None:
        """`add foo` should create billw/foo in a folder named foo."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "foo"])

        assert result.exit_code == 0
        assert "Creating branch billw/foo..." in result.output
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "billw/foo", str(tmp_path / "foo"), "origin/main"],
            cwd=str(bare_dir),
        )
        mock_run_git.assert_any_call(
            ["push", "-u", "origin", "billw/foo"],
            cwd=str(tmp_path / "foo"),
        )

    def test_config_prefix_with_trailing_slash_is_normalized(self, tmp_path: Path) -> None:
        """A branch-prefix written with a trailing slash behaves like one without."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "worktree-config.toml").write_text('branch-prefix = "billw/"\n')

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "foo"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "billw/foo", str(tmp_path / "foo"), "origin/main"],
            cwd=str(bare_dir),
        )

    def test_prefers_existing_prefixed_remote_branch(self, tmp_path: Path) -> None:
        """`add foo` should check out origin/billw/foo when it exists."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)
        listings = {"origin/billw/foo": "origin/billw/foo"}

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git(listings)) as mock_run_git:
                    result = run_cli(["add", "foo"])

        assert result.exit_code == 0
        assert "Found origin/billw/foo" in result.output
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "billw/foo", str(tmp_path / "foo"), "origin/billw/foo"],
            cwd=str(bare_dir),
        )

    def test_falls_back_to_unprefixed_remote_branch(self, tmp_path: Path) -> None:
        """`add foo` should still check out origin/foo when only it exists."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)
        listings = {"origin/foo": "origin/foo"}

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git(listings)) as mock_run_git:
                    result = run_cli(["add", "foo"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "foo", str(tmp_path / "foo"), "origin/foo"],
            cwd=str(bare_dir),
        )

    def test_branch_with_slash_is_used_verbatim(self, tmp_path: Path) -> None:
        """Names containing '/' are full branch names; the prefix never applies."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "colleague/fix"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "colleague/fix", str(tmp_path / "fix"), "origin/main"],
            cwd=str(bare_dir),
        )

    def test_explicit_branch_name_is_used_verbatim(self, tmp_path: Path) -> None:
        """-b values are exact; the prefix never applies."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "myfolder", "-b", "exact-name"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "exact-name", str(tmp_path / "myfolder"), "origin/main"],
            cwd=str(bare_dir),
        )

    def test_prefix_applies_to_linear_branch_names(self, tmp_path: Path) -> None:
        """Linear-derived names without a positional prefix get the config prefix."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "-l", "https://linear.app/modularml/issue/MKT-176/add-redirect"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "billw/MKT-176-add-redirect",
                str(tmp_path / "MKT-176-add-redirect"),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )

    def test_positional_linear_prefix_beats_config_prefix(self, tmp_path: Path) -> None:
        """`add teamx -l <url>` keeps the positional prefix."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        self.write_prefix_config(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "teamx", "-l", "https://linear.app/modularml/issue/MKT-176/add-redirect"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "teamx/MKT-176-add-redirect",
                str(tmp_path / "MKT-176-add-redirect"),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )


class TestLinearUrlAutoDetect:
    """Tests for pasted Linear URLs behaving like -l."""

    def test_pasted_url_names_branch_from_issue(self, tmp_path: Path) -> None:
        """A Linear issue URL as the positional argument implies -l."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})) as mock_run_git:
                    result = run_cli(["add", "https://linear.app/modularml/issue/MKT-176/add-redirect"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            [
                "worktree",
                "add",
                "-b",
                "MKT-176-add-redirect",
                str(tmp_path / "MKT-176-add-redirect"),
                "origin/main",
            ],
            cwd=str(bare_dir),
        )

    def test_plain_names_are_not_urls(self) -> None:
        """Ordinary branch names must never trip the URL detection."""
        from gh_wt import is_linear_issue_url

        assert not is_linear_issue_url("feature-branch")
        assert not is_linear_issue_url("billw/issue/foo")
        assert is_linear_issue_url("https://linear.app/modularml/issue/MKT-176/add-redirect")


class TestSetupScriptResolution:
    """Tests for hook resolution with and without the setup-script key."""

    def run_add(self, tmp_path: Path):
        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", side_effect=make_remote_listing_run_git({})):
                    with patch("gh_wt.subprocess.run") as mock_subprocess_run:
                        result = run_cli(["add", "feature-branch"])
        return result, mock_subprocess_run

    def test_configured_script_runs_from_repo_root(self, tmp_path: Path, monkeypatch) -> None:
        """A setup-script key resolves relative to the repo root."""
        (tmp_path / ".bare").mkdir()
        (tmp_path / "worktree-config.toml").write_text('setup-script = "worktree-setup.sh"\n')
        script = tmp_path / "worktree-setup.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        nested = tmp_path / "elsewhere"
        nested.mkdir()
        monkeypatch.chdir(nested)

        result, mock_subprocess_run = self.run_add(tmp_path)

        assert result.exit_code == 0
        mock_subprocess_run.assert_called_once_with(
            [str(script), "feature-branch"],
            cwd=str(tmp_path),
            check=True,
        )

    def test_configured_script_missing_errors(self, tmp_path: Path, monkeypatch) -> None:
        """A configured script that cannot run is a loud failure."""
        (tmp_path / ".bare").mkdir()
        (tmp_path / "worktree-config.toml").write_text('setup-script = "worktree-setup.sh"\n')
        monkeypatch.chdir(tmp_path)

        result, mock_subprocess_run = self.run_add(tmp_path)

        assert result.exit_code == 1
        assert "Error: setup-script 'worktree-setup.sh' not found or not executable" in result.output
        mock_subprocess_run.assert_not_called()

    def test_both_default_scripts_error_without_config(self, tmp_path: Path, monkeypatch) -> None:
        """Finding both default script names is ambiguous and must fail."""
        (tmp_path / ".bare").mkdir()
        for name in ("worktree-setup.sh", "setup-worktree.sh"):
            script = tmp_path / name
            script.write_text("#!/bin/sh\n")
            script.chmod(0o755)
        monkeypatch.chdir(tmp_path)

        result, mock_subprocess_run = self.run_add(tmp_path)

        assert result.exit_code == 1
        assert "Error: Found both worktree-setup.sh and setup-worktree.sh." in result.output
        mock_subprocess_run.assert_not_called()

    def test_new_default_script_name_runs(self, tmp_path: Path, monkeypatch) -> None:
        """worktree-setup.sh is found without any config."""
        (tmp_path / ".bare").mkdir()
        script = tmp_path / "worktree-setup.sh"
        script.write_text("#!/bin/sh\n")
        script.chmod(0o755)
        monkeypatch.chdir(tmp_path)

        result, mock_subprocess_run = self.run_add(tmp_path)

        assert result.exit_code == 0
        mock_subprocess_run.assert_called_once_with(
            [str(script), "feature-branch"],
            cwd=str(tmp_path),
            check=True,
        )

    def test_hint_printed_for_old_script_without_config(self, tmp_path: Path, monkeypatch) -> None:
        """The migration hint appears when only the old script name exists."""
        (tmp_path / ".bare").mkdir()
        script = tmp_path / "setup-worktree.sh"
        script.write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)

        result, _ = self.run_add(tmp_path)

        assert result.exit_code == 0
        assert "Hint: found setup-worktree.sh. Run 'gh wt init' to create worktree-config.toml and migrate." in result.output


class TestInitCommand:
    """Tests for gh wt init: migration and conversion."""

    def test_init_scaffolds_config_and_script_in_bare_layout(self, tmp_path: Path, monkeypatch) -> None:
        """init on a bare-layout repo without scripts scaffolds both files."""
        (tmp_path / ".bare").mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.run_git", return_value=""):
            with patch("builtins.input", return_value=""):
                result = run_cli(["init"])

        assert result.exit_code == 0
        assert (tmp_path / "worktree-setup.sh").is_file()
        config_text = (tmp_path / "worktree-config.toml").read_text()
        assert 'setup-script = "worktree-setup.sh"' in config_text
        assert '# branch-prefix = "billw"' in config_text

    def test_init_writes_entered_branch_prefix(self, tmp_path: Path, monkeypatch) -> None:
        """An entered prefix lands in the config uncommented, trailing slash stripped."""
        (tmp_path / ".bare").mkdir()
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.run_git", return_value=""):
            with patch("builtins.input", return_value="billw/"):
                result = run_cli(["init"])

        assert result.exit_code == 0
        assert 'branch-prefix = "billw"' in (tmp_path / "worktree-config.toml").read_text()

    def test_init_renames_old_script_when_confirmed(self, tmp_path: Path, monkeypatch) -> None:
        """Accepting the prompt renames setup-worktree.sh and points the config at it."""
        (tmp_path / ".bare").mkdir()
        old_script = tmp_path / "setup-worktree.sh"
        old_script.write_text("#!/bin/sh\ncustom\n")
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.run_git", return_value=""):
            with patch("builtins.input", side_effect=["y", ""]):
                result = run_cli(["init"])

        assert result.exit_code == 0
        assert "Renamed setup-worktree.sh to worktree-setup.sh." in result.output
        assert not old_script.exists()
        assert (tmp_path / "worktree-setup.sh").read_text() == "#!/bin/sh\ncustom\n"
        assert 'setup-script = "worktree-setup.sh"' in (tmp_path / "worktree-config.toml").read_text()

    def test_init_rename_defaults_to_yes_on_enter(self, tmp_path: Path, monkeypatch) -> None:
        """Pressing Enter at the rename prompt renames — migration is init's purpose."""
        (tmp_path / ".bare").mkdir()
        old_script = tmp_path / "setup-worktree.sh"
        old_script.write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.run_git", return_value=""):
            with patch("builtins.input", return_value=""):
                result = run_cli(["init"])

        assert result.exit_code == 0
        assert not old_script.exists()
        assert (tmp_path / "worktree-setup.sh").is_file()
        assert 'setup-script = "worktree-setup.sh"' in (tmp_path / "worktree-config.toml").read_text()

    def test_init_keeps_old_script_when_declined(self, tmp_path: Path, monkeypatch) -> None:
        """Declining the prompt keeps the old name and records it in the config."""
        (tmp_path / ".bare").mkdir()
        old_script = tmp_path / "setup-worktree.sh"
        old_script.write_text("#!/bin/sh\n")
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.run_git", return_value=""):
            with patch("builtins.input", side_effect=["n", ""]):
                result = run_cli(["init"])

        assert result.exit_code == 0
        assert "Keeping setup-worktree.sh." in result.output
        assert old_script.exists()
        assert 'setup-script = "setup-worktree.sh"' in (tmp_path / "worktree-config.toml").read_text()

    def test_init_reports_nothing_to_do(self, tmp_path: Path, monkeypatch) -> None:
        """init is a no-op when the config already exists."""
        (tmp_path / ".bare").mkdir()
        (tmp_path / "worktree-config.toml").write_text('branch-prefix = "billw"\n')
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.run_git", return_value=""):
            result = run_cli(["init"])

        assert result.exit_code == 0
        assert "Nothing to do: worktree-config.toml already exists." in result.output

    def test_init_errors_outside_git(self, tmp_path: Path, monkeypatch) -> None:
        """init outside any git repository fails."""
        monkeypatch.chdir(tmp_path)

        with patch("gh_wt.get_repo_root", return_value=None):
            with patch("gh_wt.run_git", return_value=""):
                result = run_cli(["init"])

        assert result.exit_code == 1
        assert "Error: Not a git repository" in result.output


def make_normal_clone(repo_root: Path) -> None:
    """Create a real single-commit git repo for conversion tests."""
    subprocess.run(["git", "init", "-b", "main", str(repo_root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo_root), "config", "user.name", "Test"], check=True)
    (repo_root / "file.txt").write_text("hello\n")
    subprocess.run(["git", "-C", str(repo_root), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_root), "commit", "-m", "initial"], check=True, capture_output=True)


class TestInitConversion:
    """Real-git tests for converting a normal clone to the bare layout."""

    def test_converts_clean_clone_in_place(self, tmp_path: Path, monkeypatch) -> None:
        """A clean normal clone becomes .bare/ plus a branch worktree."""
        repo_root = tmp_path / "myrepo"
        make_normal_clone(repo_root)
        monkeypatch.chdir(repo_root)

        with patch("builtins.input", side_effect=["y", ""]):
            result = run_cli(["init"])

        assert result.exit_code == 0
        assert "Converted to bare worktree layout." in result.output
        assert (repo_root / ".bare").is_dir()
        assert not (repo_root / ".git").exists()
        assert (repo_root / "main" / "file.txt").read_text() == "hello\n"
        assert (repo_root / "worktree-setup.sh").is_file()
        assert 'setup-script = "worktree-setup.sh"' in (repo_root / "worktree-config.toml").read_text()

    def test_conversion_aborts_on_dirty_tree(self, tmp_path: Path, monkeypatch) -> None:
        """Untracked files block conversion; the clone is left untouched."""
        repo_root = tmp_path / "myrepo"
        make_normal_clone(repo_root)
        (repo_root / "untracked.txt").write_text("wip\n")
        monkeypatch.chdir(repo_root)

        result = run_cli(["init"])

        assert result.exit_code == 1
        assert "Error: Working tree has uncommitted or untracked files." in result.output
        assert (repo_root / ".git").is_dir()
        assert not (repo_root / ".bare").exists()

    def test_conversion_declined_leaves_clone_untouched(self, tmp_path: Path, monkeypatch) -> None:
        """Declining the confirm prompt changes nothing."""
        repo_root = tmp_path / "myrepo"
        make_normal_clone(repo_root)
        monkeypatch.chdir(repo_root)

        with patch("builtins.input", return_value="n"):
            result = run_cli(["init"])

        assert result.exit_code == 0
        assert (repo_root / ".git").is_dir()
        assert not (repo_root / ".bare").exists()


class TestCurrentDirRemoved:
    """Tests for the guard that fires when the current directory is deleted."""

    def test_absolute_pwd_names_stale_path_and_escape_route(self, tmp_path: Path, monkeypatch) -> None:
        """The message names the vanished path and the nearest surviving ancestor."""
        stale = tmp_path / "gone"
        monkeypatch.setenv("PWD", str(stale))

        with patch("gh_wt.Path.cwd", side_effect=FileNotFoundError):
            result = run_cli(["add", "feature-branch"])

        assert result.exit_code == 1
        assert f"Error: Current directory no longer exists: {stale}" in result.output
        assert f"  Run 'cd {tmp_path}' and try again." in result.output
        assert "Traceback" not in result.output

    def test_deeply_deleted_chain_still_finds_a_surviving_ancestor(self, tmp_path: Path, monkeypatch) -> None:
        """Several missing levels degrade to the closest directory still on disk."""
        monkeypatch.setenv("PWD", str(tmp_path / "gone" / "deeper" / "deepest"))

        with patch("gh_wt.Path.cwd", side_effect=FileNotFoundError):
            result = run_cli(["status"])

        assert result.exit_code == 1
        assert f"  Run 'cd {tmp_path}' and try again." in result.output

    def test_missing_pwd_falls_back_to_home_guidance(self, monkeypatch) -> None:
        """Without a usable PWD the message omits the path and points home."""
        monkeypatch.delenv("PWD", raising=False)

        with patch("gh_wt.Path.cwd", side_effect=FileNotFoundError):
            result = run_cli(["list"])

        assert result.exit_code == 1
        assert "Error: Current directory no longer exists." in result.output
        assert "  Run 'cd' to return to your home directory, then try again." in result.output

    def test_empty_pwd_falls_back_to_home_guidance(self, monkeypatch) -> None:
        """An empty PWD is treated as unusable rather than as a bare path."""
        monkeypatch.setenv("PWD", "")

        with patch("gh_wt.Path.cwd", side_effect=FileNotFoundError):
            result = run_cli(["list"])

        assert result.exit_code == 1
        assert "Error: Current directory no longer exists." in result.output
        assert "  Run 'cd' to return to your home directory, then try again." in result.output

    @pytest.mark.parametrize("argv", [
        ["add", "feature-branch"],
        ["list"],
        ["rm", "feature-branch"],
        ["status"],
        ["init"],
        ["clone", "https://github.com/owner/repo.git"],
    ])
    def test_every_command_fails_friendly(self, argv: list[str], tmp_path: Path, monkeypatch) -> None:
        """No subcommand may leak a traceback when the current directory is gone."""
        monkeypatch.setenv("PWD", str(tmp_path / "gone"))

        with patch("gh_wt.Path.cwd", side_effect=FileNotFoundError):
            result = run_cli(argv)

        assert result.exit_code == 1
        assert "Error: Current directory no longer exists:" in result.output
        assert "Traceback" not in result.output

    def test_no_arguments_still_prints_help(self, monkeypatch) -> None:
        """Help needs no working directory, so it must survive a deleted one."""
        monkeypatch.setenv("PWD", "/definitely/gone")

        with patch("gh_wt.Path.cwd", side_effect=FileNotFoundError):
            result = run_cli([])

        assert result.exit_code == 0
        assert "usage:" in result.output.lower()
        assert "Current directory no longer exists" not in result.output

    def test_valid_current_directory_is_silent(self, tmp_path: Path, monkeypatch) -> None:
        """The guard must add no output during normal operation."""
        monkeypatch.chdir(tmp_path)
        buf = io.StringIO()

        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
            check_current_dir()

        assert buf.getvalue() == ""


class TestRemovedFolderHint:
    """Tests for the hint that fires when rm deletes the folder the shell is in."""

    def test_hint_when_shell_is_inside_removed_folder(self, tmp_path: Path, monkeypatch) -> None:
        """Removing the folder the shell occupies points the way out."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree = tmp_path / "feature"
        worktree.mkdir()
        monkeypatch.chdir(worktree)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git", return_value="feature"):
                with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                    with patch("gh_wt.remove_worktree"):
                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        assert f"Hint: your shell is inside the removed folder. Run 'cd {tmp_path.resolve()}' to continue." in result.output

    def test_hint_when_shell_is_in_a_subfolder_of_removed_folder(self, tmp_path: Path, monkeypatch) -> None:
        """Being nested deeper inside the removed worktree still strands the shell."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        nested = tmp_path / "feature" / "src" / "components"
        nested.mkdir(parents=True)
        monkeypatch.chdir(nested)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git", return_value="feature"):
                with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                    with patch("gh_wt.remove_worktree"):
                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        assert "Hint: your shell is inside the removed folder." in result.output

    def test_no_hint_when_shell_is_outside_removed_folder(self, tmp_path: Path, monkeypatch) -> None:
        """Removing an unrelated worktree leaves the shell alone."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        (tmp_path / "feature").mkdir()
        elsewhere = tmp_path / "main"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git", return_value="feature"):
                with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                    with patch("gh_wt.remove_worktree"):
                        result = run_cli(["rm", "feature"])

        assert result.exit_code == 0
        assert "Hint:" not in result.output

    def test_hint_when_merged_removes_the_folder_the_shell_is_in(self, tmp_path: Path, monkeypatch) -> None:
        """--merged sweeps several worktrees, so it must flag the stranding too."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        for name in ("feature-1", "feature-2"):
            (tmp_path / name).mkdir()
        monkeypatch.chdir(tmp_path / "feature-2")

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches") as mock_get_branches:
                with patch("gh_wt.get_pr_info") as mock_get_pr_info:
                    with patch("gh_wt.run_git"):
                        with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                            with patch("gh_wt.remove_worktree"):
                                mock_get_branches.return_value = [
                                    ("feature-1", "feature-1", str(tmp_path / "feature-1")),
                                    ("feature-2", "feature-2", str(tmp_path / "feature-2")),
                                ]
                                mock_get_pr_info.return_value = {"number": 1, "state": "MERGED", "url": "http://..."}

                                result = run_cli(["rm", "-m"])

        assert result.exit_code == 0
        assert f"Hint: your shell is inside the removed folder. Run 'cd {tmp_path.resolve()}' to continue." in result.output

    def test_no_hint_when_nothing_was_removed(self, tmp_path: Path, monkeypatch) -> None:
        """An empty removal list produces no hint."""
        monkeypatch.chdir(tmp_path)
        buf = io.StringIO()

        with contextlib.redirect_stdout(buf):
            hint_if_cwd_removed(tmp_path, [])

        assert buf.getvalue() == ""


def fake_git(responses: dict[str, str], default: str = ""):
    """Build a run_git side effect that dispatches on the git subcommand."""
    def run(args, cwd=None, check=True):
        command = " ".join(args)
        for prefix, response in responses.items():
            if command.startswith(prefix):
                return response
        return default
    return run


STACK_METADATA = {
    "schemaVersion": 1,
    "stacks": [
        {
            "number": 2331,
            "trunk": {"branch": "main"},
            "branches": [
                {"branch": "billw/MCL-133-01-api"},
                {"branch": "billw/MCL-133-02-lago"},
            ],
        }
    ],
}

STACK_VIEW = {
    "trunk": "main",
    "currentBranch": "billw/MCL-133-02-lago",
    "branches": [
        {
            "name": "billw/MCL-133-01-api",
            "isMerged": False,
            "needsRebase": False,
            "pr": {"number": 2326, "state": "OPEN"},
        },
        {
            "name": "billw/MCL-133-02-lago",
            "isMerged": False,
            "needsRebase": False,
            "pr": {"number": 2327, "state": "OPEN"},
        },
    ],
}


class TestStackMetadata:
    """Reading gh stack's per-worktree metadata off disk."""

    def test_reads_metadata_through_linked_worktree_gitdir_pointer(self, tmp_path: Path) -> None:
        """A linked worktree's .git file points at the git dir holding gh-stack."""
        git_dir = tmp_path / ".bare" / "worktrees" / "MCL-133"
        git_dir.mkdir(parents=True)
        (git_dir / "gh-stack").write_text(json.dumps(STACK_METADATA))

        worktree = tmp_path / "MCL-133"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {git_dir}\n")

        metadata = gh_wt.read_stack_metadata(worktree)

        assert metadata is not None
        assert gh_wt.stack_layers(metadata) == [
            "billw/MCL-133-01-api",
            "billw/MCL-133-02-lago",
        ]
        assert gh_wt.stack_number(metadata) == 2331

    def test_returns_none_without_stack_metadata(self, tmp_path: Path) -> None:
        """A worktree hosting no stack reads as None, not an error."""
        git_dir = tmp_path / ".bare" / "worktrees" / "plain"
        git_dir.mkdir(parents=True)
        worktree = tmp_path / "plain"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {git_dir}\n")

        assert gh_wt.read_stack_metadata(worktree) is None

    def test_invalid_metadata_is_treated_as_no_stack(self, tmp_path: Path) -> None:
        """Corrupt metadata must degrade to the ordinary worktree path."""
        git_dir = tmp_path / ".bare" / "worktrees" / "MCL-133"
        git_dir.mkdir(parents=True)
        (git_dir / "gh-stack").write_text("{not json")

        worktree = tmp_path / "MCL-133"
        worktree.mkdir()
        (worktree / ".git").write_text(f"gitdir: {git_dir}\n")

        assert gh_wt.read_stack_metadata(worktree) is None

    def test_layers_span_every_stack_in_the_file(self) -> None:
        """The metadata holds a list of stacks, so all of them count."""
        metadata = {
            "stacks": [
                {"branches": [{"branch": "a"}]},
                {"branches": [{"branch": "b"}, {"branch": "c"}]},
            ]
        }

        assert gh_wt.stack_layers(metadata) == ["a", "b", "c"]


class TestStackCommand:
    """gh wt stack converts the current worktree into a stack host."""

    def test_agent_flag_prints_the_split_guide(self) -> None:
        """Agents get the full procedure on demand, anywhere."""
        result = run_cli(["stack", "--agent"])

        assert result.exit_code == 0
        assert "splitting an oversized bottom layer" in result.output
        assert "Split by file path, not by commit" in result.output
        assert "Never\n   run 'gh stack rebase' directly" in result.output
        assert "gh wt stack --rebase" in result.output

    def test_refuses_to_stack_the_trunk_branch(self, tmp_path: Path) -> None:
        """Stacking trunk would create a nonsense main <- main stack."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git", side_effect=fake_git({
                "rev-parse --show-toplevel": str(tmp_path / "main"),
                "rev-parse --abbrev-ref": "main",
                "symbolic-ref": "origin/main",
            })):
                result = run_cli(["stack"])

        assert result.exit_code == 1
        assert "refusing to stack the trunk branch 'main'" in result.output
        assert "gh wt add <name>" in result.output

    def test_refuses_a_detached_worktree(self, tmp_path: Path) -> None:
        """gh stack needs a branch to adopt."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.run_git", side_effect=fake_git({
                "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                "rev-parse --abbrev-ref": "HEAD",
                "symbolic-ref": "origin/main",
            })):
                result = run_cli(["stack"])

        assert result.exit_code == 1
        assert "worktree is not on a branch" in result.output

    def test_refuses_a_worktree_that_already_hosts_a_stack(self, tmp_path: Path) -> None:
        """gh stack init is not idempotent, so report it before calling it."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                with patch("gh_wt.run_git", side_effect=fake_git({
                    "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                    "rev-parse --abbrev-ref": "billw/MCL-133",
                    "symbolic-ref": "origin/main",
                })):
                    result = run_cli(["stack"])

        assert result.exit_code == 1
        assert "MCL-133 already hosts a stack" in result.output

    def test_reports_a_missing_gh_stack_extension(self, tmp_path: Path) -> None:
        """The extension is a hard dependency; say how to install it."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=False):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                        "rev-parse --abbrev-ref": "billw/MCL-133",
                        "symbolic-ref": "origin/main",
                    })):
                        result = run_cli(["stack"])

        assert result.exit_code == 1
        assert "gh stack is not installed" in result.output
        assert "gh extension install github/gh-stack" in result.output

    def test_adopts_current_branch_and_enables_rerere(self, tmp_path: Path) -> None:
        """The happy path: rerere on, branch adopted, flat-naming warning shown."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree = tmp_path / "MCL-133"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.get_pr_info", return_value=None):
                     with patch("gh_wt.remote_branch_exists", return_value=False):
                      with patch("gh_wt.run_git", side_effect=fake_git({
                        "rev-parse --show-toplevel": str(worktree),
                        "rev-parse --abbrev-ref": "billw/MCL-133",
                        "symbolic-ref": "origin/main",
                        "rev-list --count": "1",
                      })) as mock_run_git:
                       with patch("subprocess.run") as mock_subprocess:
                            mock_subprocess.return_value = MagicMock(
                                returncode=0, stdout="", stderr=""
                            )
                            result = run_cli(["stack"])

        assert result.exit_code == 0
        assert "Enabling rerere..." in result.output
        assert "Renamed billw/MCL-133 to billw/MCL-133/01" in result.output
        assert "Moved worktree to MCL-133/01" in result.output
        assert "Stack created: main <- billw/MCL-133/01" in result.output
        assert "gh wt stack --add <name>" in result.output
        assert "cd MCL-133/01" in result.output

        mock_run_git.assert_any_call(
            ["config", "rerere.enabled", "true"], cwd=str(bare_dir)
        )
        mock_run_git.assert_any_call(
            ["branch", "-m", "billw/MCL-133", "billw/MCL-133/01"], cwd=str(bare_dir)
        )
        mock_subprocess.assert_any_call(
            ["gh", "stack", "init", "billw/MCL-133/01"],
            cwd=str(worktree / "01"),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_leaves_rerere_alone_when_already_enabled(self, tmp_path: Path) -> None:
        """Re-enabling an enabled setting should be silent."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                        "rev-parse --abbrev-ref": "billw/MCL-133",
                        "symbolic-ref": "origin/main",
                        "config --get rerere.enabled": "true",
                        "rev-list --count": "1",
                    })):
                        with patch("subprocess.run") as mock_subprocess:
                            mock_subprocess.return_value = MagicMock(
                                returncode=0, stdout="", stderr=""
                            )
                            result = run_cli(["stack"])

        assert result.exit_code == 0
        assert "Enabling rerere..." not in result.output

    def test_warns_and_prompts_agents_when_bottom_layer_is_oversized(self, tmp_path: Path) -> None:
        """A captured stdout means an agent is reading, so offer the split."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.output_is_piped", return_value=True):
                        with patch("gh_wt.run_git", side_effect=fake_git({
                            "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                            "rev-parse --abbrev-ref": "billw/MCL-133",
                            "symbolic-ref": "origin/main",
                            "rev-list --count": "12",
                            "diff --shortstat": " 76 files changed, 2582 insertions(+), 329 deletions(-)",
                        })):
                            with patch("subprocess.run") as mock_subprocess:
                                mock_subprocess.return_value = MagicMock(
                                    returncode=0, stdout="", stderr=""
                                )
                                result = run_cli(["stack"])

        assert result.exit_code == 0
        assert "Note: this branch is 12 commits / 2911 lines." in result.output
        assert "Agents: this branch is likely too large for one review." in result.output
        assert "gh wt stack --agent" in result.output

    def test_hides_the_agent_block_from_a_human_terminal(self, tmp_path: Path) -> None:
        """A TTY means a human is reading; the warning stays, the prompt does not."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.output_is_piped", return_value=False):
                        with patch("gh_wt.run_git", side_effect=fake_git({
                            "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                            "rev-parse --abbrev-ref": "billw/MCL-133",
                            "symbolic-ref": "origin/main",
                            "rev-list --count": "12",
                            "diff --shortstat": " 76 files changed, 2582 insertions(+), 329 deletions(-)",
                        })):
                            with patch("subprocess.run") as mock_subprocess:
                                mock_subprocess.return_value = MagicMock(
                                    returncode=0, stdout="", stderr=""
                                )
                                result = run_cli(["stack"])

        assert result.exit_code == 0
        assert "Note: this branch is 12 commits / 2911 lines." in result.output
        assert "Agents:" not in result.output

    def test_small_branch_gets_no_size_warning(self, tmp_path: Path) -> None:
        """Converting early is the good case and should stay quiet."""
        (tmp_path / ".bare").mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.output_is_piped", return_value=True):
                        with patch("gh_wt.run_git", side_effect=fake_git({
                            "rev-parse --show-toplevel": str(tmp_path / "MCL-133"),
                            "rev-parse --abbrev-ref": "billw/MCL-133",
                            "symbolic-ref": "origin/main",
                            "rev-list --count": "2",
                            "diff --shortstat": " 3 files changed, 40 insertions(+), 5 deletions(-)",
                        })):
                            with patch("subprocess.run") as mock_subprocess:
                                mock_subprocess.return_value = MagicMock(
                                    returncode=0, stdout="", stderr=""
                                )
                                result = run_cli(["stack"])

        assert result.exit_code == 0
        assert "too large for one review" not in result.output
        assert "Agents:" not in result.output


class TestStackGuardrailOnAdd:
    """gh wt add must not strand a stack layer in its own worktree."""

    def test_add_refuses_a_branch_that_is_a_stack_layer(self, tmp_path: Path) -> None:
        """Git allows this and fails later in gh stack rebase, so refuse up front."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch(
                    "gh_wt.find_stack_worktree_for_branch",
                    return_value=(tmp_path / "MCL-133" / "01-api", 1),
                ):
                    with patch("gh_wt.run_git", return_value=""):
                        result = run_cli(["add", "billw/MCL-133/01-api"])

        assert result.exit_code == 1
        assert "layer 1 of the stack in MCL-133/" in result.output
        assert "It already has a worktree" in result.output
        assert "cd MCL-133/01-api" in result.output

    def test_add_is_unaffected_when_no_stack_claims_the_branch(self, tmp_path: Path) -> None:
        """Ordinary worktrees keep working exactly as before."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_default_branch_name", return_value="main"):
                with patch("gh_wt.run_git", return_value="") as mock_run_git:
                    result = run_cli(["add", "plain-feature"])

        assert result.exit_code == 0
        mock_run_git.assert_any_call(
            ["worktree", "add", "-b", "plain-feature", str(tmp_path / "plain-feature"), "origin/main"],
            cwd=str(bare_dir),
        )

    def test_finds_the_worktree_hosting_a_layer(self, tmp_path: Path) -> None:
        """Layer lookup reports both the hosting worktree and the layer number."""
        worktrees = [("MCL-133", "billw/MCL-133-02-lago", str(tmp_path / "MCL-133"))]

        with patch("gh_wt.get_worktree_branches", return_value=worktrees):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                found = gh_wt.find_stack_worktree_for_branch(tmp_path, "billw/MCL-133-02-lago")

        assert found == (tmp_path / "MCL-133", 2)


class TestStackStatus:
    """gh wt status renders a stack worktree as its layers."""

    def test_status_lists_every_layer_with_pr_state(self, tmp_path: Path) -> None:
        """One worktree holding a stack should show all of its layers."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree_output = f"{tmp_path}/MCL-133\t\t\t(billw/MCL-133-02-lago)"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                with patch("gh_wt.get_stack_view", return_value=STACK_VIEW):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "worktree list": worktree_output,
                        "rev-parse --abbrev-ref": "billw/MCL-133-02-lago",
                        "status --porcelain": "",
                    })):
                        result = run_cli(["status"])

        assert result.exit_code == 0
        assert "Stack: #2331 - 2 layers, on layer 2" in result.output
        assert "1  billw/MCL-133-01-api  #2326 (OPEN)" in result.output
        assert "2  billw/MCL-133-02-lago #2327 (OPEN)   <- current" in result.output

    def test_status_marks_a_layer_needing_rebase_as_needing_attention(self, tmp_path: Path) -> None:
        """A stale layer should trip the existing non-zero exit."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        stale_view = json.loads(json.dumps(STACK_VIEW))
        stale_view["branches"][0]["needsRebase"] = True

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                with patch("gh_wt.get_stack_view", return_value=stale_view):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "worktree list": f"{tmp_path}/MCL-133\t\t\t(billw/MCL-133-02-lago)",
                        "rev-parse --abbrev-ref": "billw/MCL-133-02-lago",
                        "status --porcelain": "",
                    })):
                        result = run_cli(["status"])

        assert result.exit_code == 1
        assert "<- needs rebase" in result.output

    def test_status_shows_layers_without_prs_before_submit(self, tmp_path: Path) -> None:
        """Offline or pre-submit, gh stack omits the pr key entirely."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        unsubmitted = {
            "trunk": "main",
            "currentBranch": "billw/MCL-133-01-api",
            "branches": [{"name": "billw/MCL-133-01-api", "isMerged": False, "needsRebase": False}],
        }

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value={"stacks": [{"branches": []}]}):
                with patch("gh_wt.get_stack_view", return_value=unsubmitted):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "worktree list": f"{tmp_path}/MCL-133\t\t\t(billw/MCL-133-01-api)",
                        "rev-parse --abbrev-ref": "billw/MCL-133-01-api",
                        "status --porcelain": "",
                    })):
                        result = run_cli(["status"])

        assert result.exit_code == 0
        assert "(no PR)" in result.output
        assert "Stack: 1 layer," in result.output

    def test_status_falls_back_when_the_stack_view_is_unavailable(self, tmp_path: Path) -> None:
        """A failed gh stack view must not break status."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                with patch("gh_wt.get_stack_view", return_value=None):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "worktree list": f"{tmp_path}/MCL-133\t\t\t(billw/MCL-133-02-lago)",
                        "rev-parse --abbrev-ref": "billw/MCL-133-02-lago",
                        "status --porcelain": "",
                        "rev-list --count": "0",
                        "ls-remote": "abc123\trefs/heads/billw/MCL-133-02-lago",
                    })):
                        with patch("subprocess.run") as mock_subprocess:
                            mock_subprocess.return_value = MagicMock(stdout="", returncode=1)
                            result = run_cli(["status"])

        assert result.exit_code == 0
        assert "Branch: billw/MCL-133-02-lago" in result.output
        assert "Layers:" not in result.output


class TestStackRemoval:
    """gh wt rm treats a stack worktree as all-or-nothing across its layers."""

    def test_merged_removal_skips_a_stack_with_an_open_layer(self, tmp_path: Path) -> None:
        """One open PR anywhere in the stack keeps the whole worktree."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktrees = [("MCL-133", "billw/MCL-133-02-lago", str(tmp_path / "MCL-133"))]
        states = {"billw/MCL-133-01-api": "MERGED", "billw/MCL-133-02-lago": "OPEN"}

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches", return_value=worktrees):
                with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                    with patch("gh_wt.get_pr_info", side_effect=lambda b: {"state": states[b]}):
                        with patch("gh_wt.remove_worktree") as mock_remove:
                            with patch("gh_wt.run_git", return_value=""):
                                result = run_cli(["rm", "--merged"])

        assert result.exit_code == 0
        assert "No merged or closed worktrees to remove" in result.output
        mock_remove.assert_not_called()

    def test_merged_removal_deletes_every_layer_branch(self, tmp_path: Path) -> None:
        """Once the whole stack has landed, no layer branch should be stranded."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktrees = [("MCL-133", "billw/MCL-133-02-lago", str(tmp_path / "MCL-133"))]

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.get_worktree_branches", return_value=worktrees):
                with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                    with patch("gh_wt.get_pr_info", return_value={"state": "MERGED"}):
                        with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                            with patch("gh_wt.remove_worktree", return_value=True):
                                with patch("gh_wt.run_git", return_value="") as mock_run_git:
                                    result = run_cli(["rm", "--merged"])

        assert result.exit_code == 0
        assert "Removing stack worktree MCL-133 (2 layers)." in result.output
        mock_run_git.assert_any_call(
            ["branch", "-D", "billw/MCL-133-01-api"], cwd=str(bare_dir), check=False
        )
        mock_run_git.assert_any_call(
            ["branch", "-D", "billw/MCL-133-02-lago"], cwd=str(bare_dir), check=False
        )

    def test_single_removal_names_the_layer_holding_unpushed_work(self, tmp_path: Path) -> None:
        """The blocked layer may not be the one checked out, so name it."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree = tmp_path / "MCL-133"
        worktree.mkdir()
        safe = {"billw/MCL-133-01-api": True, "billw/MCL-133-02-lago": False}

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                with patch("gh_wt.is_branch_safe_to_delete", side_effect=lambda b, _: safe[b]):
                    with patch("gh_wt.remove_worktree") as mock_remove:
                        with patch("gh_wt.run_git", return_value="billw/MCL-133-02-lago"):
                            result = run_cli(["rm", "MCL-133"])

        assert result.exit_code == 1
        assert "layer 2 (billw/MCL-133-02-lago) has unpushed commits" in result.output
        assert "Use --force to override." in result.output
        mock_remove.assert_not_called()

    def test_single_removal_deletes_all_layers_with_delete_remote(self, tmp_path: Path) -> None:
        """-d should clear every layer from origin, not just the current one."""
        bare_dir = tmp_path / ".bare"
        bare_dir.mkdir()
        worktree = tmp_path / "MCL-133"
        worktree.mkdir()

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=STACK_METADATA):
                with patch("gh_wt.is_branch_safe_to_delete", return_value=True):
                    with patch("gh_wt.remove_worktree", return_value=True):
                        with patch("gh_wt.delete_remote_branch") as mock_delete_remote:
                            with patch("gh_wt.run_git", return_value="billw/MCL-133-02-lago"):
                                result = run_cli(["rm", "MCL-133", "-d"])

        assert result.exit_code == 0
        assert "Removing stack worktree MCL-133 (2 layers)." in result.output
        assert mock_delete_remote.call_count == 2
        mock_delete_remote.assert_any_call("billw/MCL-133-01-api", bare_dir)
        mock_delete_remote.assert_any_call("billw/MCL-133-02-lago", bare_dir)


class TestLayerNaming:
    """Layer folders and branches spell the same thing (B1 nested naming)."""

    def test_unnamed_layer_is_just_its_number(self) -> None:
        assert gh_wt.layer_folder_name(1) == "01"

    def test_named_layer_carries_its_number(self) -> None:
        assert gh_wt.layer_folder_name(2, "api") == "02-api"

    def test_numbers_past_nine_keep_two_digits(self) -> None:
        assert gh_wt.layer_folder_name(10, "ui") == "10-ui"

    def test_branch_nests_the_folder_under_the_stack_root(self) -> None:
        assert gh_wt.layer_branch_name("billw/some-feature", "02-api") == (
            "billw/some-feature/02-api"
        )

    def test_stack_root_drops_the_layer_segment(self) -> None:
        assert gh_wt.stack_branch_root("billw/some-feature/02-api") == "billw/some-feature"

    def test_stack_root_of_an_unprefixed_branch(self) -> None:
        assert gh_wt.stack_branch_root("some-feature/01") == "some-feature"


# --- Integration: real git and real gh stack, network calls stubbed ----------

GH_STACK_AVAILABLE = gh_wt.is_gh_stack_installed()

requires_gh_stack = pytest.mark.skipif(
    not GH_STACK_AVAILABLE, reason="gh stack extension is not installed"
)


def git(*args: str, cwd: Path) -> str:
    """Run real git in cwd and return stdout."""
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


@dataclass
class StackRepo:
    """A real bare-layout repo with a feature worktree, ready to convert."""

    root: Path
    bare: Path
    worktree: Path
    branch: str
    origin: Path

    def branches(self) -> list[str]:
        listing = git("branch", "--format=%(refname:short)", cwd=self.bare)
        return sorted(line for line in listing.split("\n") if line)

    def worktree_paths(self) -> list[str]:
        listing = git("worktree", "list", "--porcelain", cwd=self.bare)
        paths = (
            line.split(" ", 1)[1]
            for line in listing.split("\n")
            if line.startswith("worktree ")
        )
        return sorted(path for path in paths if Path(path) != self.bare)


@pytest.fixture
def stack_repo(tmp_path: Path) -> StackRepo:
    """Build origin + a bare-layout clone holding main/ and some-feature/."""
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"

    git("init", "-q", "-b", "main", str(seed), cwd=tmp_path)
    (seed / "f.txt").write_text("base\n")
    git("add", "-A", cwd=seed)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base", cwd=seed)
    git("clone", "-q", "--bare", str(seed), str(origin), cwd=tmp_path)

    root = tmp_path / "repo"
    root.mkdir()
    bare = root / ".bare"
    git("clone", "-q", "--bare", str(origin), str(bare), cwd=tmp_path)
    git("config", "remote.origin.fetch", gh_wt.REMOTE_TRACKING_REFSPEC, cwd=bare)
    git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main", cwd=bare)
    git("fetch", "-q", "origin", cwd=bare)

    git("worktree", "add", "-q", str(root / "main"), "main", cwd=bare)

    branch = "billw/some-feature"
    git("worktree", "add", "-q", "-b", branch, str(root / "some-feature"), "main", cwd=bare)
    git("push", "-q", "-u", "origin", branch, cwd=root / "some-feature")

    (root / gh_wt.CONFIG_FILE_NAME).write_text('branch-prefix = "billw"\n')

    return StackRepo(
        root=root,
        bare=bare,
        worktree=root / "some-feature",
        branch=branch,
        origin=origin,
    )


@requires_gh_stack
class TestConvertIntegration:
    """gh wt stack converts in place against a real repo."""

    def test_names_layer_one_and_moves_the_worktree(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)

        result = run_cli(["stack", "base"])

        assert result.exit_code == 0
        assert stack_repo.worktree_paths() == sorted(
            [
                str(stack_repo.root / "main"),
                str(stack_repo.root / "some-feature" / "01-base"),
            ]
        )
        assert "billw/some-feature/01-base" in stack_repo.branches()
        assert "billw/some-feature" not in stack_repo.branches()

    def test_unnamed_conversion_uses_a_bare_number(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)

        result = run_cli(["stack"])

        assert result.exit_code == 0
        assert (stack_repo.root / "some-feature" / "01").is_dir()
        assert "billw/some-feature/01" in stack_repo.branches()


class TestConvertPrGate:
    """Conversion renames layer 1, which closes a PR that branch heads."""

    prompts: list[str]

    def _convert(self, tmp_path: Path, pr_info, args, piped=True, reply="n"):
        self.prompts = []

        def fake_input(prompt: str = "") -> str:
            self.prompts.append(prompt)
            return reply

        (tmp_path / ".bare").mkdir()
        worktree = tmp_path / "MCL-133"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.get_pr_info", return_value=pr_info):
                        with patch("gh_wt.output_is_piped", return_value=piped):
                            with patch("gh_wt.remote_branch_exists", return_value=False):
                                with patch("builtins.input", fake_input):
                                    with patch("gh_wt.run_git", side_effect=fake_git({
                                        "rev-parse --show-toplevel": str(worktree),
                                        "rev-parse --abbrev-ref": "billw/MCL-133",
                                        "symbolic-ref": "origin/main",
                                        "rev-list --count": "1",
                                    })):
                                        with patch("subprocess.run") as sub:
                                            sub.return_value = MagicMock(
                                                returncode=0, stdout="", stderr=""
                                            )
                                            return run_cli(args)

    def test_piped_run_refuses_when_a_pr_is_open(self, tmp_path: Path) -> None:
        """An agent must never close a PR by omission."""
        result = self._convert(tmp_path, {"number": 123, "state": "OPEN"}, ["stack"])

        assert result.exit_code == 1
        assert "has an open PR (#123); renaming it will close that PR" in result.output
        assert "--force" in result.output

    def test_force_converts_despite_an_open_pr(self, tmp_path: Path) -> None:
        result = self._convert(
            tmp_path, {"number": 123, "state": "OPEN"}, ["stack", "-f"]
        )

        assert result.exit_code == 0
        assert "Renamed billw/MCL-133 to billw/MCL-133/01" in result.output

    def test_terminal_run_asks_and_aborts_on_no(self, tmp_path: Path) -> None:
        result = self._convert(
            tmp_path, {"number": 123, "state": "OPEN"}, ["stack"], piped=False, reply="n"
        )

        assert result.exit_code == 1
        assert self.prompts == ["Convert anyway? [y/N] "]
        assert "Aborted." in result.output

    def test_terminal_run_proceeds_on_yes(self, tmp_path: Path) -> None:
        result = self._convert(
            tmp_path, {"number": 123, "state": "OPEN"}, ["stack"], piped=False, reply="y"
        )

        assert result.exit_code == 0
        assert "Renamed billw/MCL-133 to billw/MCL-133/01" in result.output

    def test_a_merged_pr_does_not_gate(self, tmp_path: Path) -> None:
        """Only an open PR can be closed by the rename."""
        result = self._convert(tmp_path, {"number": 9, "state": "MERGED"}, ["stack"])

        assert result.exit_code == 0

    def test_reports_an_interrupted_move(self, tmp_path: Path) -> None:
        """A conversion that died between moves is named, not guessed at."""
        (tmp_path / ".bare").mkdir()
        (tmp_path / ".MCL-133.tmp").mkdir()
        worktree = tmp_path / "MCL-133"

        with patch("gh_wt.get_repo_root", return_value=tmp_path):
            with patch("gh_wt.read_stack_metadata", return_value=None):
                with patch("gh_wt.is_gh_stack_installed", return_value=True):
                    with patch("gh_wt.run_git", side_effect=fake_git({
                        "rev-parse --show-toplevel": str(worktree),
                        "rev-parse --abbrev-ref": "billw/MCL-133",
                        "symbolic-ref": "origin/main",
                    })):
                        result = run_cli(["stack"])

        assert result.exit_code == 1
        assert "a previous conversion left a worktree at .MCL-133.tmp" in result.output
        assert "git worktree move .MCL-133.tmp MCL-133" in result.output


@requires_gh_stack
class TestAddLayerIntegration:
    """gh wt stack --add grows a real stack, one layer per worktree."""

    def _convert(self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)
        assert run_cli(["stack", "base"]).exit_code == 0
        layer_one = stack_repo.worktree / "01-base"
        monkeypatch.chdir(layer_one)
        return layer_one

    def test_second_layer_gets_its_own_worktree(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._convert(stack_repo, monkeypatch)

        result = run_cli(["stack", "--add", "api"])

        assert result.exit_code == 0
        assert (stack_repo.worktree / "02-api").is_dir()
        assert "billw/some-feature/02-api" in stack_repo.branches()

    def test_host_stays_on_layer_one_after_adding(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The host must hold a branch or gh stack view stops working."""
        layer_one = self._convert(stack_repo, monkeypatch)

        run_cli(["stack", "--add", "api"])

        assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=layer_one) == (
            "billw/some-feature/01-base"
        )

    def test_third_layer_releases_and_restores_the_layer_above(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Adding past layer 2 needs the top layer's branch freed and put back."""
        self._convert(stack_repo, monkeypatch)
        assert run_cli(["stack", "--add", "api"]).exit_code == 0

        result = run_cli(["stack", "--add", "ui"])

        assert result.exit_code == 0
        assert (stack_repo.worktree / "03-ui").is_dir()
        assert git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo.worktree / "02-api"
        ) == "billw/some-feature/02-api"
        assert git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo.worktree / "03-ui"
        ) == "billw/some-feature/03-ui"

    def test_uncommitted_work_in_a_layer_survives_the_dance(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An agent mid-edit in layer 2 must not lose anything when layer 3 lands."""
        self._convert(stack_repo, monkeypatch)
        run_cli(["stack", "--add", "api"])
        (stack_repo.worktree / "02-api" / "f.txt").write_text("agent edit\n")

        assert run_cli(["stack", "--add", "ui"]).exit_code == 0

        assert (stack_repo.worktree / "02-api" / "f.txt").read_text() == "agent edit\n"
        assert "f.txt" in git(
            "status", "--short", cwd=stack_repo.worktree / "02-api"
        )

    def test_layers_are_numbered_from_the_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Conversion and --add share one numbering source."""
        self._convert(stack_repo, monkeypatch)
        run_cli(["stack", "--add", "api"])
        run_cli(["stack", "--add", "ui"])

        assert sorted(p.name for p in stack_repo.worktree.iterdir()) == [
            "01-base", "02-api", "03-ui",
        ]

    def test_refuses_a_duplicate_layer_name(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._convert(stack_repo, monkeypatch)
        run_cli(["stack", "--add", "api"])

        result = run_cli(["stack", "--add", "api"])

        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_refuses_outside_a_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(stack_repo.worktree)

        result = run_cli(["stack", "--add", "api"])

        assert result.exit_code == 1
        assert "not inside a stack" in result.output


@requires_gh_stack
class TestRenameLayerIntegration:
    """gh wt stack --rename keeps folder, branch, and stack metadata in step."""

    def _stack(self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch) -> Path:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)
        assert run_cli(["stack"]).exit_code == 0
        layer_one = stack_repo.worktree / "01"
        monkeypatch.chdir(layer_one)
        return layer_one

    def test_renames_folder_branch_and_metadata_together(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stack(stack_repo, monkeypatch)

        result = run_cli(["stack", "--rename", "01", "01-base"])

        assert result.exit_code == 0
        assert (stack_repo.worktree / "01-base").is_dir()
        assert not (stack_repo.worktree / "01").exists()
        assert "billw/some-feature/01-base" in stack_repo.branches()
        assert "billw/some-feature/01" not in stack_repo.branches()

        metadata = gh_wt.read_stack_metadata(stack_repo.worktree / "01-base")
        assert gh_wt.stack_layers(metadata) == ["billw/some-feature/01-base"]

    def test_renames_a_layer_above_the_host(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stack(stack_repo, monkeypatch)
        assert run_cli(["stack", "--add", "api"]).exit_code == 0

        result = run_cli(["stack", "--rename", "02-api", "02-service"])

        assert result.exit_code == 0
        assert (stack_repo.worktree / "02-service").is_dir()
        assert git(
            "rev-parse", "--abbrev-ref", "HEAD", cwd=stack_repo.worktree / "02-service"
        ) == "billw/some-feature/02-service"
        metadata = gh_wt.read_stack_metadata(stack_repo.worktree / "01")
        assert gh_wt.stack_layers(metadata)[-1] == "billw/some-feature/02-service"

    def test_refuses_an_unknown_layer(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stack(stack_repo, monkeypatch)

        result = run_cli(["stack", "--rename", "07", "07-nope"])

        assert result.exit_code == 1
        assert "no layer named '07'" in result.output

    def test_refuses_a_name_already_taken(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._stack(stack_repo, monkeypatch)
        run_cli(["stack", "--add", "api"])

        result = run_cli(["stack", "--rename", "01", "02-api"])

        assert result.exit_code == 1
        assert "already exists" in result.output

    def test_open_pr_gates_the_rename(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same gate as conversion: a piped run must not close a PR."""
        self._stack(stack_repo, monkeypatch)
        monkeypatch.setattr(
            gh_wt, "get_pr_info", lambda branch: {"number": 77, "state": "OPEN"}
        )

        result = run_cli(["stack", "--rename", "01", "01-base"])

        assert result.exit_code == 1
        assert "has an open PR (#77)" in result.output
        assert (stack_repo.worktree / "01").is_dir()


@requires_gh_stack
class TestStackRemovalIntegration:
    """Layer worktrees must never be removed, or swept, one at a time."""

    def _two_layer_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> Path:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)
        assert run_cli(["stack", "base"]).exit_code == 0
        layer_one = stack_repo.worktree / "01-base"
        monkeypatch.chdir(layer_one)
        assert run_cli(["stack", "--add", "api"]).exit_code == 0
        return layer_one

    def test_removing_a_single_layer_is_refused(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._two_layer_stack(stack_repo, monkeypatch)

        result = run_cli(["rm", "02-api"])

        assert result.exit_code == 1
        assert "02-api is layer 2 of the stack in some-feature/" in result.output
        assert "gh wt rm some-feature" in result.output
        assert "billw/some-feature/02-api" in stack_repo.branches()
        assert (stack_repo.worktree / "02-api").is_dir()

    def test_merged_sweep_leaves_a_stack_with_an_open_layer_intact(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The regression: sweeping a stack layer by layer as each PR lands."""
        self._two_layer_stack(stack_repo, monkeypatch)
        monkeypatch.setattr(
            gh_wt, "is_pr_removable", lambda branch: branch.endswith("01-base")
        )
        monkeypatch.setattr(gh_wt, "is_branch_safe_to_delete", lambda b, d: True)

        result = run_cli(["rm", "--merged"])

        assert result.exit_code == 0
        assert "billw/some-feature/01-base" in stack_repo.branches()
        assert "billw/some-feature/02-api" in stack_repo.branches()
        assert (stack_repo.worktree / "01-base").is_dir()
        assert (stack_repo.worktree / "02-api").is_dir()

    def test_merged_sweep_takes_the_stack_once_every_layer_lands(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._two_layer_stack(stack_repo, monkeypatch)
        monkeypatch.setattr(gh_wt, "is_pr_removable", lambda branch: True)
        monkeypatch.setattr(gh_wt, "is_branch_safe_to_delete", lambda b, d: True)

        result = run_cli(["rm", "--merged"])

        assert result.exit_code == 0
        assert "Removing stack some-feature (2 layers)." in result.output
        assert not (stack_repo.root / "some-feature").exists()
        assert "billw/some-feature/01-base" not in stack_repo.branches()
        assert "billw/some-feature/02-api" not in stack_repo.branches()

    def test_removing_the_container_takes_the_whole_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._two_layer_stack(stack_repo, monkeypatch)
        monkeypatch.chdir(stack_repo.root)
        monkeypatch.setattr(gh_wt, "is_branch_safe_to_delete", lambda b, d: True)

        result = run_cli(["rm", "some-feature", "-f"])

        assert result.exit_code == 0
        assert "Removing stack some-feature (2 layers)." in result.output
        assert not (stack_repo.root / "some-feature").exists()
        assert "billw/some-feature/01-base" not in stack_repo.branches()
        assert "billw/some-feature/02-api" not in stack_repo.branches()


@requires_gh_stack
class TestRebaseIntegration:
    """gh stack rebase cannot run with layer worktrees held; the envelope frees them."""

    def _two_layer_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> Path:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)
        assert run_cli(["stack", "base"]).exit_code == 0
        layer_one = stack_repo.worktree / "01-base"
        monkeypatch.chdir(layer_one)
        assert run_cli(["stack", "--add", "api"]).exit_code == 0
        return layer_one

    def test_bare_gh_stack_rebase_is_blocked_by_a_layer_worktree(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The wall the envelope exists to get around."""
        layer_one = self._two_layer_stack(stack_repo, monkeypatch)

        result = subprocess.run(
            ["gh", "stack", "rebase", "--no-trunk"],
            cwd=str(layer_one), capture_output=True, text=True, check=False,
        )

        assert result.returncode != 0
        assert "already used by worktree" in (result.stdout + result.stderr)

    def test_rebase_cascades_with_layer_worktrees_present(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        layer_one = self._two_layer_stack(stack_repo, monkeypatch)
        (layer_one / "base.txt").write_text("layer one work\n")
        git("add", "-A", cwd=layer_one)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base work",
            cwd=layer_one)

        result = run_cli(["stack", "--rebase"])

        assert result.exit_code == 0
        assert "Rebasing stack..." in result.output

    def test_every_layer_ends_back_on_its_branch(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._two_layer_stack(stack_repo, monkeypatch)

        assert run_cli(["stack", "--rebase"]).exit_code == 0

        for folder, branch in (
            ("01-base", "billw/some-feature/01-base"),
            ("02-api", "billw/some-feature/02-api"),
        ):
            path = stack_repo.worktree / folder
            assert git("rev-parse", "--abbrev-ref", "HEAD", cwd=path) == branch

    def test_uncommitted_work_survives_the_cascade(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._two_layer_stack(stack_repo, monkeypatch)
        (stack_repo.worktree / "02-api" / "f.txt").write_text("agent edit\n")

        assert run_cli(["stack", "--rebase"]).exit_code == 0

        assert (stack_repo.worktree / "02-api" / "f.txt").read_text() == "agent edit\n"

    def test_refuses_outside_a_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(stack_repo.worktree)

        result = run_cli(["stack", "--rebase"])

        assert result.exit_code == 1
        assert "not inside a stack" in result.output


@requires_gh_stack
class TestStackStatusIntegration:
    """A stack reads as one entry, not as one entry per layer worktree."""

    def _two_layer_stack(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(stack_repo.worktree)
        monkeypatch.setattr(gh_wt, "get_pr_info", lambda branch: None)
        monkeypatch.setattr(gh_wt, "rename_remote_branch", lambda old, new, cwd: True)
        assert run_cli(["stack", "base"]).exit_code == 0
        monkeypatch.chdir(stack_repo.worktree / "01-base")
        assert run_cli(["stack", "--add", "api"]).exit_code == 0

    def test_status_shows_one_entry_per_stack_with_layer_folders(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._two_layer_stack(stack_repo, monkeypatch)
        monkeypatch.chdir(stack_repo.root)

        result = run_cli(["status"])

        assert "some-feature\n" in result.output
        assert "Stack: 2 layers" in result.output
        assert "01-base" in result.output
        assert "02-api" in result.output

    def test_layer_worktrees_are_not_listed_separately(
        self, stack_repo: StackRepo, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Each layer must appear under its stack, not as a worktree of its own."""
        self._two_layer_stack(stack_repo, monkeypatch)
        monkeypatch.chdir(stack_repo.root)

        result = run_cli(["status"])

        headings = [line for line in result.output.split("\n") if line and not line[0].isspace()]
        assert "02-api" not in headings
        assert "01-base" not in headings
        assert "some-feature" in headings
