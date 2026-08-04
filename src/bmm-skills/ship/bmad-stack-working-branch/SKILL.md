---
name: bmad-stack-working-branch
description: 'Stacked-PR projects only: provision or refresh the clean local anchor branch `prep/stack-working` so new Copilot/BMAD work starts from the current stack culmination rather than stale `main` or a review-only `-pr-ready` branch. Use when the user says "set up the stack working branch", "refresh the prep branch from the current stack", "what branch should we start from", or before opening new interim work while the stack is still unmerged.'
---

# Stack Working Branch Workflow

**Goal:** Maintain one clean, stable local anchor branch — `prep/stack-working` by default — that
always points at the **current stack culmination** while the stack is still unmerged into `main`.
This branch is where new Copilot/BMAD sessions should begin, but it is **not** where long-lived
feature work should accumulate.

**Why this exists:** during the interim phase, `main` is missing in-flight stack changes, while the
active `-pr-ready` and integration branches are review artifacts. Starting new work from `main`
drops unreleased stack context; starting from a component `-pr-ready` branch ties new work to one
review layer instead of the whole stack. The correct bridge is a dedicated prep anchor based on the
current integration/validation tip.

## Operating contract

- `prep/stack-working` is an **anchor branch**, not an implementation branch.
- It should stay **clean and disposable**: no unique feature commits live there.
- Every new issue/story should branch **from** `prep/stack-working`, not commit directly on it.
- Whenever the canonical stack tip moves (rebase cascade, stack extension, new integration
  culmination), refresh `prep/stack-working` to that new tip.

## Conventions

- Bare paths resolve from the skill root.
- `{skill-root}` resolves to this skill's installed directory.
- `{project-root}` resolves to the project root.
- `{skill-name}` resolves to `bmad-stack-working-branch`.

## On Activation

1. Resolve customization:
   `python3 {project-root}/_bmad/scripts/resolve_customization.py --skill {skill-root} --key workflow`.
   On failure, merge `customize.toml`, `{project-root}/_bmad/custom/{skill-name}.toml`, then
   `{project-root}/_bmad/custom/{skill-name}.user.toml`: scalars override, tables deep-merge,
   keyed arrays-of-tables replace/append, other arrays append.
2. Execute `{workflow.activation_steps_prepend}` in order and load `{workflow.persistent_facts}`;
   `file:` entries resolve under `{project-root}`.
3. Load `{project-root}/_bmad/bmm/config.yaml`; resolve `user_name`, `communication_language`,
   `user_skill_level`, and the current datetime. Communicate in the configured language.
4. Greet `{user_name}`, then execute `{workflow.activation_steps_append}` in order.

<workflow>

<step n="1" goal="Confirm this is an interim stacked-PR phase">
  <action>Require the project's stacked-branching rule or equivalent stacked-PR convention. This
  workflow only applies while the current source/review stack is not yet fully merged to the default
  branch.</action>
  <action>Require a clean worktree and no merge, rebase, cherry-pick, or revert in progress before
  moving any branch ref.</action>
  <check if="the project does not use a stacked-PR convention">
    HALT: "This project does not use a stacked-PR workflow, so a dedicated stack working branch is not needed."
  </check>
</step>

<step n="2" goal="Resolve the canonical current stack culmination">
  <action>Fetch the authoritative remotes first. Require both `upstream` and `origin` when the
  project uses a fork topology.</action>
  <action>Resolve the stack culmination branch from live topology, not branch-name vibes:
  prefer the current canonical integration/validation branch that descends from the final open
  component stack tip and represents the complete cumulative diff against the real default branch.</action>
  <action>Accept either:
  1. the upstream integration/validation branch itself, or
  2. the fork-side mirror of that same branch,
  but only when both resolve to the same commit or one is the clearly newer intended successor.</action>
  <check if="multiple candidate culmination branches exist and the correct one cannot be determined unambiguously">
    HALT and report the competing refs plus SHAs. Do not guess which stack tip should anchor future work.
  </check>
  <action>Record the exact resolved culmination ref and SHA. This is the only commit the anchor
  branch may track.</action>
</step>

<step n="3" goal="Provision or refresh the anchor branch safely">
  <action>Use `prep/stack-working` as the default branch name unless the project has already
  standardized on a different anchor branch. Prefer one stable name; do not keep minting timestamped
  prep branches for this purpose.</action>
  <action>If `prep/stack-working` does not exist, create it at the resolved culmination SHA.</action>
  <action>If it exists and already points at that exact SHA, leave it unchanged.</action>
  <action>If it exists and is strictly behind the resolved culmination SHA, fast-forward it to that
  SHA without rewriting any other branch.</action>
  <check if="prep/stack-working has commits that are not ancestors of the resolved culmination SHA">
    HALT. This means someone used the anchor branch for real work. Do not silently rebase or reset
    it. Tell the user to branch or archive those unique commits first, then recreate/refresh the
    anchor branch.
  </check>
  <action>Optionally mirror the anchor branch to `origin` if the team wants a shared durable anchor,
  but never publish it to `upstream` as a review artifact unless the user explicitly asks.</action>
</step>

<step n="4" goal="Make the usage model explicit">
  <action>State the operational rule clearly:
  - Start new Copilot/BMAD sessions from `prep/stack-working`.
  - Immediately branch new issue/story work from it.
  - Never stack long-lived feature commits directly on `prep/stack-working`.
  - After any stack-mutating workflow (rebase cascade, stack extension, refreshed integration branch),
    re-run this workflow before starting the next issue.</action>
  <action>If the user wants, create the immediate next working branch from `prep/stack-working`
  after provisioning it, but keep the anchor branch itself clean.</action>
</step>

<step n="5" goal="Sync dependent project pointers to the moved head">
  <action>Determine whether any other repository depends on this one by revision while this stack is
  still unmerged. Look for git dependency pins, submodule SHAs, lockfiles, vendored copies, image
  tags, and any recorded evidence or docs that embed the revision.</action>
  <check if="no other repository pins this one by revision">
    Skip this step. Pointer sync only matters while a dependent must track an unreleased head.
  </check>
  <action>State the rule: while the depended-upon stack is not merged into the default branch and not
  part of an official release, every dependent must point at the **current canonical head**, not at
  whatever commit it was pinned to when it was last touched.</action>
  <action>Resolve the new head with the same evidence used in step 2, then confirm the commit is
  reachable from that canonical head before pinning it.</action>
  <check if="the currently pinned commit survives only on an archived or rewritten branch">
    Report it as an orphaned pin. A rebase cascade or stack extension rewrote the lineage, so the
    dependent is building against a commit no live branch contains. It must be moved to the
    equivalent commit on the canonical head, not left as-is.
  </check>
  <action>Update the pointer and regenerate any lockfile in the same change, so the pin and the
  resolution never disagree. Regenerate lockfiles with the project's own tooling; never hand-edit or
  search-and-replace the revision. A new head can move package versions and dependency sets, not just
  the SHA, and substitution silently produces a lockfile that pins a version the revision does not
  declare.</action>
  <check if="recorded evidence, release notes, or validation artifacts embed the old revision">
    Those artifacts assert that a validation ran against a specific revision. Regenerate them by
    re-running that validation against the new head. Never rewrite them to claim a run that did not
    happen, and never bump the pointer alone and leave the evidence asserting the old revision.
  </check>
  <check if="the dependent project has its own unmerged stack">
    It needs its own anchor branch before the pointer bump lands. Run this workflow in that
    repository too, and make the bump on a working branch off its anchor — never as a commit on the
    anchor itself.
  </check>
  <check if="the pointer bump requires source changes beyond the pointer itself">
    HALT and report. A bump that needs code changes is a migration, not a pointer sync, and it should
    be scoped and reviewed as its own change.
  </check>
</step>

<step n="6" goal="Report the anchor state">
  <action>Report:
  - the resolved culmination ref and SHA,
  - the anchor branch name,
  - whether it was created, unchanged, or fast-forwarded,
  - whether a shared `origin` mirror exists,
  - any dependent project whose pointer was updated, or still needs updating,
  - and the rule that this is the default branch to start future interim chats from until the stack merges into `main`.</action>
  <action>Run the resolved `{workflow.on_complete}` when non-empty.</action>
</step>

</workflow>
