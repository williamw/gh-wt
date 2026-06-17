# Stale worktree prune support

## Goal

Make `gh wt rm <folder>` handle the corner case where Git still has a worktree registry entry, but the worktree folder has already been deleted.

## Refined problem statement

Today `gh wt status` can show a stale worktree with unknown branch/status, but `gh wt rm <folder>` fails with `Worktree folder not found` because it only checks for `<repo>/<folder>` on disk. In this state, Git expects `git worktree prune`, not `git worktree remove`, because there is no root folder left to remove.

## Chosen approach

When `gh wt rm <folder>` cannot find the folder on disk, inspect Git's worktree registry. If a registered worktree has the same folder basename and its registered path is also missing, treat it as stale metadata and run `git worktree prune -v` automatically.

Do not delete local or remote branches in this stale-record path, because the checked-out branch cannot be safely resolved from a missing worktree.

## Key constraints and assumptions

- Keep existing behavior for normal worktree removal.
- Keep existing error behavior when no matching stale record exists.
- `--force` is not required for stale metadata cleanup.
- Matching by folder basename matches `gh wt rm`'s existing folder-oriented interface.
- Stale cleanup should be metadata-only; branch deletion is out of scope.

## Discovery summary

Questions answered:

- Automatic prune vs `--force`/new command: automatic prune was approved.

Assumptions accepted:

- Branch deletion should not happen in stale metadata cleanup.
- `git worktree prune -v` is the correct recovery operation for missing worktree paths.

What would change the plan:

- If the desired behavior included branch deletion for stale records, implementation would need explicit branch lookup from registry metadata and additional safety checks.
- If duplicate folder basenames across registered worktree paths are possible and need special handling, matching would need to become path-aware.

## Likely files/systems/processes affected

- `gh_wt.py`
- `test_gh_wt.py`
- `README.md`
- `pixi run test`

## Resolved operational details

- Command affected: `gh wt rm <folder> [-d] [-f]`
- Git recovery command: `git worktree prune -v`, run from `.bare/`
- Stale detection source: `git worktree list --porcelain`

## User-provided prerequisites

None.

Needed from you before implementation: nothing.

## Grill Bill workflow for this task

Route: `tenacious-only`.

The request is implementation-bound and based on a real operational failure. The plan challenges the request by distinguishing stale metadata cleanup from branch/worktree deletion, cuts unnecessary branch deletion, simplifies the design to one automatic prune path, scopes the fastest validating version to `rm <folder>`, inventories the branch-safety uncertainty, and executes only after approval.

## Skills required for execution

- `grill-bill`
- `git-worktree`
- `verification-before-completion`

## Step-by-step implementation plan

1. Add a helper that parses `git worktree list --porcelain` output into registered worktree paths.
2. Add a helper that finds a stale registered worktree by requested folder basename.
3. In `cmd_rm`, before returning `Worktree folder not found`, check for a matching stale record.
4. If found, print a clear message, run `git worktree prune -v`, print the prune output if available, and exit successfully.
5. Leave local and remote branch deletion untouched for normal removals only.
6. Add regression tests for:
   - missing folder + matching stale registry entry prunes automatically and exits 0
   - missing folder + no matching stale registry entry still errors
7. Update README to document the stale-record cleanup behavior.
8. Run the full test suite.

## Verification/testing expectations

- `pixi run test` passes.
- Tests assert the prune command is invoked from `.bare/`.
- Tests assert no branch deletion is attempted in the stale path.

## Deferred work or non-goals

- No new `gh wt prune` command.
- No branch deletion for stale records.
- No changes to `gh wt status` formatting.
- No remote branch deletion in stale-record cleanup.

## Execution options

Approved for inline execution.
