# gh-wt

GitHub CLI extension for managing repositories that use a bare-git worktree layout.

`gh wt` keeps one bare repository in `.bare/` and checks branches out as sibling
worktree directories. A `.git` file at the root points at `.bare/`, so git
commands such as `git worktree list` and `git fetch` work from the repo root:

```text
repo/
├── .bare/
├── .git          # file: "gitdir: ./.bare", so git works from repo/
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

In a bare-layout repo, `init` fixes the fetch refspec if it is missing, adds
the root `.git` pointer if it is missing, and scaffolds `worktree-config.toml`, asking for an optional branch prefix
(Enter for none). If it finds the legacy `setup-worktree.sh`, it offers to
rename it to `worktree-setup.sh`; either way the config records the name in
use. In a normal clone, `init` offers to convert it in place:
`.git/` becomes `.bare/`, the checked-out branch moves into its own worktree
folder, and local branches, stashes, and reflog all survive. Conversion
requires a fully clean tree (no modified, staged, or untracked files). Ignored
files and folders, such as `.env` files, build output, or private local
records, are moved into the new worktree at the same paths. They pass through
a temporary `.gh-wt-ignored/` folder at the root; if a conversion is
interrupted, your files are there, and `init` refuses to run again until that
folder is emptied or removed.

Add a worktree for a branch:

```bash
gh wt add feature-branch
```

If the branch already exists locally, for example one left behind by a
conversion, `add` checks it out as-is instead of creating it. It sets the
upstream to `origin/<branch>` when that exists and none is configured, and
pushes a local-only branch with `-u` unless `--local` is given.

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

The local branch safety check passes when origin already has the branch's
commits, either on `origin/<branch>` or on the default branch (`origin/main`).
A branch made with `gh wt add -L` and fast-forwarded into `main` can therefore be
removed once `main` is pushed and fetched, without `--force`. Use `--force` to
bypass the check when you know you want to delete a worktree with unpushed or
untracked work.

`--dry-run` previews the actual eligible targets for both single-folder and
`--merged` removal. `--keep-branch` retains the local branch and skips its
unpushed-commit check. Local and ignored files are protected unless `--force`
is given.

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

Use `gh stack >=0.3.0` for membership, history, PRs, merging and recovery.
`gh wt` creates independent worktrees, runs setup hooks and shows their actual
paths. Branches and folder aliases stay independent; existing nested worktrees
stay where they are.

Adopt existing PRs by full URL without pushing their branches:

```bash
gh stack link --base main https://github.com/owner/repo/pull/123 https://github.com/owner/repo/pull/124
gh stack checkout <stack>
gh wt stack setup --dry-run
gh wt stack setup
gh wt status
```

Use full PR URLs for this flow. In gh stack 0.3.0, a numeric argument is first
checked as a local branch name; if that branch exists, `link` treats it as a
branch and may push it instead of looking up the PR number.

Leave `--open` off when adopting existing PRs unless you intend to mark every
draft in the stack ready for review.

For local branches, use `gh stack init --base main <bottom> <next> ...`
to establish the stack first. Run setup from a branch belonging to that tracked
stack. When trunk belongs to several stacks, enter a member worktree so native
JSON discovery can resolve the intended stack.

`gh wt stack setup` reuses every registered owner at its actual path, preserving
its HEAD and local files. Missing branches receive normal sibling worktrees,
using the branch's final path segment as the default folder. A folder collision
stops setup before any worktree is created. Resolve it with an alias, then retry:

```bash
gh wt add api-review -b user/api -L
gh wt stack setup
```

Setup runs the normal configured hook only for new worktrees, never pushes, and
can be repeated. `--dry-run` creates no directories or refs and runs no hooks.
If a hook fails, the created worktree and branch remain; inspect them and rerun
the hook manually. Repeating setup reuses that worktree without rerunning its hook.

Add a new top layer from any member worktree:

```bash
gh wt stack add api
gh wt stack add ui -L
```

The normal branch prefix applies to a bare name. For example, with prefix
`billw`, `api` creates branch `billw/api` in sibling folder `api`. Explicit
`user/api` keeps that branch and uses folder `api`. Existing branch or folder
collisions are refused. The branch starts at the top layer, and native `gh stack
add` adopts it through its actual worktree owner. Existing worktrees never switch
branches. The new layer alone is pushed with its own upstream unless `-L` is
specified; the normal setup hook runs once after adoption and any push.

Use the actual paths shown by `gh wt status` to move between distributed layers.
Native navigation commands may report that another worktree owns the target;
enter that worktree. Status groups each discovered stack once by trunk and
ordered membership, with each layer's PR, actual path, dirty state and rebase
warning. It also shows ordinary worktrees. Missing, ambiguous or unavailable
stack information is reported explicitly. Native 0.3.0 JSON does not expose a
stack number, so the heading identifies the stack by bottom branch and trunk.

Coordinate affected agents before native history changes:

```bash
gh stack rebase --upstack --no-trunk  # cascade layers above the current fix
gh stack push
gh stack submit --auto
```

To mark only a new layer's PR ready for review, follow submission with
`gh pr ready <new-layer-PR-URL>`. Add `--open` to `submit` only when you intend to
mark every draft in the stack ready.

`--upstack` limits the cascade to layers above the current branch. `--no-trunk`
alone skips fetching and rebasing with trunk but still rebases all stack layers
onto each other, regardless of the current layer. Read `gh stack rebase --help`
for whole-stack/trunk modes. Native rebase owns
distributed-worktree coordination and conflict recovery; resolve in the worktree
it reports and use `gh stack rebase --continue` or `--abort`. Check
`git config --get rerere.enabled` rather than assuming its value. `gh stack sync`
can change refs and prune merged branches; review its flags before running it.

After an authorized merge, choose cleanup per worktree and inspect service
ownership and retained files first:

```bash
gh wt rm api --keep-branch --dry-run
gh wt rm api --keep-branch
gh wt rm --merged --dry-run
```

`--keep-branch` removes just that worktree, including a live stack member, while
retaining the local branch. It cannot be combined with `--delete-remote`.
Without it, active stack branches are protected even with `--force`.
`--merged` previews or removes eligible individual worktrees; it does not delete
a stack or container as a unit. Unavailable membership blocks branch deletion
and points to `--keep-branch` for deliberate worktree-only cleanup. Dirty or
ignored files still require explicit `--force`.

`gh wt stack` shows help. Conversion, automatic branch renaming, numbered/grouped
containers, `gh wt stack rename`, wrapper `rebase`, and old hidden flags were
removed. Use `gh stack init` or `link`, then `gh wt stack setup`.
`gh wt stack agent` prints static guidance for agents.

## Requirements

- GitHub CLI (`gh`)
- Git
- Python 3.10+ (3.11+ when a `worktree-config.toml` is present, for `tomllib`)
- Bare-git layout created by `gh wt clone` or `gh wt init`, or an existing repo with `.bare/`

## Development

Run project tooling in the repository-owned container:

```bash
make check
```

The Makefile uses the same Pixi-backed environment as `.devcontainer/`, with
source in this checkout and dependency caches in container storage. Do not
install project dependencies on the host.

To install a tested checkout as the active extension, only when intended:

```bash
gh extension remove gh-wt
gh extension install .
gh wt --help
```
