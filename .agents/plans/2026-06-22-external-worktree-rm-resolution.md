# External worktree rm resolution

## Goal

Make `gh wt rm <folder>` remove the same worktree that `gh wt status` reports when the worktree is registered with Git but lives outside the repo-root sibling layout.

## Refined problem statement

`gh wt status` lists every Git-registered worktree from `git worktree list`, including temporary or external paths such as `/private/tmp/.../modcloud-pr1176`. `gh wt rm <folder>` currently only checks `<repo-root>/<folder>`, so it can report `Error: Worktree folder not found: modcloud-pr1176` even though `status` just showed a registered worktree with that folder basename.

The stale-prune feature only handles missing registered paths. The reported failure involved an existing external detached worktree, so the path was not stale; it was simply outside the expected sibling directory.

## Chosen approach

Resolve `rm <folder>` through Git's registered worktree list before declaring the folder missing:

1. Prefer the existing sibling path `<repo-root>/<folder>` when it exists.
2. Otherwise, find a registered worktree path whose basename matches `folder`.
3. If the registered path exists, remove that actual path.
4. If the registered path is missing, keep the existing stale metadata prune behavior.
5. If the removed worktree is detached, skip local/remote branch deletion because no branch can be resolved safely.

## Key constraints and assumptions

- Preserve normal sibling worktree behavior.
- Preserve stale metadata pruning for missing registered paths.
- Do not delete branches for detached worktrees.
- Keep branch safety checks for branch-backed worktrees.
- Match by basename because the public CLI accepts folder names, not absolute paths.
- If duplicate matching basenames exist, prefer the sibling path when present; otherwise the first registered path remains the practical current behavior unless ambiguity is later reported.

## Discovery summary: questions answered, assumptions accepted, and what would change the plan

Questions answered:

- The user confirmed the observed failure occurred with `gh wt rm modcloud-pr1176`.
- Runtime reproduction showed that stale missing paths already prune correctly.
- Runtime reproduction showed that an existing external detached worktree exactly reproduces the reported error.

Assumptions accepted:

- Removing an external registered worktree by basename is the desired behavior because `status` displays it by basename.
- Detached worktree removal should be metadata/filesystem cleanup only, not branch deletion.

What would change the plan:

- If duplicate external worktrees with the same basename become a supported case, resolution should become ambiguity-aware and ask for a path.
- If users want to delete branches associated with stale missing records, that needs separate branch recovery and safety design.

## Likely files/systems/processes affected

- `gh_wt.py`
- `test_gh_wt.py`
- `README.md`
- `.agents/plans/2026-06-22-external-worktree-rm-resolution.md`

## Resolved operational details

- Command affected: `gh wt rm <folder> [-d] [-f]`
- Worktree registry source: `git worktree list --porcelain`, run from `.bare/`
- Original repro shape: external detached worktree under `/private/tmp/.../modcloud-pr1176`
- Verification commands:
  - `pixi run test`
  - local shell repro for external detached registered worktree

## User-provided prerequisites and needed from you before implementation

None.

Needed from you before implementation: nothing.

## Grill Bill workflow for this task

Route: `tenacious-only`.

This is implementation-bound debugging of an operational CLI failure. The workflow challenged the original stale-prune assumption, cut unnecessary branch deletion for detached worktrees, simplified the design to registered-path resolution, scoped the fastest validating version to `rm <folder>`, inventoried uncertainty through runtime repros, and proceeds only after approval.

## Skills required for execution

- `grill-bill`
- `debug`
- `verification-before-completion`

## Step-by-step implementation plan

1. Add a helper that resolves a requested folder name to either the repo-root sibling path or a registered worktree path from porcelain output.
2. Reuse the registered path lookup for stale missing paths so stale pruning still works.
3. Update `cmd_rm` to use the resolved path.
4. When branch resolution returns empty or `HEAD`, treat the worktree as detached: remove the worktree and skip branch/local/remote deletion.
5. Keep existing safety checks and branch deletion for branch-backed worktrees.
6. Add regression tests for:
   - removing an external detached registered worktree by basename
   - removing an external branch-backed registered worktree by basename
   - stale missing registered path still pruning
7. Update README to document external registered worktree removal and detached behavior.
8. Run full tests and the original external-detached repro.

## Verification/testing expectations

- `pixi run test` exits 0.
- Original external detached repro exits 0 for `gh wt rm modcloud-pr1176`.
- Repro no longer leaves the external worktree registered.
- Tests assert detached removal does not attempt branch deletion.

## Deferred work or non-goals

- No new `gh wt prune` command in this change.
- No duplicate-basename disambiguation.
- No branch deletion for detached worktrees.
- No branch deletion for missing stale records.
- No `status` formatting changes.

## Execution options

Approved for inline execution.
