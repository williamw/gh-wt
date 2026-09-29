"""GitHub CLI extension for bare-git worktree management."""

import contextlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

import argparse


SUBMODULE_WORKTREE_ERROR = "working trees containing submodules cannot be moved or removed"
DELETE_FAILED_ERROR = "failed to delete"
REMOVABLE_PR_STATES = {"MERGED", "CLOSED"}

CONFIG_FILE_NAME = "worktree-config.toml"
NEW_SETUP_SCRIPT_NAME = "worktree-setup.sh"
OLD_SETUP_SCRIPT_NAME = "setup-worktree.sh"
REMOTE_TRACKING_REFSPEC = "+refs/heads/*:refs/remotes/origin/*"

STACK_METADATA_NAME = "gh-stack"
STACK_OVERSIZED_COMMITS = 3
STACK_OVERSIZED_LINES = 500

# Addressed to AI agents reading gh-wt output. Both blocks are frozen literals:
# never interpolate branch names, PR titles, or anything else fetched from the
# remote into them, or a hostile branch name becomes an instruction channel.
AGENT_SPLIT_POINTER = """\
Agents: this branch is likely too large for one review. Run
'gh wt stack agent' for the layer-splitting procedure, then offer
to split it with the user."""

AGENT_SPLIT_GUIDE = """\
gh wt stack - splitting an oversized bottom layer

The user converted a worktree into a stack. Conversion renamed the branch and
moved it into its own layer folder, so the container now holds one folder per
layer. The bottom layer still holds every commit, which is usually too much for
one review. Offer to split it, and follow this procedure if they accept.

1. Read the diff against trunk and group changed files into layers that each
   stand alone: a layer should build, pass its own tests, and be reviewable
   without the layers above it.
2. Split by file path, not by commit. Commits in the original history were
   never required to be individually green; file groups can be.
3. Create each layer with 'gh wt stack add <name>'. It numbers the layer,
   names its branch to match its folder, and gives it its own worktree.
4. Build upward from the bottom: in the bottom layer's folder, reset the files
   belonging higher and commit, then add the next layer and restore that
   layer's files in its own folder. Repeat to the top.
5. Verify each layer independently before moving up. A layer that cannot be
   made green means the cut is in the wrong place; fold it into its neighbour
   rather than shipping it red.
6. Each layer has its own worktree, so layers can be worked in parallel. Never
   run 'gh stack rebase' directly - it checks each branch out in the bottom
   layer's folder and fails on any branch another worktree holds. Run
   'gh wt stack rebase', which frees the layer worktrees first and puts them
   back afterwards. Every agent must be idle and committed before it runs.
7. rerere is enabled, so repeated conflict resolutions stick across the
   cascading rebases that follow review feedback.

Useful commands: gh wt stack add, gh wt stack rename, gh wt stack rebase,
gh stack view, gh stack submit."""

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


def check_current_dir() -> None:
    """Exit with guidance when the current directory has been removed.

    Every command needs a working directory, and os.getcwd() raises once the
    directory is unlinked - which happens when `gh wt rm` deletes the worktree
    the shell is standing in. The shell's PWD survives as a plain string, so it
    names the vanished path and locates the nearest directory still on disk.
    """
    try:
        Path.cwd()
        return
    except FileNotFoundError:
        pass

    stale = os.environ.get("PWD", "")
    escape = next((parent for parent in Path(stale).parents if parent.is_dir()), None) if stale else None

    if stale:
        print(f"Error: Current directory no longer exists: {stale}", file=sys.stderr)
    else:
        print("Error: Current directory no longer exists.", file=sys.stderr)

    if escape:
        print(f"  Run 'cd {escape}' and try again.", file=sys.stderr)
    else:
        print("  Run 'cd' to return to your home directory, then try again.", file=sys.stderr)
    sys.exit(1)


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


def write_config_scaffold(repo_root: Path, script_name: str = NEW_SETUP_SCRIPT_NAME, branch_prefix: str = "") -> Path:
    """Write the starter worktree-config.toml into the repo root."""
    if branch_prefix:
        prefix_line = f'branch-prefix = "{branch_prefix}"'
    else:
        prefix_line = '# branch-prefix = "billw"'
    config_path = repo_root / CONFIG_FILE_NAME
    config_path.write_text(f'setup-script = "{script_name}"\n{prefix_line}\n')
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


def hint_if_cwd_removed(invocation_dir: Path, removed_paths: list[Path]) -> None:
    """Nudge when the shell that ran `rm` is sitting inside a removed worktree.

    Without this the next command in that shell fails on a missing directory,
    so the cause is pointed out while it is still obvious.
    """
    invocation = invocation_dir.resolve()
    for removed in removed_paths:
        removed = removed.resolve()
        if invocation == removed or removed in invocation.parents:
            print(f"Hint: your shell is inside the removed folder. Run 'cd {removed.parent}' to continue.")
            return


def git_worktree_remove(worktree_path: str, bare_dir: str, force: bool) -> subprocess.CompletedProcess:
    """Run `git worktree remove` and hand back the raw result."""
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(worktree_path)
    return run_git_result(args, cwd=bare_dir)


def remove_worktree(worktree_path: str, bare_dir: str, force: bool = False) -> bool:
    """Remove a worktree, deinitializing submodules if Git requires it.

    Returns True when the folder is gone. Git empties the worktree and then
    rmdir's the folder, so a file that reappears mid-delete - macOS writing
    .DS_Store back is the usual culprit - fails the command with "Directory not
    empty" and leaves an empty shell behind. Git has already unlinked its own
    metadata by that point, so clearing the leftovers is on us.
    """
    result = git_worktree_remove(worktree_path, bare_dir, force)

    if result.returncode != 0 and SUBMODULE_WORKTREE_ERROR in result.stderr:
        print(f"Deinitializing submodules in {worktree_path}...")
        run_git(["submodule", "deinit", "-f", "--all"], cwd=worktree_path)
        result = git_worktree_remove(worktree_path, bare_dir, force=True)

    if result.returncode == 0:
        return True

    leftover = Path(worktree_path)
    # Only clean up after a delete Git started and could not finish. Every other
    # refusal - a dirty worktree without --force, most of all - is Git guarding
    # work that is still there, and deleting it here would throw that away.
    if DELETE_FAILED_ERROR not in result.stderr or not leftover.exists():
        print(f"Error: {result.stderr.strip()}", file=sys.stderr)
        return False

    print(f"Warning: git worktree remove failed: {result.stderr.strip()}", file=sys.stderr)
    print(f"Cleaning up leftover files in {leftover.name}...", file=sys.stderr)
    # Twice: whatever raced Git into the folder can race us the first time too.
    for _ in range(2):
        shutil.rmtree(leftover, ignore_errors=True)
        if not leftover.exists():
            break

    run_git(["worktree", "prune"], cwd=bare_dir)

    if leftover.exists():
        print(f"Error: could not delete {worktree_path}", file=sys.stderr)
        return False
    return True


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
    run_git(["fetch", "--prune", "origin"], cwd=str(bare_dir), check=False)
    return True


def get_branch_start_point(bare_dir: Path, branch: str) -> str:
    """Return a usable start point for a branch in this bare repository."""
    remote_branch = f"origin/{branch}"
    if run_git(["rev-parse", "--verify", "--quiet", remote_branch], cwd=str(bare_dir), check=False):
        return remote_branch
    if run_git(["rev-parse", "--verify", "--quiet", branch], cwd=str(bare_dir), check=False):
        return branch
    return remote_branch


def resolve_git_dir(worktree_path: Path) -> Optional[Path]:
    """Resolve a worktree's own git dir from its .git entry, without calling git.

    A linked worktree's .git is a file holding 'gitdir: <path>'; the primary
    worktree's is a directory.
    """
    git_entry = worktree_path / ".git"
    if git_entry.is_dir():
        return git_entry

    try:
        pointer = git_entry.read_text().strip()
    except OSError:
        return None

    if not pointer.startswith("gitdir:"):
        return None
    return worktree_path / pointer.removeprefix("gitdir:").strip()


def read_stack_metadata(worktree_path: Path) -> Optional[dict]:
    """Read gh-stack metadata for a worktree, or None when it hosts no stack.

    gh stack stores metadata in the worktree's own git dir, not the common dir,
    so this resolves that rather than guessing from the folder name (which goes
    stale as soon as the stack moves between layers).
    """
    git_dir = resolve_git_dir(worktree_path)
    if not git_dir:
        return None

    try:
        return json.loads((git_dir / STACK_METADATA_NAME).read_text())
    except (OSError, ValueError):
        return None


def stack_layers(metadata: dict) -> list[str]:
    """Return every stacked branch name, bottom layer first."""
    layers = []
    for stack in metadata.get("stacks", []):
        for entry in stack.get("branches", []):
            branch = entry.get("branch")
            if branch:
                layers.append(branch)
    return layers


def stack_number(metadata: dict) -> Optional[int]:
    """Return the GitHub stack number, when the stack has been submitted."""
    for stack in metadata.get("stacks", []):
        if stack.get("number"):
            return stack["number"]
    return None


def layer_folder_name(position: int, name: str = "") -> str:
    """Name a layer folder: '01' bare, '02-api' when the layer has a name.

    A name may carry its own number, since rename takes whole folder names
    and '02-api' reads as the obvious thing to type. It must be the right one.
    """
    number = f"{position:02d}"
    given, _, rest = name.partition("-")
    if rest and len(given) == 2 and given.isdigit():
        if given != number:
            print(
                f"Error: '{name}' would be layer {number}; name it '{number}-{rest}' or just '{rest}'.",
                file=sys.stderr,
            )
            sys.exit(1)
        name = rest
    return f"{number}-{name}" if name else number


def layer_branch_name(stack_root: str, folder: str) -> str:
    """Branch for a layer, nested under the stack root so both spell the same."""
    return f"{stack_root}/{folder}"


def stack_branch_root(layer_branch: str) -> str:
    """The ref path layers nest under: 'billw/x/02-api' -> 'billw/x'."""
    return layer_branch.rsplit("/", 1)[0]


def find_stack_worktree_for_branch(repo_root: Path, branch: str) -> Optional[tuple[Path, int]]:
    """Find the worktree hosting a stack containing branch, with its layer number.

    Reads local metadata only, so this stays offline and cheap enough to run on
    every 'gh wt add'.
    """
    for _folder, _branch, worktree_path in get_worktree_branches(repo_root):
        metadata = read_stack_metadata(Path(worktree_path))
        if not metadata:
            continue
        layers = stack_layers(metadata)
        if branch in layers:
            return Path(worktree_path), layers.index(branch) + 1
    return None


def get_stack_view(worktree_path: Path) -> Optional[dict]:
    """Return live 'gh stack view --json' data, or None when it is unavailable."""
    try:
        result = subprocess.run(
            ["gh", "stack", "view", "--json"],
            cwd=str(worktree_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None

    if result.returncode != 0 or not result.stdout:
        return None
    try:
        return json.loads(result.stdout)
    except ValueError:
        return None


def is_gh_stack_installed() -> bool:
    """Report whether the gh stack extension is available."""
    try:
        result = subprocess.run(
            ["gh", "stack", "--version"], capture_output=True, text=True, check=False
        )
    except OSError:
        return False
    return result.returncode == 0


def measure_branch_against_trunk(worktree_path: Path, trunk: str) -> tuple[int, int]:
    """Return commits ahead of trunk and lines changed against it."""
    commits = run_git(
        ["rev-list", "--count", f"{trunk}..HEAD"], cwd=str(worktree_path), check=False
    )
    shortstat = run_git(
        ["diff", "--shortstat", f"{trunk}...HEAD"], cwd=str(worktree_path), check=False
    )

    lines = 0
    for part in shortstat.split(","):
        if "insertion" in part or "deletion" in part:
            lines += int(part.strip().split()[0])
    return int(commits or "0"), lines


def enable_rerere(bare_dir: Path) -> None:
    """Turn on rerere so conflict resolutions survive the cascading rebases.

    gh stack's README claims init does this; it does not. In a bare layout this
    writes to .bare/config, shared by every worktree.
    """
    if run_git(["config", "--get", "rerere.enabled"], cwd=str(bare_dir), check=False) == "true":
        return

    print("Enabling rerere...")
    run_git(["config", "rerere.enabled", "true"], cwd=str(bare_dir))


def output_is_piped() -> bool:
    """Report whether stdout is captured rather than shown in a terminal.

    An agent running 'gh wt stack' captures output; a human does not. This is
    what gates the agent-facing block.
    """
    return not sys.stdout.isatty()


def warn_if_bottom_layer_is_oversized(worktree_path: Path, trunk: str) -> None:
    """Warn when the adopted bottom layer is still too large to review at once."""
    commits, lines = measure_branch_against_trunk(worktree_path, trunk)
    if commits <= STACK_OVERSIZED_COMMITS and lines <= STACK_OVERSIZED_LINES:
        return

    print()
    print(f"Note: this branch is {commits} commits / {lines} lines. The bottom layer is")
    print("still too large for one review. Redistribute with 'gh stack modify'.")

    if output_is_piped():
        print()
        print(AGENT_SPLIT_POINTER)


STACK_MOVE_TEMP_PREFIX = "."
STACK_MOVE_TEMP_SUFFIX = ".tmp"


def stack_move_temp_path(worktree_path: Path) -> Path:
    """Sibling path a worktree parks at while its folder becomes a container."""
    name = f"{STACK_MOVE_TEMP_PREFIX}{worktree_path.name}{STACK_MOVE_TEMP_SUFFIX}"
    return worktree_path.parent / name


def rename_remote_branch(old: str, new: str, cwd: Path) -> bool:
    """Rename a branch on GitHub, which closes any PR it is the head of.

    GitHub retargets PRs based on the branch but closes the one it heads, so
    callers gate this behind an explicit confirmation.

    GitHub rejects a rename where one name nests under the other (layer 1's
    `feat` -> `feat/01-base`) as an invalid branch name, because the old ref
    still occupies the path. Those go through a sibling name first, waiting
    out the moment GitHub keeps the old ref after a rename returns.
    """
    if new.startswith(f"{old}/") or old.startswith(f"{new}/"):
        temp = f"{old}.gh-wt-rename"
        return (
            rename_remote_branch(old, temp, cwd)
            and wait_for_remote_branch_to_go(old, cwd)
            and rename_remote_branch(temp, new, cwd)
        )

    result = subprocess.run(
        ["gh", "api", "-X", "POST", f"repos/{{owner}}/{{repo}}/branches/{old}/rename",
         "-f", f"new_name={new}"],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"Error: could not rename {old} on origin.", file=sys.stderr)
        print(result.stderr.strip() or result.stdout.strip(), file=sys.stderr)
        return False
    return True


REMOTE_BRANCH_GONE_TIMEOUT = 15.0


def wait_for_remote_branch_to_go(branch: str, cwd: Path) -> bool:
    """Poll until GitHub stops serving a branch it has just renamed away."""
    deadline = time.monotonic() + REMOTE_BRANCH_GONE_TIMEOUT
    while time.monotonic() < deadline:
        result = subprocess.run(
            ["gh", "api", f"repos/{{owner}}/{{repo}}/git/ref/heads/{branch}"],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return True
        time.sleep(0.5)

    print(f"Error: origin still has {branch} after renaming it away.", file=sys.stderr)
    return False


def ensure_rename_is_allowed(branch: str, action: str) -> None:
    """Stop when renaming would close an open PR, unless the user insists.

    Prompts on a terminal; a captured run must pass --force, so an agent can
    never close a PR by omission.
    """
    pr_info = get_pr_info(branch)
    if not pr_info or pr_info.get("error") or pr_info.get("state") != "OPEN":
        return

    number = pr_info.get("number")
    if output_is_piped():
        print(
            f"Error: {branch} has an open PR (#{number}); renaming it will close that PR.",
            file=sys.stderr,
        )
        print("Re-run with --force to continue anyway.", file=sys.stderr)
        sys.exit(1)

    print(f"{branch} has an open PR (#{number}). Renaming it for the stack will close that PR.")
    if not confirm(f"{action} anyway? [y/N] "):
        print("Aborted.")
        sys.exit(1)


def rename_stack_branch(old: str, new: str, bare_dir: Path, worktree_path: Path) -> None:
    """Rename a layer branch locally and on origin, then refresh tracking refs."""
    if remote_branch_exists(bare_dir, old):
        if not rename_remote_branch(old, new, worktree_path):
            sys.exit(1)

    run_git(["branch", "-m", old, new], cwd=str(bare_dir))
    run_git(["fetch", "--prune", "origin"], cwd=str(bare_dir), check=False)


def move_worktree_into_container(bare_dir: Path, worktree_path: Path, folder: str) -> Path:
    """Turn a worktree folder into a container holding that worktree as a layer.

    Git refuses to move a worktree into its own subdirectory, so this parks it
    at a sibling first. A run interrupted between the two moves leaves the
    worktree at that sibling, which the next run reports rather than guesses at.
    """
    temp_path = stack_move_temp_path(worktree_path)
    layer_path = worktree_path / folder

    run_git(["worktree", "move", str(worktree_path), str(temp_path)], cwd=str(bare_dir))
    worktree_path.mkdir()
    run_git(["worktree", "move", str(temp_path), str(layer_path)], cwd=str(bare_dir))
    return layer_path


def report_interrupted_move(worktree_path: Path) -> None:
    """Report a conversion that died between the two worktree moves."""
    temp_path = stack_move_temp_path(worktree_path)
    if not temp_path.exists():
        return

    print(
        f"Error: a previous conversion left a worktree at {temp_path.name}.",
        file=sys.stderr,
    )
    print("Move it back, then retry:", file=sys.stderr)
    print(file=sys.stderr)
    print(f"  git worktree move {temp_path.name} {worktree_path.name}", file=sys.stderr)
    sys.exit(1)


def local_branch_exists(bare_dir: Path, branch: str) -> bool:
    """Check whether the branch exists locally."""
    return bool(
        run_git(["branch", "--list", branch], cwd=str(bare_dir), check=False)
    )


def run_gh_stack(args: list[str], cwd: Path) -> None:
    """Run a gh stack subcommand, reporting its own error text on failure."""
    result = subprocess.run(
        ["gh", "stack", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        sys.exit(1)


def find_stack_host(repo_root: Path, start: Path) -> Optional[Path]:
    """The worktree holding stack metadata for the stack `start` sits in.

    Layers are sibling worktrees inside one container folder, and only the
    bottom layer carries gh stack's metadata, so a layer finds its host by
    looking across the container rather than at itself. The container is a
    plain directory, so from there `start` itself is the container.
    """
    toplevel = run_git(["rev-parse", "--show-toplevel"], cwd=str(start), check=False)
    if not toplevel:
        container = start.resolve()
    elif read_stack_metadata(Path(toplevel)):
        return Path(toplevel)
    else:
        container = Path(toplevel).parent
    for _, _, path in get_worktree_branches(repo_root):
        candidate = Path(path)
        if candidate.parent == container and read_stack_metadata(candidate):
            return candidate
    return None


def layer_worktree_paths(repo_root: Path, container: Path) -> list[Path]:
    """Every worktree sitting inside a stack container, in folder order."""
    paths = [
        Path(path)
        for _, _, path in get_worktree_branches(repo_root)
        if Path(path).parent == container
    ]
    return sorted(paths, key=lambda path: path.name)


def find_busy_layer(layer_paths: list[Path]) -> Optional[Path]:
    """The first layer worktree with a rebase or merge in progress."""
    for path in layer_paths:
        git_dir = resolve_git_dir(path)
        if not git_dir:
            continue
        if any(
            (git_dir / marker).exists()
            for marker in ("rebase-merge", "rebase-apply", "MERGE_HEAD")
        ):
            return path
    return None


def release_layer_worktrees(layer_paths: list[Path]) -> list[Path]:
    """Detach layer worktrees so the host can check their branches out.

    Detaching leaves each working tree exactly as it is, so an agent's
    uncommitted work survives its branch moving underneath it.
    """
    released = []
    for path in layer_paths:
        branch = run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(path), check=False
        )
        if not branch or branch == "HEAD":
            continue
        run_git(["switch", "--detach"], cwd=str(path))
        released.append(path)
    return released


def restore_layer_worktrees(layer_paths: list[Path], stack_root: str) -> int:
    """Put every detached layer worktree back on the branch its folder names.

    Folder and branch spell the same thing, so nothing has to be remembered
    across a failure or across separate runs of rebase --continue.
    """
    restored = 0
    for path in layer_paths:
        head = run_git(
            ["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(path), check=False
        )
        if head != "HEAD":
            continue
        run_git(
            ["switch", layer_branch_name(stack_root, path.name)],
            cwd=str(path),
            check=False,
        )
        restored += 1
    return restored


@contextlib.contextmanager
def layer_branches_released(layer_paths: list[Path], stack_root: str):
    """Free layer branches for the host, and hand them back however it ends."""
    released = release_layer_worktrees(layer_paths)
    try:
        yield
    finally:
        restore_layer_worktrees(released, stack_root)


def cmd_stack_add(args, repo_root: Path) -> None:
    """Add a layer on top of the stack and give it its own worktree."""
    bare_dir = repo_root / ".bare"
    host = find_stack_host(repo_root, Path.cwd())
    if not host:
        print(
            "Error: not inside a stack. Run 'gh wt stack' to convert this worktree first.",
            file=sys.stderr,
        )
        sys.exit(1)

    layers = stack_layers(read_stack_metadata(host) or {})
    container = host.parent
    stack_root = stack_branch_root(layers[0])

    folder = layer_folder_name(len(layers) + 1, args.add)
    layer_branch = layer_branch_name(stack_root, folder)
    layer_path = container / folder

    if layer_path.exists() or local_branch_exists(bare_dir, layer_branch):
        print(f"Error: layer '{folder}' already exists.", file=sys.stderr)
        sys.exit(1)

    # Positions keep folders unique, so two layers can both be called "api"
    # without colliding. That is a mistake rather than a feature.
    existing = [layer.rsplit("/", 1)[-1] for layer in layers]
    if args.add and any(name.split("-", 1)[-1] == args.add for name in existing):
        clash = next(name for name in existing if name.split("-", 1)[-1] == args.add)
        print(f"Error: layer '{args.add}' already exists as {clash}.", file=sys.stderr)
        sys.exit(1)

    others = [path for path in layer_worktree_paths(repo_root, container) if path != host]

    busy = find_busy_layer(others)
    if busy:
        print(
            f"Error: {busy.name} has a rebase in progress; finish or abort it first.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Adding layer {folder}...")
    with layer_branches_released(others, stack_root):
        run_gh_stack(["top"], host)
        run_gh_stack(["add", layer_branch], host)
        run_git(["switch", layers[0]], cwd=str(host))

    run_git(["worktree", "add", str(layer_path), layer_branch], cwd=str(bare_dir))
    print(f"Created {layer_branch}")

    # The hook takes the worktree's path under the repo root, and a layer sits
    # one level down, inside its container.
    hook_arg = str(layer_path.relative_to(repo_root))
    run_setup_worktree_hook(repo_root, Path.cwd(), hook_arg, load_config(repo_root))
    print(f"Worktree created. To use it, run:\n\ncd {os.path.relpath(layer_path)}")


def write_stack_metadata(worktree_path: Path, metadata: dict) -> None:
    """Write gh stack's metadata back into the worktree's own git dir."""
    git_dir = resolve_git_dir(worktree_path)
    if not git_dir:
        print("Error: could not locate the stack metadata.", file=sys.stderr)
        sys.exit(1)
    (git_dir / STACK_METADATA_NAME).write_text(json.dumps(metadata, indent=2))


def rename_layer_in_metadata(metadata: dict, old: str, new: str) -> dict:
    """Point stack metadata at a renamed layer branch.

    git branch -m leaves gh stack's metadata naming a branch that no longer
    exists, so a rename has to rewrite both.
    """
    for stack in metadata.get("stacks", []):
        for entry in stack.get("branches", []):
            if entry.get("branch") == old:
                entry["branch"] = new
    return metadata


def cmd_stack_rename(args, repo_root: Path) -> None:
    """Rename a layer's folder, branch, and metadata entry together."""
    bare_dir = repo_root / ".bare"
    old_folder, new_folder = args.rename

    host = find_stack_host(repo_root, Path.cwd())
    if not host:
        print(
            "Error: not inside a stack. Run 'gh wt stack' to convert this worktree first.",
            file=sys.stderr,
        )
        sys.exit(1)

    metadata = read_stack_metadata(host) or {}
    layers = stack_layers(metadata)
    container = host.parent
    stack_root = stack_branch_root(layers[0])

    old_branch = layer_branch_name(stack_root, old_folder)
    if old_branch not in layers:
        print(f"Error: no layer named '{old_folder}'.", file=sys.stderr)
        sys.exit(1)

    new_branch = layer_branch_name(stack_root, new_folder)
    new_path = container / new_folder
    if new_path.exists() or local_branch_exists(bare_dir, new_branch):
        print(f"Error: layer '{new_folder}' already exists.", file=sys.stderr)
        sys.exit(1)

    old_path = container / old_folder
    if not args.force:
        ensure_rename_is_allowed(old_branch, "Rename")

    rename_stack_branch(old_branch, new_branch, bare_dir, old_path)
    write_stack_metadata(host, rename_layer_in_metadata(metadata, old_branch, new_branch))
    run_git(["worktree", "move", str(old_path), str(new_path)], cwd=str(bare_dir))

    print(f"Renamed layer {old_folder} to {new_folder}")
    print(f"Renamed branch {old_branch} to {new_branch}")


def cmd_stack_rebase(args, repo_root: Path) -> None:
    """Cascade the stack with its layer worktrees temporarily out of the way.

    gh stack rebase checks each layer out in the host, so every other worktree
    has to let go of its branch first.
    """
    host = find_stack_host(repo_root, Path.cwd())
    if not host:
        print(
            "Error: not inside a stack. Run 'gh wt stack' to convert this worktree first.",
            file=sys.stderr,
        )
        sys.exit(1)

    layers = stack_layers(read_stack_metadata(host) or {})
    container = host.parent
    stack_root = stack_branch_root(layers[0])
    others = [path for path in layer_worktree_paths(repo_root, container) if path != host]

    if args.rebase_continue:
        result = subprocess.run(
            ["gh", "stack", "rebase", "--continue"],
            cwd=str(host), capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            print(f"Error: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
            sys.exit(1)
        restored = restore_layer_worktrees(others, stack_root)
        print(f"Restored {restored} layer {'worktree' if restored == 1 else 'worktrees'}.")
        return

    busy = find_busy_layer(others)
    if busy:
        print(
            f"Error: {busy.name} has a rebase in progress; finish or abort it first.",
            file=sys.stderr,
        )
        sys.exit(1)

    released = release_layer_worktrees(others)
    count = len(released)
    print(f"Detaching {count} layer {'worktree' if count == 1 else 'worktrees'}...")

    print("Rebasing stack...")
    result = subprocess.run(
        ["gh", "stack", "rebase"],
        cwd=str(host), capture_output=True, text=True, check=False,
    )

    if result.returncode != 0:
        # Leave the layers detached: restoring them now would take back the
        # branch the half-finished rebase still needs.
        print(result.stdout.strip() or result.stderr.strip(), file=sys.stderr)
        print(file=sys.stderr)
        print(
            f"Resolve in {host.name}, then run 'gh wt stack rebase --continue'.",
            file=sys.stderr,
        )
        sys.exit(1)

    restored = restore_layer_worktrees(released, stack_root)
    print(f"Restored {restored} layer {'worktree' if restored == 1 else 'worktrees'}.")


def cmd_stack(args):
    """Convert the current worktree into the host for a stack of PRs."""
    if args.agent:
        print(AGENT_SPLIT_GUIDE)
        return

    repo_root = get_repo_root()
    if not repo_root:
        print("Error: Not in a bare-git repository", file=sys.stderr)
        sys.exit(1)

    if args.add:
        cmd_stack_add(args, repo_root)
        return

    if args.rename:
        cmd_stack_rename(args, repo_root)
        return

    if args.rebase or args.rebase_continue:
        cmd_stack_rebase(args, repo_root)
        return

    bare_dir = repo_root / ".bare"
    worktree_path = Path(run_git(["rev-parse", "--show-toplevel"]))
    branch = run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=str(worktree_path), check=False)
    trunk = get_default_branch_name(repo_root)

    if not branch or branch == "HEAD":
        print("Error: worktree is not on a branch. Check one out first.", file=sys.stderr)
        sys.exit(1)

    if branch == trunk:
        print(f"Error: refusing to stack the trunk branch '{branch}'.", file=sys.stderr)
        print("Create a feature worktree first: gh wt add <name>", file=sys.stderr)
        sys.exit(1)

    if read_stack_metadata(worktree_path):
        print(
            f"Error: {worktree_path.name} already hosts a stack. Run 'gh stack view' to see it.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not is_gh_stack_installed():
        print("Error: gh stack is not installed.", file=sys.stderr)
        print("Install it with: gh extension install github/gh-stack", file=sys.stderr)
        sys.exit(1)

    report_interrupted_move(worktree_path)

    folder = layer_folder_name(1, args.name or "")
    layer_branch = layer_branch_name(branch, folder)

    if not args.force:
        ensure_rename_is_allowed(branch, "Convert")

    enable_rerere(bare_dir)

    print(f"Converting {worktree_path.name} into a stack...")
    rename_stack_branch(branch, layer_branch, bare_dir, worktree_path)
    print(f"Renamed {branch} to {layer_branch}")

    layer_path = move_worktree_into_container(bare_dir, worktree_path, folder)
    print(f"Moved worktree to {worktree_path.name}/{folder}")

    result = subprocess.run(
        ["gh", "stack", "init", layer_branch],
        cwd=str(layer_path),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"Error: {result.stderr.strip() or result.stdout.strip()}", file=sys.stderr)
        sys.exit(1)

    print()
    print(f"Stack created: {trunk} <- {layer_branch}")
    print()
    print("Add the next layer with:")
    print()
    print("  gh wt stack add <name>")
    print()
    print("Your shell is still at the old path. To continue:")
    print()
    print(f"  cd {worktree_path.name}/{folder}")

    warn_if_bottom_layer_is_oversized(layer_path, trunk)


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


def prompt_branch_prefix() -> str:
    """Ask for a branch prefix to scaffold into the config; Enter means none."""
    return input("Branch prefix (e.g. billw/), Enter for none: ").strip().rstrip("/")


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

    config_path = write_config_scaffold(repo_root, script_name, prompt_branch_prefix())
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

    config_path = write_config_scaffold(repo_root, branch_prefix=prompt_branch_prefix())
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
    run_git(["fetch", "--prune", "origin"], cwd=str(bare_dir))

    folder_name = branch if branch_name else branch.split("/")[-1]

    if branch_prefix and not branch_name and "/" not in branch:
        checkout_branch, remote_exists = resolve_prefixed_branch(bare_dir, branch, branch_prefix)
    else:
        checkout_branch = branch_name or branch
        remote_exists = remote_branch_exists(bare_dir, checkout_branch)

    # Git only refuses the branch a worktree currently holds, so it would happily
    # check out a sibling layer here and only fail later, mid 'gh stack rebase'.
    hosting_stack = find_stack_worktree_for_branch(repo_root, checkout_branch)
    if hosting_stack:
        host_path, layer = hosting_stack
        container = host_path.parent
        folder = checkout_branch.rsplit("/", 1)[-1]
        print(
            f"Error: that branch is layer {layer} of the stack in {container.name}/.",
            file=sys.stderr,
        )
        print("It already has a worktree. Work on it there:", file=sys.stderr)
        print(file=sys.stderr)
        print(f"  cd {container.name}/{folder}", file=sys.stderr)
        sys.exit(1)

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


def find_stack_container(repo_root: Path, folder: str) -> Optional[Path]:
    """The container folder of a stack, when `folder` names one.

    A container is a plain directory, not a worktree: the layers inside it are
    the worktrees, and the bottom one carries the metadata.
    """
    container = repo_root / folder
    if not container.is_dir():
        return None

    hosts = [
        path
        for path in layer_worktree_paths(repo_root, container)
        if read_stack_metadata(path)
    ]
    return container if hosts else None


def remove_stack_container(
    container: Path,
    repo_root: Path,
    bare_dir: Path,
    force: bool,
    delete_remote: bool,
) -> bool:
    """Remove every layer worktree of a stack, then its branches and folder.

    A stack is all-or-nothing, so nothing is removed until every layer has been
    checked.
    """
    layer_paths = layer_worktree_paths(repo_root, container)
    host = next((path for path in layer_paths if read_stack_metadata(path)), None)
    layers = stack_layers(read_stack_metadata(host) or {}) if host else []

    if not force:
        for index, candidate in enumerate(layers, start=1):
            if is_branch_safe_to_delete(candidate, str(bare_dir)):
                continue
            print(
                f"Error: layer {index} ({candidate}) has unpushed commits. "
                f"Use --force to override.",
                file=sys.stderr,
            )
            sys.exit(1)

    layer_word = "layer" if len(layers) == 1 else "layers"
    print(f"Removing stack {container.name} ({len(layers)} {layer_word}).")

    for path in layer_paths:
        if not remove_worktree(str(path), str(bare_dir), force):
            return False

    for candidate in layers:
        run_git(["branch", "-D", candidate], cwd=str(bare_dir), check=False)
        if delete_remote:
            delete_remote_branch(candidate, bare_dir)

    if container.exists() and not any(container.iterdir()):
        container.rmdir()

    print(f"Removed {container.name}")
    return True


def find_host_beside(worktree_path: Path) -> Optional[Path]:
    """The stack host sharing this worktree's container, if the container holds one.

    Reads the filesystem only. The removal paths this guards run inside tightly
    mocked git sequences, and a stray git call there would break them.
    """
    container = worktree_path.parent
    if not container.is_dir():
        return None

    for sibling in sorted(container.iterdir()):
        if sibling.is_dir() and read_stack_metadata(sibling):
            return sibling
    return None


def refuse_removing_a_lone_layer(repo_root: Path, worktree_path: Path, branch: str) -> None:
    """Stop a layer worktree being removed on its own, which would kill its branch."""
    container = worktree_path.parent
    if container == repo_root:
        return

    host = find_host_beside(worktree_path)
    if not host:
        return

    layers = stack_layers(read_stack_metadata(host) or {})
    position = layers.index(branch) + 1 if branch in layers else len(layers)
    print(
        f"Error: {worktree_path.name} is layer {position} of the stack in {container.name}/.",
        file=sys.stderr,
    )
    print("Remove the whole stack instead:", file=sys.stderr)
    print(file=sys.stderr)
    print(f"  gh wt rm {container.name}", file=sys.stderr)
    sys.exit(1)


def cmd_rm(args):
    """Remove a worktree or all worktrees with merged or closed PRs."""
    folder = args.folder
    delete_remote = args.delete_remote
    merged = args.merged
    force = args.force
    invocation_dir = Path.cwd()
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
        removed_paths = []
        skipped_branches = []
        failed_folders = []

        # Removing a stack takes every layer at once, so later entries in this
        # snapshot may already be gone by the time the loop reaches them.
        swept: set[Path] = set()

        for folder_name, branch, worktree_path in worktrees:
            # Skip detached or unknown branches
            if branch.startswith("("):
                continue

            if Path(worktree_path) in swept:
                continue

            # Read layers before removal: stack metadata lives inside the
            # worktree's git dir and is destroyed along with the worktree.
            metadata = read_stack_metadata(Path(worktree_path))
            layers = stack_layers(metadata) if metadata else []

            # A layer above the bottom carries no metadata of its own. Sweeping
            # it here would delete a live layer branch out of an intact stack.
            if not layers and find_host_beside(Path(worktree_path)):
                continue

            # Layers spread across their own worktrees share a container folder
            # below the repo root; a host sitting directly at the root is the
            # older single-worktree layout and still removes in place.
            container = Path(worktree_path).parent
            if layers and container != repo_root:
                if all(is_pr_removable(candidate) for candidate in layers):
                    unsafe = [
                        candidate for candidate in layers
                        if not is_branch_safe_to_delete(candidate, str(bare_dir))
                    ]
                    if not force and unsafe:
                        skipped_branches.extend(unsafe)
                        continue
                    swept.update(layer_worktree_paths(repo_root, container))
                    if remove_stack_container(
                        container, repo_root, bare_dir, force, delete_remote
                    ):
                        removed_paths.append(container)
                    else:
                        failed_folders.append(container.name)
                continue

            doomed_branches = layers or [branch]

            # A stack is all-or-nothing: every layer's PR must be finished.
            if all(is_pr_removable(candidate) for candidate in doomed_branches):
                # Check safety BEFORE removing anything
                unsafe = [
                    candidate for candidate in doomed_branches
                    if not is_branch_safe_to_delete(candidate, str(bare_dir))
                ]
                if not force and unsafe:
                    skipped_branches.extend(unsafe)
                    continue  # Skip this worktree entirely

                if layers:
                    layer_word = "layer" if len(layers) == 1 else "layers"
                    print(f"Removing stack worktree {folder_name} ({len(layers)} {layer_word}).")
                else:
                    print(f"Removing {folder_name} ({branch})...")
                if not remove_worktree(worktree_path, str(bare_dir), force):
                    # Keep going: one stuck folder shouldn't strand the rest.
                    failed_folders.append(folder_name)
                    continue

                for candidate in doomed_branches:
                    if layers:
                        # A stack deletes several branches; one stale ref must
                        # not strand the layers behind it.
                        run_git(["branch", "-D", candidate], cwd=str(bare_dir), check=False)
                    else:
                        run_git(["branch", "-D", candidate], cwd=str(bare_dir))

                    # Optionally delete from remote
                    if delete_remote:
                        delete_remote_branch(candidate, bare_dir)

                print(f"Removed {folder_name}")
                removed_paths.append(Path(worktree_path))

        hint_if_cwd_removed(invocation_dir, removed_paths)

        if not removed_paths:
            print("No merged or closed worktrees to remove")
        else:
            print(f"Removed {len(removed_paths)} merged or closed worktree(s)")

        if skipped_branches:
            print(
                f"Error: Local branch(es) have unpushed commits: {', '.join(skipped_branches)}. "
                f"Push changes first.",
                file=sys.stderr
            )

        if failed_folders:
            print(
                f"Error: Could not remove: {', '.join(failed_folders)}. "
                f"Their branches were left in place.",
                file=sys.stderr
            )

        if skipped_branches or failed_folders:
            sys.exit(1)
    else:
        container = find_stack_container(repo_root, folder)
        if container:
            if not remove_stack_container(
                container, repo_root, bare_dir, force, delete_remote
            ):
                sys.exit(1)
            hint_if_cwd_removed(invocation_dir, [container])
            return

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
            if not remove_worktree(str(worktree_path), str(bare_dir), force):
                sys.exit(1)
            print(f"Removed {folder}")
            hint_if_cwd_removed(invocation_dir, [worktree_path])
            return

        refuse_removing_a_lone_layer(repo_root, worktree_path, branch)

        # Read layers before removal: stack metadata lives inside the worktree's
        # git dir and is destroyed along with the worktree.
        metadata = read_stack_metadata(worktree_path)
        layers = stack_layers(metadata) if metadata else []
        doomed_branches = layers or [branch]

        # Check safety BEFORE removing anything
        if not force:
            for index, candidate in enumerate(doomed_branches, start=1):
                if is_branch_safe_to_delete(candidate, str(bare_dir)):
                    continue
                if layers:
                    print(
                        f"Error: layer {index} ({candidate}) has unpushed commits. "
                        f"Use --force to override.",
                        file=sys.stderr
                    )
                else:
                    print(
                        f"Error: Local branch '{candidate}' has unpushed commits. "
                        f"Push changes first.",
                        file=sys.stderr
                    )
                sys.exit(1)

        if layers:
            layer_word = "layer" if len(layers) == 1 else "layers"
            print(f"Removing stack worktree {folder} ({len(layers)} {layer_word}).")
        else:
            print(f"Removing {folder} ({branch})...")
        if not remove_worktree(str(worktree_path), str(bare_dir), force):
            sys.exit(1)

        for candidate in doomed_branches:
            if layers:
                # A stack deletes several branches; one stale ref must not
                # strand the layers behind it.
                run_git(["branch", "-D", candidate], cwd=str(bare_dir), check=False)
            else:
                run_git(["branch", "-D", candidate], cwd=str(bare_dir))

            # Optionally delete from remote
            if delete_remote:
                delete_remote_branch(candidate, bare_dir)

        print(f"Removed {folder}")
        hint_if_cwd_removed(invocation_dir, [worktree_path])


def get_pr_info(branch: str) -> Optional[dict]:
    """Get PR info for a branch using gh CLI.

    Returns dict with number, state, url or None if no PR/error.
    """
    try:
        result = subprocess.run(
            ["gh", "pr", "view", branch, "--json", "number,state,url,isDraft"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            return json.loads(result.stdout)
    except FileNotFoundError:
        return {"error": "gh CLI not installed"}
    except Exception:
        pass
    return None


def is_pr_removable(branch: str) -> bool:
    """Report whether a branch's PR is merged or closed."""
    pr_info = get_pr_info(branch)
    return bool(pr_info) and pr_info.get("state") in REMOVABLE_PR_STATES


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


def print_stack_layers(folder_name: str, status_msg: str, view: dict, number: Optional[int]) -> bool:
    """Print a stack worktree as an ordered layer list; report if it needs attention."""
    layers = view.get("branches", [])
    current = view.get("currentBranch", "")
    position = next(
        (index for index, layer in enumerate(layers, start=1) if layer.get("name") == current),
        None,
    )

    heading = f"#{number} - " if number else ""
    position_msg = f", on layer {position}" if position else ""
    layer_word = "layer" if len(layers) == 1 else "layers"
    print(folder_name)
    print(f"  Stack: {heading}{len(layers)} {layer_word}{position_msg}")
    print(f"  Status: {status_msg}")
    print("  Layers:")

    width = max((len(layer.get("name", "")) for layer in layers), default=0)
    needs_attention = False

    for index, layer in enumerate(layers, start=1):
        name = layer.get("name", "")
        pr = layer.get("pr")
        if pr:
            state = "MERGED" if layer.get("isMerged") else pr.get("state", "")
            pr_msg = f"#{pr.get('number')} ({state})"
        else:
            pr_msg = "(no PR)"

        # No "current" marker: every layer is checked out in its own worktree,
        # so pointing at the host's branch would read as the others being away.
        marker_msg = ""
        if layer.get("needsRebase"):
            marker_msg = "   <- needs rebase"
            needs_attention = True
        print(f"    {index}  {name.ljust(width)} {pr_msg}{marker_msg}")

    return needs_attention


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

                # A stack renders once, under its container. Layers above the
                # bottom carry no metadata, so they are skipped here and listed
                # by the host instead of appearing as worktrees of their own.
                metadata = read_stack_metadata(worktree_path)
                if not metadata and find_host_beside(worktree_path):
                    continue

                if metadata:
                    stack_view = get_stack_view(worktree_path)
                    if stack_view:
                        container = worktree_path.parent
                        heading = (
                            container.name if container != repo_root else folder_name
                        )
                        if print_stack_layers(
                            heading, status_msg, stack_view, stack_number(metadata)
                        ):
                            needs_attention = True
                        print()
                        continue

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
                            state = pr_info['state']
                            if state == 'OPEN' and pr_info.get('isDraft'):
                                state = 'DRAFT'
                            pr_msg = f"#{pr_info['number']} ({state}) - {pr_info['url']}"
                            if state == 'MERGED':
                                branch_deleted = True

                print(f"{folder_name}")
                print(f"  Branch: {branch}")
                print(f"  Status: {status_msg}")
                if origin_msg:
                    print(f"  Origin: {origin_msg}")
                print(f"  PR: {pr_msg}")
                print()

    sys.exit(1 if needs_attention else 0)


STACK_USAGE = """\
gh wt stack [NAME] [-f]
       gh wt stack add NAME
       gh wt stack rename OLD NEW [-f]
       gh wt stack rebase [--continue]
       gh wt stack agent"""

STACK_VERBS = ("add", "rename", "rebase", "agent")


def parse_stack_verb(verb: str, argv: list[str]) -> argparse.Namespace:
    """Parse `gh wt stack <verb> ...` into the namespace the old flags produce.

    The verbs are reserved words in the spot where layer 1's name goes, so
    they are split off before the stack parser, which would read them as NAME.
    """
    parser = argparse.ArgumentParser(prog=f"gh wt stack {verb}", allow_abbrev=False)
    if verb == "add":
        parser.add_argument("add", metavar="NAME",
                            help="Name for the new layer; it is numbered for you")
    elif verb == "rename":
        parser.add_argument("rename", nargs=2, metavar=("OLD", "NEW"),
                            help="Layer folder names, e.g. 01 01-base")
        parser.add_argument("-f", "--force", action="store_true",
                            help="Rename even when it will close an open PR")
    elif verb == "rebase":
        parser.add_argument("--continue", dest="rebase_continue", action="store_true",
                            help="Finish a rebase that stopped on a conflict")

    args = argparse.Namespace(
        command="stack", name=None, force=False, add=None, rename=None,
        rebase=verb == "rebase", rebase_continue=False, agent=verb == "agent",
    )
    return parser.parse_args(argv, namespace=args)


def cli(argv: list[str] | None = None):
    """Entry point. Parses argv and dispatches to subcommand."""
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) > 1 and argv[0] == "stack" and argv[1] in STACK_VERBS:
        args = parse_stack_verb(argv[1], argv[2:])
        check_current_dir()
        cmd_stack(args)
        return

    parser = argparse.ArgumentParser(
        prog="gh-wt",
        description="Manage bare-git worktrees.",
        epilog="Stacked PRs: run 'gh wt stack' inside a worktree. Agents: gh wt stack agent",
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

    p_stack = subparsers.add_parser(
        "stack", help="Convert this worktree into the host for a stack of PRs",
        usage=STACK_USAGE,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "subcommands:\n"
            "  add NAME         Add a layer on top of the stack, in its own worktree\n"
            "  rename OLD NEW   Rename a layer's folder, branch, and metadata\n"
            "  rebase           Cascade the stack, freeing layer worktrees first\n"
            "  agent            Print the layer-splitting procedure for AI agents\n"
            "\n"
            "add, rename, rebase, and agent are reserved: they cannot name layer 1."
        ),
    )
    p_stack.add_argument("name", nargs="?", default=None,
                         help="Name for layer 1 (default: unnamed, folder '01')")
    p_stack.add_argument("-f", "--force", action="store_true",
                         help="Rename even when it will close an open PR")
    # The flag spellings predate the subcommands and stay as hidden aliases.
    p_stack.add_argument("-n", "--add", default=None, help=argparse.SUPPRESS)
    p_stack.add_argument("--rename", nargs=2, default=None, help=argparse.SUPPRESS)
    p_stack.add_argument("--rebase", action="store_true", help=argparse.SUPPRESS)
    p_stack.add_argument("--continue", dest="rebase_continue", action="store_true",
                         help=argparse.SUPPRESS)
    p_stack.add_argument("--agent", action="store_true", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)

    if args.command is None:
        parser.print_help()
        sys.exit(0)

    check_current_dir()

    dispatch = {
        "clone": cmd_clone,
        "init": cmd_init,
        "list": cmd_list,
        "add": cmd_add,
        "rm": cmd_rm,
        "status": cmd_status,
        "stack": cmd_stack,
    }
    dispatch[args.command](args)


if __name__ == "__main__":
    cli()
