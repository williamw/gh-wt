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
