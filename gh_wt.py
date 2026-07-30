"""GitHub CLI extension for bare-git worktree management."""

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import argparse


SUBMODULE_WORKTREE_ERROR = "working trees containing submodules cannot be moved or removed"
REMOVABLE_PR_STATES = {"MERGED", "CLOSED"}

CONFIG_FILE_NAME = "worktree-config.toml"
NEW_SETUP_SCRIPT_NAME = "worktree-setup.sh"
OLD_SETUP_SCRIPT_NAME = "setup-worktree.sh"
REMOTE_TRACKING_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"

SETUP_WORKTREE_TEMPLATE = """\
#!/usr/bin/env bash
set -euo pipefail

worktree=${1%/}
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
target_dir="$script_dir/$worktree"

if [[ ! -d "$target_dir" ]]; then
  echo "Error: $target_dir is not a directory" >&2
  exit 1
fi

# Add project-specific setup for the new worktree here, e.g.:
# cp "$script_dir/main/.env.local" "$target_dir/.env.local"
"""


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


def load_config(repo_root: Path) -> dict:
    """Read worktree-config.toml from the repo root; empty dict when absent."""
    config_path = repo_root / CONFIG_FILE_NAME
    if not config_path.is_file():
        return {}

    try:
        import tomllib
    except ModuleNotFoundError:
        print(f"Error: {CONFIG_FILE_NAME} requires Python 3.11 or newer.", file=sys.stderr)
        sys.exit(1)

    try:
        config = tomllib.loads(config_path.read_text())
    except tomllib.TOMLDecodeError as error:
        print(f"Error: Cannot parse {CONFIG_FILE_NAME}: {error}", file=sys.stderr)
        sys.exit(1)

    for key in ("branch-prefix", "setup-script"):
        if key in config and not isinstance(config[key], str):
            print(f"Error: Cannot parse {CONFIG_FILE_NAME}: {key} must be a string", file=sys.stderr)
            sys.exit(1)
    return config


def get_branch_prefix(config: dict) -> str:
    """Return the configured branch prefix without a trailing slash."""
    return (config.get("branch-prefix") or "").rstrip("/")


def write_config_scaffold(repo_root: Path, script_name: str = NEW_SETUP_SCRIPT_NAME) -> Path:
    """Write the starter worktree-config.toml into the repo root."""
    config_path = repo_root / CONFIG_FILE_NAME
    config_path.write_text(f'setup-script = "{script_name}"\n# branch-prefix = "billw"\n')
    return config_path


def write_setup_worktree_script(repo_root: Path, script_name: str = NEW_SETUP_SCRIPT_NAME) -> Path:
    """Write a starter, executable setup script into the repo root.

    The starter validates the worktree folder and leaves a commented slot for
    project-specific setup. `gh wt add` picks it up via
    run_setup_worktree_hook, which only runs it when it is executable.
    """
    script_path = repo_root / script_name
    script_path.write_text(SETUP_WORKTREE_TEMPLATE)
    script_path.chmod(0o755)
    return script_path


def resolve_setup_script(repo_root: Path, invocation_dir: Path, config: dict) -> Optional[tuple[Path, Path]]:
    """Pick the setup script to run and the directory to run it from.

    A configured setup-script resolves relative to the repo root and must be
    executable. Without the key, both default names are tried in the
    invocation directory; finding both is an error because the choice is
    ambiguous.
    """
    configured = config.get("setup-script")
    if configured:
        script = repo_root / configured
        if not script.is_file() or not os.access(script, os.X_OK):
            print(
                f"Error: setup-script '{configured}' not found or not executable in {repo_root}",
                file=sys.stderr,
            )
            sys.exit(1)
        return script, repo_root

    new_script = invocation_dir / NEW_SETUP_SCRIPT_NAME
    old_script = invocation_dir / OLD_SETUP_SCRIPT_NAME
    if new_script.is_file() and old_script.is_file():
        print(
            f"Error: Found both {NEW_SETUP_SCRIPT_NAME} and {OLD_SETUP_SCRIPT_NAME}. "
            f"Set setup-script in {CONFIG_FILE_NAME} to choose one.",
            file=sys.stderr,
        )
        sys.exit(1)

    for script in (new_script, old_script):
        if script.is_file() and os.access(script, os.X_OK):
            return script, invocation_dir
    return None


def run_setup_worktree_hook(repo_root: Path, invocation_dir: Path, folder_name: str, config: dict) -> None:
    """Run the optional setup hook for a newly created worktree."""
    resolved = resolve_setup_script(repo_root, invocation_dir, config)
    if resolved is None:
        return

    script, run_dir = resolved
    sys.stdout.flush()
    try:
        subprocess.run([str(script), folder_name], cwd=str(run_dir), check=True)
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


def parse_linear_issue_url(url: str) -> Optional[str]:
    """Extract a branch-friendly name from a Linear issue URL.

    https://linear.app/modularml/issue/MKT-176/add-redirect-for-page becomes
    MKT-176-add-redirect-for-page; a URL without a title slug yields just the
    issue ID. Query strings, fragments, and trailing slashes are ignored.

    Returns None when the URL has no /issue/{id} path.
    """
    segments = [segment for segment in urlsplit(url).path.split("/") if segment]
    if "issue" not in segments:
        return None
    after_issue = segments[segments.index("issue") + 1:][:2]
    if not after_issue:
        return None
    return "-".join(after_issue)


def is_linear_issue_url(value: str) -> bool:
    """True when the value is an http(s) URL that parses as a Linear issue URL."""
    return urlsplit(value).scheme in ("http", "https") and parse_linear_issue_url(value) is not None


def resolve_linear_branch(args) -> str:
    """Resolve the branch name for `add --linear`, exiting on invalid usage."""
    if args.branch_name:
        print("Error: Cannot use --linear with --branch-name", file=sys.stderr)
        sys.exit(1)

    parsed = parse_linear_issue_url(args.linear)
    if not parsed:
        print(f"Error: Not a Linear issue URL: {args.linear}", file=sys.stderr)
        sys.exit(1)

    prefix = (args.branch or "").rstrip("/")
    return f"{prefix}/{parsed}" if prefix else parsed


def remote_branch_exists(bare_dir: Path, branch: str) -> bool:
    """Check whether origin has the branch, via remote-tracking refs."""
    return bool(run_git(["branch", "-r", "--list", f"origin/{branch}"], cwd=str(bare_dir)))


def resolve_prefixed_branch(bare_dir: Path, branch: str, prefix: str) -> tuple[str, bool]:
    """Pick the branch to check out for a bare name under a configured prefix.

    Prefers an existing remote branch with the prefix, then the bare name,
    and otherwise names a new prefixed branch.

    Returns:
        (branch to check out, whether it exists on origin).
    """
    prefixed = f"{prefix}/{branch}"
    if remote_branch_exists(bare_dir, prefixed):
        print(f"Found origin/{prefixed}")
        return prefixed, True
    if remote_branch_exists(bare_dir, branch):
        return branch, True
    return prefixed, False


def ensure_remote_tracking_refspec(bare_dir: Path) -> bool:
    """Configure the remote-tracking fetch refspec when origin lacks it.

    Fresh `git clone --bare` repos have no fetch refspec, so `git fetch`
    never updates refs/remotes/origin/* and remote-branch detection breaks.

    Returns:
        True when the refspec was added (followed by a fetch).
    """
    if not run_git(["config", "--get", "remote.origin.url"], cwd=str(bare_dir), check=False):
        return False

    existing = run_git(["config", "--get-all", "remote.origin.fetch"], cwd=str(bare_dir), check=False)
    if REMOTE_TRACKING_REFSPEC in existing.splitlines():
        return False

    run_git(["config", "remote.origin.fetch", REMOTE_TRACKING_REFSPEC], cwd=str(bare_dir))
    run_git(["fetch", "origin"], cwd=str(bare_dir), check=False)
    return True


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

    ensure_remote_tracking_refspec(bare_dir)

    # Get default branch
    default_branch = get_default_branch_name(repo_root)
    folder_name = default_branch.split("/")[-1]

    # Create worktree for default branch
    run_git(
        ["worktree", "add", str(repo_root / folder_name), default_branch],
        cwd=str(bare_dir),
    )

    print(f"Created {repo_root / folder_name}")

    script_path = write_setup_worktree_script(repo_root)
    print(f"Created {script_path}")

    config_path = write_config_scaffold(repo_root)
    print(f"Created {config_path}")


def confirm(prompt: str, default: bool = False) -> bool:
    """Ask a yes/no question on stdin; Enter picks the default."""
    reply = input(prompt).strip().lower()
    if not reply:
        return default
    return reply in ("y", "yes")


def init_bare_layout(repo_root: Path) -> None:
    """Adopt an existing bare-layout repo: fix the refspec, scaffold config, migrate the old script name."""
    refspec_added = ensure_remote_tracking_refspec(repo_root / ".bare")
    if refspec_added:
        print("Configured remote fetch refspec and fetched origin.")

    if (repo_root / CONFIG_FILE_NAME).is_file():
        if not refspec_added:
            print(f"Nothing to do: {CONFIG_FILE_NAME} already exists.")
        return

    old_script = repo_root / OLD_SETUP_SCRIPT_NAME
    script_name = NEW_SETUP_SCRIPT_NAME
    if old_script.is_file():
        if confirm(f"Rename {OLD_SETUP_SCRIPT_NAME} to {NEW_SETUP_SCRIPT_NAME}? [Y/n] ", default=True):
            old_script.rename(repo_root / NEW_SETUP_SCRIPT_NAME)
            print(f"Renamed {OLD_SETUP_SCRIPT_NAME} to {NEW_SETUP_SCRIPT_NAME}.")
        else:
            script_name = OLD_SETUP_SCRIPT_NAME
            print(f"Keeping {OLD_SETUP_SCRIPT_NAME}.")
    elif not (repo_root / NEW_SETUP_SCRIPT_NAME).is_file():
        script_path = write_setup_worktree_script(repo_root)
        print(f"Created {script_path}")

    config_path = write_config_scaffold(repo_root, script_name)
    print(f"Created {config_path}")


def remove_checked_out_files(repo_root: Path) -> None:
    """Delete the old checked-out files; safe because the tree was verified clean."""
    import shutil
    for entry in repo_root.iterdir():
        if entry.name == ".bare":
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()


def convert_normal_clone(repo_root: Path) -> None:
    """Convert a normal clone into the .bare/ + worktree layout, in place.

    The object database moves rather than re-clones, so local branches,
    stashes, and reflog survive. Requires a fully clean tree because the old
    checked-out files at the root are deleted after the move.
    """
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_root))
    if branch == "HEAD":
        print("Error: Cannot convert with a detached HEAD. Check out a branch first.", file=sys.stderr)
        sys.exit(1)

    if run_git(["status", "--porcelain"], cwd=str(repo_root), check=False):
        print(
            "Error: Working tree has uncommitted or untracked files. "
            "Commit, stash, or remove them, then re-run.",
            file=sys.stderr,
        )
        sys.exit(1)

    folder_name = branch.split("/")[-1]
    if not confirm(f"Convert this clone to the bare worktree layout (.bare/ + {folder_name}/ worktree)? [y/N] "):
        return

    bare_dir = repo_root / ".bare"
    (repo_root / ".git").rename(bare_dir)
    run_git(["config", "core.bare", "true"], cwd=str(bare_dir))
    ensure_remote_tracking_refspec(bare_dir)
    remove_checked_out_files(repo_root)
    run_git(["worktree", "add", str(repo_root / folder_name), branch], cwd=str(bare_dir))

    print("Converted to bare worktree layout.")
    print(f"Created {repo_root / folder_name}")

    script_path = write_setup_worktree_script(repo_root)
    print(f"Created {script_path}")

    config_path = write_config_scaffold(repo_root)
    print(f"Created {config_path}")


def cmd_init(args):
    """Set up worktree config, migrating old repos or converting normal clones."""
    repo_root = get_repo_root()
    if repo_root:
        init_bare_layout(repo_root)
        return

    toplevel = run_git(["rev-parse", "--show-toplevel"], check=False)
    if toplevel:
        convert_normal_clone(Path(toplevel))
        return

    print("Error: Not a git repository", file=sys.stderr)
    sys.exit(1)


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
    if args.branch and not args.linear and is_linear_issue_url(args.branch):
        args.linear, args.branch = args.branch, None

    branch = args.branch
    base_branch = args.base_branch
    branch_name = args.branch_name
    local = args.local
    invocation_dir = Path.cwd()

    if args.linear:
        branch = resolve_linear_branch(args)
    elif branch is None:
        print("Error: FOLDER_OR_BRANCH argument is required (unless using --linear/-l)", file=sys.stderr)
        sys.exit(1)

    repo_root = get_repo_root()
    if not repo_root:
        print("Error: Not in a bare-git repository", file=sys.stderr)
        sys.exit(1)

    bare_dir = repo_root / ".bare"
    config = load_config(repo_root)
    branch_prefix = get_branch_prefix(config)

    if base_branch is None:
        base_branch = get_default_branch_name(repo_root)

    print("Fetching from origin...")
    run_git(["fetch", "origin"], cwd=str(bare_dir))

    folder_name = branch if branch_name else branch.split("/")[-1]

    if branch_prefix and not branch_name and "/" not in branch:
        checkout_branch, remote_exists = resolve_prefixed_branch(bare_dir, branch, branch_prefix)
    else:
        checkout_branch = branch_name or branch
        remote_exists = remote_branch_exists(bare_dir, checkout_branch)

    worktree_path = repo_root / folder_name

    if worktree_path.exists():
        print(f"Error: Folder already exists: {folder_name}", file=sys.stderr)
        sys.exit(1)

    if remote_exists:
        if local:
            print(f"Error: Branch '{checkout_branch}' already exists on origin. Cannot use --local with an existing remote branch.", file=sys.stderr)
            sys.exit(1)
        run_git(
            ["worktree", "add", "-b", checkout_branch, str(worktree_path), f"origin/{checkout_branch}"],
            cwd=str(bare_dir),
        )
    else:
        if checkout_branch != (branch_name or args.branch):
            print(f"Creating branch {checkout_branch}...")
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

    run_setup_worktree_hook(repo_root, invocation_dir, folder_name, config)
    print(f"Worktree created. To use it, run:\n\ncd {folder_name}")

    if not (repo_root / CONFIG_FILE_NAME).is_file() and (invocation_dir / OLD_SETUP_SCRIPT_NAME).is_file():
        print(f"Hint: found {OLD_SETUP_SCRIPT_NAME}. Run 'gh wt init' to create {CONFIG_FILE_NAME} and migrate.")


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

    subparsers.add_parser("init", help="Set up worktree config; converts normal clones to the bare layout")

    subparsers.add_parser("list", help="List worktrees")

    p_add = subparsers.add_parser("add", help="Add a worktree for a branch",
                                     allow_abbrev=False)
    p_add.add_argument("branch", metavar="FOLDER_OR_BRANCH", nargs="?", default=None,
                       help="Branch or folder name; a Linear issue URL is detected automatically")
    p_add.add_argument("-B", "--base-branch", default=None,
                       help="Base branch for new worktrees (default: repo default branch)")
    p_add.add_argument("-b", "--branch-name", default=None,
                       help="Branch name to create or check out (default: branch argument)")
    p_add.add_argument("-l", "--linear", default=None, metavar="URL",
                       help="Name the branch from a Linear issue URL")
    p_add.add_argument("-L", "--local", action="store_true",
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
        "init": cmd_init,
        "list": cmd_list,
        "add": cmd_add,
        "rm": cmd_rm,
        "status": cmd_status,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    cli()
