# gh-wt

GitHub CLI extension for managing repositories that use a bare-git worktree layout.

`gh wt` keeps one bare repository in `.bare/` and checks branches out as sibling
worktree directories:

```text
repo/
├── .bare/
├── main/
├── feature-a/
└── feature-b/
```

## Installation

Install from GitHub:

```bash
gh extension install williamw/gh-wt
```

For local development, clone and install from the working copy:

```bash
git clone https://github.com/williamw/gh-wt.git
cd gh-wt
gh extension install .
```

## Commands

Clone a repository into the `.bare/` layout and create the default branch worktree:

```bash
gh wt clone owner/repo
```

Add a worktree for a branch:

```bash
gh wt add feature-branch
```

Branch names with slashes keep the branch name intact and use the final path
segment as the folder name:

```bash
gh wt add user/feature-branch
# folder: feature-branch
# branch: user/feature-branch
```

Use `--branch-name` when the folder name should differ from the branch name:

```bash
gh wt add feature-branch --branch-name user/feature-branch
gh wt add feature-branch -b user/feature-branch
```

`--base-branch` controls the source branch for new worktrees. It does not rename
the new branch. Use `-B` as the short form:

```bash
gh wt add feature-branch --base-branch main
gh wt add feature-branch -B main
```

`--local` creates a branch locally without pushing to origin. Use `-l` as the
short form:

```bash
gh wt add feature-branch --local
gh wt add feature-branch -l
```

After a worktree is created, `gh wt add` looks in the current directory for an
executable `setup-worktree.sh` and runs it with the new worktree folder name:

```bash
./setup-worktree.sh feature-branch
```

If the setup script exits non-zero, `gh wt add` exits non-zero too. The created
worktree is left in place.

Remove a worktree:

```bash
gh wt rm feature-branch
```

`rm` resolves the actual checked-out branch from the worktree before deleting
the local branch, so the folder name does not need to match the branch name. Add
`-d` or `--delete-remote` to delete the resolved branch from `origin` too:

```bash
gh wt rm feature-branch -d
```

Remove all worktrees whose associated pull requests are merged or closed:

```bash
gh wt rm --merged
```

Use `--force` to bypass the local branch safety check when you know you want to
delete a worktree with unpushed or untracked work.

If Git refuses to remove a worktree because it contains submodules, `gh wt rm`
deinitializes submodules in that worktree and retries with `--force` before
deleting the branch.

`gh wt rm <folder>` can also remove Git-registered worktrees outside the repo
root, such as temporary worktrees under `/private/tmp`, by matching the folder
basename shown by `gh wt status`. If that worktree is detached, `gh wt rm`
removes the worktree only and skips local or remote branch deletion.

If the worktree folder was already deleted but Git still has stale worktree
metadata for that folder, `gh wt rm <folder>` automatically runs
`git worktree prune -v` to clear the stale record. This cleanup does not delete
local or remote branches because the checked-out branch can no longer be safely
resolved from the missing worktree.

List worktrees:

```bash
gh wt list
```

Show status for all worktrees:

```bash
gh wt status
```

## Requirements

- GitHub CLI (`gh`)
- Git
- Python 3.10+
- Bare-git layout created by `gh wt clone`, or an existing repo with `.bare/`

## Development

Install dependencies:

```bash
pixi install
```

Run tests:

```bash
pixi run test
```

Install the local checkout as the active `gh wt` extension:

```bash
gh extension remove gh-wt 2>/dev/null || true
gh extension install .
gh wt --help
```
