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
gh wt add <branch-or-folder> [-B|--base-branch <branch>] [-b|--branch-name <branch>]
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
- **Base branch naming:** `--base-branch` / `-B` selects the source branch for
  new worktrees; do not reintroduce the old `--base` option.
- **Remove resolves the real branch:** `rm <folder>` inspects the worktree's
  checked-out branch before safety checks, branch deletion, and optional remote
  deletion.
- **Submodule-aware removal:** `remove_worktree()` retries with submodule
  deinitialization and `git worktree remove --force` only when Git reports that
  submodules block removal.

## Testing Notes

- Tests exercise behavior through public command paths and helper functions.
- External Git/GitHub operations are mocked where possible.
- Keep command-line behavior backward-compatible unless the README is updated in
  the same change.
