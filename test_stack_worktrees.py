"""Public CLI contracts using isolated Git repositories and pinned gh-stack.

The container deliberately has no GitHub credentials or network at test runtime.
Native tracking is created by public `gh stack init`, never private catalog writes.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

from test_gh_wt import run_cli


def git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", *args],
        cwd=cwd, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def stack(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["gh", "stack", *args], cwd=cwd, capture_output=True, text=True)
    if check:
        assert result.returncode == 0, result.stdout + result.stderr
    return result


@dataclass
class Repo:
    root: Path
    bare: Path
    owner: Path
    main: Path
    origin: Path

    def branches(self) -> str:
        return git(self.bare, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")

    def owners(self) -> str:
        return git(self.bare, "worktree", "list", "--porcelain")

    def view(self, path: Path | None = None) -> dict:
        return json.loads(stack(path or self.owner, "view", "--json").stdout)

    def hook(self, fail: bool = False) -> Path:
        log = self.root / "hook.log"
        script = self.root / "worktree-setup.sh"
        script.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "' + str(log) + '"\n' + ("exit 17\n" if fail else ""))
        script.chmod(0o755)
        (self.root / "worktree-config.toml").write_text('branch-prefix = "dev"\nsetup-script = "worktree-setup.sh"\n')
        return log


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Repo:
    monkeypatch.setenv("GH_STACK_NO_UPDATE_NOTIFIER", "1")
    monkeypatch.setenv("GIT_EDITOR", "true")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    seed = tmp_path / "seed"
    git(tmp_path, "init", "-q", "-b", "main", str(seed))
    (seed / "source.txt").write_text("base\n")
    (seed / ".gitignore").write_text("ignored.dat\n")
    git(seed, "add", ".")
    git(seed, "commit", "-qm", "base")
    origin = tmp_path / "origin.git"
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    root = tmp_path / "repo"
    root.mkdir()
    bare = root / ".bare"
    git(root, "clone", "-q", "--bare", str(origin), str(bare))
    git(bare, "config", "user.name", "Fixture")
    git(bare, "config", "user.email", "fixture@example.invalid")
    git(bare, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
    git(bare, "fetch", "-q", "origin")
    git(bare, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    (root / ".git").write_text("gitdir: ./.bare\n")
    (root / "worktree-config.toml").write_text('branch-prefix = "dev"\n')
    main = root / "main"
    owner = root / "custom-label"
    git(bare, "worktree", "add", "-q", str(main), "main")
    git(bare, "worktree", "add", "-q", "-b", "dev/base", str(owner), "main")
    # Public init creates tracking; the second layer has no checkout initially.
    stack(owner, "init", "--base", "main", "dev/base", "dev/api")
    git(owner, "switch", "-q", "dev/base")
    monkeypatch.chdir(owner)
    return Repo(root, bare, owner, main, origin)


def snapshot(repo: Repo) -> tuple[str, str, list[str]]:
    return repo.branches(), repo.owners(), sorted(str(p.relative_to(repo.root)) for p in repo.root.iterdir())


class TestStackSurface:
    def test_bare_stack_prints_help_without_mutation(self, repo: Repo) -> None:
        before = snapshot(repo)
        result = run_cli(["stack"])
        assert result.exit_code == 0
        assert "setup" in result.output and "add" in result.output
        assert snapshot(repo) == before

    @pytest.mark.parametrize("args", [
        ["feature"], ["rename", "old", "new"], ["rebase"],
        ["--add", "api"], ["--rename", "old", "new"], ["--rebase"], ["--agent"],
    ])
    def test_removed_commands_reject_without_mutation(self, repo: Repo, args: list[str]) -> None:
        before = snapshot(repo)
        result = run_cli(["stack", *args])
        assert result.exit_code != 0
        assert "gh stack" in result.output or "usage:" in result.output.lower()
        assert snapshot(repo) == before

    def test_agent_guidance_uses_native_rebase(self) -> None:
        result = run_cli(["stack", "agent"])
        assert result.exit_code == 0
        assert "gh stack rebase" in result.output
        assert "gh wt stack setup" in result.output
        assert "gh wt stack rebase" not in result.output


class TestStackSetup:
    def test_setup_reuses_alias_and_dirty_trunk_runs_only_new_hook(self, repo: Repo) -> None:
        log = repo.hook()
        (repo.main / "source.txt").write_text("unrelated player notes\n")
        (repo.owner / "ignored.dat").write_text("private cache")
        (repo.owner / "notes.txt").write_text("untracked notes")
        heads = repo.branches()
        result = run_cli(["stack", "setup"])
        assert result.exit_code == 0, result.output
        assert repo.owner.is_dir()
        assert git(repo.owner, "branch", "--show-current") == "dev/base"
        assert git(repo.root / "api", "branch", "--show-current") == "dev/api"
        assert repo.branches() == heads
        assert (repo.main / "source.txt").read_text() == "unrelated player notes\n"
        assert (repo.owner / "ignored.dat").read_text() == "private cache"
        assert (repo.owner / "notes.txt").read_text() == "untracked notes"
        assert log.read_text().splitlines() == ["api"]
        assert run_cli(["stack", "setup"]).exit_code == 0
        assert log.read_text().splitlines() == ["api"]

    def test_dry_run_has_no_directories_refs_or_hooks(self, repo: Repo) -> None:
        log = repo.hook()
        before = snapshot(repo)
        result = run_cli(["stack", "setup", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "api" in result.output
        assert snapshot(repo) == before
        assert not log.exists()

    def test_collision_is_reported_before_creation(self, repo: Repo) -> None:
        (repo.root / "api").mkdir()
        (repo.root / "api" / "notes").write_text("preserve")
        before = snapshot(repo)
        result = run_cli(["stack", "setup"])
        assert result.exit_code != 0
        assert "alias" in result.output.lower() or "-b" in result.output
        assert snapshot(repo) == before
        assert (repo.root / "api" / "notes").read_text() == "preserve"

    def test_hook_failure_reports_remaining_worktree(self, repo: Repo) -> None:
        repo.hook(fail=True)
        result = run_cli(["stack", "setup"])
        assert result.exit_code != 0
        assert "api" in result.output and "hook" in result.output.lower()
        assert git(repo.root / "api", "branch", "--show-current") == "dev/api"

    def test_untracked_current_branch_has_actionable_error(self, repo: Repo, monkeypatch) -> None:
        git(repo.bare, "worktree", "add", "-q", "-b", "unrelated", str(repo.root / "unrelated"), "main")
        monkeypatch.chdir(repo.root / "unrelated")
        before = snapshot(repo)
        result = run_cli(["stack", "setup"])
        assert result.exit_code != 0
        assert "gh stack checkout" in result.output
        assert snapshot(repo) == before

    def test_busy_owner_fails_before_new_worktrees(self, repo: Repo) -> None:
        gitdir = Path(git(repo.owner, "rev-parse", "--absolute-git-dir"))
        (gitdir / "rebase-merge").mkdir()
        before = snapshot(repo)
        result = run_cli(["stack", "setup"])
        assert result.exit_code != 0
        assert snapshot(repo) == before


class TestStackAdd:
    @pytest.mark.parametrize("local", [False, True])
    def test_new_member_is_flat_and_preserves_other_heads(self, repo: Repo, local: bool) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        log = repo.hook()
        owners = {p: git(p, "symbolic-ref", "HEAD") for p in (repo.owner, repo.main, repo.root / "api")}
        result = run_cli(["stack", "add", "ui", *(["-L"] if local else [])])
        assert result.exit_code == 0, result.output
        ui = repo.root / "ui"
        assert git(ui, "branch", "--show-current") == "dev/ui"
        assert [b["name"] for b in repo.view(ui)["branches"]] == ["dev/base", "dev/api", "dev/ui"]
        assert all(git(p, "symbolic-ref", "HEAD") == head for p, head in owners.items())
        assert log.read_text().splitlines() == ["ui"]
        remote = git(repo.origin, "branch", "--format=%(refname:short)")
        assert ("dev/ui" in remote.splitlines()) is not local
        if not local:
            assert git(ui, "rev-parse", "--abbrev-ref", "@{upstream}") == "origin/dev/ui"

    def test_existing_target_is_not_adopted_or_overwritten(self, repo: Repo) -> None:
        (repo.root / "ui").mkdir()
        before = snapshot(repo)
        result = run_cli(["stack", "add", "ui", "-L"])
        assert result.exit_code != 0
        assert snapshot(repo) == before
        assert [b["name"] for b in repo.view()["branches"]] == ["dev/base", "dev/api"]

    def test_add_when_top_has_no_worktree_preserves_existing_owner(self, repo: Repo) -> None:
        old_head = git(repo.owner, "symbolic-ref", "HEAD")
        result = run_cli(["stack", "add", "ui", "-L"])
        assert result.exit_code == 0, result.output
        assert git(repo.owner, "symbolic-ref", "HEAD") == old_head
        assert git(repo.root / "ui", "branch", "--show-current") == "dev/ui"
        assert [b["name"] for b in repo.view()["branches"]][-1] == "dev/ui"


class TestDistributedStatus:
    def test_two_stacks_are_grouped_independently_with_real_aliases(self, repo: Repo, monkeypatch) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        other = repo.root / "other-label"
        git(repo.bare, "worktree", "add", "-q", "-b", "other/base", str(other), "main")
        stack(other, "init", "--base", "main", "other/base", "other/top")
        monkeypatch.chdir(other)
        assert run_cli(["stack", "setup"]).exit_code == 0
        (repo.main / "notes.txt").write_text("family notes")
        monkeypatch.chdir(repo.root)
        result = run_cli(["status"])
        assert "dev/base" in result.output and "dev/api" in result.output
        assert "other/base" in result.output and "other/top" in result.output
        assert "custom-label" in result.output and "other-label" in result.output
        assert "main" in result.output
        assert "uncommitted" in result.output.lower() or "dirty" in result.output.lower()
        # Each layer renders once, rather than repeating the shared catalog.
        for name in ("dev/api", "other/top"):
            assert result.output.count(name) == 1, result.output


class TestStackRemoval:
    def test_explicit_keep_branch_removes_only_selected_owner(self, repo: Repo) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        branches = repo.branches()
        result = run_cli(["rm", "api", "--keep-branch"])
        assert result.exit_code == 0, result.output
        assert not (repo.root / "api").exists()
        assert repo.owner.exists() and repo.main.exists()
        assert repo.branches() == branches
        assert [b["name"] for b in repo.view()["branches"]] == ["dev/base", "dev/api"]

    def test_keep_branch_dry_run_preserves_refs_files_and_owners(self, repo: Repo) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        before = snapshot(repo)
        result = run_cli(["rm", "api", "--keep-branch", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "api" in result.output
        assert snapshot(repo) == before

    def test_active_branch_cannot_be_silently_deleted(self, repo: Repo) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        before = snapshot(repo)
        result = run_cli(["rm", "api"])
        assert result.exit_code != 0
        assert "--keep-branch" in result.output
        assert snapshot(repo) == before

    def test_merged_sweep_dry_run_reports_actual_ordinary_target(self, repo: Repo, monkeypatch) -> None:
        ordinary = repo.root / "landed"
        git(repo.bare, "worktree", "add", "-q", "-b", "landed", str(ordinary), "main")
        monkeypatch.setattr("gh_wt.get_pr_info", lambda branch: {"number": 9, "state": "MERGED"} if branch == "landed" else None)
        before = snapshot(repo)
        result = run_cli(["rm", "--merged", "--dry-run"])
        assert result.exit_code == 0, result.output
        assert "landed" in result.output
        assert snapshot(repo) == before

    def test_merged_sweep_can_explicitly_keep_branches(self, repo: Repo, monkeypatch) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        monkeypatch.setattr("gh_wt.get_pr_info", lambda branch: {"number": 9, "state": "MERGED"} if branch == "dev/api" else None)
        before = repo.branches()
        result = run_cli(["rm", "--merged", "--keep-branch"])
        assert result.exit_code == 0, result.output
        assert not (repo.root / "api").exists()
        assert repo.owner.exists() and repo.main.exists()
        assert repo.branches() == before

    @pytest.mark.parametrize("args", [
        ["api", "--keep-branch", "--delete-remote"],
        ["api", "--merged"],
    ])
    def test_incompatible_removal_flags_fail_before_mutation(self, repo: Repo, args: list[str]) -> None:
        before = snapshot(repo)
        assert run_cli(["rm", *args]).exit_code != 0
        assert snapshot(repo) == before


class TestExtensionErrors:
    @pytest.mark.parametrize("mode", ["missing", "old", "failed"])
    def test_preflight_errors_preserve_git_state(self, repo: Repo, monkeypatch, tmp_path: Path, mode: str) -> None:
        shim = tmp_path / "bin"
        shim.mkdir()
        gh = shim / "gh"
        gh.write_text("#!/bin/sh\n" + {
            "missing": "echo 'unknown command stack' >&2; exit 1\n",
            "old": "echo 'gh-stack version 0.2.0'; exit 0\n",
            "failed": "echo 'fixture upstream failure' >&2; exit 9\n",
        }[mode])
        gh.chmod(0o755)
        monkeypatch.setenv("PATH", str(shim) + os.pathsep + os.environ["PATH"])
        before = snapshot(repo)
        result = run_cli(["stack", "setup"])
        assert result.exit_code != 0
        assert snapshot(repo) == before
        if mode == "old":
            assert "0.3" in result.output
        if mode == "failed":
            assert "fixture upstream failure" in result.output


class TestNativeDistributedRebase:
    """Exercise upstream ownership and recovery without a gh-wt rebase wrapper."""

    def prepare(self, repo: Repo) -> Path:
        api = repo.root / "api-owner"
        git(repo.bare, "worktree", "add", "-q", str(api), "dev/api")
        return api

    def commit(self, path: Path, filename: str, text: str) -> None:
        (path / filename).write_text(text)
        git(path, "add", filename)
        git(path, "commit", "-qm", filename)

    def test_native_rebase_updates_distributed_owner_without_detach(self, repo: Repo) -> None:
        api = self.prepare(repo)
        self.commit(api, "api.txt", "API\n")
        self.commit(repo.owner, "base.txt", "new base\n")
        (repo.main / "notes.txt").write_text("unrelated dirty main")
        heads = {p: git(p, "symbolic-ref", "HEAD") for p in (repo.owner, api, repo.main)}
        result = stack(repo.owner, "rebase", "--no-trunk")
        assert result.returncode == 0
        assert (api / "base.txt").read_text() == "new base\n"
        assert git(api, "merge-base", "--is-ancestor", "dev/base", "dev/api") == ""
        assert all(git(p, "symbolic-ref", "HEAD") == h for p, h in heads.items())
        assert (repo.main / "notes.txt").read_text() == "unrelated dirty main"

    @pytest.mark.parametrize("recovery", ["continue", "abort"])
    def test_native_conflict_recovery_keeps_branch_owners(self, repo: Repo, recovery: str) -> None:
        api = self.prepare(repo)
        self.commit(api, "source.txt", "API conflicting edit\n")
        self.commit(repo.owner, "source.txt", "base conflicting edit\n")
        before = repo.branches()
        result = stack(repo.owner, "rebase", "--no-trunk", check=False)
        assert result.returncode != 0
        assert "conflict" in (result.stdout + result.stderr).lower()
        if recovery == "continue":
            (api / "source.txt").write_text("resolved\n")
            git(api, "add", "source.txt")
        stack(repo.owner, "rebase", "--" + recovery)
        assert git(repo.owner, "branch", "--show-current") == "dev/base"
        assert git(api, "branch", "--show-current") == "dev/api"
        if recovery == "abort":
            assert repo.branches() == before
        else:
            assert (api / "source.txt").read_text() == "resolved\n"


class TestOccupiedAndDetachedOwners:
    def test_existing_stack_branch_accepts_normal_alias_then_setup_reuses_it(self, repo: Repo) -> None:
        log = repo.hook()
        result = run_cli(["add", "api-alias", "-b", "dev/api", "-L"])
        assert result.exit_code == 0, result.output
        alias = repo.root / "api-alias"
        assert git(alias, "branch", "--show-current") == "dev/api"
        before = snapshot(repo)
        result = run_cli(["stack", "setup"])
        assert result.exit_code == 0, result.output
        assert snapshot(repo) == before
        assert log.read_text().splitlines() == ["api-alias"]
        assert not (repo.root / "api").exists()

    def test_detached_current_worktree_refuses_setup_without_mutation(self, repo: Repo) -> None:
        git(repo.owner, "switch", "--detach", "-q")
        before = snapshot(repo)
        result = run_cli(["stack", "setup"])
        assert result.exit_code != 0
        assert any(text in result.output.lower() for text in ("detached", "not on any branch", "gh stack checkout"))
        assert snapshot(repo) == before

    def test_busy_top_owner_prevents_new_member_creation(self, repo: Repo) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        top = repo.root / "api"
        gitdir = Path(git(top, "rev-parse", "--absolute-git-dir"))
        (gitdir / "rebase-merge").mkdir()
        before = snapshot(repo)
        result = run_cli(["stack", "add", "ui", "-L"])
        assert result.exit_code != 0
        assert snapshot(repo) == before
        assert [b["name"] for b in repo.view()["branches"]] == ["dev/base", "dev/api"]

    def test_keep_branch_still_preserves_dirty_worktree(self, repo: Repo) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        api = repo.root / "api"
        (api / "notes.txt").write_text("unfinished work")
        result = run_cli(["rm", "api", "--keep-branch"])
        assert result.exit_code != 0
        assert (api / "notes.txt").read_text() == "unfinished work"
        assert "refs/heads/dev/api" in repo.branches()


class TestRemovalWithUnavailableExtension:
    def hide_extension(self, tmp_path: Path, monkeypatch) -> None:
        bindir = tmp_path / "missing-extension-bin"
        bindir.mkdir()
        executable = bindir / "gh"
        executable.write_text("#!/bin/sh\necho 'unknown command stack' >&2\nexit 1\n")
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", str(bindir) + os.pathsep + os.environ["PATH"])

    def test_ordinary_removal_does_not_require_optional_extension(self, repo: Repo, tmp_path: Path, monkeypatch) -> None:
        root = tmp_path / "ordinary"
        root.mkdir()
        bare = root / ".bare"
        git(root, "clone", "-q", "--bare", str(repo.origin), str(bare))
        git(bare, "config", "remote.origin.fetch", "+refs/heads/*:refs/remotes/origin/*")
        git(bare, "fetch", "-q", "origin")
        git(bare, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
        (root / ".git").write_text("gitdir: ./.bare\n")
        git(bare, "worktree", "add", "-q", str(root / "main"), "main")
        git(bare, "worktree", "add", "-q", "-b", "ordinary-topic", str(root / "ordinary-topic"), "main")
        monkeypatch.chdir(root)
        self.hide_extension(tmp_path, monkeypatch)
        result = run_cli(["rm", "ordinary-topic"])
        assert result.exit_code == 0, result.output
        assert not (root / "ordinary-topic").exists()
        assert "ordinary-topic" not in git(bare, "branch", "--format=%(refname:short)").splitlines()

    def test_unavailable_membership_prevents_deletion_but_allows_keep_branch(self, repo: Repo, tmp_path: Path, monkeypatch) -> None:
        assert run_cli(["stack", "setup"]).exit_code == 0
        self.hide_extension(tmp_path, monkeypatch)
        before = snapshot(repo)
        result = run_cli(["rm", "api", "--force"])
        assert result.exit_code != 0
        assert "--keep-branch" in result.output
        assert snapshot(repo) == before
        result = run_cli(["rm", "api", "--keep-branch"])
        assert result.exit_code == 0, result.output
        assert "refs/heads/dev/api" in repo.branches()
