# gh-wt: Agent Guide

## Overview

`gh-wt` is a GitHub CLI extension for repositories that use a bare-git
worktree layout. It stores the bare repository in `.bare/` and checks branches
out as sibling directories.

## Layout

```text
gh-wt/
├── gh-wt          # GitHub CLI extension entry point
├── gh_wt.py       # Main implementation module
├── test_gh_wt.py  # pytest suite
├── pixi.toml      # Pixi environment/tasks
└── README.md      # User-facing docs
```

## Commands

```bash
gh wt clone <owner/repo>
gh wt init
gh wt add [<branch-or-folder-or-linear-url>] [-B|--base-branch <branch>] [-b|--branch-name <branch>] [-l|--linear <url>] [-L|--local]
gh wt list
gh wt status
gh wt rm <folder> [-d|--delete-remote] [-f|--force]
gh wt rm --merged [-d|--delete-remote] [-f|--force]
gh wt stack [NAME] [-f|--force]
gh wt stack add NAME [-L|--local]
gh wt stack rename OLD NEW [-f|--force]
gh wt stack rebase [--no-trunk | --continue]
gh wt stack agent
```

## Development

Install dependencies:

```bash
pixi install
```

Run tests:

```bash
pixi run test
```

Install this checkout as the local `gh wt` extension:

```bash
gh extension remove gh-wt 2>/dev/null || true
gh extension install .
gh wt --help
```

## Key Design Decisions

- **Subprocess over a Git library:** direct `git` CLI calls keep dependencies
  minimal and respect the user's Git config.
- **Bare repo in `.bare/`:** enables sibling worktree directories such as
  `repo/main` and `repo/feature-x`.
- **Branch/folder split:** `gh wt add folder --branch-name user/feature`
  lets the worktree folder differ from the branch name.
- **Clone scaffolds the setup hook:** `gh wt clone` writes an executable starter
  `worktree-setup.sh` (see `SETUP_WORKTREE_TEMPLATE`) into the repo root that
  validates the worktree folder and leaves a commented slot for project setup.
  It must not `cd`/`exec` into a subshell — the hook runs as a subprocess, so a
  bare `cd` cannot change the parent shell, and an `exec`'d shell traps
  non-interactive callers.
- **Config file is TOML, not YAML:** `worktree-config.toml` in the repo root is
  parsed with stdlib `tomllib` (Python 3.11+), keeping the extension free of
  third-party dependencies. Keys: `branch-prefix`, `setup-script`. Missing file
  means all behavior is unchanged; invalid TOML is a hard error.
- **Branch-prefix exemption rules:** the prefix applies only to bare names (no
  `/`) without `--branch-name`; lookup prefers `origin/<prefix>/<name>` over
  `origin/<name>` (`resolve_prefixed_branch`), else creates `<prefix>/<name>`.
  A positional prefix in `--linear` mode beats the config prefix.
- **Setup-script naming transition:** `worktree-setup.sh` is the current name,
  `setup-worktree.sh` the legacy one. A configured `setup-script` resolves from
  the repo root and must be executable (hard error otherwise); without the key,
  both defaults are tried in the invocation dir and finding both is an error.
- **`init` migrates and converts:** in a bare layout it fixes the fetch refspec
  and scaffolds config (offering the script rename); in a normal clone it
  converts in place (`.git` → `.bare`) after requiring a fully clean tree, so
  local branches, stashes, and reflog survive. Prompts (`confirm`) live only in
  `init` — `add` must stay non-interactive.
- **Fetch refspec is managed:** `git clone --bare` writes no fetch refspec, so
  remote-tracking refs never update. `ensure_remote_tracking_refspec` installs
  `+refs/heads/*:refs/remotes/origin/*` in `clone` and `init`; remote-branch
  detection assumes it.
- **Base branch naming:** `--base-branch` / `-B` selects the source branch for
  new worktrees; do not reintroduce the old `--base` option.
- **Linear owns `-l`:** `--linear` / `-l` names the branch from a Linear issue
  URL (`parse_linear_issue_url`); `--local` uses `-L`. Do not give `-l` back to
  `--local`.
- **Remove resolves the real branch:** `rm <folder>` inspects the worktree's
  checked-out branch before safety checks, branch deletion, and optional remote
  deletion.
- **Submodule-aware removal:** `remove_worktree()` retries with submodule
  deinitialization and `git worktree remove --force` only when Git reports that
  submodules block removal.
- **Removal returns a bool and cleans up after Git:** Git empties a worktree and
  then rmdir's the folder, so anything that reappears mid-delete (macOS
  rewriting `.DS_Store`) fails the command with "Directory not empty" — after
  Git has already unlinked its own metadata. `remove_worktree()` deletes the
  leftovers itself and runs `git worktree prune`, then reports success as a
  bool rather than exiting. `rm --merged` records failures and keeps going so
  one stuck folder cannot strand the worktrees behind it, exiting non-zero at
  the end and leaving the failed worktree's branch in place.
- **One folder per layer:** conversion moves the worktree into a container
  (`some-feature/` becomes a plain directory holding `01-base/`), so layers can
  be worked in parallel. Git refuses to move a worktree into its own
  subdirectory, so `move_worktree_into_container` parks it at a sibling first;
  an interrupted run is reported by `report_interrupted_move` rather than
  guessed at.
- **Folder path equals branch path:** a layer folder and its branch spell the
  same thing under the prefix. That forces conversion to rename layer 1, since
  a ref cannot be both a leaf and a directory - `billw/some-feature` existing
  blocks `billw/some-feature/02-api` from being created at all, locally and on
  origin.
- **Renaming layer 1 closes its PR:** GitHub closes any PR whose head branch is
  renamed, and no API avoids it. `ensure_rename_is_allowed` prompts on a
  terminal and hard-fails a captured run, so an agent cannot close a PR by
  omission. `--force` is the only way through.
- **Fetches prune:** renaming a branch leaves a stale remote-tracking ref that
  makes the next plain `git fetch` fail with a directory/file conflict, so
  every fetch gh-wt runs passes `--prune`.
- **The bottom layer is the host:** it carries gh stack's metadata and is the
  one layer without its own separate worktree, because `gh stack view --json`
  exits 2 on a detached HEAD and `gh wt status` would silently lose the stack.
- **gh stack only adds at the top:** so `gh wt stack add` detaches the layer
  worktrees, climbs with `gh stack top`, adds, parks the host back on layer 1,
  and reattaches. Detaching preserves each working tree exactly, so an agent's
  uncommitted work survives.
- **`stack add` pushes like `add`:** the new layer goes to origin with `-u`
  unless `-L`/`--local`. The worktree already exists by then, so a failed push
  warns instead of failing; `gh stack submit` pushes it later anyway.
- **Restoring needs no saved state:** `restore_layer_worktrees` derives each
  branch from the folder name, which is why `rebase --continue` can pick up
  in a later process after a conflict.
- **`gh stack rebase` cannot run directly:** it checks each layer out in the
  host and fails on any branch another worktree holds. `gh wt stack rebase`
  wraps it in the release/restore envelope, and leaves the layers detached on
  conflict rather than taking back a branch the half-finished rebase needs.
- **Removal guards are filesystem-only:** `find_host_beside` reads sibling
  directories instead of calling git, because the removal paths run inside
  tightly mocked `run_git` sequences that a stray call would break.
- **`rm` never takes one layer:** a layer worktree has no metadata of its own,
  so the old code deleted its branch as an ordinary worktree - and `rm --merged`
  would sweep a whole stack layer by layer as each PR landed.
  `refuse_removing_a_lone_layer` and the container branch in the sweep stop
  both.
- **The old single-worktree layout still works:** a host sitting directly at the
  repo root is pre-container and removes in place, which is why both removal
  paths compare the container against `repo_root` before treating it as a stack
  folder.
- **The `add` guardrail exists because Git does not guard:** Git refuses only
  the branch a worktree *currently* holds, so `gh wt add <sibling-layer>`
  succeeds and fails days later mid-rebase. `find_stack_worktree_for_branch`
  blocks it up front and points at the layer's own folder.
- **Stack detection is filesystem-only:** `read_stack_metadata` resolves the
  worktree's git dir from its `.git` pointer file rather than calling
  `git rev-parse --git-dir`. gh stack stores metadata per-worktree
  (`.bare/worktrees/<name>/gh-stack`), never in the common dir.
- **Metadata is written, not just read:** `git branch -m` leaves gh stack naming
  a branch that no longer exists, so `rename` rewrites the metadata too.
- **Live PR state comes from `gh stack view --json`:** one call replaces N
  `gh pr view` calls for a stack. It exits 2 outside a stack and omits the `pr`
  key before submit, so both are handled as normal, not as errors.
- **Agent-facing text is frozen:** `AGENT_SPLIT_POINTER` and
  `AGENT_SPLIT_GUIDE` are string literals with no interpolation. Formatting a
  branch name or PR title into them would turn remote-controlled data into
  agent instructions.
- **`output_is_piped()` gates the agent block and the PR prompt:** captured
  stdout means an agent is reading. It is a named seam because
  `io.StringIO.isatty` cannot be patched in tests.
- **Stack verbs are reserved words, not subparsers:** `gh wt stack NAME`
  already takes a positional, so `cli()` splits `add`/`rename`/`rebase`/`agent`
  off before argparse (`parse_stack_verb`) and builds the same namespace the
  old `--add`/`--rename`/`--rebase`/`--agent` flags produce. Those flags stay
  as hidden aliases; a verb cannot name layer 1.
- **`gh wt stack` refuses trunk:** `gh stack init main` succeeds and creates a
  nonsense `main <- main` stack. It also refuses detached HEADs, already-stacked
  worktrees (`gh stack init` is not idempotent), and a missing extension.
- **`rerere` is set by gh-wt:** `gh stack init` does not enable it despite its
  README. `enable_rerere` writes to the shared `.bare/config`.
- **Deleted current directory is handled once:** `check_current_dir()` runs in
  `cli()` just before dispatch, so every command reports the same friendly error
  instead of a `Path.cwd()` traceback. It reads the vanished path from `$PWD`,
  which survives as a string, and walks up to the nearest surviving ancestor for
  the suggested `cd`. `rm` additionally calls `hint_if_cwd_removed()` to flag the
  stranding at its source, since `rm` is what usually causes it.

## Testing Notes

- Tests exercise behavior through public command paths and helper functions.
- External Git/GitHub operations are mocked where possible.
- Keep command-line behavior backward-compatible unless the README is updated in
  the same change.
