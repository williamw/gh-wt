# gh-wt: Agent Guide

`gh-wt` manages a bare repository in `.bare/` with independent worktrees.
The root `.git` file points at `.bare/`; folder aliases do not identify branches
or stacks. See README.md for ordinary clone/init/add configuration.

## Commands

```bash
gh wt clone <owner/repo>
gh wt init
gh wt add <folder-or-branch> [-B <base>] [-b <branch>] [-L]
gh wt list
gh wt status
gh wt rm <folder> [--keep-branch] [--dry-run] [-d] [-f]
gh wt rm --merged [--keep-branch] [--dry-run] [-d] [-f]
gh wt stack setup [--dry-run]
gh wt stack add <name> [-L]
gh wt stack agent
```

## Native stacks

- Require gh stack >=0.3.0 for stack operations. Use supported
  `gh stack view --json` for membership and PR state. Never read or write private
  stack catalog files. Resolve actual owners from Git worktree porcelain output.
- A shared catalog can contain several stacks. Group status by trunk and ordered
  membership, never flatten all branches into one stack. Native 0.3.0 JSON has no
  stack number; identify a stack by its bottom branch and trunk instead.
- Native gh stack owns membership, rebase, publication, merging and recovery.
  There is no wrapper rebase journal, detach/restore operation, stack conversion,
  grouped container creation or branch renaming. Old commands and flags must fail
  with guidance to gh stack init/link and gh wt stack setup.
- Setup preflights all destinations, reuses existing owners unchanged, and runs
  normal hooks only for newly created worktrees. Dry run creates no dirs/refs and
  runs no hooks. Existing nested worktrees stay in place.
- Stack add creates a flat worktree for a new branch at the top layer, then uses
  public native add adoption from the top owner. If the top has no owner, create
  the new destination on top first and let native add switch only that new
  worktree. Never move another worktree's HEAD. Push only the new branch with its
  own upstream unless -L; run its hook after adoption and any push.
- Preserve dirty/unrelated worktrees, untracked/ignored data and existing HEADs.
  Refuse busy affected owners and collisions before mutation. On partial setup,
  native adoption, push or hook failure, name the retained worktree and recovery.
- Removal targets one registered worktree at a time. --keep-branch retains the
  local branch, including active stack branches; it conflicts with --delete-remote.
  Without it, active members cannot be deleted even with --force. Unavailable or
  ambiguous membership blocks branch deletion. --merged dry run previews real
  eligible targets and never removes a whole stack/container automatically.
- Agent-facing guidance stays static: never interpolate remote branch names or
  PR titles into agent instructions. Diagnostic data must remain diagnostic data.

## Ordinary behavior to preserve

- Subprocess Git commands respect user configuration; Python stdlib only.
- Config is worktree-config.toml, parsed with tomllib. Prefixes apply to bare
  names; explicit -b bypasses them. -B selects base; -l selects Linear; -L is local.
- Hooks receive the folder path relative to repo root. A configured script must
  be executable; conflicting default names are an error. Hooks run as subprocesses
  and cannot change the caller's shell directory.
- init conversion preserves ignored files in .gh-wt-ignored before moving them
  into the new checkout. Preserve interrupted staging and refuse guessing.
- clone/init manage the remote fetch refspec and root .git pointer. Fetches prune.
- rm resolves actual branch refs, supports external registered paths, and
  preserves branches when stale worktree records are pruned. Submodule and
  post-removal leftover handling remain in remove_worktree.
- Branch deletion requires pushed commits or commits present on origin/trunk,
  unless explicit --force. --keep-branch skips this ref-deletion check while
  still guarding local/ignored files. Do not broaden force implicitly.
- Handle a deleted current directory with check_current_dir and removal hints.

## Development and verification

Use the repository-owned Makefile and .devcontainer configuration. `make check`
runs the Pixi-backed toolchain in a container; no host dependency installs.
Retain ordinary regressions and exercise native-stack behavior with isolated
real-Git fixtures, without credentials, real PR mutations or service changes.
Verify before claiming completion. Installing this checkout globally requires
separate intent; normal implementation and test work must not replace an active
extension link.
