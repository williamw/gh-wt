"""GitHub CLI extension for bare-git worktree management."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

import argparse


SUBMODULE_WORKTREE_ERROR = "working trees containing submodules cannot be moved or removed"
REMOVABLE_PR_STATES = {"MERGED", "CLOSED"}


def run_git(args: list[str], cwd: Optional[str] = None, check: bool = True) -> str:
    """Run git command and return stdout.
    
    Args:
        args: List of git command arguments (without 'git' prefix).
        cwd: Working directory to run git in.
        check: If True, exit on non-zero return code.
        
    Returns:
        Command stdout as string.
    """
    cmd = ["git"] + args
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def run_git_result(args: list[str], cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run git command and return the raw completed process."""
    return subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)


def run_setup_worktree_hook(invocation_dir: Path, folder_name: str) -> None:
    """Run the optional setup hook for a newly created worktree."""
    setup_script = invocation_dir / "setup-worktree.sh"
    if not setup_script.is_file() or not os.access(setup_script, os.X_OK):
        return

    try:
        subprocess.run(["./setup-worktree.sh", folder_name], cwd=str(invocation_dir), check=True)
    except subprocess.CalledProcessError as error:
        sys.exit(error.returncode)


def remove_worktree(worktree_path: str, bare_dir: str, force: bool = False) -> None:
    """Remove a worktree, deinitializing submodules if Git requires it."""
    remove_args = ["worktree", "remove"]
    if force:
        remove_args.append("--force")
    remove_args.append(worktree_path)

    result = run_git_result(remove_args, cwd=bare_dir)
    if result.returncode == 0:
        return

    if SUBMODULE_WORKTREE_ERROR in result.stderr:
        print(f"Deinitializing submodules in {worktree_path}...")
        run_git(["submodule", "deinit", "-f", "--all"], cwd=worktree_path)

        retry_result = run_git_result(["worktree", "remove", "--force", worktree_path], cwd=bare_dir)
        if retry_result.returncode == 0:
            return

        print(f"Error: {retry_result.stderr}", file=sys.stderr)
        sys.exit(1)

    print(f"Error: {result.stderr}", file=sys.stderr)
    sys.exit(1)


def get_repo_root() -> Optional[Path]:
    """Find bare repo root by finding directory containing .bare/.
    
    Walks up from current directory looking for a .bare/ folder.
    
    Returns:
        Path to repo root if found, None otherwise.
    """
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / ".bare").is_dir():
            return parent
    return None


def get_default_branch_name(repo_root: Path) -> str:
    """Get default branch name from origin/HEAD, with common branch fallbacks.
    
    Args:
        repo_root: Path to bare repository root (contains .bare/).
        
    Returns:
        Branch name (e.g., 'main' or 'master'), or 'main' as fallback.
    """
    bare_dir = repo_root / ".bare"
    try:
        result = run_git(
            ["symbolic-ref", "refs/remotes/origin/HEAD", "--short"],
            cwd=str(bare_dir),
            check=False,
        )
        if result.startswith("origin/"):
            return result[7:]

        remote_branches = run_git(
            ["branch", "-r", "--format=%(refname:short)"],
            cwd=str(bare_dir),
            check=False,
        )
        remote_branch_names = {
            branch[7:]
            for branch in remote_branches.splitlines()
            if branch.startswith("origin/") and not branch.startswith("origin/HEAD")
        }

        local_branches = run_git(
            ["branch", "--format=%(refname:short)"],
            cwd=str(bare_dir),
            check=False,
        )
        branch_names = remote_branch_names | set(local_branches.splitlines())
        if "main" in branch_names:
            return "main"
        if "master" in branch_names:
            return "master"
    except Exception:
        pass
    return "main"


def get_branch_start_point(bare_dir: Path, branch: str) -> str:
    """Return a usable start point for a branch in this bare repository."""
    remote_branch = f"origin/{branch}"
    if run_git(["rev-parse", "--verify", "--quiet", remote_branch], cwd=str(bare_dir), check=False):
        return remote_branch
    if run_git(["rev-parse", "--verify", "--quiet", branch], cwd=str(bare_dir), check=False):
        return branch
    return remote_branch


def cmd_clone(args):
    """Clone a repository with bare layout and create default branch worktree."""
    repo = args.repo
    print(f"Cloning {repo}...")
    repo_name = repo.split("/")[-1]
    if repo_name.endswith(".git"):
        repo_name = repo_name[:-4]

    # Create repo directory and clone bare into .bare/
    repo_root = Path.cwd() / repo_name
    bare_dir = repo_root / ".bare"
    bare_dir.mkdir(parents=True, exist_ok=True)
    
    # Clone bare repository using git (into .bare/ subdirectory)
    try:
        subprocess.run(
            ["git", "clone", "--bare", f"https://github.com/{repo}.git", str(bare_dir)],
            check=True,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError:
        import shutil
        if repo_root.exists():
            shutil.rmtree(repo_root)
        print(f"Error: Repository not found: {repo}", file=sys.stderr)
        print("  Make sure the repository exists and you have access to it.", file=sys.stderr)
        sys.exit(1)

    # Get default branch
    default_branch = get_default_branch_name(repo_root)
    folder_name = default_branch.split("/")[-1]

    # Create worktree for default branch
    run_git(
        ["worktree", "add", str(repo_root / folder_name), default_branch],
        cwd=str(bare_dir),
    )

    print(f"Created {repo_root / folder_name}")


def cmd_list(args):
    """List worktrees."""
    repo_root = get_repo_root()
    if not repo_root:
        print("Error: Not in a bare-git repository", file=sys.stderr)
        sys.exit(1)

    result = run_git(["worktree", "list"], cwd=str(repo_root / ".bare"))
    print(result)


def cmd_add(args):
    """Add a worktree for a branch."""
    branch = args.branch
    base_branch = args.base_branch
    branch_name = args.branch_name
    local = args.local
    invocation_dir = Path.cwd()
    repo_root = get_repo_root()
    if not repo_root:
        print("Error: Not in a bare-git repository", file=sys.stderr)
        sys.exit(1)

    bare_dir = repo_root / ".bare"

    if base_branch is None:
        base_branch = get_default_branch_name(repo_root)

    print("Fetching from origin...")
    run_git(["fetch", "origin"], cwd=str(bare_dir))

    checkout_branch = branch_name or branch
    folder_name = branch if branch_name else branch.split("/")[-1]

    # Check if remote branch exists
    remote_branches = run_git(
        ["branch", "-r", "--list", f"origin/{checkout_branch}"],
        cwd=str(bare_dir),
    )

    worktree_path = repo_root / folder_name

    if worktree_path.exists():
        print(f"Error: Folder already exists: {folder_name}", file=sys.stderr)
        sys.exit(1)

    if remote_branches:
        if local:
            print(f"Error: Branch '{checkout_branch}' already exists on origin. Cannot use --local with an existing remote branch.", file=sys.stderr)
            sys.exit(1)
        run_git(
            ["worktree", "add", "-b", checkout_branch, str(worktree_path), f"origin/{checkout_branch}"],
            cwd=str(bare_dir),
        )
    else:
        if local:
            run_git(
                ["worktree", "add", "-b", checkout_branch, str(worktree_path), base_branch],
                cwd=str(bare_dir),
            )
        else:
            start_point = get_branch_start_point(bare_dir, base_branch)
            run_git(
                ["worktree", "add", "-b", checkout_branch, str(worktree_path), start_point],
                cwd=str(bare_dir),
            )
            run_git(["push", "-u", "origin", checkout_branch], cwd=str(worktree_path))

    run_setup_worktree_hook(invocation_dir, folder_name)
    print(f"Worktree created. To use it, run:\n\ncd {folder_name}")


def remove_worktree_path(worktree_path: Path, bare_dir: Path, force: bool) -> None:
    """Remove a worktree at the given path, with a python-level force fallback if needed."""
    cmd = ["git", "worktree", "remove", str(worktree_path)]
    if force:
        cmd.append("--force")

    res = subprocess.run(cmd, cwd=str(bare_dir), capture_output=True, text=True)
    if res.returncode != 0:
        if worktree_path.exists():
            print(f"Warning: git worktree remove failed with error: {res.stderr.strip()}", file=sys.stderr)
            print(f"Attempting manual force cleanup of remaining files in {worktree_path.name}...", file=sys.stderr)
            import shutil
            shutil.rmtree(worktree_path, ignore_errors=True)
            # Run prune to clean up Git's metadata since the folder was manually deleted
            run_git(["worktree", "prune"], cwd=str(bare_dir))
        else:
            print(f"Error: {res.stderr.strip()}", file=sys.stderr)
            sys.exit(1)


def parse_worktree_porcelain_paths(output: str) -> list[Path]:
    """Return registered worktree paths from git worktree porcelain output."""
    paths = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line.removeprefix("worktree ")))
    return paths


def find_registered_worktree_record(folder: str, bare_dir: Path) -> Optional[Path]:
    """Find a registered worktree whose folder name matches the request."""
    output = run_git(["worktree", "list", "--porcelain"], cwd=str(bare_dir), check=False)
    for registered_path in parse_worktree_porcelain_paths(output):
        if registered_path.name == folder:
            return registered_path
    return None


def prune_stale_worktree_record(folder: str, stale_path: Path, bare_dir: Path) -> None:
    """Prune stale worktree metadata for a missing worktree folder."""
    print(f"Worktree folder not found, but a stale Git worktree record exists for {folder}.")
    print("Pruning stale worktree metadata...")
    prune_output = run_git(["worktree", "prune", "-v"], cwd=str(bare_dir), check=False)
    if prune_output:
        print(prune_output)
    print(f"Removed stale worktree record: {stale_path.name}")


def delete_remote_branch(branch: str, bare_dir: Path) -> None:
    """Delete origin branch if it exists; warn and continue when already absent."""
    remote_ref = f"refs/remotes/origin/{branch}"
    if not run_git(["rev-parse", "--verify", "--quiet", remote_ref], cwd=str(bare_dir), check=False):
        print(f"Warning: Remote branch not found: origin/{branch}", file=sys.stderr)
        return

    run_git(["push", "origin", ":" + branch], cwd=str(bare_dir), check=False)


def cmd_rm(args):
    """Remove a worktree or all worktrees with merged or closed PRs."""
    folder = args.folder
    delete_remote = args.delete_remote
    merged = args.merged
    force = args.force
    repo_root = get_repo_root()
    if not repo_root:
        print("Error: Not in a bare-git repository", file=sys.stderr)
        sys.exit(1)

    if merged and folder:
        print("Error: Cannot specify both folder and --merged (-m) flag", file=sys.stderr)
        sys.exit(1)

    if not merged and not folder:
        print("Error: folder argument is required (unless using --merged/-m)", file=sys.stderr)
        sys.exit(1)

    bare_dir = repo_root / ".bare"

    if merged:
        # Remove all worktrees with merged or closed PRs
        worktrees = get_worktree_branches(repo_root)
        removed_count = 0
        skipped_branches = []

        for folder_name, branch, worktree_path in worktrees:
            # Skip detached or unknown branches
            if branch.startswith("("):
                continue

            # Check PR status
            pr_info = get_pr_info(branch)
            if pr_info and pr_info.get("state") in REMOVABLE_PR_STATES:
                # Check safety BEFORE removing anything
                if not force and not is_branch_safe_to_delete(branch, str(bare_dir)):
                    skipped_branches.append(branch)
                    continue  # Skip this worktree entirely

                print(f"Removing {folder_name} ({branch})...")
                remove_worktree(worktree_path, str(bare_dir), force)

                # Delete local branch (safe at this point)
                run_git(["branch", "-D", branch], cwd=str(bare_dir))

                # Optionally delete from remote
                if delete_remote:
                    delete_remote_branch(branch, bare_dir)

                print(f"Removed {folder_name}")
                removed_count += 1

        if skipped_branches:
            print(
                f"Error: Local branch(es) have unpushed commits: {', '.join(skipped_branches)}. "
                f"Push changes first.",
                file=sys.stderr
            )
            sys.exit(1)

        if removed_count == 0:
            print("No merged or closed worktrees to remove")
        else:
            print(f"Removed {removed_count} merged or closed worktree(s)")
    else:
        worktree_path = repo_root / folder

        if not worktree_path.exists():
            registered_path = find_registered_worktree_record(folder, bare_dir)
            if registered_path and registered_path.exists():
                worktree_path = registered_path
            elif registered_path:
                prune_stale_worktree_record(folder, registered_path, bare_dir)
                return
            else:
                print(f"Error: Worktree folder not found: {folder}", file=sys.stderr)
                sys.exit(1)

        branch = run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=str(worktree_path),
        )
        if not branch or branch == "HEAD":
            print(f"Removing {folder} (detached)...")
            remove_worktree(str(worktree_path), str(bare_dir), force)
            print(f"Removed {folder}")
            return

        # Check safety BEFORE removing anything
        if not force and not is_branch_safe_to_delete(branch, str(bare_dir)):
            print(
                f"Error: Local branch '{branch}' has unpushed commits. "
                f"Push changes first.",
                file=sys.stderr
            )
            sys.exit(1)

        print(f"Removing {folder} ({branch})...")
        remove_worktree(str(worktree_path), str(bare_dir), force)

        # Delete local branch (safe at this point)
        run_git(["branch", "-D", branch], cwd=str(bare_dir))

        # Optionally delete from remote
        if delete_remote:
            delete_remote_branch(branch, bare_dir)

        print(f"Removed {folder}")


def get_pr_info(branch: str) -> Optional[dict]:
    """Get PR info for a branch using gh CLI.

    Returns dict with number, state, url or None if no PR/error.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number,state,url"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            import json
            return json.loads(result.stdout)
    except FileNotFoundError:
        return {"error": "gh CLI not installed"}
    except Exception:
        pass
    return None


def get_worktree_branches(repo_root: Path) -> list[tuple[str, str, str]]:
    """Get list of worktrees with their folder names, branches, and paths.

    Returns:
        List of (folder_name, branch_name, worktree_path) tuples.
        Excludes the .bare directory itself.
    """
    bare_dir = repo_root / ".bare"
    result = run_git(["worktree", "list"], cwd=str(bare_dir), check=False)

    worktrees = []
    for line in result.split("\n"):
        if not line.strip():
            continue

        parts = line.split()
        if not parts:
            continue

        worktree_path = Path(parts[0])

        # Skip the .bare directory itself
        if worktree_path == bare_dir:
            continue

        folder_name = worktree_path.name

        # Get branch name
        try:
            branch = run_git(
                ["rev-parse", "--abbrev-ref", "HEAD"],
                cwd=str(worktree_path),
                check=False
            )
            if not branch or branch == "HEAD":
                branch = "(detached)"
        except Exception:
            branch = "(unknown)"

        worktrees.append((folder_name, branch, str(worktree_path)))

    return worktrees


def is_branch_safe_to_delete(branch: str, bare_dir: str) -> bool:
    """Check if a branch is safe to delete (no unpushed commits).

    A branch is safe if:
    - Local branch exists
    - Origin branch exists
    - Local commit is equal to or behind origin commit (no unpushed work)

    Args:
        branch: Branch name to check
        bare_dir: Path to the bare repository (.bare/)

    Returns:
        True if branch can be safely deleted, False otherwise
    """
    try:
        # Get local branch commit
        local_commit = run_git(
            ["rev-parse", branch],
            cwd=bare_dir,
            check=False
        )
        if not local_commit:
            return False

        # Get origin branch commit
        origin_commit = run_git(
            ["rev-parse", f"origin/{branch}"],
            cwd=bare_dir,
            check=False
        )
        if not origin_commit:
            return False

        # Check if local is ancestor of or equal to origin
        # (meaning local has nothing origin doesn't have)
        merge_base = run_git(
            ["merge-base", branch, f"origin/{branch}"],
            cwd=bare_dir,
            check=False
        )

        # Safe if: local == origin, or local is behind origin
        # This is true when merge-base equals local commit
        return merge_base == local_commit

    except Exception:
        return False


def cmd_status(args):
    """Show status of all worktrees."""
    repo_root = get_repo_root()
    if not repo_root:
        print("Error: Not in a bare-git repository", file=sys.stderr)
        sys.exit(2)

    bare_dir = repo_root / ".bare"

    # Get worktree list
    result = run_git(["worktree", "list"], cwd=str(bare_dir), check=False)
    if not result:
        print("No worktrees found.")
        sys.exit(0)

    needs_attention = False

    # Parse worktree list and show details for each
    for line in result.split("\n"):
        if line.strip():
            # Worktree path is first column, may have tab-separated branch info
            parts = line.split()
            if parts:
                worktree_path = Path(parts[0])

                # Skip the .bare directory itself (it's the bare repo, not a worktree)
                if worktree_path == bare_dir:
                    continue

                folder_name = worktree_path.name

                # Get branch name from worktree
                try:
                    branch = run_git(
                        ["rev-parse", "--abbrev-ref", "HEAD"],
                        cwd=str(worktree_path),
                        check=False
                    )
                    if not branch or branch == "HEAD":
                        branch = "(detached)"
                except Exception:
                    branch = "(unknown)"

                # Get uncommitted changes
                try:
                    status_output = run_git(
                        ["status", "--porcelain"],
                        cwd=str(worktree_path),
                        check=False
                    )
                    if status_output.strip():
                        # Count changes
                        lines = [l for l in status_output.split("\n") if l.strip()]
                        status_msg = f"{len(lines)} uncommitted changes"
                        needs_attention = True
                    else:
                        status_msg = "Clean"
                except Exception:
                    status_msg = "(unknown)"

                # Get ahead/behind origin
                origin_msg = ""
                branch_deleted = False
                if branch and branch != "(detached)" and branch != "(unknown)":
                    try:
                        ahead = int(run_git(
                            ["rev-list", "--count", f"origin/{branch}..HEAD"],
                            cwd=str(worktree_path),
                            check=False
                        ) or "0")
                        behind = int(run_git(
                            ["rev-list", "--count", f"HEAD..origin/{branch}"],
                            cwd=str(worktree_path),
                            check=False
                        ) or "0")

                        if ahead > 0 and behind > 0:
                            origin_msg = f"{ahead} ahead / {behind} behind origin"
                            needs_attention = True
                        elif ahead > 0:
                            origin_msg = f"{ahead} commits ahead of origin"
                        elif behind > 0:
                            origin_msg = f"{behind} commits behind origin"
                            needs_attention = True
                        else:
                            # Check if remote branch exists
                            remote_check = run_git(
                                ["ls-remote", "origin", f"refs/heads/{branch}"],
                                cwd=str(worktree_path),
                                check=False
                            )
                            if remote_check.strip():
                                origin_msg = "Up to date"
                            else:
                                # Check if branch was ever pushed
                                local_ref = run_git(
                                    ["rev-parse", "--abbrev-ref", branch + "@{upstream}"],
                                    cwd=str(worktree_path),
                                    check=False
                                )
                                if local_ref:
                                    branch_deleted = True
                                    origin_msg = "Branch deleted on remote"
                                    needs_attention = True
                                else:
                                    origin_msg = "(no upstream configured)"
                    except Exception:
                        origin_msg = "(no upstream configured)"

                # Get PR info
                pr_msg = "Not found"
                if branch and not branch.startswith("("):
                    pr_info = get_pr_info(branch)
                    if pr_info:
                        if "error" in pr_info:
                            pr_msg = f"({pr_info['error']})"
                        else:
                            pr_msg = f"#{pr_info['number']} ({pr_info['state']}) - {pr_info['url']}"
                            if pr_info['state'] == 'MERGED':
                                branch_deleted = True

                print(f"{folder_name}")
                print(f"  Branch: {branch}")
                print(f"  Status: {status_msg}")
                if origin_msg:
                    print(f"  Origin: {origin_msg}")
                print(f"  PR: {pr_msg}")
                print()

    sys.exit(1 if needs_attention else 0)


def cli(argv: list[str] | None = None):
    """Entry point. Parses argv and dispatches to subcommand."""
    parser = argparse.ArgumentParser(
        prog="gh-wt",
        description="Manage bare-git worktrees.",
        allow_abbrev=False,
    )
    subparsers = parser.add_subparsers(dest="command")

    p_clone = subparsers.add_parser("clone", help="Clone a repository with bare layout")
    p_clone.add_argument("repo")

    subparsers.add_parser("list", help="List worktrees")

    p_add = subparsers.add_parser("add", help="Add a worktree for a branch",
                                     allow_abbrev=False)
    p_add.add_argument("branch", metavar="FOLDER_OR_BRANCH")
    p_add.add_argument("-B", "--base-branch", default=None,
                       help="Base branch for new worktrees (default: repo default branch)")
    p_add.add_argument("-b", "--branch-name", default=None,
                       help="Branch name to create or check out (default: branch argument)")
    p_add.add_argument("-l", "--local", action="store_true",
                       help="Create branch locally without pushing to origin")

    p_rm = subparsers.add_parser("rm", help="Remove a worktree")
    p_rm.add_argument("folder", nargs="?", default=None)
    p_rm.add_argument("-d", "--delete-remote", action="store_true",
                      help="Also delete the branch from remote origin")
    p_rm.add_argument("-m", "--merged", action="store_true",
                      help="Remove all worktrees with merged or closed PRs")
    p_rm.add_argument("-f", "--force", action="store_true",
                      help="Force removal even with untracked/unpushed files")

    subparsers.add_parser("status", help="Show status of all worktrees")

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    dispatch = {
        "clone": cmd_clone,
        "list": cmd_list,
        "add": cmd_add,
        "rm": cmd_rm,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    cli()
