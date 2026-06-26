# Remove closed PR worktrees with `gh wt rm -m`

## Goal

Make `gh wt rm -m` remove worktrees whose associated pull requests are closed as well as merged.

## Refined problem statement

The current `rm -m` behavior only removes worktrees when `gh pr view <branch>` returns `state == "MERGED"`. Users also want abandoned branches whose PRs are closed to be cleaned up by the same command.

## Chosen approach

Treat PR states `MERGED` and `CLOSED` as removable for `rm -m`. Keep the existing safety checks, branch deletion behavior, and optional remote deletion unchanged.

## Key constraints and assumptions

- Do not change single-worktree `gh wt rm <folder>` behavior.
- Do not change `--force` semantics.
- Do not remove worktrees for open PRs or branches with no PR.
- Closed PRs should still respect the local unpushed-commit safety check unless `--force` is used.

## Discovery summary

- `gh_wt.py` gates merged cleanup with `pr_info.get("state") == "MERGED"`.
- `test_gh_wt.py` currently includes a test asserting closed PRs are skipped, so tests must be updated.
- README describes `rm --merged` as removing only merged PR worktrees.

What would change the plan: if closed PRs needed a separate flag or different safety policy. The user confirmed they want `gh wt rm -m` itself to include closed PRs.

## Likely files affected

- `gh_wt.py`
- `test_gh_wt.py`
- `README.md`

## User-provided prerequisites

None.

## Needed from you before implementation

Nothing.

## Grill Bill workflow

Route: tenacious-only.

Sequence followed:
- Challenged the request by checking whether this should be a new flag or existing flag behavior.
- Cut unnecessary work by preserving the existing `-m` command and safety logic.
- Simplified the design to a small predicate change plus tests/docs.
- Scoped the fastest validating version to `MERGED` + `CLOSED` only.
- Inventoried uncertainties: closed PR inclusion is confirmed; open/no-PR behavior remains unchanged.

## Skills required for execution

- `grill-bill`
- `verification-before-completion`

## Step-by-step implementation plan

1. Add or inline a predicate so `rm -m` treats `MERGED` and `CLOSED` PR states as removable.
2. Update tests in `TestRemoveMergedFlag` so closed PR worktrees are removed and open/no-PR worktrees remain skipped.
3. Update README wording to say `rm --merged` removes worktrees with merged or closed PRs.
4. Run the test suite with `pixi run test`.

## Verification/testing expectations

- `pixi run test` passes.
- Tests prove closed PRs are removed by `rm -m`.
- Tests prove open PRs are still skipped.

## Deferred work / non-goals

- Renaming `--merged` is out of scope.
- Adding a separate `--closed` flag is out of scope.
- Changing remote deletion behavior is out of scope.

## Execution options

- Execute inline in this session.
- Stop after the plan.
