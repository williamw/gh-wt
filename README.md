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

`clone` also writes an executable starter `worktree-setup.sh` and a
`worktree-config.toml` into the new repo root, and configures the remote
fetch refspec so `git fetch` keeps `origin/*` remote-tracking refs up to
date. The starter setup script validates the new worktree folder and leaves
a commented slot for project-specific setup (copying `.env` files, installing
dependencies, and so on). Edit it to taste, or delete it if you do not want
the hook to run.

Set up an existing repo — either a bare-git layout that predates the config
file, or a plain clone you want converted to the worktree layout:

```bash
gh wt init
```

In a bare-layout repo, `init` fixes the fetch refspec if it is missing and
scaffolds `worktree-config.toml`, asking for an optional branch prefix
(Enter for none). If it finds the legacy `setup-worktree.sh`, it offers to
rename it to `worktree-setup.sh`; either way the config records the name in
use. In a normal clone, `init` offers to convert it in place:
`.git/` becomes `.bare/`, the checked-out branch moves into its own worktree
folder, and local branches, stashes, and reflog all survive. Conversion
requires a fully clean tree (no modified, staged, or untracked files).

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

`--local` creates a branch locally without pushing to origin. Use `-L` as the
short form (`-l` now belongs to `--linear`):

```bash
gh wt add feature-branch --local
gh wt add feature-branch -L
```

`--linear` names the branch from a Linear issue URL. Use `-l` as the short
form. The issue ID and title slug become the branch name, and a positional
argument acts as a branch prefix (with or without a trailing slash):

```bash
gh wt add billw/ -l https://linear.app/modularml/issue/MKT-176/add-redirect-for-mojo-package-submission-page
# branch: billw/MKT-176-add-redirect-for-mojo-package-submission-page
# folder: MKT-176-add-redirect-for-mojo-package-submission-page

gh wt add -l https://linear.app/modularml/issue/MKT-176/add-redirect-for-mojo-package-submission-page
# branch: MKT-176-add-redirect-for-mojo-package-submission-page
```

A URL without a title slug yields just the issue ID; query strings, fragments,
and trailing slashes are ignored. `--linear` cannot be combined with
`--branch-name`, since both control the branch name.

Pasting a Linear issue URL as the positional argument works without the flag —
it is detected automatically and behaves exactly like `-l`:

```bash
gh wt add https://linear.app/modularml/issue/MKT-176/add-redirect-for-mojo-package-submission-page
```

## Configuration

Each repo can have a `worktree-config.toml` in the repo root (next to the
setup script). Both keys are optional:

```toml
setup-script = "worktree-setup.sh"
branch-prefix = "billw"
```

`branch-prefix` prepends `<prefix>/` to bare branch names. `gh wt add foo`
first checks out `origin/billw/foo` if it exists, then `origin/foo`, and
otherwise creates a new `billw/foo` branch. The prefix never applies when the
name already contains a `/` (full branch names, including other people's
branches), when `--branch-name` is given (exact names stay exact), or when a
positional prefix is used with `--linear` (it wins over the config). Linear-
derived branch names do get the prefix.

`setup-script` names the setup hook, resolved relative to the repo root. When
set, the script must exist and be executable — a missing configured script is
an error, not a silent skip.

Without the key, `gh wt add` looks in the current directory for an executable
`worktree-setup.sh`, then the legacy `setup-worktree.sh`. Finding both is an
error, since the choice is ambiguous — set `setup-script` to pick one. Repos
still using the legacy name without a config get a one-line hint pointing at
`gh wt init`.

The hook runs after the worktree is created, with the new folder name as its
argument:

```bash
./worktree-setup.sh feature-branch
```

If the setup script exits non-zero, `gh wt add` exits non-zero too. The created
worktree is left in place.

Remove a worktree:

```bash
gh wt rm feature-branch
```

`rm` resolves the actual checked-out branch from the worktree before deleting
the local branch, so the folder name does not need to match the branch name. Add
`-d` or `--delete-remote` to delete the resolved branch from `origin` too. If
the resolved branch is already missing from `origin`, `rm` warns and continues:

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

## Stacked PRs

`gh wt stack` turns the worktree you are standing in into a stack of pull
requests, with one folder per layer so several people or agents can work
different layers at the same time. It is a converter, not a creator: start work
normally, and convert when the change outgrows one review.

```bash
gh wt add some-feature
cd some-feature
# ...work grows too large for one PR...
gh wt stack base
```

Conversion renames the branch to `billw/some-feature/01-base`, moves the
worktree to `some-feature/01-base/`, and turns `some-feature/` into a plain
container folder holding the layers. The name argument is optional: without it
layer 1 is just `01`, and `gh wt stack --rename 01 01-base` renames it later.
Conversion also enables `git rerere` in the shared `.bare/config`, which
`gh stack init` does not do despite its README.

Add layers, each in its own worktree:

```bash
cd 01-base
gh wt stack --add api      # -> some-feature/02-api, billw/some-feature/02-api
gh wt stack --add ui       # -> some-feature/03-ui,  billw/some-feature/03-ui
```

```text
some-feature/
├── 01-base/   billw/some-feature/01-base
├── 02-api/    billw/some-feature/02-api
└── 03-ui/     billw/some-feature/03-ui
```

**Folder and branch always spell the same thing.** The branch path under the
prefix is the folder path, which is why conversion has to rename layer 1: a ref
cannot be both a leaf and a directory, so `billw/some-feature` existing would
block `billw/some-feature/02-api` from ever being created.

**Renaming layer 1 closes an open PR on it.** GitHub closes any pull request
whose head branch is renamed. `gh wt stack` prompts before doing it, and a run
whose output is captured — an agent, a script — fails instead, so `--force` is
required to go ahead. `gh wt stack --rename` is gated the same way.

**The bottom layer is the host.** It holds `gh stack`'s metadata and is the
only layer without a separate worktree of its own, because `gh stack view`
needs the host on a branch. `gh stack add` also only works at the top of a
stack, so `gh wt stack --add` briefly detaches the layer worktrees above,
climbs, adds, and puts them back. Uncommitted work in those folders is
preserved.

**Cascade with `gh wt stack --rebase`, never `gh stack rebase`.** The latter
checks each layer out in turn and dies on any branch another worktree holds.
`--rebase` detaches every layer worktree first and restores it afterwards:

```bash
gh wt stack --rebase
```

Every agent must be idle and committed when it runs. On a conflict the layers
are left detached and the message points you at the host folder; resolve there
and run `gh wt stack --rebase --continue`.

`gh wt stack` refuses to run on the trunk worktree, on a detached HEAD, on a
worktree that already hosts a stack, and when the `gh stack` extension is
missing. Install it with `gh extension install github/gh-stack`.

If the adopted branch is still too large for one review (more than 3 commits
or 500 changed lines), `gh wt stack` says so. When its output is being captured
rather than shown in a terminal — as when an AI agent runs it — it also prints
a short block pointing at `gh wt stack --agent`, which prints the full
layer-splitting procedure. Run `gh wt stack --agent` yourself to see exactly
what agents are told.

`gh wt status` renders a stack once, under its container, rather than once per
layer worktree:

```text
some-feature
  Stack: #2331 - 3 layers
  Status: Clean
  Layers:
    1  billw/some-feature/01-base  #2326 (OPEN)
    2  billw/some-feature/02-api   #2327 (OPEN)
    3  billw/some-feature/03-ui    #2328 (OPEN)
```

A layer needing a rebase is flagged and makes `status` exit non-zero. If
`gh stack view` is unavailable, `status` falls back to the ordinary
single-branch display.

`gh wt rm` treats a stack as all-or-nothing. Remove it by its container name:

```bash
gh wt rm some-feature
```

Removing a single layer is refused, because deleting one layer's branch out of
a live stack strands the rest. `gh wt rm --merged` removes a stack only when
every layer's PR is merged or closed, and never sweeps layers one at a time as
their individual PRs land.

Known limitation: `gh stack trunk` and `gh stack checkout <trunk>` cannot work
in this layout, because trunk is already checked out in its own worktree.


Show status for all worktrees:

```bash
gh wt status
```

## Requirements

- GitHub CLI (`gh`)
- Git
- Python 3.10+ (3.11+ when a `worktree-config.toml` is present, for `tomllib`)
- Bare-git layout created by `gh wt clone` or `gh wt init`, or an existing repo with `.bare/`

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
