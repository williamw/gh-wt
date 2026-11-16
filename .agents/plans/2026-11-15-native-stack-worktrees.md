# Native gh-stack worktrees

Approved 2026-11-15 for implementation with sub-agents. Baseline source:
`b4d7c9c4dcaf665f20df2cf48a60d3ef0a09cd2a`.

The owner explicitly removed branch-renaming conversion and grouped/numbered
containers entirely. Deliver a reviewable PR; do not merge or change the installed
extension under this implementation request. Related skill changes are delivered
separately through the owner's upstream workflow.

## Goal and scope

Hero case: an existing tracked stack distributed across normal worktrees gets
one stable worktree per layer, missing worktrees receive normal setup hooks,
status groups the real membership, and native gh stack owns history and PRs.
Preserve dirty/unrelated worktrees, source/ignored files, main notes and existing
served checkouts. No real test PR merges, production state, credentials or live
services in verification. No new grouped layouts, forced numbering, branch/folder
coupling, automatic migration, private metadata writes or wrapper rebase journal.

Docs-only is insufficient because status/setup/add/removal currently encode the
obsolete host/container model. Retaining the detach wrapper duplicates upstream
recovery. Keep worktree utilities; remove that obsolete architecture outright.


## Acceptance criteria

- AC1 [user-stated]: remove stack conversion, branch-renaming/PR-closing machinery,
  grouped containers, numbered layer creation, direct catalog writes, and
  detach/restore operations. Existing nested worktrees are not moved automatically.
- AC2 [user-approved direction]: gh stack owns membership, history, PR publication,
  merging and recovery. gh wt owns actual worktree paths, hooks, status and safe
  removal; folder labels never establish branch or stack identity.
- AC3 [agent-proposed]: setup is idempotent; dry run creates no dirs/refs and runs
  no hooks. Reuse existing worktrees unchanged, including unrelated dirty ones;
  run hooks only for new worktrees. Preserve untracked/ignored data and HEADs.
- AC4 [agent-proposed]: add creates a flat independent worktree and a new upstream
  stack member using public gh stack operations; no other worktree switches branch
  or detaches. Normal gh-wt prefix/alias/hook/upstream rules apply; -L keeps local.
- AC5 [agent-proposed]: status groups actual distributed layers once per stack,
  supports multiple stacks, reports unavailable/ambiguous information, and retains
  ordinary worktree status. Do not flatten the shared catalog into one stack.
- AC6 [agent-proposed]: unsupported/missing gh stack, collisions and busy affected
  worktrees fail before unsafe mutations. Hook/partial setup failures report what
  remains. Preserve meaningful upstream errors and recovery guidance.
- AC7 [agent-proposed]: per-worktree removal supports --keep-branch and --dry-run;
  --merged dry run previews actual targets. Active stack branches are not silently
  deleted. No automatic whole-stack/container deletion or cleanup after merge.
- AC8 [source-of-truth]: owner project dependencies/tests execute in the repo-owned
  container through Makefile, with the existing Pixi toolchain/lock extended to
  linux-64 while retaining osx-arm64. No host project dependency installs, broad
  home/credential/socket mounts or interruptions to household services.
- AC9 [agent-proposed]: retained ordinary gh-wt regression tests and meaningful new
  public CLI/real-Git fixtures pass. Native gh-stack 0.3.0 distributed-worktree
  rebase/conflict behavior is exercised in isolation, not merely assumed from docs.


## CLI and state contract

These are the proposed surfaces approved with the plan:

- gh wt stack: show help; no conversion or file mutation.
- gh wt stack setup [--dry-run]: operate on an already tracked current stack.
  Obtain membership through supported gh stack JSON and map branches with Git's
  actual worktree list. Create missing normal sibling worktrees; reuse all owners.
  For a folder collision, stop and explain normal gh wt add <alias> -b <branch>
  resolution. No pushes or existing-worktree hook reruns during setup.
- gh wt stack add <name> [-L|--local]: new layer plus normal flat worktree. Preserve
  the normal branch-prefix/folder conventions without coupling names together.
  Preflight owners/targets. Prepare the branch/worktree and adopt it through public
  upstream operations in the correct owner, avoiding movement of other HEADs.
  Push only the new layer by default, tracking itself; -L keeps it local.
- gh wt stack agent: retain useful static agent guidance, rewritten for this flow.
- gh wt stack rename/rebase, old positional conversion and hidden old flags: reject
  with actionable usage guidance. Rebase/recovery goes to gh stack directly.
- gh wt rm <folder> --keep-branch [--dry-run]: deliberate single-worktree cleanup.
- gh wt rm --merged [--dry-run]: preview/remove eligible merged worktrees safely.
  Preserve normal remote deletion opt-in and refuse incompatible flag combinations.
- gh wt status: render stack identity/PRs, actual paths/branches/dirty state and
  ordinary worktrees; use supported structured output, not catalog internals.

Use gh stack >=0.3.0 supported capabilities. Example missing tracking guidance:
"No tracked stack for the current branch. Use gh stack checkout <stack> first."
Removed feature guidance should say conversion/grouped layouts were removed and
point to gh stack init/link plus gh wt stack setup. Messages must explain the
actual state/action; do not print untrusted branch text as agent instructions.

Happy path: gh stack link --base main --open <PRs...>; gh stack checkout <stack>;
gh wt stack setup; gh wt status. Existing PR-number link avoids branch pushes.
Native history: gh stack rebase modes, submit, push, merge. No wrapping of journals,
manual catalog mutation or automatic stash/reset/prune.


## Implementation and verification

Implementation owns gh_wt.py, README.md and AGENTS.md. Test author owns existing
and new regression tests, .devcontainer, Makefile and Pixi files. Keep their
work independent against the approved contract; a read-only tester runs the
complete `make check` after both writers finish. No project dependencies install
on the host. Builds/runs are capped at two CPUs and 2 GiB, with no home, token or
Podman-socket mounts. Pin and verify gh-stack 0.3.0 for isolated native Git tests.

Keep ordinary regressions and verify setup/add/status/removal through the public
CLI, including aliases, multiple stacks, unrelated dirty worktrees, collisions,
hook failures, missing capabilities, mutation-free previews and retained branches.
Use actual native distributed-worktree rebase/conflict recovery fixtures with
network/account access disabled. Existing nested worktrees remain in place.

Native view JSON lacks a stack number in v0.3.0: group by actual ordered membership
and show PRs. Do not read/write private catalog schemas. Full PR URLs avoid the
numeric-local-branch ambiguity in push-free `gh stack link` adoption. Detached
worktrees remain untouched without guessing ownership from paths or SHAs.

Commit and publish only after independent verification. Preserve the approved
contract, report exact checks and limitations, and leave merges/install/sync to
their own authorization.
